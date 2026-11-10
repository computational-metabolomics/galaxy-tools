import gzip
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from catalogue import (
    build as catalogue_build,
    dependencies,
    metadata as catalogue_metadata,
    rendering,
    REPO_URL,
    sections as catalogue_sections,
    tools as catalogue_tools,
)
from catalogue.documentation import render_document
from lxml import html as lxml_html


ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "catalogue"


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.references = []
        self.headings = []
        self._heading = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if tag == "a" and attributes.get("href"):
            self.references.append(attributes["href"])
        if tag in {"img", "link", "script"}:
            attribute = "href" if tag == "link" else "src"
            if attributes.get(attribute):
                self.references.append(attributes[attribute])
        if tag in {"h1", "th"}:
            self._heading = [tag, []]

    def handle_endtag(self, tag):
        if self._heading and self._heading[0] == tag:
            self.headings.append((tag, "".join(self._heading[1]).strip()))
            self._heading = None

    def handle_data(self, data):
        if self._heading:
            self._heading[1].append(data)


def parse_html(path):
    parser = DocumentParser()
    parser.feed(path.read_text(encoding="utf8"))
    return parser


class VersionComparisonTests(unittest.TestCase):
    def test_candidate_with_more_components_is_compared(self):
        self.assertTrue(dependencies._newer_than("1.0", "1.0.1"))

    def test_candidate_with_fewer_components_is_compared(self):
        self.assertFalse(dependencies._newer_than("1.1.1", "1.1"))

    def test_unparseable_versions_are_unknown(self):
        self.assertIsNone(dependencies._newer_than("release", "next"))


class ReleaseTagTests(unittest.TestCase):
    def test_version_follows_the_name_even_when_the_name_has_digits(self):
        for tag, version in (
            ("v6.3.1", "6.3.1"),
            ("tool-6.3.1", "6.3.1"),
            ("PROJECT_4.4.10", "4.4.10"),
            ("ms2query-1.5.0", "1.5.0"),
            ("py3-v1.2", "1.2"),
            ("release3", "3"),
            ("release3rc1", "3rc1"),
            ("V4.3.0", "4.3.0"),
            ("Release3.1.0", "3.1.0"),
            ("r1.2.3", "1.2.3"),
            ("version2.10.1", "2.10.1"),
            ("V2.0", "2.0"),
        ):
            with self.subTest(tag=tag):
                self.assertEqual(dependencies._clean_tag(tag), version)

    def test_best_version_compares_whole_versions(self):
        self.assertEqual(dependencies._best_version(["V1.9", "V2.0", "V10.1"]), "10.1")

    def test_platform_suffixes_never_become_release_versions(self):
        ambiguous = [
            "v1.2.3-linux-64", "v1.2.3-linux-x86_64", "v1.2.3-cuda12",
            "v1-linux-64", "tool-1-linux-64.1", "nightly",
        ]
        for tag in ambiguous:
            with self.subTest(tag=tag):
                self.assertIsNone(dependencies._clean_tag(tag))
        self.assertEqual(dependencies._best_version([*ambiguous, "v1.2.2"]), "1.2.2")
        for tag in ("1.2.3rc1", "1.2.3.post1", "1.2.3+linux.64", "1!2.3", "msbuddy_data_v0.3.0"):
            with self.subTest(tag=tag):
                self.assertEqual(dependencies._clean_tag(tag), "0.3.0" if tag.startswith("msbuddy") else tag)


class BioconductorSourceTests(unittest.TestCase):
    def test_package_index_is_parsed(self):
        content = gzip.compress(
            b"Package: ExamplePackage\nVersion: 1.38.0\n\n"
            b"Package: OtherPackage\nVersion: 4.8.0\n"
        )

        self.assertEqual(
            dependencies._parse_bioconductor_index(content),
            {"ExamplePackage": "1.38.0", "OtherPackage": "4.8.0"},
        )

    @patch("catalogue.dependencies.bioconductor_latest", return_value="1.38.0")
    def test_bioconductor_source_is_resolved(self, latest):
        source = dependencies.resolve_upstream(
            {
                "url": "https://bioconductor.org/packages/ExamplePackage/",
                "project": "ExamplePackage",
            }
        )

        self.assertEqual(source["provider"], "bioconductor")
        self.assertEqual(source["label"], "Bioconductor")
        self.assertEqual(source["version"], "1.38.0")
        latest.assert_called_once_with("ExamplePackage")


class UpstreamSourceTests(unittest.TestCase):
    @patch("catalogue.dependencies.github_latest", return_value="2.1.0")
    def test_github_source_is_resolved(self, latest):
        source = dependencies.resolve_upstream(
            "https://github.com/example/project"
        )

        self.assertEqual(source["provider"], "github")
        self.assertEqual(source["label"], "GitHub")
        self.assertEqual(source["version"], "2.1.0")
        latest.assert_called_once_with("example", "project")

    @patch("catalogue.dependencies.bitbucket_latest", return_value="3.4.0")
    def test_bitbucket_source_is_resolved(self, latest):
        source = dependencies.resolve_upstream(
            "https://bitbucket.org/example/project"
        )

        self.assertEqual(source["provider"], "bitbucket")
        self.assertEqual(source["label"], "Bitbucket")
        self.assertEqual(source["version"], "3.4.0")
        latest.assert_called_once_with("example", "project")

    @patch("catalogue.dependencies.pypi_latest", return_value="4.5.0")
    def test_pypi_source_is_resolved(self, latest):
        source = dependencies.resolve_upstream(
            "https://pypi.org/project/example-package/"
        )

        self.assertEqual(source["provider"], "pypi")
        self.assertEqual(source["label"], "PyPI")
        self.assertEqual(source["version"], "4.5.0")
        latest.assert_called_once_with("example-package")


class SuiteMainDependencyTests(unittest.TestCase):
    def write_suite(self, root, hints):
        suite = root / "tools" / "suite"
        suite.mkdir(parents=True)
        (suite / "README.md").write_text("# Suite\n\n" + hints)
        (suite / ".shed.yml").write_text("name: suite\n")
        for name, package, version in (("a_convert", "python", "3.9"), ("b_predict", "toolpkg", "1.0")):
            (suite / f"{name}.xml").write_text(
                f'<tool id="{name}" name="{name}" version="1.0+galaxy0"><requirements>'
                f'<requirement type="package" version="{version}">{package}</requirement>'
                "</requirements><help>Help.</help></tool>"
            )

    def test_main_dependency_that_no_wrapper_requires_fails_the_build(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.write_suite(root, "Main dependency: tool-pkg\n")
            with self.assertRaisesRegex(ValueError, "'tool-pkg' is not a package requirement of any wrapper"):
                catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)

    def test_main_dependency_with_no_package_requirements_fails_but_readme_only_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_suite(root, "Main dependency: toolpkg\n\nConda channel: bioconda\n")
            suite = root / "tools" / "suite"
            for path in suite.glob("*.xml"):
                path.write_text(f'<tool id="{path.stem}" name="{path.stem}" version="1"><help>Help.</help></tool>')
            with self.assertRaisesRegex(ValueError, "'toolpkg' is not a package requirement"):
                catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            for path in suite.glob("*.xml"):
                path.unlink()
            sections = catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            self.assertEqual(sections[0]["count"], 0)
            self.assertEqual(sections[0]["main_dependency"]["sources"][0]["url"],
                             "https://anaconda.org/bioconda/toolpkg")

    def test_suite_badge_includes_non_main_packages(self):
        def conda_info(name, version, **kwargs):
            info = dependencies._empty_conda_info(name, "conda-forge")
            return {**info, "latest_version": "9.9", "out_of_date": name == "python"}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.write_suite(root, "Main dependency: toolpkg\n")
            with patch("catalogue.dependencies._conda_info", side_effect=conda_info):
                section, = catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
        self.assertIs(section["requirement_out_of_date"], True)

    def test_declared_main_dependency_is_used_and_fallbacks_get_no_upstream(self):
        from catalogue.version_report import write_version_report

        upstream = {"label": "GitHub", "url": "https://github.com/example/toolpkg", "version": "3.10"}
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            # The helper wrapper sorts first and does not use the main package.
            self.write_suite(
                root, "Main dependency: toolpkg\n\nUpstream source: https://github.com/example/toolpkg\n"
            )
            with patch("catalogue.dependencies._resolve_upstream_cached", return_value=upstream):
                section, = catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            report = root / "versions.md"
            write_version_report([section], report)
            upstream_rows = report.read_text().split("### Upstream releases/tags")[1]

        self.assertEqual(section["main_dependency"]["name"], "toolpkg")
        self.assertEqual(
            {
                wrapper["id"]: [source["label"] for source in wrapper["main_dependency"]["sources"]]
                for wrapper in section["wrappers"]
            },
            {"a_convert": [], "b_predict": ["GitHub"]},
        )
        self.assertNotIn("| python |", upstream_rows)
        self.assertIn("| toolpkg | 1.0 | GitHub | 3.10 | Newer available |", upstream_rows)

    def test_inference_requires_unique_package_or_exact_id_and_suite_agreement(self):
        def data(identity, names):
            return {"id": identity, "requirements": [{"name": name, "version": "1"} for name in names]}

        cases = (
            ([data("wrapper", ["pkg"])], "pkg"),
            ([data("pkg", ["python", "pkg"])], "pkg"),
            ([data("Pkg", ["python", "pkg"])], None),
            ([data("pkg_predict", ["python", "pkg"])], None),
            ([data("a", ["pkg"]), data("b", [])], "pkg"),
            ([data("a", ["pkg"]), data("b", ["python"])], None),
            ([data("a", ["pkg"]), data("b", ["pkg", "python"])], None),
        )
        for wrappers, expected in cases:
            files = [(Path(f"{index}.xml"), wrapper) for index, wrapper in enumerate(wrappers)]
            with self.subTest(wrappers=wrappers):
                self.assertEqual(catalogue_sections.resolve_main_dependency(Path("suite"), {}, files), expected)
        for states, expected in (([], None), ([False, None], None), ([None, True], True), ([False, False], False)):
            with self.subTest(states=states):
                self.assertIs(dependencies.dependency_status(states), expected)

    def test_mixed_pins_are_compared_individually_and_channel_is_scoped(self):
        from catalogue.version_report import write_version_report

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_suite(root, "Main dependency: toolpkg\n\nConda channel: bioconda\n\n"
                             "Upstream source: https://github.com/example/toolpkg\n")
            suite = root / "tools/suite"
            predictor = suite / "b_predict.xml"
            (suite / "c_predict.xml").write_text(
                predictor.read_text().replace("b_predict", "c_predict").replace('version="1.0"', 'version="2.0"')
            )
            upstream = {"label": "GitHub", "url": "https://github.com/example/toolpkg", "version": "1.5"}
            with patch("catalogue.dependencies._resolve_upstream_cached", return_value=upstream), patch(
                "catalogue.dependencies._conda_info", wraps=dependencies._conda_info,
            ) as lookup, patch("catalogue.dependencies.requests.get", side_effect=AssertionError("network")):
                section, = catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            self.assertEqual(section["main_dependency"]["version"], "multiple")
            self.assertTrue(section["main_dependency"]["sources"][-1]["newer"])
            for call in lookup.call_args_list:
                self.assertEqual(call.kwargs["preferred_channel"], "bioconda" if call.args[0] == "toolpkg" else None)
            report = root / "versions.md"
            write_version_report([section], report)
            self.assertIn("| toolpkg | 1.0 | GitHub | 1.5 | Newer available |", report.read_text())
            self.assertIn("| toolpkg | 2.0 | GitHub | 1.5 | No newer version found |", report.read_text())

    def test_invalid_entry_settings_preserve_output_and_do_not_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_suite(root, "")
            suite = root / "tools/suite"
            site = root / "site"
            site.mkdir()
            marker = site / "existing.txt"
            marker.write_text("Previous site")
            for hints, shed in (
                ("Main dependency: missing\n", True),
                ("Conda channel: bioconda\n", True),
                ("Upstream source: https://github.com/example/tool\n", True),
                ("", False),
            ):
                with self.subTest(hints=hints, shed=shed):
                    (suite / "README.md").write_text("# Suite\n\n" + hints)
                    if not shed:
                        (suite / ".shed.yml").unlink()
                    with patch("catalogue.dependencies.requests.get", side_effect=AssertionError("network")):
                        with self.assertRaisesRegex(ValueError, "Main dependency|main_dependency|.shed.yml"):
                            catalogue_build.build_catalogue(site, root / "tools")
                    self.assertEqual(marker.read_text(), "Previous site")

    def test_manager_homepage_is_not_a_python_release_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
            manager = root / "data_managers/example"
            with (manager / ".shed.yml").open("a") as handle:
                handle.write("homepage_url: https://github.com/example/models\n")
            with patch("catalogue.dependencies.requests.get", side_effect=AssertionError("network")):
                sections = catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            section = next(section for section in sections if section["kind"] == "data-manager")
            self.assertEqual(section["main_dependency"]["name"], "python")
            self.assertEqual(section["main_dependency"]["sources"], [])
            page = (root / "site" / section["slug"] / "index.html").read_text()
            self.assertIn('href="https://github.com/example/models"', page)


class ManagerCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)
        self.manager = self.root / "data_managers/example"
        self.manifest = self.manager / "data_manager/assets.json"
        self.site = self.root / "site"

    def build(self):
        sections = catalogue_build.build_catalogue(self.site, self.root / "tools", offline=True)
        return next(section for section in sections if section["kind"] == "data-manager")

    def test_summary_uses_tool_versions_and_details_keep_declared_compatibility(self):
        self.manifest.write_text(json.dumps({
            "current_release": "models-2026",
            "releases": {
                "models-2025": {"software_compatibility": "example-package >=1,<2"},
                "models-2026": {"software_compatibility": "example-package >=2,<3"},
            },
        }))
        section = self.build()
        self.assertEqual(section["version_tool"], "7.0.0")
        self.assertEqual(section["software_compatibility"][0]["release"], "models-2026")
        self.assertIn("example-package >=2,<3", section["search_text"])
        index = lxml_html.parse(str(self.site / "index.html"))
        row, = index.xpath("//tr[td/a[@href='example-data-manager/index.html']]")
        software_cell = row.find("td[@class='cell-dep']")
        self.assertIn("example-package", software_cell.text_content())
        self.assertIn("1.0.0", software_cell.text_content())
        self.assertNotIn("models-2026", software_cell.text_content())
        self.assertNotIn(">=2,<3", software_cell.text_content())
        self.assertNotIn("Used by:", software_cell.text_content())
        self.assertFalse(software_cell.xpath(".//a[@href='example-analysis/wrappers/example.html']"))
        self.assertEqual(section["tool_software"][0]["version"], "1.0.0")
        self.assertNotIn("python", row.text_content())
        for page in (self.site / section["slug"] / "index.html", self.site / section["wrappers"][0]["page_url"]):
            document = lxml_html.parse(str(page))
            summary, = document.xpath("//main//section[contains(@class, 'entry-software')]")
            self.assertIn("example-package >=2,<3", summary.text_content())
            self.assertIn("example-package >=1,<2", summary.text_content())
            self.assertIn("Related software", summary.text_content())
            self.assertNotIn("· current", summary.text_content())
            self.assertNotIn("python", summary.text_content())
            self.assertTrue(summary.xpath(".//a[contains(@href, '/data_manager/assets.json')]"))
            self.assertNotIn("Installer dependencies", document.getroot().text_content())
            self.assertNotIn("python", document.getroot().text_content())

    def test_asset_links_only_supplement_the_readme_and_wrapper_help(self):
        readme = self.manager / "README.md"
        original = readme.read_text()
        for links, expected in (
            ("", {"Asset catalogue", "Model card: example/reference-models"}),
            ("[Releases](data_manager/assets.json)", {"Model card: example/reference-models"}),
            ("[Releases](data_manager/assets.json)\n\n"
             "[Model card](https://huggingface.co/example/reference-models/)", set()),
        ):
            with self.subTest(links=links):
                readme.write_text(original + "\n\n## Reference data\n\n" + links)
                section = self.build()
                document = lxml_html.parse(str(self.site / section["slug"] / "index.html"))
                self.assertEqual(set(document.xpath("//section[@id='reference-assets']//a/text()")), expected)
                self.assertEqual(bool(document.xpath("//section[@id='reference-assets']")), bool(expected))
                # The separate wrapper page must still provide its own asset links.
                wrapper_page = self.site / section["wrappers"][0]["page_url"]
                document = lxml_html.parse(str(wrapper_page))
                self.assertEqual(len(document.xpath("//section[@id='reference-assets']//a")), 2)
        source = self.manager / "data_manager/example.xml"
        source.write_text(source.read_text().replace(
            "## Example reference data", "## Example reference data\n\n"
            "[Releases](assets.json)\n\n[Model card](https://huggingface.co/example/reference-models)",
        ))
        self.build()
        document = lxml_html.parse(str(wrapper_page))
        self.assertFalse(document.xpath("//section[@id='reference-assets']"))
        self.assertEqual(len(document.xpath(
            "//section[@id='tool-help']//a[contains(@href, 'assets.json') or contains(@href, 'huggingface.co')]"
        )), 2)

    def test_manager_dependency_tables_keep_other_packages(self):
        source = self.manager / "data_manager/example.xml"
        xml = source.read_text().replace(
            "</requirements>", '<requirement type="package" version="1.0">other-package</requirement></requirements>',
        )
        source.write_text(xml)
        for multiple in (False, True):
            with self.subTest(multiple=multiple):
                if multiple:
                    (source.parent / "other.xml").write_text(
                        xml.replace('id="data_manager_example"', 'id="data_manager_other"'),
                    )
                section = self.build()
                pages = [self.site / section["slug"] / "index.html"]
                pages.extend(self.site / wrapper["page_url"] for wrapper in section["wrappers"])
                for page in pages:
                    document = lxml_html.parse(str(page))
                    table, = document.xpath("//section[contains(@class, 'entry-software')]//table")
                    self.assertIn("other-package", table.text_content())
                    self.assertNotIn("python", table.text_content())
                self.assertIn("python", [r["name"] for r in section["wrappers"][0]["requirements"]])

    def test_missing_compatibility_is_not_inferred_from_manager_or_python_version(self):
        for has_manifest in (True, False):
            with self.subTest(has_manifest=has_manifest):
                if not has_manifest:
                    self.manifest.unlink()
                section = self.build()
                document = lxml_html.parse(str(self.site / section["slug"] / "index.html"))
                compatibility, = document.xpath("//h3[.='Data compatibility']/following-sibling::div[1]")
                self.assertIn("Compatibility not specified", compatibility.text_content())
                self.assertNotIn("python", compatibility.text_content())
                self.assertNotIn("3.12", compatibility.text_content())
                self.assertNotIn("7.0.0", compatibility.text_content())

    def test_unmatched_managers_fall_back_to_declared_compatibility(self):
        source = self.root / "tools/example_analysis/example.xml"
        source.write_text(source.read_text().replace('from_data_table="example_models"', 'from_data_table="other"'))
        self.manifest.write_text(json.dumps({
            "releases": {"models-2026": {"software_compatibility": "example-package >=2,<3"}},
        }))
        section = self.build()
        self.assertEqual(section["tool_software"], [])
        index = lxml_html.parse(str(self.site / "index.html"))
        row, = index.xpath("//tr[td/a[@href='example-data-manager/index.html']]")
        self.assertIn("example-package >=2,<3", row.text_content())
        self.assertNotIn("python", row.text_content())

    def test_related_software_keeps_distinct_versions_and_omits_python(self):
        original = (self.root / "tools/example_analysis/example.xml").read_text()
        for name, package, version in (
            ("same", "example-package", "1.0.0"),
            ("newer", "example-package", "2.0.0"),
            ("runtime", "python", "3.12"),
        ):
            folder = self.root / "tools" / name
            folder.mkdir()
            (folder / "README.md").write_text(f"# {name}\n")
            (folder / ".shed.yml").write_text(f"name: {name}\nowner: example\ndescription: {name}\n")
            (folder / "tool.xml").write_text(
                original.replace('id="example_analysis"', f'id="{name}"')
                .replace('name="Example analysis"', f'name="{name}"')
                .replace('version="1.0.0">example-package', f'version="{version}">{package}'),
            )
        section = self.build()
        packages = {(p["name"], p["version"]): p for p in section["tool_software"]}
        self.assertEqual(set(packages), {("example-package", "1.0.0"), ("example-package", "2.0.0")})
        self.assertEqual(len(packages[("example-package", "1.0.0")]["tools"]), 2)
        self.assertEqual(section["main_dependency"]["name"], "python")
        self.assertEqual(section["wrappers"][0]["tool_software"], section["tool_software"])

    def test_every_release_file_needs_an_https_url_and_both_checksums(self):
        asset = {
            "filename": "reference.tsv",
            "url": "https://example.org/reference.tsv",
            "md5": "d41d8cd98f00b204e9800998ecf8427e",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }
        # A release that is not published may have an unknown licence, but its files are pinned.
        self.manifest.write_text(json.dumps({"license": "unknown", "releases": {"1": {"assets": {"data": asset}}}}))
        self.build()
        for field, value, message in (
            ("md5", None, "release 1: missing or invalid md5 for reference.tsv"),
            ("sha256", "abc123", "release 1: missing or invalid sha256 for reference.tsv"),
            ("url", "http://example.org/reference.tsv", "release 1: reference.tsv needs an https url"),
            ("filename", " ", "release 1: asset data needs a filename"),
        ):
            with self.subTest(field=field):
                broken = {name: content for name, content in asset.items() if name != field}
                if value is not None:
                    broken[field] = value
                self.manifest.write_text(json.dumps({"releases": {"1": {"assets": {"data": broken}}}}))
                with self.assertRaisesRegex(ValueError, message):
                    self.build()

    def test_multiple_managers_keep_their_own_software_compatibility(self):
        self.manifest.write_text(json.dumps({
            "galaxy_data_manager": "data_manager_example",
            "releases": {"1": {"software_compatibility": "first-package ==1.0"}},
        }))
        xml = (self.manager / "data_manager/example.xml").read_text()
        (self.manager / "data_manager/other.xml").write_text(
            xml.replace('id="data_manager_example"', 'id="data_manager_other"')
        )
        other = self.manager / "other"
        other.mkdir()
        (other / "assets.json").write_text(json.dumps({
            "galaxy_data_manager": "data_manager_other",
            "releases": {"2": {"software_compatibility": "second-package ==2.0"}},
        }))
        section = self.build()
        for wrapper in section["wrappers"]:
            expected, absent = (
                ("first-package", "second-package") if wrapper["id"] == "data_manager_example"
                else ("second-package", "first-package")
            )
            document = lxml_html.parse(str(self.site / wrapper["page_url"]))
            summary, = document.xpath("//main//section[contains(@class, 'entry-software')]")
            self.assertIn(expected, summary.text_content())
            self.assertNotIn(absent, summary.text_content())


class MarkdownSecurityTests(unittest.TestCase):
    def test_markdown_html_is_sanitized(self):
        source = """# Notes

## Safe heading

<script>alert("unsafe")</script>
<a href="javascript:alert(1)" onclick="alert(2)">unsafe link</a>
<img src="https://example.org/image.png" onerror="alert(3)">
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "README.md"
            path.write_text(source, encoding="utf8")
            rendered = str(rendering.render_readme(path))

        self.assertIn('<h2 id="readme-safe-heading">Safe heading</h2>', rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("onerror", rendered)

    def test_package_homepages_link_only_http_urls(self):
        template = rendering.env.from_string(
            "{% import '_macros.html' as ui %}"
            "{{ ui.requirements_table(r) }}{{ ui.requirements_table(r, used_by=true) }}"
        )
        for home, links in (("https://example.org/pkg", 2), ("javascript:alert(1)", 0), (None, 0)):
            with self.subTest(home=home):
                html = template.render(r=[{"name": "pkg", "version": "1", "home": home, "used_by": "All"}])
                self.assertEqual(html.count('href="'), links)


class MetadataTests(unittest.TestCase):
    def test_missing_readme_is_rejected_with_its_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            folder = Path(temporary_directory) / "example"
            folder.mkdir()
            with self.assertRaisesRegex(
                ValueError,
                str(folder / "README.md"),
            ):
                catalogue_metadata.load_metadata(folder)

    def test_readme_names_have_wrapper_and_folder_fallbacks(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            folder = Path(temporary_directory) / "example"
            folder.mkdir()
            readme = folder / "README.md"
            readme.write_text("# Example suite\n\nDocumentation.\n")
            tool_files = [(folder / "tool.xml", {"name": "Example wrapper"})]
            self.assertEqual(catalogue_sections.section_name(
                folder, catalogue_metadata.load_metadata(folder), tool_files,
            ), "Example suite")
            readme.write_text("Documentation without a heading.\n")
            self.assertEqual(catalogue_sections.section_name(folder, {}, tool_files), "Example wrapper")
            self.assertEqual(catalogue_sections.section_name(folder, {}, []), "example")


class WrapperDocumentationTests(unittest.TestCase):
    def test_macro_expansion_nested_pages_and_source_links(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            folder = root / "tools" / "example"
            folder.mkdir(parents=True)
            (folder / "README.md").write_text(
                "# Example\n\n[First tool](first/tool.xml) and [source](runner.py).\n\n"
                "![Figure](figure.png)\n"
            )
            (folder / ".shed.yml").write_text("name: example\n")
            (folder / "figure.png").write_bytes(b"test image bytes")
            (folder / "runner.py").write_text("# Source link target\n")
            (folder / "tokens.xml").write_text(
                '<macros><token name="@VERSION@">1.2.3</token></macros>'
            )
            (folder / "macros.xml").write_text('''<macros>
<import>../tokens.xml</import>
<xml name="requirements"><requirements>
<requirement type="package" version="@VERSION@">example</requirement>
</requirements></xml>
<xml name="help"><help><![CDATA[
Example @VERSION@
=================

See Details_.

Details
-------

.. image:: ../figure.png

.. image:: $PATH_TO_IMAGES/standard.png

.. image:: ${PATH_TO_IMAGES}/standard.png

`Other wrapper <../second/tool.xml>`_
]]></help></xml>
</macros>''')
            for name in ("first", "second"):
                subfolder = folder / name
                subfolder.mkdir()
                images_folder = subfolder / "static" / "images"
                images_folder.mkdir(parents=True)
                (images_folder / "standard.png").write_bytes(b"standard Galaxy image")
                (subfolder / "tool.xml").write_text(f'''<tool id="{name}" name="{name}" version="@VERSION@+galaxy0">
<macros><import>../macros.xml</import></macros>
<expand macro="requirements"/>
<command>touch must-not-exist</command>
<expand macro="help"/>
</tool>''')
            site = root / "site"
            section = catalogue_build.build_catalogue(site, root / "tools", offline=True)[0]
            self.assertEqual([w["file"] for w in section["wrappers"]], ["first/tool.xml", "second/tool.xml"])
            self.assertEqual(len({w["page_url"] for w in section["wrappers"]}), 2)
            for wrapper in section["wrappers"]:
                self.assertEqual(wrapper["version_tool"], "1.2.3")
                self.assertEqual(wrapper["requirements"][0]["version"], "1.2.3")
                self.assertIn(wrapper["file"], wrapper["source_url"])
                page = site / wrapper["page_url"]
                html = page.read_text()
                self.assertIn("Example 1.2.3", html)
                self.assertIn(f"<code>{wrapper['id']}</code>", html)
                target = "../second/tool.html" if wrapper["id"] == "first" else "tool.html"
                self.assertIn(f'href="{target}"', html)
                parser = parse_html(page)
                self.assertIn("help-details", parser.ids)
                self.assertIn("#help-details", parser.references)
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                images = [ref for ref in parser.references if ref.endswith("figure.png")]
                self.assertEqual(len(images), 1)
                self.assertEqual((page.parent / images[0]).read_bytes(), b"test image bytes")
                standard_images = [ref for ref in parser.references if ref.endswith("standard.png")]
                self.assertEqual(len(standard_images), 2)
                for reference in standard_images:
                    self.assertEqual((page.parent / reference).read_bytes(), b"standard Galaxy image")
            landing_page = site / "example" / "index.html"
            landing = landing_page.read_text()
            landing_parser = parse_html(landing_page)
            wrapper_links = {
                reference for reference in landing_parser.references
                if reference.startswith("wrappers/")
            }
            self.assertEqual(wrapper_links, {wrapper["url"] for wrapper in section["wrappers"]})
            for wrapper in section["wrappers"]:
                self.assertIn(wrapper["id"], landing_parser.ids)
            self.assertEqual(landing.count('class="tools-table suite-requirements"'), 1)
            self.assertEqual(landing.count("<h2>Requirements</h2>"), 1)
            self.assertIn("/blob/main/tools/example/runner.py", landing)
            self.assertFalse((folder / "must-not-exist").exists())

    def test_invalid_xml_missing_macros_and_missing_help_identify_source(self):
        sources_to_reject = (
            '<tool>',
            '<tool><macros><import>missing.xml</import></macros><help>Notes</help></tool>',
            '<tool><expand macro="missing"/><help>Notes</help></tool>',
            '<tool><command>echo test</command></tool>',
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "broken.xml"
            for source in sources_to_reject:
                with self.subTest(source=source):
                    path.write_text(source)
                    with self.assertRaisesRegex(ValueError, "broken.xml"):
                        catalogue_tools.parse_tool(path)

    def test_non_tool_xml_is_skipped_but_broken_tools_still_fail(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
            test_data = root / "tools" / "example_analysis" / "test-data"
            test_data.mkdir()
            (test_data / "input.xml").write_text("<deliberately-invalid-fixture>")
            sections = catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            self.assertEqual(len(sections), 3)
            (test_data / "broken.xml").write_text('<tool id="broken" name="Broken"')
            with self.assertRaisesRegex(ValueError, "broken.xml"):
                catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)

    def test_rst_tables_math_and_external_links_render(self):
        source = """Heading
-------

===== =====
Input Value
===== =====
one   two
===== =====

.. math::

   x^2 = \\frac{a}{b}

`Documentation <https://example.org/docs>`_
"""
        html = str(render_document(source, "restructuredtext", Path("tool.xml"), heading_offset=2))
        self.assertIn("<h3>Heading</h3>", html)
        self.assertIn("<table", html)
        self.assertIn("<math", html)
        self.assertIn("<mfrac>", html)
        self.assertIn('href="https://example.org/docs"', html)

    def test_admonitions_keep_their_classes_but_other_classes_are_removed(self):
        html = str(render_document(".. warning::\n\n   Check the input.\n", "restructuredtext", Path("tool.xml")))
        self.assertIn('<aside class="admonition warning">', html)
        self.assertIn('<p class="admonition-title">Warning</p>', html)
        html = str(render_document(
            '<p class="banner">Text</p>\n\n<aside class="banner">Aside</aside>\n', "markdown", Path("README.md"),
        ))
        self.assertNotIn("banner", html)

    def test_readme_anchors_do_not_clash_with_page_ids(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
            (root / "tools" / "example_project" / "README.md").write_text(
                "# Example Project\n\n## Galaxy tool\n\nSee [the plan](#galaxy-tool).\n"
            )
            catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            document = parse_html(root / "site" / "example-project" / "index.html")
        self.assertEqual(len(document.ids), len(set(document.ids)))
        self.assertIn("readme-galaxy-tool", document.ids)
        self.assertIn("#readme-galaxy-tool", document.references)

    def test_readme_title_and_named_anchors_resolve_like_github(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
            analysis = root / "tools" / "example_analysis"
            (analysis / "README.md").write_text(
                '# Example Analysis\n\n## Example Analysis\n\n<a name="install"></a>\nInstall it.\n\n'
                "[Back to top](#example-analysis) [repeat](#example-analysis-1) [install](#install) "
                "[help](example.xml)\n"
            )
            wrapper = analysis / "example.xml"
            wrapper.write_text(wrapper.read_text().replace(
                "Select an installed reference entry.",
                "See [the overview](README.md#example-analysis) and [installation](README.md#install).",
            ))
            catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)
            page = root / "site" / "example-analysis" / "index.html"
            html = page.read_text()
            document = parse_html(page)
            help_page = parse_html(root / "site" / "example-analysis" / "wrappers" / "example.html")
        # The page heading shows the title; the README keeps only its anchor.
        self.assertEqual([text for tag, text in document.headings if tag == "h1"], ["Example Analysis"])
        self.assertIn('<span id="readme-example-analysis"></span>', html)
        self.assertNotIn('<h1 id="readme-example-analysis"', html)
        self.assertIn('<h2 id="readme-example-analysis-1">Example Analysis</h2>', html)
        self.assertIn('<a name="readme-install">', html)
        self.assertLessEqual(
            {"#readme-example-analysis", "#readme-example-analysis-1", "#readme-install"}, set(document.references),
        )
        self.assertLessEqual(
            {"../index.html#readme-example-analysis", "../index.html#readme-install"}, set(help_page.references),
        )

    def test_restructuredtext_contents_backlinks_to_the_top_resolve(self):
        html = str(render_document(
            ".. contents::\n   :backlinks: top\n\nInputs\n======\n\nText.\n",
            "restructuredtext", Path("tool.xml"), id_prefix="help-",
        ))
        self.assertIn('id="help-contents"', html)
        self.assertIn('href="#help-contents"', html)

    def test_heading_anchors_match_github_and_missing_ones_fail(self):
        html = str(render_document(
            "## Inputs & outputs\n\n## Notes\n\n## Notes\n\n## Übersicht\n\n"
            "[io](#inputs--outputs) [n](#notes-1) [u](#übersicht) [top](#top) [back](#)"
            " [encoded](#%C3%BCbersicht)\n",
            "markdown", Path("README.md"), id_prefix="readme-",
        ))
        document = DocumentParser()
        document.feed(html)
        self.assertEqual(
            document.ids, ["readme-inputs--outputs", "readme-notes", "readme-notes-1", "readme-übersicht"]
        )
        self.assertEqual(
            document.references,
            ["#readme-inputs--outputs", "#readme-notes-1", "#readme-übersicht", "#top", "#", "#readme-%C3%BCbersicht"],
        )
        with self.assertRaisesRegex(ValueError, "missing headings or anchors: #installation"):
            render_document("## Setup\n\nSee [install](#installation).\n", "markdown", Path("README.md"))

    def test_root_links_separate_images_and_unreadable_paths(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
            (root / "CONTRIBUTING.md").write_text("# Contributing\n")
            for kind in ("tools", "data_managers"):
                entry = root / kind / "shared_name"
                (entry / "docs").mkdir(parents=True)
                (entry / "docs" / "overview.png").write_bytes(kind.encode())
                (entry / "README.md").write_text(
                    f"# Shared {kind}\n\n![overview](docs/overview.png) [guide](/CONTRIBUTING.md)\n"
                )
            test_data = root / "tools" / "example_analysis" / "test-data"
            test_data.mkdir()
            (test_data / "missing-link.xml").symlink_to(root / "missing.xml")
            (test_data / "folder.xml").mkdir()
            site = root / "site"
            catalogue_build.build_catalogue(site, root / "tools", offline=True)
            for kind in ("tools", "data_managers"):
                image = site / "assets" / kind / "shared_name" / "docs" / "overview.png"
                self.assertEqual(image.read_bytes(), kind.encode())
            self.assertIn("/blob/main/CONTRIBUTING.md", (site / "shared-tools" / "index.html").read_text())

    def test_images_cannot_link_to_html_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entry = root / "tools" / "entry"
            entry.mkdir(parents=True)
            (entry / "page.html").write_text("<script>alert(1)</script>")
            (entry / "README.md").write_text("# Entry\n\n![x](page.html)\n")
            with self.assertRaisesRegex(ValueError, "GIF, JPEG, PNG, SVG or WebP"):
                catalogue_build.build_catalogue(root / "site", root / "tools", offline=True)

    def test_unsupported_formats_invalid_rst_and_includes_fail_clearly(self):
        for source, format in (
            ("help", "html"),
            (".. missing-directive::\n", "restructuredtext"),
            (".. include:: /etc/passwd\n", "restructuredtext"),
            (".. raw:: html\n\n   <script>alert(1)</script>\n", "restructuredtext"),
        ):
            with self.subTest(format=format, source=source):
                with self.assertRaisesRegex(ValueError, "unsafe.xml"):
                    render_document(source, format, Path("unsafe.xml"))


class CatalogueRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)
        self.entry = self.root / "tools" / "example_analysis"
        self.site = self.root / "site"

    def build(self):
        return catalogue_build.build_catalogue(self.site, self.root / "tools", offline=True)

    def test_svg_xml_encodings_entities_and_active_content(self):
        (self.entry / "README.md").write_text("# Example Analysis\n\n![image](image.svg)\n")
        svg = '<svg xmlns="http://www.w3.org/2000/svg">{}</svg>'
        unsafe = [svg.format(body) for body in (
            '<script>/* marker */</script>',
            '<s:script xmlns:s="http://www.w3.org/2000/svg"/>',
            '<g onload="marker()"/>',
            '<a href="java&#x73;cript:marker()"/>',
            '<a href="java&#x09;script:marker()"/>',
            '<foreignObject><p>HTML</p></foreignObject>',
            '<set attributeName="href" to="https://example.org"/>',
        )]
        unsafe.append('<svg xmlns="http://www.w3.org/2000/svg" onload="marker()"/>')
        for encoding in ("utf-8", "utf-16"):
            for content in unsafe:
                with self.subTest(encoding=encoding, svg=content):
                    image = f'<?xml version="1.0" encoding="{encoding}"?>' + content
                    (self.entry / "image.svg").write_bytes(image.encode(encoding))
                    with self.assertRaisesRegex(ValueError, "must not contain scripts"):
                        self.build()
                    self.assertFalse((self.site / "assets/tools/example_analysis/image.svg").exists())
            safe = f'<?xml version="1.0" encoding="{encoding}"?>' + svg.format('<text>only = 3</text>')
            (self.entry / "image.svg").write_bytes(safe.encode(encoding))
            self.build()
            self.assertEqual((self.site / "assets/tools/example_analysis/image.svg").read_bytes(), safe.encode(encoding))
        (self.entry / "image.svg").write_bytes(b"not XML")
        with self.assertRaisesRegex(ValueError, "invalid SVG image"):
            self.build()
        for prolog in (
            '<!DOCTYPE svg [<!ENTITY text "marker">]>',
            '<!DOCTYPE svg SYSTEM "https://example.invalid/external.dtd">',
            '<?xml-stylesheet type="text/xsl" href="https://example.invalid/style.xsl"?>',
        ):
            (self.entry / "image.svg").write_text(prolog + svg.format(""))
            with self.subTest(prolog=prolog), self.assertRaisesRegex(ValueError, "active content"):
                self.build()

    def test_cross_page_links_validate_both_directions_after_rendering(self):
        (self.entry / "README.md").write_text(
            "# Example Analysis\n\n## Übersicht\n\n[help](example.xml#using-the-example) [top](example.xml#top)\n"
        )
        wrapper = self.entry / "example.xml"
        wrapper.write_text(wrapper.read_text().replace(
            "## Using the example", "[overview](README.md#%C3%BCbersicht)\n\n## Using the example"
        ))
        self.build()
        page = self.site / "example-analysis" / "index.html"
        self.assertIn("wrappers/example.html#help-using-the-example", page.read_text())
        for target in ("example.xml#missing", "README.md#missing"):
            (self.entry / "README.md").write_text(f"# Example Analysis\n\n## Übersicht\n\n[broken]({target})\n")
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, r"example-analysis/index.html:.*missing"):
                self.build()
        (self.entry / "README.md").write_text("# Example Analysis\n\n## Übersicht\n")
        wrapper.write_text(wrapper.read_text().replace("README.md#%C3%BCbersicht", "README.md#missing"))
        with self.assertRaisesRegex(ValueError, r"wrappers/example.html:.*missing"):
            self.build()

    def test_wrapper_licence_links_use_the_entry_file_or_default(self):
        for own_file, label, expected in (
            (False, "MIT", "/blob/main/LICENSE"),
            (True, "Entry licence", "/blob/main/tools/example_analysis/LICENSE"),
        ):
            with self.subTest(own_file=own_file):
                license_file = self.entry / "LICENSE"
                if own_file:
                    license_file.write_text("GPL-3.0\n")
                else:
                    license_file.unlink(missing_ok=True)
                self.build()
                for relative in ("index.html", "wrappers/example.html"):
                    document = lxml_html.parse(str(self.site / "example-analysis" / relative))
                    licence, = document.xpath("//main//a[starts-with(., 'Wrapper licence:')]")
                    self.assertEqual(licence.text_content(), f"Wrapper licence: {label}")
                    self.assertEqual(licence.get("href"), f"{REPO_URL}{expected}")


class EmptyCatalogueTests(unittest.TestCase):
    def test_empty_roots_and_nested_fixtures_are_not_catalogue_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            shutil.copytree(FIXTURES, root / "tests" / "fixtures" / "catalogue")
            for readmes in (False, True):
                if readmes:
                    for name in ("tools", "data_managers"):
                        folder = root / name
                        folder.mkdir()
                        (folder / "README.md").write_text("# Contribution instructions\n")
                with self.subTest(contribution_readmes=readmes):
                    sections = catalogue_build.build_catalogue(site, root / "tools", offline=True)
                    self.assertEqual(sections, [])
                    self.assertEqual(list(site.rglob("*.html")), [site / "index.html"])
                    html = (site / "index.html").read_text()
                    document = parse_html(site / "index.html")
                    self.assertIn("empty-catalogue", document.ids)
                    self.assertIn("No catalogue entries found.", html)
                    self.assertNotIn("tool-search", document.ids)
                    self.assertNotIn("Example Analysis", html)
                    self.assertIn(REPO_URL, document.references)


class RepositoryLinkTests(unittest.TestCase):
    def test_custom_repository_and_revision_reach_every_source_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, site = root / "repository", root / "site"
            shutil.copytree(FIXTURES, repository)
            entry = repository / "tools/example_analysis"
            (entry / "LICENSE").write_text("Example licence\n")
            (entry / "README.md").write_text(
                (entry / "README.md").read_text()
                + "\n[Licence](LICENSE) · [Files](test-data)\n"
            )
            (entry / "test-data").mkdir()
            repo_url = "https://github.com/contributor/catalogue-fork"
            for source_ref, encoded_ref in (("abc123", "abc123"), ("trial/msbuddy", "trial%2Fmsbuddy")):
                with self.subTest(source_ref=source_ref):
                    catalogue_build.build_catalogue(
                        site, repository / "tools", offline=True,
                        repo_url=repo_url + "/", source_ref=source_ref,
                    )
                    references = set()
                    for page in site.rglob("*.html"):
                        html = page.read_text()
                        self.assertIn("contributor / catalogue-fork", html)
                        self.assertNotIn(REPO_URL, html)
                        references.update(parse_html(page).references)
                    expected = (
                        "blob/{ref}/LICENSE",
                        "blob/{ref}/tools/example_analysis/LICENSE",
                        "tree/{ref}/tools/example_analysis/",
                        "tree/{ref}/tools/example_analysis/test-data",
                        "blob/{ref}/tools/example_analysis/example.xml",
                        "blob/{ref}/data_managers/example/data_manager/example.xml",
                        "blob/{ref}/data_managers/example/data_manager/assets.json",
                    )
                    self.assertIn(repo_url, references)
                    for path in expected:
                        self.assertIn(repo_url + "/" + path.format(ref=encoded_ref), references)
                    self.assertIn("https://github.com/example/analysis", references)
                    self.assertIn("https://huggingface.co/example/reference-models", references)

    def test_invalid_source_settings_do_not_remove_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            site.mkdir()
            marker = site / "existing.txt"
            marker.write_text("Keep existing output")
            for settings in ({"repo_url": "javascript:alert(1)"}, {"source_ref": " "}):
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    catalogue_build.build_catalogue(site, offline=True, **settings)
                self.assertTrue(marker.exists())


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        temporary_root = Path(cls.temporary_directory.name)
        cls.repository = temporary_root / "repository"
        shutil.copytree(FIXTURES, cls.repository)
        cls.site = temporary_root / "site"
        cls.sections = catalogue_build.build_catalogue(
            site_dir=cls.site,
            docs_dir=cls.repository / "tools",
            offline=True,
        )
        cls.html_files = sorted(cls.site.rglob("*.html"))
        cls.parsed = {
            path.resolve(): parse_html(path) for path in cls.html_files
        }

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def test_complete_offline_render_has_expected_files(self):
        entry_count = sum(
            len(list((self.repository / folder).glob("*/README.md")))
            for folder in ("tools", "data_managers")
        )
        self.assertEqual(len(self.sections), entry_count)
        self.assertEqual(
            len(self.html_files), entry_count + 1 + sum(section["count"] for section in self.sections)
        )
        self.assertEqual(
            [section["title"] for section in self.sections],
            sorted((section["title"] for section in self.sections), key=str.casefold),
        )
        self.assertEqual(
            {section["slug"] for section in self.sections},
            {"example-analysis", "example-project", "example-data-manager"},
        )
        self.assertTrue((self.site / ".nojekyll").exists())
        self.assertTrue((self.site / "static" / "style.css").exists())
        self.assertTrue((self.site / "static" / "app.js").exists())

    def test_offline_build_labels_every_page(self):
        for path in self.html_files:
            with self.subTest(page=path):
                self.assertEqual(path.read_text().count("Offline build:"), 1)

    def test_live_build_labels_available_prereleases_without_offline_notice(self):
        def conda_info(name, version, **kwargs):
            return {
                **dependencies._empty_conda_info(name, "bioconda"),
                "latest_version": "999.0rc1",
                "out_of_date": True,
            }

        upstream = {
            "label": "GitHub",
            "url": "https://github.com/example/project",
            "version": "999.0rc1",
        }
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            with patch("catalogue.dependencies._conda_info", side_effect=conda_info), patch(
                "catalogue.dependencies._resolve_upstream_cached", return_value=upstream,
            ), patch("catalogue.toolshed.repository_links", return_value=[]):
                catalogue_build.build_catalogue(site_dir=site, docs_dir=self.repository / "tools")
            for path in site.rglob("*.html"):
                with self.subTest(page=path):
                    html = path.read_text()
                    self.assertNotIn("Offline build:", html)
                    self.assertIn("999.0rc1 (pre-release)", html)
                    self.assertNotIn("999.0rc1", html.replace("999.0rc1 (pre-release)", ""))

    def test_index_lists_supported_dependency_sources(self):
        index_html = (self.site / "index.html").read_text(encoding="utf8")
        self.assertIn(
            "https://anaconda.org/bioconda/example-package",
            index_html,
        )
        self.assertIn(
            "https://github.com/example/analysis",
            index_html,
        )

    def test_toolshed_links_distinguish_published_missing_and_unchecked_destinations(self):
        for status, label in (("published", "suite_example"), ("missing", "Not currently listed"),
                              ("unknown", "availability not checked")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                repositories = [{
                    "owner": "example", "name": name, "kind": kind, "status": status,
                    "url": f"https://toolshed.g2.bx.psu.edu/view/example/{name}",
                } for name, kind in (("suite_example", "suite"), ("example_analysis", "repository"))]
                site = Path(directory) / "site"
                with patch("catalogue.toolshed.repository_links", return_value=repositories):
                    catalogue_build.build_catalogue(site, self.repository / "tools", offline=True)
                for relative in ("index.html", "wrappers/example.html"):
                    document = lxml_html.parse(str(site / "example-analysis" / relative))
                    notice, = document.xpath("//p[@class='entry-toolshed']")
                    self.assertIn(label, notice.text_content())
                    links = notice.xpath("./a")
                    self.assertEqual(len(links), {"published": 1, "missing": 0, "unknown": 2}[status])
                    for link in links:
                        self.assertEqual(link.get("target"), "_blank")
                        self.assertIn("noopener", link.get("rel"))

    def test_readme_hints_feed_metadata_without_repeating_in_the_body(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "README.md"
            source.write_text(
                "# Example\n\n## Overview\n\nScientific overview.\n\n"
                "Main dependency: example-package\n\nConda channel: bioconda\n\n"
                "Upstream source: [Project](https://github.com/example/project)\n\n"
                "## Usage\n\n- Main dependency: list-example\n\n"
                "```text\nConda channel: code-example\n```\n"
            )
            metadata = catalogue_metadata.load_metadata(source.parent)
            self.assertEqual(metadata["main_dependency"], "example-package")
            self.assertEqual(metadata["upstream_sources"], ["https://github.com/example/project"])
            document = lxml_html.fragment_fromstring(str(rendering.render_readme(source)), create_parent="div")
            self.assertEqual(document.xpath("./h2/text()"), ["Overview", "Usage"])
            self.assertEqual(document.xpath("./p/text()"), ["Scientific overview."])
            self.assertIn("Main dependency: list-example", document.xpath(".//li")[0].text_content())
            self.assertIn("Conda channel: code-example", document.xpath(".//pre")[0].text_content())

    def test_readme_overview_replaces_the_entry_lead_and_retains_links(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            shutil.copytree(FIXTURES, repository)
            readme = repository / "tools/example_analysis/README.md"
            readme.write_text(
                "# Example Analysis\n\n## Overview\n\nA **scientific** introduction.\n\n"
                "## Usage\n\nRead the [overview](#overview), [title](#example-analysis) "
                "and [wrapper help](example.xml).\n\n"
                "### Running the example\n\nReturn to [usage](#usage) or "
                "[this step](#running-the-example).\n"
            )
            manager_readme = repository / "data_managers/example/README.md"
            manager_readme.write_text("# Example Data Manager\n\n## Usage\n\nSynthetic provider instructions.\n")
            site = Path(directory) / "site"
            catalogue_build.build_catalogue(site, repository / "tools", offline=True)
            document = lxml_html.parse(str(site / "example-analysis/index.html"))
            intro, = document.xpath("//section[@aria-label='Overview']")
            self.assertIn("A scientific introduction.", intro.text_content())
            self.assertTrue(intro.xpath("./p/strong"))
            self.assertFalse(document.xpath("//p[@class='lead']"))
            for anchor in ("overview", "example-analysis", "usage", "running-the-example"):
                self.assertEqual(len(document.xpath(f"//*[@id='readme-{anchor}']")), 1)
            usage, = document.xpath("//section[contains(@class, 'entry-usage')]")
            self.assertIn("x-ignore", usage.attrib)
            self.assertEqual(usage.xpath("./h2/text()"), ["Usage"])
            self.assertEqual(usage.xpath("./h3/text()"), ["Running the example"])
            self.assertEqual(len(document.xpath("//h2[.='Usage']")), 1)
            self.assertEqual(usage.xpath(".//a/@href"), [
                "#readme-overview", "#readme-example-analysis", "wrappers/example.html",
                "#readme-usage", "#readme-running-the-example",
            ])
            following = intro.xpath("following-sibling::*")
            self.assertEqual(following[0].get("class"), "entry-toolshed")
            self.assertEqual(following[1], usage)
            self.assertEqual([section.xpath("./h2/text()") for section in following[2:6]], [
                ["Galaxy tool(s)"], ["Requirements"], ["Required data tables"], ["Sources and licence"],
            ])
            self.assertEqual(document.xpath("//section[@id='README']/h2/text()"), ["Sources and licence"])
            wrapper = lxml_html.parse(str(site / "example-analysis/wrappers/example.html"))
            self.assertTrue(wrapper.xpath("//p[@class='lead']"))
            fallback = lxml_html.parse(str(site / "example-data-manager/index.html"))
            self.assertTrue(fallback.xpath("//p[@class='lead']"))
            self.assertFalse(fallback.xpath("//section[contains(@class, 'entry-usage')]"))
            self.assertEqual(fallback.xpath("//section[@id='README']/h2/text()"), ["Usage", "Sources and licence"])
            self.assertIn("Synthetic provider", fallback.xpath("//section[@id='README']")[0].text_content())
            readme.write_text(
                '# Example Analysis\n\n## Overview <a id="summary"></a>\n\nIntroduction.\n\n'
                '## Usage\n\nRead the [summary](#summary).\n'
            )
            rendered = rendering.render_readme(readme)
            overview, body = rendering._split_readme_overview(rendered)
            self.assertFalse(overview)
            self.assertEqual(body, rendered)
            self.assertIn('id="readme-summary"', body)

    def test_readme_usage_extraction_preserves_custom_headings_and_ignores_nonleading_sections(self):
        for rendered in (
            "<h2>Installation</h2><p>Install it.</p>",
            "<h2>Usage</h2><h2>Notes</h2><p>Later.</p>",
            "<p>Introduction.</p><h2>Usage</h2><p>Run it.</p>",
            "<h2>Installation</h2><p>Install it.</p><h2>Usage</h2><p>Run it.</p>",
        ):
            with self.subTest(rendered=rendered):
                self.assertEqual(rendering._split_readme_usage(rendered), ("", rendered))
        rendered = (
            '<span id="title"></span><h2 id="usage">Usage <a id="custom"></a></h2>'
            '<p><a href="wrappers/example.html">Wrapper help</a></p>'
            '<h3 id="step">Step</h3><p>Run it.</p><h2 id="notes">Notes</h2><p>Later.</p>'
        )
        usage, body = rendering._split_readme_usage(rendered)
        usage = lxml_html.fragment_fromstring(str(usage), create_parent="div")
        body = lxml_html.fragment_fromstring(str(body), create_parent="div")
        self.assertEqual(usage.xpath(".//*[@id]/@id"), ["title", "usage", "custom", "step"])
        self.assertEqual(usage.xpath(".//a/@href"), ["wrappers/example.html"])
        self.assertEqual(usage.xpath("./h2/@id"), ["usage"])
        self.assertEqual(body.xpath(".//*[@id]/@id"), ["notes"])
        self.assertEqual(body.xpath("./p/text()"), ["Later."])

    def test_wrapper_citations_reuse_readme_references_and_keep_missing_doi_links(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            shutil.copytree(FIXTURES, repository)
            folder = repository / "tools/example_analysis"
            readme = folder / "README.md"
            readme.write_text(readme.read_text() + (
                "\n## Acknowledgements\n\n"
                "- Example, A. (2024). [An example paper](https://doi.org/10.1234/example). "
                "*Example Journal*, 4(2), 12–15.\n"
            ))
            xml = folder / "example.xml"
            xml.write_text(xml.read_text().replace("</tool>", (
                '<citations><citation type="doi">10.1234/Example</citation>'
                '<citation type="doi">10.1234/missing</citation></citations></tool>'
            )))
            site = Path(directory) / "site"
            catalogue_build.build_catalogue(site, repository / "tools", offline=True)
            document = lxml_html.parse(str(site / "example-analysis/wrappers/example.html"))
            full, fallback = document.xpath("//section[@id='citations']/ul/li")
            self.assertIn("Example, A. (2024).", full.text_content())
            self.assertIn("4(2), 12–15.", full.text_content())
            self.assertEqual(full.xpath("./em/text()"), ["Example Journal"])
            link, = full.xpath("./a")
            self.assertEqual(link.text, "An example paper")
            self.assertEqual(link.get("href"), "https://doi.org/10.1234/example")
            self.assertEqual(link.get("target"), "_blank")
            self.assertIn("noopener", link.get("rel"))
            self.assertEqual(fallback.text_content(), "10.1234/missing")
            self.assertEqual(fallback.xpath("./a/@href"), ["https://doi.org/10.1234/missing"])

    def test_readme_citation_details_exclude_other_sections_and_page_specific_links(self):
        rendered = (
            '<h2>Usage</h2><ul><li><a href="https://doi.org/10.1234/usage">Usage example</a></li></ul>'
            '<h2>Acknowledgements</h2><ul>'
            '<li>Author. <a href="https://doi.org/10.1234/paper">Paper title</a>.</li>'
            '<li><a href="https://doi.org/10.1234/relative">Paper</a> <a href="#notes">Notes</a></li>'
            '<li id="local-credit"><a href="https://doi.org/10.1234/local">Paper</a></li>'
            '</ul><h2>Wrapper contributors</h2><ul>'
            '<li><a href="https://doi.org/10.1234/contributor">Contributor</a></li></ul>'
        )
        self.assertEqual(set(rendering._readme_citation_details(rendered)), {"10.1234/paper"})

    def test_index_software_status_includes_upstream_and_preserves_unknowns(self):
        cases = (
            ([False, False], "No newer version found"),
            ([False, True], "Newer version available"),
            ([True, None], "Newer version available"),
            ([False, None], "Version status unknown"),
            ([None], "Version status unknown"),
            ([], "Version status unknown"),
        )
        for comparisons, expected in cases:
            with self.subTest(comparisons=comparisons), tempfile.TemporaryDirectory() as directory:
                sections = deepcopy(self.sections)
                sources = [
                    {"label": label, "url": url, "version": "1.0", "newer": newer}
                    for (label, url), newer in zip((
                        ("Bioconda", "https://anaconda.org/bioconda/example-package"),
                        ("GitHub", "https://github.com/example/analysis"),
                    ), comparisons)
                ]
                for section in sections:
                    if section["main_dependency"]:
                        section["main_dependency"]["sources"] = sources
                    for package in section.get("tool_software", []):
                        package["sources"] = sources
                site = Path(directory)
                rendering.render_index_page(sections, [], site)
                document = lxml_html.parse(str(site / "index.html"))
                for slug in ("example-analysis", "example-data-manager"):
                    status, = document.xpath(
                        f"//tr[td/a[@href='{slug}/index.html']]"
                        "//span[contains(@class, 'software-status ')]"
                    )
                    self.assertEqual(status.text_content(), expected)

    def test_offline_index_does_not_claim_software_versions_are_current(self):
        document = lxml_html.parse(str(self.site / "index.html"))
        statuses = document.xpath("//span[contains(@class, 'software-status ')]")
        self.assertTrue(statuses)
        self.assertEqual({status.text_content() for status in statuses}, {"Version status unknown"})

    def test_generated_page_ids_are_unique(self):
        for source, document in self.parsed.items():
            self.assertEqual(len(document.ids), len(set(document.ids)), f"{source}: duplicate IDs")

    def test_each_wrapper_has_its_own_help_page_and_source(self):
        for section in self.sections:
            for wrapper in section["wrappers"]:
                with self.subTest(wrapper=wrapper["page_url"]):
                    page = self.site / wrapper["page_url"]
                    document = self.parsed[page.resolve()]
                    self.assertEqual(
                        [text for tag, text in document.headings if tag == "h1"],
                        [wrapper["name"]],
                    )
                    self.assertIn("tool-help", document.ids)
                    self.assertIn(wrapper["source_url"], document.references)

    def test_suite_has_one_kind_label_and_separate_wrapper_and_software_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "tests/fixtures/catalogue-ui/tools", root / "tools")
            site = root / "site"
            suite, = catalogue_build.build_catalogue(site, root / "tools", offline=True)
            document = lxml_html.parse(str(site / suite["slug"] / "index.html"))
            label, = document.xpath("//p[@class='documentation-label']")
            self.assertEqual(label.text_content().strip(), "Tool suite")
            wrapper_table, = document.xpath("//table[contains(@class, 'entry-tools-table')]")
            self.assertEqual(wrapper_table.xpath(".//th/text()"), ["Tool", "Wrapper version"])
            self.assertEqual(len(wrapper_table.xpath("./tbody/tr")), len(suite["wrappers"]))
            for row, wrapper in zip(wrapper_table.xpath("./tbody/tr"), suite["wrappers"]):
                self.assertEqual(row.xpath("./td[1]/a/@href"), [wrapper["url"]])
                self.assertEqual(row.xpath("./td[2]/a/@href"), [wrapper["source_url"]])
            software, = document.xpath("//section[contains(@class, 'entry-software')]")
            self.assertEqual(len(software.xpath(".//table")), 1)
            group, = software.xpath("preceding-sibling::section[@id='galaxy-tools']")
            self.assertEqual(len(group.xpath(".//table")), 1)
            self.assertEqual(group.xpath("./h2/text()"), ["Galaxy tool(s)"])
            self.assertEqual(software.xpath("./h2/text()"), ["Requirements"])
            self.assertEqual(len(document.xpath("//main//table")), 2)
            expected = catalogue_sections.consolidate_requirements(suite["wrappers"])
            self.assertEqual(len(software.xpath(".//tbody/tr")), len(expected))
            for requirement in expected:
                self.assertIn(requirement["name"], software.text_content())
                self.assertIn(requirement["version"], software.text_content())
            self.assertFalse(document.xpath("//section[contains(@class, 'entry-summary')]"))
            sources, = document.xpath("//section[@id='README']//h2[.='Sources and licence']")
            self.assertTrue(sources.xpath("following-sibling::ul//a[starts-with(., 'Wrapper licence:')]"))

    def test_data_managers_and_consumers_have_bidirectional_table_links(self):
        managers = [section for section in self.sections if section["kind"] == "data-manager"]
        self.assertEqual(len(managers), 1)
        consumers = {"example_models": "example_analysis"}
        tools = {
            wrapper["id"]: wrapper for section in self.sections
            if section["kind"] == "tool" for wrapper in section["wrappers"]
        }
        for section in managers:
            wrapper = section["wrappers"][0]
            with self.subTest(manager=wrapper["id"]):
                self.assertEqual({table["name"] for table in wrapper["tables"]}, {"example_models", "unused_models"})
                page = self.site / wrapper["page_url"]
                html = page.read_text()
                self.assertIn("Supplied data tables", html)
                self.assertIn("Wrapper version", html)
                self.assertIn("Wrapper licence", html)
                self.assertIn("Asset catalogue", html)
                self.assertIn("https://huggingface.co/example/reference-models", html)
                self.assertIn("/blob/main/data_managers/", wrapper["source_url"])
                self.assertIn("/wrappers/data_manager/", wrapper["page_url"])
                self.assertEqual(wrapper["main_dependency"]["name"], "python")
                for table in wrapper["tables"]:
                    if table["name"] in consumers:
                        consumer = tools[consumers[table["name"]]]
                        self.assertEqual([link["page_url"] for link in table["links"]], [consumer["page_url"]])
                        reciprocal, = [t for t in consumer["tables"] if t["name"] == table["name"]]
                        self.assertEqual([link["page_url"] for link in reciprocal["links"]], [wrapper["page_url"]])
                    else:
                        self.assertEqual(table["links"], [])
                        self.assertIn("No consuming wrapper in this repository yet.", html)

    def test_manager_version_report_uses_installer_packages_not_model_versions(self):
        from catalogue.version_report import write_version_report

        managers = [section for section in self.sections if section["kind"] == "data-manager"]
        path = self.site / "manager-versions.md"
        write_version_report(managers, path)
        report = path.read_text()
        self.assertEqual(report.count("| python | 3.12 |"), 1)
        self.assertIn("not model/data releases", report)
        self.assertIn("No lookups configured", report.split("### Upstream releases/tags")[1])
        manager, = managers
        self.assertEqual(manager["version_tool"], "7.0.0")
        self.assertNotIn("| python | 7.0.0 |", report)


if __name__ == "__main__":
    unittest.main()
