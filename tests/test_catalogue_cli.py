import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class CatalogueEntryPointTests(unittest.TestCase):
    def test_direct_script_and_module_commands_build_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            shutil.copytree(
                ROOT / "catalogue", root / "catalogue",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            fixtures = ROOT / "tests/fixtures/catalogue"
            for name in ("tools", "data_managers"):
                shutil.copytree(fixtures / name, root / name)
            report = root / "versions.md"
            commands = (
                ([str(root / "catalogue/build.py")], Path(directory)),
                (["-m", "catalogue.build"], root),
                (["-m", "catalogue.cli"], root),
            )
            for command, cwd in commands:
                with self.subTest(command=command):
                    # Each command must write its own site and report.
                    report.unlink(missing_ok=True)
                    shutil.rmtree(root / "site", ignore_errors=True)
                    result = subprocess.run(
                        [sys.executable, *command, "--offline", "--repo-url",
                         "https://github.com/example/catalogue", "--source-ref", "refactor/test",
                         "--version-report", str(report)],
                        cwd=cwd, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "Catalogue generated.")
                    self.assertEqual(result.stderr, "")
                    index = (root / "site/index.html").read_text()
                    self.assertIn("example / catalogue", index)
                    self.assertIn("/blob/refactor%2Ftest/LICENSE", index)
                    self.assertIn("Example Analysis", index)
                    self.assertIn("Example Analysis", report.read_text())

    def test_section_assembly_returns_complete_wrapper_links_without_templates(self):
        result = subprocess.run(
            [sys.executable, "-c", (
                "import json\n"
                "from pathlib import Path\n"
                "from unittest.mock import patch\n"
                "with patch('jinja2.Environment', side_effect=AssertionError('template setup')):\n"
                "    from catalogue import metadata, references, sections, tools\n"
                "    fixtures = Path('tests/fixtures/catalogue').resolve()\n"
                "    entries = [\n"
                "        (folder, metadata.load_metadata(folder), list(tools.discover_tools(folder)))\n"
                "        for folder in (fixtures / 'tools/example_analysis', fixtures / 'data_managers/example')\n"
                "    ]\n"
                "    tables = references.reference_tables(entries)\n"
                "    built = [sections.build_section(*entry, tables, offline=True,\n"
                "                                    repo_url='https://github.com/example/repo', source_ref='a/b')\n"
                "             for entry in entries]\n"
                "print(json.dumps([[section['slug'], [{key: wrapper[key] for key in\n"
                "    ('file', 'url', 'page_url', 'source_url')} for wrapper in section['wrappers']]]\n"
                "    for section in built]))\n"
            )],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        (tool_slug, (tool,)), (manager_slug, (manager,)) = json.loads(result.stdout)
        self.assertEqual(tool, {
            "file": "example.xml",
            "url": "wrappers/example.html",
            "page_url": f"{tool_slug}/wrappers/example.html",
            "source_url": "https://github.com/example/repo/blob/a%2Fb/tests/fixtures/catalogue/tools/example_analysis/example.xml",
        })
        self.assertEqual(manager["file"], "data_manager/example.xml")
        self.assertEqual(manager["url"], "wrappers/data_manager/example.html")
        self.assertEqual(manager["page_url"], f"{manager_slug}/wrappers/data_manager/example.html")


if __name__ == "__main__":
    unittest.main()
