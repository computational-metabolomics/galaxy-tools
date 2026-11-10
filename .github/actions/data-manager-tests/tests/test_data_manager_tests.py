"""Check reference-data CI selection, staging and failure handling."""

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from lxml import etree
from planemo.ci import filter_paths

FIXTURES = Path(__file__).resolve().parent
ACTION = FIXTURES.parent
helper = SimpleNamespace(**runpy.run_path(str(ACTION / "scripts/prepare.py")))
STEPS = yaml.safe_load((ACTION / "action.yml").read_text())["runs"]["steps"]


def report_result(tool_id, index, status="success"):
    return {
        "id": f"{tool_id}-{index}",
        "has_data": True,
        "data": {"tool_id": tool_id, "test_index": index, "status": status},
    }


class DataManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "staged"
        self.tool = self.root / "tools/example/tool.xml"
        self.manager = self.root / "data_managers/provider"
        self.manager_tool = self.manager / "data_manager/tool.xml"
        for filename, destination in (
            ("tool.xml", self.tool),
            ("data_manager.xml", self.manager_tool),
        ):
            destination.parent.mkdir(parents=True)
            shutil.copyfile(FIXTURES / filename, destination)
        for repo in (self.tool.parent, self.manager):
            (repo / ".shed.yml").touch()
        (self.manager / "data_manager_conf.xml").write_text("""<data_managers>
<data_manager tool_file="data_manager/tool.xml" id="install_model">
<data_table name="models"><output>
    <column name="value"/>
    <column name="name"/>
    <column name="path" output_ref="out_file">
        <move type="file">
            <source>${path}</source>
            <target base="${GALAXY_DATA_MANAGER_DATA_PATH}">models/${value}/reference.txt</target>
        </move>
        <value_translation>${GALAXY_DATA_MANAGER_DATA_PATH}/models/${value}/reference.txt</value_translation>
        <value_translation type="function">abspath</value_translation>
    </column>
</output></data_table>
</data_manager></data_managers>""")
        (self.manager / "tool_data_table_conf.xml.sample").write_text("""<tables>
<table name="models"><columns>value,name,path</columns>
<file path="tool-data/models.loc"/></table></tables>""")

    def prepare(self, tools=None):
        return helper.prepare(
            self.root, [self.tool] if tools is None else tools, self.workspace
        )

    def run_command(self, command, cwd=None, **env):
        return subprocess.run(
            command,
            cwd=cwd or ACTION.parents[2],
            env={
                **os.environ,
                "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                **env,
            },
            capture_output=True,
            text=True,
        )

    def run_setup(self, output, discovered="", commit_range=""):
        scripts = self.root / ".github/actions/data-manager-tests/scripts"
        if not scripts.exists():
            scripts.parent.mkdir(parents=True)
            scripts.symlink_to(ACTION / "scripts", target_is_directory=True)
        return self.run_command(
            ["bash", str(ACTION / "scripts/setup.sh")],
            cwd=self.root,
            RUNNER_TEMP=str(self.root),
            DISCOVERED_REPOSITORIES=discovered,
            MAX_CHUNKS="4",
            COMMIT_RANGE=commit_range,
            GITHUB_OUTPUT=str(output),
        )

    def test_staging_preserves_sources_and_deduplicates_managers(self):
        tree = etree.parse(str(self.manager_tool))
        tests = tree.find("tests")
        tests.append(deepcopy(tests.find("test[@expect_num_outputs='1']")))
        tree.write(str(self.manager_tool))
        sibling = self.tool.with_name("sibling.xml")
        sibling.write_text(
            self.tool.read_text().replace('id="example"', 'id="sibling"')
        )
        fixture = self.tool.parent / "test-data/input.txt"
        fixture.parent.mkdir()
        fixture.write_text("example input\n")
        originals = {
            path: path.read_bytes()
            for path in (self.tool, sibling, self.manager_tool, fixture)
        }
        selection = self.prepare([self.tool, sibling, self.tool, self.manager_tool])
        self.assertEqual(
            selection,
            {
                "tools": ["tools/example/tool.xml", "tools/example/sibling.xml"],
                "managers": ["data_managers/provider/data_manager/tool.xml"],
            },
        )
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(
                (self.workspace / path.relative_to(self.root)).read_bytes(), content
            )
        config = etree.parse(str(self.workspace / "tool_data_table_conf.xml"))
        location = Path(config.find("table/file").get("path"))
        self.assertEqual(location, self.workspace / "installed/models.loc")
        self.assertEqual(location.read_bytes(), b"")
        output = self.root / "outputs"
        helper.write_outputs(selection, self.workspace, output)
        self.assertEqual(
            output.read_text().count(str(self.workspace / "data_managers/provider")), 1
        )
        self.assertIn(
            f"planemo-options=--tool_data_table {self.workspace}/tool_data_table_conf.xml "
            f"--tool_data_path {self.workspace}/installed\n",
            output.read_text(),
        )
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_ordinary_manager_only_and_empty_selections(self):
        tree = etree.parse(str(self.tool))
        tree.getroot().remove(tree.find("inputs"))
        tree.write(str(self.tool))
        sibling = self.tool.with_name("sibling.xml")
        shutil.copyfile(self.tool, sibling)
        (self.tool.parent / "README.rst").symlink_to("../../README.rst")
        for index, tools in enumerate(
            ([self.tool], [self.manager_tool], [self.tool, self.manager_tool], [])
        ):
            with self.subTest(tools=tools):
                self.workspace = self.root / f"staged-{index}"
                selection = self.prepare(tools)
                self.assertEqual(len(selection["tools"]), self.tool in tools)
                self.assertEqual(len(selection["managers"]), self.manager_tool in tools)
                output = self.workspace / "outputs"
                helper.write_outputs(selection, self.workspace, output)
                if not selection["managers"]:
                    self.assertIn("planemo-options=\n", output.read_text())
                if selection["tools"]:
                    staged = self.workspace / "tools/example"
                    self.assertEqual(
                        (staged / "README.rst").readlink(), Path("../../README.rst")
                    )
                    self.assertFalse((staged / "sibling.xml").exists())
                    self.assertTrue(sibling.exists())

    def test_unsafe_paths_and_missing_or_ambiguous_providers(self):
        with self.assertRaises(ValueError):
            self.prepare([self.root / "../outside.xml"])
        other = self.manager.with_name("other")
        shutil.copytree(self.manager, other)
        messages = {
            2: r"tools/example/tool\.xml: data tables \(models\) are supplied by more than one",
            0: r"tools/example/tool\.xml: no single data manager .*not supplied by any: models$",
        }
        for providers, message in messages.items():
            with self.subTest(providers=providers):
                if not providers:
                    shutil.rmtree(self.manager)
                    shutil.rmtree(other)
                with self.assertRaisesRegex(ValueError, message):
                    self.prepare()
                self.assertFalse(self.workspace.exists())

    def test_invalid_table_and_positive_test_requirements(self):
        original = self.manager_tool.read_text()
        self.manager_tool.write_text(
            original.replace('expect_num_outputs="1"', 'expect_failure="true"')
        )
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.prepare()
        self.manager_tool.write_text(original)
        original = self.tool.read_text()
        self.tool.write_text(
            original.replace('expect_num_outputs="1"', 'expect_failure="true"')
        )
        with self.assertRaisesRegex(ValueError, "positive analysis tests"):
            self.prepare()
        self.tool.write_text(original)
        schema = self.manager / "tool_data_table_conf.xml.sample"
        schema.write_text(
            schema.read_text().replace('name="models"', 'name="../escape"')
        )
        with self.assertRaisesRegex(ValueError, "table declaration"):
            self.prepare()
        self.assertFalse((self.workspace / "escape.loc").exists())

    def test_changed_paths_select_pairs_and_ignore_non_tool_xml(self):
        (self.tool.parent / "fixture.xml").write_text("<deliberately-invalid-fixture>")
        pair = ["tools/example", "data_managers/provider"]
        for changed, discovered, expected in (
            (["tools/example/tool.xml"], [], pair),
            (["data_managers/provider/data_manager_conf.xml"], [pair[1]], pair),
            ([".github/actions/data-manager-tests/action.yml"], [], pair),
            ([".github/workflows/pr.yaml"], [], pair),
            (["README.md"], [], []),
            ([], [pair[0]], pair),
        ):
            with self.subTest(changed=changed):
                self.assertCountEqual(
                    helper.augment_repositories(self.root, discovered, changed),
                    expected,
                )

    def test_reports_require_every_macro_expanded_test_to_succeed(self):
        self.prepare()
        report = self.root / "report.json"
        for kind, tool_id in (("tools", "example"), ("managers", "install_model")):
            valid = [report_result(tool_id, i) for i in range(2)]
            report.write_text(json.dumps({"tests": valid}))
            helper.verify_report(self.workspace, kind, report)
            for invalid in (
                [],
                valid[:1],
                [valid[0], valid[0]],
                [valid[0], report_result(tool_id, 2)],
                *[
                    [valid[0], report_result(tool_id, 1, status)]
                    for status in ("skip", "failure", "error")
                ],
            ):
                with self.subTest(kind=kind, results=invalid):
                    report.write_text(json.dumps({"tests": invalid}))
                    with self.assertRaisesRegex(
                        ValueError, "Missing, skipped or failed"
                    ):
                        helper.verify_report(self.workspace, kind, report)

    def test_empty_and_removed_repositories_are_not_selected(self):
        empty = self.root / "empty"
        empty.mkdir()
        deleted = ["tools/removed/tool.xml", "data_managers/removed/.shed.yml"]
        for tools_present in (False, True):
            if tools_present:
                (empty / "tools").mkdir()
            for discovered in ([], ["."], ["tools/removed", "data_managers/removed"]):
                with self.subTest(tools_present=tools_present, discovered=discovered):
                    self.assertEqual(
                        helper.augment_repositories(empty, discovered, deleted), []
                    )
        ordinary = self.root / "ordinary"
        ordinary.mkdir()
        (ordinary / ".dockstore.yml").touch()
        self.assertEqual(
            helper.augment_repositories(self.root, ["ordinary", ".", "removed"], []),
            ["ordinary"],
        )

    def test_setup_empty_selection_and_invalid_revision(self):
        for index, (discovered, commit_range) in enumerate(
            (("", ""), (".\ntools/removed", ""), ("", ":invalid-revision:"))
        ):
            output = self.root / f"output-{index}"
            result = self.run_setup(output, discovered, commit_range)
            if commit_range:
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("chunk-count=1\n", output.read_text())
                self.assertIn("chunk-list=[0]\n", output.read_text())
                for name, delimiter in (
                    ("repository-list", "CHANGED_REPOSITORIES"),
                    ("test-repository-list", "TEST_REPOSITORIES"),
                ):
                    self.assertIn(f"{name}<<{delimiter}\n{delimiter}\n", output.read_text())

    def test_setup_lints_changed_repositories_and_tests_partners(self):
        output = self.root / "output"
        result = self.run_setup(output, "data_managers/provider")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = output.read_text()
        self.assertIn(
            "repository-list<<CHANGED_REPOSITORIES\ndata_managers/provider\nCHANGED_REPOSITORIES\n", text
        )
        self.assertIn(
            "test-repository-list<<TEST_REPOSITORIES\ndata_managers/provider\ntools/example\n"
            "TEST_REPOSITORIES\n",
            text,
        )
        self.assertIn("chunk-count=2\n", text)

    def test_partners_listed_in_tt_skip_are_not_added(self):
        (self.root / ".tt_skip").write_text("tools/example\n")
        self.assertEqual(
            helper.augment_repositories(self.root, ["data_managers/provider"], []),
            ["data_managers/provider"],
        )

    def test_preparation_fails_before_staging_an_excluded_dependency(self):
        for path in ("data_managers/provider", "data_managers/provider/data_manager/tool.xml"):
            (self.root / ".tt_skip").write_text(path + "\n")
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, r"tools/example: requires data manager data_managers/provider, excluded by .tt_skip"
            ):
                self.prepare()
            self.assertFalse(self.workspace.exists())

    def test_preparation_respects_explicitly_selected_excluded_tools(self):
        (self.root / ".tt_skip").write_text("tools/example\ndata_managers/provider\n")
        self.assertEqual(self.prepare([self.tool, self.manager_tool]), {"tools": [], "managers": []})

    def test_staging_repoints_links_that_leave_a_repository(self):
        shared = self.root / "shared-test-data" / "input.txt"
        shared.parent.mkdir()
        shared.write_text("shared input\n")
        test_data = self.tool.parent / "test-data"
        test_data.mkdir()
        (test_data / "input.txt").symlink_to("../../../shared-test-data/input.txt")
        (test_data / "alias.txt").symlink_to("input.txt")
        self.prepare()
        staged = self.workspace / "tools/example/test-data"
        self.assertEqual((staged / "input.txt").read_text(), "shared input\n")
        self.assertEqual((staged / "input.txt").readlink(), shared)
        self.assertEqual((staged / "alias.txt").readlink(), Path("input.txt"))

    def test_workflows_share_discovery(self):
        root = ACTION.parents[2]
        workflows = {
            name: yaml.load((root / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
            for name in ("pr.yaml", "ci.yaml")
        }
        for name, workflow in workflows.items():
            with self.subTest(workflow=name):
                setup = workflow["jobs"]["setup"]
                for output in ("repository-list", "test-repository-list", "chunk-count", "chunk-list"):
                    self.assertEqual(setup["outputs"][output], "${{ steps.augment.outputs." + output + " }}")
                step = next(step for step in setup["steps"] if step.get("id") == "augment")
                self.assertEqual(step["run"], "bash .github/actions/data-manager-tests/scripts/setup.sh")
                self.assertEqual(step["env"]["DISCOVERED_REPOSITORIES"], "${{ steps.discover.outputs.repository-list }}")
                # Tests get tool/data-manager partners; lint keeps IUC's list of changed repositories.
                test = workflow["jobs"]["test"]
                self.assertIn("needs.setup.outputs.test-repository-list != ''", test["if"])
                runner = next(step for step in test["steps"] if step.get("id") == "test")
                self.assertEqual(runner["with"]["repository-list"], "${{ needs.setup.outputs.test-repository-list }}")
                self.assertIn("needs.setup.outputs.test-repository-list != ''", workflow["jobs"]["combine_outputs"]["if"])
                lint = next(step for step in workflow["jobs"]["lint"]["steps"] if step.get("id") == "lint")
                self.assertEqual(lint["with"]["repository-list"], "${{ needs.setup.outputs.repository-list }}")

    def test_tool_shed_publication_requires_switch_and_main_push(self):
        workflow = yaml.load(
            (ACTION.parents[2] / ".github/workflows/pr.yaml").read_text(), Loader=yaml.BaseLoader
        )
        deploy = workflow["jobs"]["deploy"]
        condition = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        # Exact conjunction needs the opt-in variable and excludes PRs, manual runs and other branches.
        self.assertEqual(
            " ".join(deploy["if"].split()),
            f"vars.ENABLE_TOOLSHED_PUBLISHING == 'true' && {condition}",
        )
        self.assertEqual(deploy["needs"], ["setup", "lint", "combine_outputs"])
        report = workflow["jobs"]["deploy-report"]
        self.assertEqual(report["needs"], ["deploy"])
        self.assertEqual(
            " ".join(report["if"].split()),
            f"always() && {condition} && needs.deploy.result != 'skipped'",
        )

    def test_tool_shed_uploads_follow_mirror_verification_and_iuc_order(self):
        workflow = yaml.load(
            (ACTION.parents[2] / ".github/workflows/pr.yaml").read_text(), Loader=yaml.BaseLoader
        )
        deploy = workflow["jobs"]["deploy"]
        uploads = [step for step in deploy["steps"] if step.get("with", {}).get("mode") == "deploy"]
        self.assertEqual([step["with"]["shed-target"] for step in uploads], ["testtoolshed", "toolshed"])
        self.assertEqual(
            [step["with"]["shed-key"] for step in uploads],
            ["${{ secrets.TTS_API_KEY }}", "${{ secrets.TS_API_KEY }}"],
        )
        for step in uploads:
            self.assertEqual(step["with"]["repository-list"], "${{ needs.setup.outputs.repository-list }}")
            self.assertNotIn("if", step)
        self.assertEqual(uploads[0]["continue-on-error"], "true")
        self.assertNotIn("continue-on-error", uploads[1])
        verification = next(step for step in deploy["steps"] if "--verify-only" in step.get("run", ""))
        self.assertLess(deploy["steps"].index(verification), deploy["steps"].index(uploads[0]))
        self.assertNotIn("continue-on-error", verification)
        self.assertNotIn("if", verification)

    def test_fixture_xml_is_excluded_from_production_discovery(self):
        root = ACTION.parents[2]
        context = Mock()
        fixture = root / "tests/fixtures/catalogue/tools/example/tool.xml"
        with patch("planemo.ci.os.getcwd", return_value=str(root)):
            self.assertEqual(
                filter_paths(context, [str(FIXTURES / "tool.xml"), str(fixture)],
                             chunk_count=1, chunk=0, exclude_from=[str(root / ".tt_skip")]),
                [],
            )

    def test_managers_are_verified_before_tools_run(self):
        runners = [
            s
            for s in STEPS
            if s.get("uses", "").startswith("galaxyproject/planemo-ci-action@")
        ]
        manager, tool = runners
        gate = next(s for s in STEPS if "--kind managers" in s.get("run", ""))
        self.assertLess(STEPS.index(manager), STEPS.index(gate))
        self.assertLess(STEPS.index(gate), STEPS.index(tool))
        self.assertNotIn("always()", tool.get("if", ""))
        for runner, kind in ((manager, "managers"), (tool, "tools")):
            self.assertEqual(
                runner["with"]["repository-list"],
                "${{ steps.prepare.outputs." + kind + " }}",
            )
            self.assertEqual(
                runner["with"]["additional-planemo-options"],
                "${{ steps.prepare.outputs.planemo-options }}",
            )

    def test_report_merge_preserves_failures_and_empty_chunks(self):
        merge = next(
            s for s in STEPS if "planemo merge_test_reports" in s.get("run", "")
        )
        self.assertIn("always()", merge["if"])
        for statuses in (
            [],
            ["failure"],
            ["failure", "success"],
        ):
            with (
                self.subTest(statuses=statuses),
                tempfile.TemporaryDirectory() as directory,
            ):
                upload = Path(directory) / "upload"
                upload.mkdir()
                for index, status in enumerate(statuses):
                    name = (
                        "manager_tool_test_output.json"
                        if index == 0
                        else "tool_test_output.json"
                    )
                    (upload / name).write_text(
                        json.dumps({"tests": [report_result("example", index, status)]})
                    )
                result = self.run_command(
                    ["bash", "-e", "-c", merge["run"]], cwd=directory
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                merged = json.loads((upload / "tool_test_output.json").read_text())
                self.assertCountEqual(
                    [t["data"]["status"] for t in merged["tests"]], statuses
                )
                self.assertTrue((upload / "tool_test_output.html").is_file())


if __name__ == "__main__":
    unittest.main()
