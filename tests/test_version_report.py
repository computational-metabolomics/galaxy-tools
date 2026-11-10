import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from catalogue.dependencies import build_main_sources, check_conda_package
from catalogue.version_report import _status, write_version_report


class VersionReportTests(unittest.TestCase):
    def test_summary_separates_package_upstream_and_unknown(self):
        requirement = {
            "name": "model",
            "version": "1.0",
            "channel": "bioconda",
            "latest_version": "1.0",
            "out_of_date": False,
        }
        wrapper = {
            "requirements": [requirement, {"name": "helper", "version": "2"}],
            "main_dependency": {
                "name": "model",
                "version": "1.0",
                "channel_url": "https://anaconda.org/bioconda/model",
                "sources": [
                    {
                        "label": "bioconda",
                        "url": "https://anaconda.org/bioconda/model",
                        "version": "1.0",
                        "newer": False,
                    },
                    {
                        "label": "PyPI",
                        "url": "https://pypi.org/project/model",
                        "version": "1.1",
                        "newer": True,
                    },
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            write_version_report(
                [{"title": "Model | <suite>", "wrappers": [wrapper, wrapper]}], path
            )
            report = path.read_text()
        self.assertEqual(report.count("| bioconda | 1.0 | No newer version found |"), 1)
        self.assertEqual(report.count("| PyPI | 1.1 | Newer available |"), 1)
        self.assertIn("| helper | 2 | Unknown | Unknown | Unknown |", report)
        self.assertIn("Model &#124; &lt;suite&gt;", report)

    def test_wrapperless_entry_reports_configured_upstream_sources(self):
        section = {
            "title": "Example Project",
            "wrappers": [],
            "main_dependency": {
                "name": "example-project",
                "version": "",
                "sources": [{
                    "label": "GitHub",
                    "url": "https://github.com/example/project",
                    "version": "1.2.0",
                    "newer": None,
                }],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            write_version_report([section], path)
            upstream_report = path.read_text().split("### Upstream releases/tags")[1]
        self.assertIn(
            "| Example Project | example-project | Unpinned | GitHub | 1.2.0 | Unknown |",
            upstream_report,
        )
        self.assertNotIn("No lookups configured", upstream_report)

    def test_empty_catalogue_reports_no_configured_lookups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            write_version_report([], path)
            report = path.read_text()
        self.assertEqual(report.count("No lookups configured"), 2)
        self.assertNotIn("Newer available", report)
        self.assertNotIn("No newer version found", report)

    def test_incomparable_conda_versions_remain_unknown(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "name": "model",
            "latest_version": "nightly-other",
        }
        with patch("catalogue.dependencies.requests.get", return_value=response):
            result = check_conda_package("model", "nightly")
        self.assertIsNone(result["out_of_date"])

    def test_known_numbering_mismatch_is_not_reported_as_current(self):
        sources = build_main_sources(
            {"version": "33"},
            [
                {
                    "url": "https://bitbucket.org/example/tool",
                    "version": "4.4.10",
                    "compare_versions": False,
                }
            ],
            offline=True,
        )
        self.assertEqual(sources[0]["version"], "4.4.10")
        self.assertIsNone(sources[0]["newer"])

    def test_prerelease_label_does_not_require_a_comparable_pin(self):
        self.assertEqual(_status(True, "3.15.0rc2"), "Newer available (pre-release)")
        self.assertEqual(_status(None, "2.0rc1"), "Unknown (pre-release)")
        self.assertEqual(_status(False, "2.0rc1"), "No newer version found (pre-release)")
        self.assertEqual(_status(True, "2.0"), "Newer available")
        self.assertEqual(_status(True, "nightly"), "Unknown")


if __name__ == "__main__":
    unittest.main()
