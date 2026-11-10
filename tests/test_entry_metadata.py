import tempfile
import unittest
from pathlib import Path

import yaml
from catalogue.metadata import load_metadata


class EntryInformationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.readme = self.folder / "README.md"
        self.readme.write_text("# Example tool\n\nScientific **purpose**.\n", encoding="utf8")
        self.shed = self.folder / ".shed.yml"

    def test_readme_only_entry_has_title_and_prose_without_inventing_dependencies(self):
        self.assertEqual(load_metadata(self.folder), {
            "readme_title": "Example tool", "readme_description": "Scientific purpose.",
            "description": "", "homepage": "",
        })
        for source in ("Overview\n========\n", "Introduction.\n\n# Later heading\n", "    # Code example\n"):
            with self.subTest(source=source):
                self.readme.write_text(source, encoding="utf8")
                self.assertEqual(load_metadata(self.folder)["readme_title"], "")
        self.readme.unlink()
        with self.assertRaisesRegex(ValueError, r"README.md: file is required"):
            load_metadata(self.folder)

    def test_legacy_metadata_requests_migration_instead_of_silently_losing_information(self):
        (self.folder / "metadata.yaml").write_text("main_dependency: example\n", encoding="utf8")
        with self.assertRaisesRegex(ValueError, r"metadata.yaml: no longer supported.*README.md.*.shed.yml"):
            load_metadata(self.folder)

    def test_standalone_hints_allow_inline_code_and_sources_but_ignore_documentation_examples(self):
        self.readme.write_text("""# Example *tool*

```markdown
Main dependency: fenced-example
```

    Main dependency: indented-example

> Main dependency: quoted-example

- Main dependency: list-example

Main dependency: `example-package`

Conda channel: `bioconda`

Upstream source: https://pypi.org/project/example-package/

Upstream source: [Development](https://github.com/example/package)

Scientific **purpose** with [documentation](https://example.org/docs).
""", encoding="utf8")
        information = load_metadata(self.folder)
        self.assertEqual(information["readme_title"], "Example tool")
        self.assertEqual(information["readme_description"], "Scientific purpose with documentation.")
        self.assertEqual(information["main_dependency"], "example-package")
        self.assertEqual(information["conda_channel"], "bioconda")
        self.assertEqual(information["upstream_sources"], [
            "https://pypi.org/project/example-package/", "https://github.com/example/package",
        ])
        self.readme.write_text("Conda channel: bioconda\n\nUpstream source: https://example.org\n", encoding="utf8")
        self.assertNotIn("main_dependency", load_metadata(self.folder))

    def test_invalid_or_duplicate_readme_hints_fail_with_the_file_path(self):
        for text, message in (
            ("Main dependency:", "must contain one package"),
            ("Main dependency: a, b", "must contain one package"),
            ("Conda channel: https://example.org", "must contain one package or channel"),
            ("Main dependency: a\n\nMain dependency: b", "duplicate 'main dependency'"),
            ("Conda channel: a\n\nConda channel: b", "duplicate 'conda channel'"),
            ("Upstream source:", "absolute http"),
            ("Upstream source: ftp://example.org", "absolute http"),
            ("Upstream source: https://example.org extra", "absolute http"),
            ("Upstream source: [Unsafe](javascript:alert)", "absolute http"),
            ("Upstream source: [One](https://one.org) and [Two](https://two.org)", "one URL"),
            ("Upstream source: [One](https://one.org) extra", "one URL"),
        ):
            with self.subTest(text=text):
                self.readme.write_text(text, encoding="utf8")
                with self.assertRaisesRegex(ValueError, rf"README.md: .*{message}"):
                    load_metadata(self.folder)

    def test_shed_description_precedence_and_automatic_repository_templates(self):
        data = {
            "description": "Entry overview", "homepage_url": "https://example.org",
            "auto_tool_repositories": {"name_template": "{{ tool_id }}"},
            "repositories": {"one": {"description": "Repository overview"}},
            "suite": {"description": "Suite overview"},
        }
        for expected in ("Entry overview", "Repository overview", "Suite overview", ""):
            self.shed.write_text(yaml.safe_dump(data), encoding="utf8")
            information = load_metadata(self.folder)
            self.assertEqual(information["description"], expected)
            self.assertEqual(information["homepage"], "https://example.org")
            self.assertNotIn("upstream_sources", information)
            if "description" in data:
                del data["description"]
            elif data["repositories"]:
                data["repositories"] = {}
            else:
                data["suite"] = {}
        data["repositories"] = {
            "one": {"description": "Identical description"},
            "two": {"description": "Identical description"},
        }
        self.shed.write_text(yaml.safe_dump(data), encoding="utf8")
        self.assertEqual(load_metadata(self.folder)["description"], "Identical description")
        data["repositories"]["two"]["description"] = "Different description"
        data["suite"]["description"] = "{{ tool_name }} is preserved as data"
        self.shed.write_text(yaml.safe_dump(data), encoding="utf8")
        self.assertEqual(load_metadata(self.folder)["description"], "{{ tool_name }} is preserved as data")

    def test_invalid_consumed_shed_fields_fail_without_enforcing_unrelated_schema(self):
        for suite in (False, None):
            with self.subTest(suite=suite):
                self.shed.write_text(yaml.safe_dump({
                    "description": "Repository overview", "suite": suite, "repositories": None,
                }), encoding="utf8")
                self.assertEqual(load_metadata(self.folder)["description"], "Repository overview")
        for value, message in (
            ([], "YAML mapping"),
            (0, "YAML mapping"),
            ({"description": ["not a string"]}, "description.*must be a string"),
            ({"homepage_url": "file:///etc/passwd"}, "homepage_url.*absolute http"),
            ({"homepage_url": "https://[invalid"}, "homepage_url.*absolute http"),
            ({"suite": []}, "suite.*YAML mappings"),
            ({"suite": True}, "suite.*YAML mappings"),
            ({"repositories": False}, "repositories.*YAML mappings"),
            ({"repositories": {"example": "invalid"}}, "repositories.example.*YAML mapping"),
            ({"repositories": {"example": {"homepage_url": False}}}, "homepage_url.*must be a string"),
        ):
            with self.subTest(value=value):
                self.shed.write_text(yaml.safe_dump(value), encoding="utf8")
                with self.assertRaisesRegex(ValueError, rf".shed.yml: .*{message}"):
                    load_metadata(self.folder)
        self.shed.write_text("description: [unfinished", encoding="utf8")
        with self.assertRaisesRegex(ValueError, r".shed.yml: invalid YAML"):
            load_metadata(self.folder)


if __name__ == "__main__":
    unittest.main()
