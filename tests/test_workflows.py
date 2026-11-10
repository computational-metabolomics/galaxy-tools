import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent


class WorkflowTests(unittest.TestCase):
    def test_required_pr_checks_cover_all_ci_jobs(self):
        # BaseLoader preserves the Actions key "on" instead of YAML 1.1's boolean.
        workflows = {
            path.name: yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            for path in (ROOT / ".github" / "workflows").glob("*.y*ml")
        }
        docs = workflows["docs.yaml"]
        self.assertIn("pull_request", docs["on"])
        self.assertFalse(docs["on"]["pull_request"])
        self.assertEqual(docs["jobs"]["build"]["name"], "Validate and build")
        gates = [
            (filename, job)
            for filename, workflow in workflows.items()
            for job in workflow.get("jobs", {}).values()
            if job.get("name") == "Check workflow success"
        ]
        self.assertEqual(len(gates), 1)
        filename, gate = gates[0]
        self.assertEqual(filename, "pr.yaml")
        self.assertIn("pull_request", workflows[filename]["on"])
        self.assertFalse(workflows[filename]["on"]["pull_request"])
        # New checks must join the merge gate; deployment jobs are exempt.
        required = set(workflows[filename]["jobs"]) - {"determine-success", "deploy", "deploy-report"}
        self.assertEqual(set(gate["needs"]), required)
        self.assertIn("always()", gate["if"])
        conditions = " ".join(
            step["if"] for step in gate["steps"] if step.get("run") == "exit 1"
        )
        for job in ("setup", "mirror_verification"):
            self.assertIn(f"needs.{job}.result != 'success'", conditions)
            self.assertNotIn(f"needs.{job}.result != 'skipped'", conditions)
        for job in required - {"setup", "mirror_verification"}:
            with self.subTest(job=job):
                self.assertIn(
                    f"needs.{job}.result != 'success' && "
                    f"needs.{job}.result != 'skipped'",
                    conditions,
                )

    def test_pr_mirror_verification_is_read_only_and_has_no_duplicate_trigger(self):
        workflow = yaml.load((ROOT / ".github/workflows/pr.yaml").read_text(), Loader=yaml.BaseLoader)
        job = workflow["jobs"]["mirror_verification"]
        self.assertEqual(job["if"], "github.event_name == 'pull_request'")
        self.assertEqual(job["permissions"], {"contents": "read"})
        steps = job["steps"]
        check = next(step for step in steps if "--dry-run" in step.get("run", ""))
        self.assertEqual(check["if"], "steps.changes.outputs.required == 'true'")
        self.assertEqual(check["shell"], "bash")  # Actions enables pipefail for explicit bash.
        self.assertNotIn("--publish", str(job))
        self.assertNotIn("secrets.", str(job))
        standalone = yaml.load((ROOT / ".github/workflows/hf-mirror.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertNotIn("pull_request", standalone["on"])

    def test_mirror_path_selection_handles_relevant_unrelated_and_failed_diffs(self):
        workflow = yaml.load((ROOT / ".github/workflows/pr.yaml").read_text(), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["mirror_verification"]["steps"]
        script = next(step["run"] for step in steps if step.get("id") == "changes")
        relevant = [
            "data_managers/example/assets.json", ".github/scripts/hf_mirror.py",
            ".github/workflows/hf-mirror.yml", ".github/workflows/pr.yaml",
            "tests/test_hf_mirror.py", "tests/fixtures/hf-mirror-smoke/files/model.txt",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed, output = root / "changed", root / "outputs"
            env = {**os.environ, "RUNNER_TEMP": directory, "GITHUB_OUTPUT": str(output),
                   "BASE_SHA": "base", "HEAD_SHA": "head", "CHANGED_FILES": str(changed)}
            for path in [*relevant, "README.md", "tools/example/tool.xml", "data_managers-old/example", ""]:
                with self.subTest(path=path):
                    changed.write_bytes((path + "\0").encode() if path else b"")
                    output.write_text("")
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", 'git() { cat "$CHANGED_FILES"; }\n' + script],
                        env=env, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.read_text(), f"required={str(path in relevant).lower()}\n")
            output.write_text("")
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", "git() { return 1; }\n" + script],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "")

    def test_preview_artifact_is_uploaded_without_deploying_feature_branches(self):
        workflow = yaml.load((ROOT / ".github/workflows/docs.yaml").read_text(), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["build"]["steps"]
        preview = next(step for step in steps if step.get("with", {}).get("name") == "catalogue-preview")
        self.assertTrue(preview["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(preview["if"], "github.event_name == 'pull_request' || github.ref != 'refs/heads/main'")
        self.assertEqual(preview["with"]["path"], "site")
        self.assertEqual(preview["with"]["if-no-files-found"], "error")
        main_only = "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
        self.assertEqual(workflow["jobs"]["deploy"]["if"], main_only)
        for step in steps:
            if any(action in step.get("uses", "") for action in ("configure-pages", "upload-pages-artifact")):
                self.assertEqual(step["if"], main_only)

    def test_catalogue_source_links_use_pr_head_or_current_commit(self):
        workflow = yaml.load((ROOT / ".github/workflows/docs.yaml").read_text(), Loader=yaml.BaseLoader)
        build = workflow["jobs"]["build"]
        self.assertEqual(
            build["env"]["CATALOGUE_REPO_URL"],
            "${{ github.server_url }}/${{ github.event.pull_request.head.repo.full_name || github.repository }}",
        )
        self.assertEqual(
            build["env"]["CATALOGUE_SOURCE_REF"],
            "${{ github.event.pull_request.head.sha || github.sha }}",
        )
        commands = [
            step["run"] for step in build["steps"]
            if "python catalogue/build.py" in step.get("run", "")
        ]
        self.assertTrue(commands, "Catalogue build commands must be present")
        for command in commands:
            with self.subTest(command=command):
                self.assertIn('--repo-url "$CATALOGUE_REPO_URL"', command)
                self.assertIn('--source-ref "$CATALOGUE_SOURCE_REF"', command)

    def test_write_permissions_stay_within_job_allowlist(self):
        # As in IUC, other jobs rely on the repository's read-only token default.
        allowed_writes = {
            ("ci.yaml", "setup"): {"issues", "pull-requests"},
            ("ci.yaml", "combine_outputs"): {"issues", "pull-requests"},
            ("docs.yaml", "deploy"): {"id-token", "pages"},
            ("pr.yaml", "deploy-report"): {"issues", "pull-requests"},
            ("slash.yaml", "slashCommandDispatch"): {"contents", "issues", "pull-requests"},
        }
        for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            jobs = workflow["jobs"]
            for name, holder in [("<workflow>", workflow), *jobs.items()]:
                permissions = holder.get("permissions", {})
                self.assertIsInstance(permissions, dict, f"{path.name} {name}")
                granted = {scope for scope, level in permissions.items() if level == "write"}
                with self.subTest(workflow=path.name, job=name):
                    self.assertLessEqual(granted, allowed_writes.get((path.name, name), set()))


if __name__ == "__main__":
    unittest.main()
