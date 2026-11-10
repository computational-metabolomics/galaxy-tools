import copy
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from http.client import HTTPResponse, IncompleteRead
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import call, MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "hf_mirror.py"
SPEC = importlib.util.spec_from_file_location("hf_mirror", SCRIPT)
mirror = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mirror)


def http_response(body, length):
    headers = b"" if length is None else f"Content-Length: {length}\r\n".encode()
    socket = Mock()
    socket.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)
    response = HTTPResponse(socket)
    response.begin()
    return response


class MissingRepository(Exception):
    pass


class MirrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = "computational-metabolomics/example-model"
        self.revision = "models-1.0.0-r1"
        self.payloads = {"model": b"original model", "fast": b"original filter"}
        self.catalogue = {
            "name": "Example model",
            "tags": ["galaxy", "metabolite", "metabolite-annotation"],
            "description": "Original model and filter checkpoints.",
            "source_url": "https://example.org/record/123",
            "license": "cc-by-4.0",
            "license_url": "https://example.org/license",
            "attribution": "Original authors, example publication.",
            "releases": {
                "1.0.0": {
                    "mirror": {"repo": self.repo, "revision": self.revision},
                    "assets": {
                        role: {
                            "filename": f"{role}.ckpt",
                            "url": f"https://example.org/{role}.ckpt",
                            "md5": hashlib.md5(payload).hexdigest(),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                        for role, payload in self.payloads.items()
                    },
                }
            },
        }
        self.write_catalogue()
        self.api = Mock()
        self.info = SimpleNamespace(private=False, gated=False, sha="c" * 40, siblings=[])
        self.api.repo_info.return_value = self.info
        self.api.list_repo_refs.return_value = SimpleNamespace(tags=[], branches=[])
        self.api.create_commit.return_value = SimpleNamespace(oid="a" * 40)
        self.sdk = ModuleType("huggingface_hub")
        self.sdk.HfApi = Mock(return_value=self.api)
        self.sdk.CommitOperationAdd = lambda **kwargs: SimpleNamespace(**kwargs)
        errors = ModuleType("huggingface_hub.errors")
        errors.RepositoryNotFoundError = MissingRepository
        sdk_patch = patch.dict("sys.modules", {"huggingface_hub": self.sdk, "huggingface_hub.errors": errors})
        sdk_patch.start()
        self.addCleanup(sdk_patch.stop)
        output_patch = patch("sys.stdout", new_callable=io.StringIO)
        self.output = output_patch.start()
        self.addCleanup(output_patch.stop)

    def write_catalogue(self, name="pilot"):
        path = self.root / "data_managers" / name / "data_manager" / "assets.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.catalogue))

    def write_local_files(self):
        directory = self.root / "files"
        directory.mkdir()
        for role, payload in self.payloads.items():
            (directory / f"{role}.ckpt").write_bytes(payload)
        return directory

    def response(self, url, timeout):
        self.assertEqual(timeout, 60)
        filename = url.rsplit("/", 1)[-1]
        if filename == "README.md":
            payload = mirror.card(self.catalogue, "1.0.0", self.catalogue["releases"]["1.0.0"])
        else:
            payload = self.payloads[filename.removesuffix(".ckpt")]
        return io.BytesIO(payload)

    def assert_no_mutations(self):
        self.api.create_repo.assert_not_called()
        self.api.create_commit.assert_not_called()
        self.api.create_tag.assert_not_called()

    def test_discovery_skips_releases_without_mirror(self):
        releases = mirror.discover(self.root)
        self.assertEqual(len(releases), 1)
        del self.catalogue["releases"]["1.0.0"]["mirror"]
        self.write_catalogue("not_opted_in")
        self.assertEqual(len(mirror.discover(self.root)), 1)
        self.catalogue = {}
        self.write_catalogue("unregistered")
        self.assertEqual(len(mirror.discover(self.root)), 1)

    def test_invalid_metadata_and_duplicate_targets_fail_before_network(self):
        original = copy.deepcopy(self.catalogue)
        changes = [
            lambda c: c.pop("tags"),
            lambda c: c.update(tags="metabolomics"),
            lambda c: c.update(tags=[" "]),
            lambda c: c.update(tags=["metabolite\nannotation"]),
            lambda c: c.update(tags=["metabolite", "metabolite"]),
            lambda c: c.pop("name"),
            lambda c: c.update(description=None),
            lambda c: c.update(license_note=[]),
            lambda c: c.update(license_note=None),
            lambda c: c.update(galaxy_data_manager="../manager"),
            lambda c: c.update(sources=[]),
            lambda c: c.update(sources={"Reference": "file:///private/source"}),
            lambda c: c.pop("license"),
            lambda c: c.update(attribution=""),
            lambda c: c.update(license_url="file:///private/license"),
            lambda c: c.update(software_license="MIT"),
            lambda c: c.update(software_license_url="https://example.org/LICENSE"),
            lambda c: c.update(software_license="MIT", software_license_url="file:///private/LICENSE"),
            lambda c: c.update(
                license="UNKNOWN", software_license="MIT", software_license_url="https://example.org/LICENSE"
            ),
            lambda c: c.update(hub_license="MIT"),
            lambda c: c.update(software_url="file:///private/source"),
            lambda c: c.update(doi=None),
            lambda c: c.update(doi="10.1234/"),
            lambda c: c.update(doi="https://doi.org/10.1234/data"),
            lambda c: c.update(current_release="unknown"),
            lambda c: c.update(current_release=None),
            lambda c: c["releases"]["1.0.0"].update(software_compatibility=""),
            lambda c: c["releases"]["1.0.0"].update(software_compatibility=None),
            lambda c: c["releases"]["1.0.0"].update(software_compatibility="1.0\n2.0"),
            lambda c: c["releases"]["1.0.0"].update(software_compatibility="1.0|2.0"),
            lambda c: c["releases"]["1.0.0"].update(software_compatibility="`1.0`"),
            lambda c: c["releases"]["1.0.0"].update(source_url="file:///private/source"),
            lambda c: c["releases"]["1.0.0"].update(attribution=""),
            lambda c: c["releases"]["1.0.0"].update(license="MIT"),
            lambda c: c["releases"]["1.0.0"].update(license_url="https://example.org/LICENSE"),
            lambda c: c["releases"]["1.0.0"].update(license="unknown", license_url="https://example.org/LICENSE"),
            lambda c: c["releases"]["1.0.0"].update(license="MIT", license_url="file:///private/LICENSE"),
            lambda c: c["releases"]["1.0.0"].update(license_note=[]),
            lambda c: c["releases"]["1.0.0"].update(hub_license="MIT"),
            lambda c: c["releases"]["1.0.0"].update(software_url="file:///private/source"),
            lambda c: c["releases"]["1.0.0"].update(software_license="MIT"),
            lambda c: c["releases"]["1.0.0"].update(software_license_url="https://example.org/LICENSE"),
            lambda c: c["releases"]["1.0.0"].update(
                software_license="MIT", software_license_url="file:///private/LICENSE"
            ),
            lambda c: c["releases"]["1.0.0"]["mirror"].update(repo="other-org/example-model"),
            lambda c: c["releases"]["1.0.0"]["mirror"].update(revision="../unsafe"),
            lambda c: c["releases"]["1.0.0"]["mirror"].update(revision="main"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(filename="../escape"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(filename="fast.ckpt"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(sha256="bad"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].pop("md5"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(license="MIT"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(license_url="https://example.org/LICENSE"),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(
                license="unknown", license_url="https://example.org/LICENSE"
            ),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(
                license="MIT", license_url="file:///private/LICENSE"
            ),
            lambda c: c["releases"]["1.0.0"]["assets"]["model"].update(doi="https://doi.org/10.1234/data"),
        ]
        with patch.object(mirror, "urlopen") as fetch:
            for case, change in enumerate(changes):
                self.catalogue = copy.deepcopy(original)
                change(self.catalogue)
                self.write_catalogue()
                with self.subTest(case=case), self.assertRaises(ValueError):
                    mirror.run(self.root)
            multiple = copy.deepcopy(original)
            multiple["releases"]["2.0.0"] = copy.deepcopy(multiple["releases"]["1.0.0"])
            multiple["releases"]["2.0.0"]["mirror"]["revision"] = "models-2.0.0-r1"
            multiple["releases"]["unmirrored"] = {"assets": {}}
            for current in (None, "unknown", "unmirrored"):
                self.catalogue = copy.deepcopy(multiple)
                if current is not None:
                    self.catalogue["current_release"] = current
                self.write_catalogue()
                with self.subTest(current=current), self.assertRaisesRegex(ValueError, "current_release"):
                    mirror.run(self.root)
            self.catalogue = multiple
            self.catalogue["current_release"] = "2.0.0"
            self.catalogue["releases"]["2.0.0"]["mirror"]["repo"] = "computational-metabolomics/other"
            self.write_catalogue()
            with self.assertRaisesRegex(ValueError, "one mirror repository"):
                mirror.run(self.root)
            self.catalogue = original
            self.write_catalogue()
            self.write_catalogue("duplicate")
            with self.assertRaisesRegex(ValueError, "Duplicate mirror target"):
                mirror.run(self.root)
            self.catalogue["releases"]["1.0.0"]["mirror"]["revision"] = "models-1.0.0-r2"
            self.write_catalogue("duplicate")
            with self.assertRaisesRegex(ValueError, "belongs to another catalogue"):
                mirror.run(self.root)
            fetch.assert_not_called()
        self.sdk.HfApi.assert_not_called()
        self.assert_no_mutations()

    def test_dry_run_verifies_original_bytes_without_auth_or_writes(self):
        with (
            patch.dict(os.environ, {"HF_TOKEN": "unused-test-token"}),
            patch.object(mirror, "urlopen", side_effect=self.response) as fetch,
        ):
            mirror.main(["--root", str(self.root)])
        self.sdk.HfApi.assert_called_once_with(endpoint=mirror.HUB, token=False)
        self.assertEqual(fetch.call_count, 2)
        self.assert_no_mutations()
        self.assertIn(self.revision, self.output.getvalue())
        self.assertIn("Original authors", self.output.getvalue())

    def test_empty_discovery_requires_no_sdk_token_or_network(self):
        empty = self.root / "empty"
        empty.mkdir()
        for mode in ([], ["--dry-run"], ["--verify-only"], ["--publish"]):
            with (
                self.subTest(mode=mode),
                patch.dict(os.environ, {"HF_TOKEN": ""}),
                patch.dict("sys.modules", {"huggingface_hub": None, "huggingface_hub.errors": None}),
                patch.object(mirror, "urlopen") as fetch,
            ):
                mirror.main(["--root", str(empty), *mode])
                fetch.assert_not_called()
        self.sdk.HfApi.assert_not_called()
        self.assert_no_mutations()
        self.assertEqual(self.output.getvalue().count("No opted-in model releases found"), 4)

    def test_workflow_publication_requires_explicit_opt_in(self):
        workflow = (SCRIPT.parents[1] / "workflows/hf-mirror.yml").read_text()
        publication = workflow.split("- name: Publish verified releases and verify repeat is unchanged\n", 1)[1]
        condition = publication.split("if: >-\n", 1)[1].split("\n        env:", 1)[0]
        self.assertEqual(condition.strip(), "\n          ".join([
            "vars.ENABLE_HF_PUBLISHING == 'true' &&",
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
        ]))

    def test_verify_only_checks_tagged_assets_without_auth_or_writes(self):
        commit = "b" * 40
        self.api.list_repo_refs.return_value.tags = [SimpleNamespace(name=self.revision, target_commit=commit)]
        with (
            patch.dict(os.environ, {"HF_TOKEN": "unused-test-token"}),
            patch.object(mirror, "urlopen", side_effect=self.response) as fetch,
        ):
            mirror.main(["--root", str(self.root), "--verify-only"])
        self.sdk.HfApi.assert_called_once_with(endpoint=mirror.HUB, token=False)
        self.assertEqual(
            fetch.call_args_list,
            [
                call(f"{mirror.HUB}/{self.repo}/resolve/{commit}/1.0.0/{role}.ckpt", timeout=60)
                for role in self.payloads
            ],
        )
        self.assertIn("Published assets verified", self.output.getvalue())
        self.assertNotIn("README.md", self.output.getvalue())
        self.assert_no_mutations()

    def test_verify_only_requires_a_published_tag_without_upstream_fallback(self):
        for missing_repo in (True, False):
            self.api.repo_info.side_effect = MissingRepository() if missing_repo else None
            with (
                self.subTest(missing_repo=missing_repo),
                patch.object(mirror, "urlopen") as fetch,
                self.assertRaisesRegex(ValueError, "Missing published release tag"),
            ):
                mirror.run(self.root, verify_only=True)
            fetch.assert_not_called()
            self.assert_no_mutations()

    def test_verify_only_rejects_missing_or_corrupt_files(self):
        commit = "b" * 40
        self.api.list_repo_refs.return_value.tags = [SimpleNamespace(name=self.revision, target_commit=commit)]
        url = f"{mirror.HUB}/{self.repo}/resolve/{commit}/1.0.0/model.ckpt"
        for missing in (True, False):
            with (
                self.subTest(missing=missing),
                patch.object(
                    mirror,
                    "urlopen",
                    return_value=io.BytesIO(b"wrong bytes"),
                    side_effect=HTTPError(url, 404, "Missing file", {}, None) if missing else None,
                ) as fetch,
                self.assertRaisesRegex(
                    HTTPError if missing else ValueError, "Missing file" if missing else "sha256 mismatch"
                ),
            ):
                mirror.run(self.root, verify_only=True)
            fetch.assert_called_once_with(url, timeout=60)
            self.assert_no_mutations()

    def test_source_checks_both_hashes_and_removes_bad_download(self):
        payload = b"original model"
        valid = {name: hashlib.new(name, payload).hexdigest() for name in ("md5", "sha256")}
        destination = self.root / "download"
        for algorithm in valid:
            expected = dict(valid, **{algorithm: "0" * len(valid[algorithm])})
            with (
                self.subTest(algorithm=algorithm),
                patch.object(mirror, "urlopen", return_value=io.BytesIO(payload)) as fetch,
                patch.object(mirror.time, "sleep") as sleep,
                self.assertRaisesRegex(ValueError, f"{algorithm} mismatch"),
            ):
                mirror.fetch("https://example.org/model", destination, expected)
            fetch.assert_called_once()
            sleep.assert_not_called()
            self.assertFalse(destination.exists())

    def test_dummy_fixtures_are_isolated_and_local_cli_verifies_both_releases(self):
        root = SCRIPT.parents[2]
        fixtures = root / "tests" / "fixtures" / "hf-mirror-smoke"
        releases = mirror.discover(fixtures)
        self.assertEqual([version for _, version, _ in releases], ["0.0.0", "0.0.1"])
        for entry, revision in zip(releases, ("example-v1", "example-v2")):
            self.assertIn(b"Synthetic CI fixtures", mirror.card(*entry))
            self.assertEqual(
                entry[2]["mirror"], {"repo": "computational-metabolomics/example-fixture", "revision": revision}
            )
            self.assertNotIn(entry[2]["mirror"], [item["mirror"] for _, _, item in mirror.discover(root)])
        with patch.object(mirror, "urlopen", wraps=mirror.urlopen) as fetch:
            mirror.main(["--root", str(fixtures), "--local-dir", str(fixtures / "files"), "--dry-run"])
        self.assertEqual(fetch.call_count, 4)
        self.assertTrue(all(call.args[0].startswith("file://") for call in fetch.call_args_list))
        self.sdk.HfApi.assert_called_once_with(endpoint=mirror.HUB, token=False)
        self.assert_no_mutations()

    def test_cards_preserve_catalogue_metadata_and_optional_fields(self):
        detailed = copy.deepcopy(self.catalogue)
        detailed.update(
            galaxy_data_manager="example_data_manager",
            software_url="https://example.org/software",
            software_license="MIT",
            software_license_url="https://example.org/software/LICENSE",
            license_note="Example model terms differ from the software terms.",
        )
        detailed["releases"]["1.0.0"]["software_compatibility"] = "Example software 1.x"
        unknown = copy.deepcopy(detailed)
        unknown.update(license="other-open", hub_license="unknown")
        mixed = copy.deepcopy(detailed)
        mixed["releases"]["1.0.0"]["assets"]["fast"].update(
            license="CC0-1.0", license_url="https://example.org/filter/LICENSE"
        )
        default_license = "[cc-by-4.0](https://example.org/license)"
        mixed_license = (
            default_license + " (default)<br>"
            "[fast.ckpt](https://huggingface.co/computational-metabolomics/example-model/"
            "blob/models-1.0.0-r1/1.0.0/fast.ckpt): [CC0-1.0](https://example.org/filter/LICENSE)"
        )
        cases = [
            ("minimal", self.catalogue, "cc-by-4.0", default_license),
            ("detailed", detailed, "cc-by-4.0", default_license),
            ("unknown", unknown, "unknown", "[other-open](https://example.org/license) (designation)"),
            ("mixed", mixed, None, mixed_license),
        ]
        for name, catalogue, hub_license, model_license in cases:
            version = "1.0.0"
            release = catalogue["releases"][version]
            with self.subTest(name=name):
                content = mirror.card(catalogue, version, release).decode()
                frontmatter = dict(line.split(": ", 1) for line in content.split("---", 2)[1].strip().splitlines())
                if hub_license:
                    self.assertEqual(json.loads(frontmatter["license"]), hub_license)
                else:
                    self.assertNotIn("license", frontmatter)
                self.assertEqual(json.loads(frontmatter["tags"]), catalogue["tags"])
                for text in (f"# {catalogue['name']}\n", catalogue["description"], catalogue["attribution"]):
                    self.assertIn(text, content)
                self.assertIn(
                    f"Current release: [{version}]({mirror.HUB}/{release['mirror']['repo']}/tree/"
                    f"{release['mirror']['revision']}).",
                    content,
                )
                software_link = (
                    f"[GitHub and documentation]({catalogue['software_url']})" if "software_url" in catalogue else "—"
                )
                sources = content.split("## Sources and documentation\n\n", 1)[1].split("\n\n## Licensing", 1)[0]
                self.assertEqual(
                    sources.splitlines(),
                    [
                        "| Version | GitHub and documentation | Original source |",
                        "| --- | --- | --- |",
                        f"| {version} | {software_link} | [Original source]({catalogue['source_url']}) |",
                    ],
                )
                base = f"https://huggingface.co/{release['mirror']['repo']}/blob/{release['mirror']['revision']}/{version}"
                license_headers = ["Version", "Model/data license"]
                license_cells = [version, model_license]
                if "software_license" in catalogue:
                    license_headers.append("Software license")
                    license_cells.append(f"[{catalogue['software_license']}]({catalogue['software_license_url']})")
                if "license_note" in catalogue:
                    license_headers.append("Notes")
                    license_cells.append(catalogue["license_note"])
                licensing = content.split("## Licensing\n\n", 1)[1].split("\n\n## Versions and files", 1)[0]
                self.assertEqual(
                    licensing.splitlines(),
                    [
                        "| " + " | ".join(license_headers) + " |",
                        "| " + " | ".join("---" for _ in license_headers) + " |",
                        "| " + " | ".join(license_cells) + " |",
                    ],
                )
                if "galaxy_data_manager" in catalogue:
                    self.assertIn(f"`{catalogue['galaxy_data_manager']}` data manager", content)
                    self.assertIn("(https://github.com/computational-metabolomics/galaxy-tools)", content)
                    self.assertIn("Galaxy's Upload tool", content)
                else:
                    self.assertNotIn("Galaxy tool", content)
                if "license_note" in catalogue:
                    self.assertEqual(content.count(catalogue["license_note"]), 1)
                version_link = (
                    f"[{version}]({mirror.HUB}/{release['mirror']['repo']}/tree/{release['mirror']['revision']})"
                )
                compatibility = release.get("software_compatibility")
                compatibility = f"`{compatibility}`" if compatibility else "Not specified"
                files = content.split("## Versions and files\n\n", 1)[1]
                self.assertIn("| Version | Recorded software compatibility | File | SHA-256 |", files)
                self.assertEqual(
                    len([line for line in files.splitlines() if line.startswith("| [")]), len(release["assets"])
                )
                for asset in release["assets"].values():
                    url = f"{base}/{asset['filename']}"
                    self.assertIn(
                        f"| {version_link} (current) | {compatibility} | "
                        f"[{version}/{asset['filename']}]({url}) | `{asset['sha256'].lower()}` |",
                        files,
                    )
        original = mirror.card(*mirror.discover(self.root)[0])
        self.catalogue.update(doi="10.1002/(SICI)1099<1>3.0.CO;2-5", sources={"Extra": "https://example.org"})
        self.write_catalogue()
        self.assertEqual(mirror.card(*mirror.discover(self.root)[0]), original)
        self.catalogue.update(license="other-open", hub_license="unknown")
        for license_id, hub_license in (("other-open", "unknown"), ("OTHER-OPEN", "unknown"), ("MIT", "mit")):
            self.catalogue["releases"]["1.0.0"].update(
                license=license_id, license_url="https://example.org/release/LICENSE"
            )
            self.write_catalogue()
            with self.subTest(license=license_id):
                content = mirror.card(*mirror.discover(self.root)[0]).decode()
                self.assertIn(f'license: "{hub_license}"', content)
                self.assertIn(f"[{license_id}](https://example.org/release/LICENSE)", content)
        self.catalogue["license_note"] = "First | second\nNext line."
        self.write_catalogue()
        content = mirror.card(*mirror.discover(self.root)[0]).decode()
        self.assertIn("| First \\| second<br>Next line. |", content)

    def test_local_files_reject_missing_directories_and_symlinks_before_downloads(self):
        with patch.object(mirror, "urlopen") as fetch:
            for kind in ("missing", "directory", "symlink", "version-symlink"):
                directory = self.root / kind
                directory.mkdir()
                source = directory / "model.ckpt"
                if kind == "directory":
                    source.mkdir()
                elif kind == "symlink":
                    target = self.root / "outside.ckpt"
                    target.write_bytes(self.payloads["model"])
                    source.symlink_to(target)
                elif kind == "version-symlink":
                    (directory / "1.0.0").symlink_to(self.write_local_files(), target_is_directory=True)
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "non-symlink"):
                    mirror.run(self.root, local_dir=directory)
            # Only read-only tag lookups happen before the check: nothing is downloaded or written.
            fetch.assert_not_called()
        self.assert_no_mutations()

    def test_published_dry_run_uses_only_hub_files_even_with_empty_local_directory(self):
        commit = "b" * 40
        self.api.list_repo_refs.return_value.tags = [SimpleNamespace(name=self.revision, target_commit=commit)]
        empty = self.root / "empty-files"
        empty.mkdir()
        with patch.object(mirror, "urlopen", side_effect=self.response) as fetch:
            mirror.run(self.root, local_dir=empty)
        self.assertEqual(
            fetch.call_args_list,
            [call(f"{mirror.HUB}/{self.repo}/resolve/{commit}/1.0.0/{role}.ckpt", timeout=60)
             for role in self.payloads]
            + [call(f"{mirror.HUB}/{self.repo}/resolve/{self.info.sha}/README.md", timeout=60)],
        )
        self.assertIn("Existing release verified; no changes.", self.output.getvalue())
        self.assert_no_mutations()

    def test_published_release_md5_is_checked_on_the_hub(self):
        self.api.list_repo_refs.return_value.tags = [SimpleNamespace(name=self.revision, target_commit="b" * 40)]
        self.catalogue["releases"]["1.0.0"]["assets"]["model"]["md5"] = "0" * 32
        self.write_catalogue()
        for options in ({}, {"verify_only": True}):
            with (
                self.subTest(options=options),
                patch.object(mirror, "urlopen", side_effect=self.response),
                self.assertRaisesRegex(ValueError, "md5 mismatch"),
            ):
                mirror.run(self.root, **options)
        self.assert_no_mutations()

    def test_local_files_require_both_hashes_without_network_fallback_or_writes(self):
        directory = self.write_local_files()
        original = copy.deepcopy(self.catalogue)
        for algorithm in ("md5", "sha256"):
            self.catalogue = copy.deepcopy(original)
            asset = self.catalogue["releases"]["1.0.0"]["assets"]["model"]
            asset[algorithm] = "0" * len(asset[algorithm])
            self.write_catalogue()
            with (
                self.subTest(algorithm=algorithm),
                patch.dict(os.environ, {"HF_TOKEN": "test-token"}),
                patch.object(mirror, "urlopen", wraps=mirror.urlopen) as fetch,
                self.assertRaisesRegex(ValueError, f"{algorithm} mismatch"),
            ):
                mirror.run(self.root, publish=True, local_dir=directory)
            fetch.assert_called_once_with((directory / "model.ckpt").resolve().as_uri(), timeout=60)
            self.assert_no_mutations()

    def test_download_retries_are_bounded_and_restart_partial_bytes(self):
        payload = b"complete original"
        expected = {name: hashlib.new(name, payload).hexdigest() for name in ("md5", "sha256")}
        destination = self.root / "download"
        url = "https://example.org/model"
        failures = [HTTPError(url, code, "temporary", {}, None) for code in (500, 502, 503, 504)]
        failures += [URLError("temporary"), TimeoutError(), ConnectionError(), IncompleteRead(b"partial")]
        for failure in failures:
            with (
                self.subTest(failure=failure),
                patch.object(mirror, "urlopen", side_effect=[failure, io.BytesIO(payload)]) as fetch,
                patch.object(mirror.time, "sleep") as sleep,
                self.assertLogs(level="WARNING"),
            ):
                mirror.fetch(url, destination, expected)
            self.assertEqual(fetch.call_args_list, [call(url, timeout=60)] * 2)
            sleep.assert_called_once_with(2)
            self.assertEqual(destination.read_bytes(), payload)
        for recover in (True, False):
            partial = MagicMock()
            partial.__enter__.return_value = partial
            partial.read.side_effect = [b"discard these partial bytes", TimeoutError()]
            outcomes = [partial, io.BytesIO(payload)] if recover else [partial, TimeoutError(), TimeoutError()]
            with (
                self.subTest(partial_recovery=recover),
                patch.object(mirror, "urlopen", side_effect=outcomes) as fetch,
                patch.object(mirror.time, "sleep") as sleep,
                self.assertLogs(level="WARNING"),
            ):
                if recover:
                    mirror.fetch(url, destination, expected)
                    self.assertEqual(destination.read_bytes(), payload)
                else:
                    with self.assertRaises(TimeoutError):
                        mirror.fetch(url, destination, expected)
                    self.assertFalse(destination.exists())
            self.assertEqual(fetch.call_count, 2 if recover else 3)
            self.assertEqual(sleep.call_args_list, [call(2)] if recover else [call(2), call(4)])
        for code in (403, 404):
            with (
                self.subTest(permanent_status=code),
                patch.object(mirror, "urlopen", side_effect=HTTPError(url, code, "permanent", {}, None)) as fetch,
                patch.object(mirror.time, "sleep") as sleep,
                self.assertRaises(HTTPError),
            ):
                mirror.fetch(url, destination, expected)
            fetch.assert_called_once()
            sleep.assert_not_called()
            self.assertFalse(destination.exists())

    def test_sized_http_reads_retry_truncation_but_not_complete_wrong_bytes(self):
        payload = b"complete original"
        expected = {"sha256": hashlib.sha256(payload).hexdigest()}
        destination = self.root / "download"
        url = "https://example.org/model"

        short = (b"partial", len(payload))
        complete = (payload, len(payload))
        cases = [
            ("recovers", [short, complete], None),
            ("exhausted", [short] * 3, IncompleteRead),
            ("complete wrong bytes", [(b"wrong", 5)], ValueError),
            ("unknown length", [(payload, None)], None),
        ]
        for name, bodies, error in cases:
            with (
                self.subTest(name=name),
                patch.object(mirror, "urlopen", side_effect=[http_response(*body) for body in bodies]) as fetch,
                patch.object(mirror.time, "sleep") as sleep,
            ):
                if error:
                    with self.assertRaises(error):
                        mirror.fetch(url, destination, expected)
                    self.assertFalse(destination.exists())
                else:
                    mirror.fetch(url, destination, expected)
                    self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(fetch.call_args_list, [call(url, timeout=60)] * len(bodies))
            self.assertEqual(sleep.call_args_list, [call(2), call(4)][:len(bodies) - 1])

    def test_publication_verifies_completed_commit_before_tag(self):
        events = []
        directory = self.write_local_files()
        original_urlopen = mirror.urlopen
        self.api.repo_info.side_effect = [MissingRepository(), self.info]

        def commit(**kwargs):
            for operation in kwargs["operations"][:-1]:
                role = Path(operation.path_in_repo).stem
                self.assertEqual(Path(operation.path_or_fileobj).read_bytes(), self.payloads[role])
            events.append("commit")
            return SimpleNamespace(oid="a" * 40)

        self.api.create_commit.side_effect = commit
        self.api.create_tag.side_effect = lambda **kwargs: events.append("tag")

        def response(url, timeout):
            if url.startswith("file://"):
                return original_urlopen(url, timeout=timeout)
            if "/resolve/" in url:
                self.assertIn(f"/resolve/{'a' * 40}/", url)
                events.append("verify")
            return self.response(url, timeout)

        with patch.dict(os.environ, {"HF_TOKEN": "test-token"}), patch.object(mirror, "urlopen", side_effect=response):
            mirror.run(self.root, publish=True, local_dir=directory)
        self.assertEqual(events, ["commit", "verify", "verify", "verify", "tag"])
        operations = self.api.create_commit.call_args.kwargs["operations"]
        self.assertEqual({op.path_in_repo for op in operations}, {"1.0.0/model.ckpt", "1.0.0/fast.ckpt", "README.md"})
        self.api.create_tag.assert_called_once_with(
            repo_id=self.repo, repo_type="model", tag=self.revision, revision="a" * 40, exist_ok=False
        )
        self.api.create_repo.assert_called_once_with(
            repo_id=self.repo, repo_type="model", private=False, exist_ok=False
        )
        self.assertEqual(self.api.create_commit.call_args.kwargs["parent_commit"], self.info.sha)

    def test_existing_release_updates_only_main_card_and_repeat_is_noop(self):
        self.api.list_repo_refs.return_value.tags = [SimpleNamespace(name=self.revision, target_commit="b" * 40)]
        for old_card in (b"Old card", 404, 403):
            self.api.reset_mock()
            self.output.seek(0)
            self.output.truncate()
            self.info.sha = "c" * 40

            def response(url, timeout):
                self.assertNotIn(f"/resolve/{'b' * 40}/README.md", url)
                if url.endswith(f"/resolve/{'c' * 40}/README.md"):
                    if isinstance(old_card, int):
                        raise HTTPError(url, old_card, "unavailable", {}, None)
                    return io.BytesIO(old_card)
                return self.response(url, timeout)

            with (
                self.subTest(old_card=old_card),
                patch.dict(os.environ, {"HF_TOKEN": "test-token"}),
                patch.object(mirror, "urlopen", side_effect=response) as fetch,
            ):
                if old_card == 403:
                    with self.assertRaises(HTTPError):
                        mirror.run(self.root, publish=True)
                    self.assert_no_mutations()
                    continue
                mirror.run(self.root)
                self.assert_no_mutations()
                self.assertIn("Would update model card on main", self.output.getvalue())
                fetch.reset_mock()
                mirror.run(self.root, publish=True)
                self.api.create_commit.assert_called_once()
                commit = self.api.create_commit.call_args.kwargs
                self.assertEqual(commit["revision"], "main")
                self.assertEqual(commit["parent_commit"], "c" * 40)
                self.assertEqual([op.path_in_repo for op in commit["operations"]], ["README.md"])
                self.assertEqual(commit["operations"][0].path_or_fileobj, mirror.card(*mirror.discover(self.root)[0]))
                self.assertEqual(
                    fetch.call_args_list[-1].args[0], f"{mirror.HUB}/{self.repo}/resolve/{'a' * 40}/README.md"
                )
                self.assertTrue(all("/resolve/" in call.args[0] for call in fetch.call_args_list))
                self.api.create_repo.assert_not_called()
                self.api.create_tag.assert_not_called()
                self.info.sha = "a" * 40
                self.api.reset_mock()
                mirror.run(self.root, publish=True)
                self.assert_no_mutations()
                self.assertIn("Existing release verified; no changes.", self.output.getvalue())

    def test_multiple_versions_publish_additively_and_repeat_without_changing_tags(self):
        directory = self.root / "versioned-files"
        template = self.catalogue["releases"].pop("1.0.0")
        self.catalogue["current_release"] = "2.0.0"
        self.catalogue["hub_license"] = "unknown"
        self.catalogue["license_note"] = "Inherited caveat for original model files."
        expected_files = {}

        def add_release(version):
            release = copy.deepcopy(template)
            release["mirror"]["revision"] = f"models-{version}-r1"
            release.update(
                source_url=f"https://example.org/record/{version}",
                attribution=f"Original authors of {version}.",
                software_url=f"https://example.org/software/{version}",
                software_license="Apache-2.0",
                software_license_url=f"https://example.org/software/{version}/LICENSE",
            )
            (directory / version).mkdir(parents=True)
            for asset in release["assets"].values():
                payload = f"{version}: {asset['filename']}".encode()
                (directory / version / asset["filename"]).write_bytes(payload)
                expected_files[f"{version}/{asset['filename']}"] = payload
                for algorithm in ("md5", "sha256"):
                    asset[algorithm] = hashlib.new(algorithm, payload).hexdigest()
            self.catalogue["releases"][version] = release

        add_release("1.0.0")
        add_release("2.0.0")

        def version_cell(version, current=False):
            label = f"[{version}]({mirror.HUB}/{self.repo}/tree/models-{version}-r1)"
            return label + (" (current)" if current else "")

        self.catalogue["releases"]["1.0.0"]["assets"]["fast"].update(
            license="CC0-1.0", license_url="https://example.org/filter/LICENSE"
        )
        current = self.catalogue["releases"]["2.0.0"]
        current.update(
            software_compatibility="Example software 2.x",
            license="MIT",
            license_url="https://example.org/record/2.0.0/LICENSE",
            license_note=None,
            software_license="MIT",
        )
        snapshot = mirror.card(self.catalogue, "2.0.0", current, overview=False)
        self.assertIn(b'license: "mit"', snapshot)
        self.assertIn("# Example model — 2.0.0".encode(), snapshot)
        self.assertNotIn(b"1.0.0", snapshot)
        self.assertNotIn(self.catalogue["license_note"].encode(), snapshot)
        self.assertIn(f"| {version_cell('2.0.0')} | `Example software 2.x` |".encode(), snapshot)
        self.assertNotIn(b"(current)", snapshot)
        current["hub_license"] = "unknown"
        self.assertIn(b'license: "unknown"', mirror.card(self.catalogue, "2.0.0", current, overview=False))
        del current["hub_license"]
        original_metadata = copy.deepcopy(self.catalogue)
        mirror.card(self.catalogue, "2.0.0", current)
        self.assertEqual(self.catalogue, original_metadata)
        self.write_catalogue()
        commits, tags = {self.info.sha: {}}, {}
        main = self.info.sha
        original_urlopen = mirror.urlopen

        def repo_info(**kwargs):
            return SimpleNamespace(
                private=False,
                gated=False,
                sha=main,
                siblings=[SimpleNamespace(rfilename=path) for path in commits[main]],
            )

        def commit(**kwargs):
            nonlocal main
            self.assertEqual(kwargs["parent_commit"], main)
            self.assertEqual(kwargs["revision"], "main")
            files = dict(commits[main])
            for operation in kwargs["operations"]:
                content = operation.path_or_fileobj
                files[operation.path_in_repo] = content if isinstance(content, bytes) else Path(content).read_bytes()
            main = f"{len(commits):040x}"
            commits[main] = files
            return SimpleNamespace(oid=main)

        def tag(**kwargs):
            self.assertFalse(kwargs["exist_ok"])
            self.assertNotIn(kwargs["tag"], tags)
            tags[kwargs["tag"]] = kwargs["revision"]

        def response(url, timeout):
            if url.startswith("file://"):
                return original_urlopen(url, timeout=timeout)
            revision, path = url.split("/resolve/", 1)[1].split("/", 1)
            return io.BytesIO(commits[revision][path])

        self.api.repo_info.side_effect = repo_info
        self.api.list_repo_refs.side_effect = lambda **kwargs: SimpleNamespace(
            tags=[SimpleNamespace(name=name, target_commit=revision) for name, revision in tags.items()], branches=[]
        )
        self.api.create_commit.side_effect = commit
        self.api.create_tag.side_effect = tag
        with patch.dict(os.environ, {"HF_TOKEN": "test-token"}), patch.object(mirror, "urlopen", side_effect=response):
            mirror.run(self.root, publish=True, local_dir=directory)
            self.assertEqual(self.api.create_commit.call_count, 2)
            self.assertEqual(self.api.create_tag.call_count, 2)
            self.api.create_repo.assert_not_called()
            readme = commits[main]["README.md"]
            self.assertIn(b"# Example model\n", readme)
            self.assertIn(f"Current release: [2.0.0]({mirror.HUB}/{self.repo}/tree/models-2.0.0-r1).".encode(), readme)
            self.assertNotIn(b"license:", readme.split(b"---", 2)[1])
            self.assertIn(f"| {version_cell('2.0.0', current=True)} | `Example software 2.x` |".encode(), readme)
            self.assertIn(f"| {version_cell('1.0.0')} | Not specified |".encode(), readme)
            self.assertIn(b"| Version | Attribution |", readme)
            licensing = readme.split(b"## Licensing\n\n", 1)[1].split(b"\n\n## Versions and files", 1)[0]
            self.assertIn(b"| Version | Model/data license | Software license | Notes |", licensing)
            self.assertEqual(len(licensing.splitlines()), len(self.catalogue["releases"]) + 2)
            files = readme.split(b"## Versions and files\n\n", 1)[1]
            self.assertEqual(
                len([line for line in files.splitlines() if line.startswith(b"| [")]), len(expected_files)
            )
            for version, item in self.catalogue["releases"].items():
                self.assertIn(
                    f"| {version} | [GitHub and documentation]({item['software_url']}) | "
                    f"[Original source]({item['source_url']}) |".encode(),
                    readme,
                )
                self.assertIn(f"| {version} | {item['attribution']} |".encode(), readme)
                license_id = item.get("license", self.catalogue["license"])
                license_url = item.get("license_url", self.catalogue["license_url"])
                model_license = f"[{license_id}]({license_url})"
                if version == "1.0.0":
                    model_license += " (default) (designation)"
                note = item.get("license_note", self.catalogue["license_note"])
                compatibility = item.get("software_compatibility")
                compatibility = f"`{compatibility}`" if compatibility else "Not specified"
                base = f"{mirror.HUB}/{self.repo}/blob/{item['mirror']['revision']}/{version}"
                for asset in sorted(item["assets"].values(), key=lambda entry: entry["filename"]):
                    self.assertIn(
                        f"| {version_cell(version, current=version == '2.0.0')} | {compatibility} | "
                        f"[{version}/{asset['filename']}]({base}/{asset['filename']}) | `{asset['sha256']}` |".encode(),
                        files,
                    )
                    if "license" in asset:
                        model_license += (
                            f"<br>[{asset['filename']}]({base}/{asset['filename']}): "
                            f"[{asset['license']}]({asset['license_url']})"
                        )
                self.assertIn(
                    f"| {version} | {model_license} | "
                    f"[{item['software_license']}]({item['software_license_url']}) | {note or '—'} |".encode(),
                    licensing,
                )
            self.assertNotIn(b"2.0.0", commits[tags[self.revision]]["README.md"])
            self.assertIn(self.catalogue["license_note"].encode(), commits[tags[self.revision]]["README.md"])
            self.assertEqual(
                {path: content for path, content in commits[main].items() if path != "README.md"}, expected_files
            )
            snapshots = {name: (revision, dict(commits[revision])) for name, revision in tags.items()}

            self.catalogue["releases"] = dict(reversed(list(self.catalogue["releases"].items())))
            self.write_catalogue()
            self.api.reset_mock()
            mirror.run(self.root, publish=True, local_dir=directory)
            self.assert_no_mutations()
            self.assertEqual(commits[main]["README.md"], readme)

            add_release("0.5.0")
            self.catalogue["releases"]["0.5.0"]["license_note"] = "Terms recorded for 0.5.0."
            self.write_catalogue()
            mirror.run(self.root, publish=True, local_dir=directory)
            self.assertEqual(self.api.create_commit.call_count, 2)
            self.api.create_tag.assert_called_once_with(
                repo_id=self.repo,
                repo_type="model",
                tag="models-0.5.0-r1",
                revision=tags["models-0.5.0-r1"],
                exist_ok=False,
            )
            self.assertEqual(
                [operation.path_in_repo for operation in self.api.create_commit.call_args.kwargs["operations"]],
                ["README.md"],
            )
            readme = commits[main]["README.md"]
            self.assertIn(b"Terms recorded for 0.5.0.", readme)
            self.assertLess(
                readme.index(f"| {version_cell('2.0.0', current=True)} |".encode()),
                readme.index(f"| {version_cell('0.5.0')} |".encode()),
            )
            self.assertEqual(
                {path: content for path, content in commits[main].items() if path != "README.md"}, expected_files
            )
            for name, (revision, files) in snapshots.items():
                self.assertEqual(tags[name], revision)
                self.assertEqual(commits[revision], files)
            self.catalogue["releases"] = dict(reversed(list(self.catalogue["releases"].items())))
            self.write_catalogue()
            self.api.reset_mock()
            mirror.run(self.root, publish=True, local_dir=directory)
            self.assert_no_mutations()
            self.assertEqual(commits[main]["README.md"], readme)

            self.catalogue["current_release"] = "1.0.0"
            self.write_catalogue()
            self.api.reset_mock()
            mirror.run(self.root, publish=True, local_dir=directory)
            self.api.create_commit.assert_called_once()
            self.api.create_tag.assert_not_called()
            self.assertEqual(
                [operation.path_in_repo for operation in self.api.create_commit.call_args.kwargs["operations"]],
                ["README.md"],
            )
            readme = commits[main]["README.md"]
            self.assertIn(b"Current release: [1.0.0]", readme)
            self.assertLess(
                readme.index(f"| {version_cell('1.0.0', current=True)} |".encode()),
                readme.index(f"| {version_cell('2.0.0')} |".encode()),
            )
            self.assertIn(b"record/2.0.0/LICENSE", readme)
            self.assertIn(b"Original authors of 1.0.0.", readme)
            self.assertIn(b"Original authors of 2.0.0.", readme)
            self.assertEqual(
                {path: content for path, content in commits[main].items() if path != "README.md"}, expected_files
            )
            for name, (revision, files) in snapshots.items():
                self.assertEqual(tags[name], revision)
                self.assertEqual(commits[revision], files)

    def test_private_and_gated_repositories_fail_before_downloads_or_writes(self):
        with patch.object(mirror, "urlopen") as fetch, patch.dict(os.environ, {"HF_TOKEN": "test-token"}):
            for field, value in (("private", True), ("gated", "auto")):
                setattr(self.info, field, value)
                for options in ({}, {"publish": True}, {"verify_only": True}):
                    with (
                        self.subTest(field=field, options=options),
                        self.assertRaisesRegex(ValueError, "public and ungated"),
                    ):
                        mirror.run(self.root, **options)
                setattr(self.info, field, False)
            fetch.assert_not_called()
        self.assert_no_mutations()

    def test_existing_main_assets_must_match_and_commit_pins_inspected_parent(self):
        self.info.siblings = [SimpleNamespace(rfilename="1.0.0/model.ckpt"), SimpleNamespace(rfilename="README.md")]
        with (
            patch.dict(os.environ, {"HF_TOKEN": "test-token"}),
            patch.object(mirror, "urlopen", side_effect=self.response) as fetch,
        ):
            mirror.run(self.root, publish=True)
        self.assertIn(f"/resolve/{self.info.sha}/1.0.0/model.ckpt", fetch.call_args_list[0].args[0])
        self.api.create_repo.assert_not_called()
        self.assertEqual(self.api.create_commit.call_args.kwargs["parent_commit"], self.info.sha)
        self.api.reset_mock()
        with (
            patch.dict(os.environ, {"HF_TOKEN": "test-token"}),
            patch.object(
                mirror, "urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(b"conflicting original")
            ) as fetch,
        ):
            for publish in (False, True):
                with self.subTest(publish=publish), self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                    mirror.run(self.root, publish=publish)
            self.assertEqual(fetch.call_count, 2)
        self.assert_no_mutations()

    def test_failed_remote_verification_does_not_tag(self):
        def response(url, timeout):
            if f"/resolve/{'a' * 40}/" in url or url.endswith(f"/resolve/{'c' * 40}/README.md"):
                return io.BytesIO(b"wrong bytes")
            return self.response(url, timeout)

        for existing in (False, True):
            self.api.reset_mock()
            self.api.list_repo_refs.return_value.tags = (
                [SimpleNamespace(name=self.revision, target_commit="b" * 40)] if existing else []
            )
            with (
                self.subTest(existing=existing),
                patch.dict(os.environ, {"HF_TOKEN": "test-token"}),
                patch.object(mirror, "urlopen", side_effect=response),
                self.assertRaisesRegex(ValueError, "sha256 mismatch"),
            ):
                mirror.run(self.root, publish=True)
            self.api.create_commit.assert_called_once()
            self.api.create_tag.assert_not_called()

    def test_publish_requires_token_and_rejects_branch_collision(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "HF_TOKEN"):
            mirror.run(self.root, publish=True)
        for options in ({"publish": True}, {"local_dir": self.root}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "cannot be combined"):
                mirror.run(self.root, verify_only=True, **options)
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            mirror.main(["--verify-only", "--publish"])
        self.sdk.HfApi.assert_not_called()
        self.api.list_repo_refs.return_value.branches = [SimpleNamespace(name=self.revision)]
        for verify_only in (False, True):
            with self.subTest(verify_only=verify_only), self.assertRaisesRegex(ValueError, "already names a branch"):
                mirror.run(self.root, verify_only=verify_only)
        self.assert_no_mutations()


if __name__ == "__main__":
    unittest.main()
