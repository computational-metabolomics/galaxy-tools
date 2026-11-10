import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import yaml
from catalogue import toolshed


class ToolShedLinksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        toolshed._published_repositories.cache_clear()
        self.addCleanup(toolshed._published_repositories.cache_clear)

    def configure(self, data):
        (self.folder / ".shed.yml").write_text(yaml.safe_dump(data), encoding="utf8")

    def tools(self, *ids):
        return [(self.folder / f"{tool_id}.xml", {
            "id": tool_id, "name": f"{tool_id} tool", "description": "Analyse spectra",
        }) for tool_id in ids]

    def test_explicit_repositories_use_owner_overrides_and_suite_first(self):
        self.configure({
            "owner": "default-owner", "name": "not-a-published-repository",
            "suite": {"name": "suite_analysis"},
            "repositories": {
                "analysis": {"owner": "contributor"},
                "suite_analysis": {},
            },
        })
        links = toolshed.repository_links(self.folder, [], offline=True)
        self.assertEqual([(link["owner"], link["name"], link["kind"]) for link in links], [
            ("default-owner", "suite_analysis", "suite"),
            ("contributor", "analysis", "repository"),
        ])
        self.assertEqual(links[1]["url"], "https://toolshed.g2.bx.psu.edu/view/contributor/analysis")

    def test_automatic_repositories_honour_cfmid_rename_and_lowercase_tool_ids(self):
        self.configure({
            "owner": "computational-metabolomics", "name": "unused",
            "auto_tool_repositories": {
                "name_template": "{{ 'cfmid4' if tool_id == 'cfmid' else tool_id }}",
            },
            "suite": {"name": "suite_cfmid4"},
        })
        links = toolshed.repository_links(self.folder, self.tools("cfmid", "CFMID_Predict"), offline=True)
        self.assertEqual([link["name"] for link in links], ["suite_cfmid4", "cfmid4", "cfmid_predict"])

    def test_missing_owner_and_missing_shed_file_do_not_infer_links(self):
        self.assertEqual(toolshed.repository_links(self.folder, self.tools("analysis")), [])
        self.configure({"name": "analysis"})
        with patch.object(toolshed.requests, "get") as get:
            self.assertEqual(toolshed.repository_links(self.folder, self.tools("analysis")), [])
        get.assert_not_called()

    def test_offline_keeps_configured_link_without_a_lookup_or_publication_claim(self):
        self.configure({"owner": "author", "name": "analysis"})
        with patch.object(toolshed.requests, "get") as get:
            links = toolshed.repository_links(self.folder, [], offline=True)
        get.assert_not_called()
        self.assertEqual(links[0]["status"], "unknown")
        self.assertEqual(links[0]["name"], "analysis")

    def test_successful_lookup_distinguishes_absent_deleted_and_existing_repositories(self):
        self.configure({"owner": "author", "repositories": {
            "present": {}, "absent": {}, "deleted": {}, "deprecated": {}, "other-owner": {},
        }})
        response = Mock()
        response.json.return_value = [
            {"owner": "author", "name": "present", "deleted": False},
            {"owner": "author", "name": "deleted", "deleted": True},
            {"owner": "author", "name": "deprecated", "deleted": False, "deprecated": True},
            {"owner": "someone-else", "name": "other-owner", "deleted": False},
        ]
        with patch.object(toolshed.requests, "get", return_value=response) as get:
            links = toolshed.repository_links(self.folder, [])
            toolshed.repository_links(self.folder, [])
        get.assert_called_once_with(
            "https://toolshed.g2.bx.psu.edu/api/repositories", params={"owner": "author"}, timeout=10,
        )
        self.assertEqual({link["name"]: link["status"] for link in links}, {
            "present": "published", "absent": "missing", "deleted": "missing",
            "deprecated": "published", "other-owner": "missing",
        })

    def test_failed_or_invalid_api_response_is_unknown_not_missing(self):
        self.configure({"owner": "author", "name": "analysis"})
        for data in ({"error": "unavailable"}, [None], [{"name": "analysis", "deleted": "false"}]):
            with self.subTest(data=data):
                toolshed._published_repositories.cache_clear()
                response = Mock()
                response.json.return_value = data
                with patch.object(toolshed.requests, "get", return_value=response):
                    self.assertEqual(toolshed.repository_links(self.folder, [])[0]["status"], "unknown")
        toolshed._published_repositories.cache_clear()
        with patch.object(toolshed.requests, "get", side_effect=requests.Timeout("timeout")):
            self.assertEqual(toolshed.repository_links(self.folder, [])[0]["status"], "unknown")

    def test_unsafe_url_components_are_rejected_before_network_access(self):
        for field in ("owner", "name"):
            for value in ("../../outside", "author/repo", "https://example.org", "bad?query", "with space"):
                with self.subTest(field=field, value=value):
                    self.configure({"owner": "author", "name": "analysis", field: value})
                    with patch.object(toolshed.requests, "get") as get:
                        with self.assertRaisesRegex(ValueError, r"\.shed.yml:.*letters, numbers"):
                            toolshed.repository_links(self.folder, [])
                    get.assert_not_called()

    def test_auto_name_templates_cannot_access_attributes_calls_or_external_variables(self):
        for source in (
            "{{ tool_id.__class__ }}", "{{ tool_id.lower() }}", "{{ tool_id[0] }}",
            "{{ cycler('a') }}", "{{ unknown }}", "{% for x in tool_id %}{{ x }}{% endfor %}",
        ):
            with self.subTest(source=source):
                self.configure({"owner": "author", "auto_tool_repositories": {"name_template": source}})
                with self.assertRaisesRegex(ValueError, r"\.shed.yml: invalid 'auto_tool_repositories.name_template'"):
                    toolshed.repository_links(self.folder, self.tools("analysis"), offline=True)

    def test_auto_name_template_is_required_and_result_is_validated(self):
        self.configure({"owner": "author", "auto_tool_repositories": {"description_template": "description"}})
        with self.assertRaisesRegex(ValueError, "name_template.*non-empty string"):
            toolshed.repository_links(self.folder, self.tools("analysis"), offline=True)
        self.configure({"owner": "author", "auto_tool_repositories": {"name_template": "../{{ tool_id }}"}})
        with self.assertRaisesRegex(ValueError, "letters, numbers"):
            toolshed.repository_links(self.folder, self.tools("analysis"), offline=True)


if __name__ == "__main__":
    unittest.main()
