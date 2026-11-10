#!/usr/bin/env python3
"""Verify and mirror Galaxy data-manager model assets on Hugging Face.

Read data_managers/*/data_manager/assets.json and preview by default.
Use --publish to upload verified files and cards without moving existing tags.
Use --verify-only to require already-published, checksum-matching release files.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


HUB = "https://huggingface.co"
ROOT = Path(__file__).resolve().parents[2]
RELEASE_METADATA = (
    "source_url",
    "attribution",
    "license",
    "license_url",
    "license_note",
    "hub_license",
    "software_url",
    "software_license",
    "software_license_url",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def component(value):
    """Check that a name is safe for use as a path component."""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)
        and ".." not in value
        and not value.endswith((".", ".lock"))
    )


def https_url(value):
    """Check for an absolute HTTPS URL without embedded credentials."""
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password


def valid_doi(value):
    """Check the format of a bare model/data DOI."""
    return isinstance(value, str) and value.isprintable() and re.fullmatch(r"10\.[0-9]{4,9}/\S+", value)


def release_metadata(catalogue, release):
    """Overlay release-specific metadata on catalogue defaults."""
    metadata = {**catalogue, **{key: release[key] for key in RELEASE_METADATA if key in release}}
    if "license_note" in release and release["license_note"] is None:
        metadata.pop("license_note", None)
    if (
        "license" in release
        and "hub_license" not in release
        and str(release["license"]).lower() != str(catalogue.get("license")).lower()
    ):
        metadata.pop("hub_license", None)
    return metadata


def discover(root):
    """Validate every opted-in release before any downloads or Hub requests."""
    releases, targets, owners = [], set(), {}
    for path in sorted(Path(root).glob("data_managers/*/data_manager/assets.json")):
        catalogue = json.loads(path.read_text())
        require(isinstance(catalogue, dict), f"{path}: expected an object")
        versions = catalogue.get("releases", {})
        require(isinstance(versions, dict), f"{path}: releases must be an object")
        mirrored = {}
        for version, release in versions.items():
            require(isinstance(release, dict), f"{path}: invalid release {version}")
            if "mirror" not in release:
                continue
            for field in ("license", "software_license"):
                require(
                    (field in release) == (f"{field}_url" in release),
                    f"{path}: release {version} must override {field} and {field}_url together",
                )
            metadata = release_metadata(catalogue, release)
            tags = metadata.get("tags")
            require(
                isinstance(tags, list)
                and tags
                and all(isinstance(tag, str) and tag.strip() and tag.isprintable() for tag in tags)
                and len(tags) == len(set(tags)),
                f"{path}: invalid tags; expected a non-empty list of unique, non-empty strings",
            )
            for field in ("name", "description", "attribution", "license_note"):
                if field == "license_note" and field not in metadata:
                    continue
                require(
                    isinstance(metadata.get(field), str) and metadata[field].strip(),
                    f"{path}: missing or invalid {field}",
                )
            if "galaxy_data_manager" in catalogue:
                require(component(catalogue["galaxy_data_manager"]), f"{path}: invalid galaxy_data_manager tool ID")
            if "sources" in catalogue:
                require(
                    isinstance(catalogue["sources"], dict)
                    and all(
                        isinstance(label, str) and label.strip() and https_url(url)
                        for label, url in catalogue["sources"].items()
                    ),
                    f"{path}: invalid sources; expected labels mapped to HTTPS URLs",
                )
            if "software_license" in metadata or "software_license_url" in metadata:
                require(
                    isinstance(metadata.get("software_license"), str)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", metadata["software_license"])
                    and https_url(metadata.get("software_license_url")),
                    f"{path}: missing or invalid software license or software_license_url",
                )
            if "software_url" in metadata:
                require(https_url(metadata["software_url"]), f"{path}: invalid software_url")
            if "hub_license" in metadata:
                require(
                    metadata["hub_license"] == "unknown",
                    f"{path}: hub_license may only downgrade the Hub classification to unknown",
                )
            if "doi" in catalogue:
                require(
                    valid_doi(catalogue["doi"]),
                    f"{path}: invalid doi; expected a bare model/data DOI such as 10.1234/example",
                )
            for field in ("source_url", "license_url"):
                require(https_url(metadata.get(field)), f"{path}: invalid {field}")
            require(
                isinstance(metadata.get("license"), str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", metadata["license"])
                and metadata["license"].lower() not in ("unknown", "unspecified", "unlicensed", "none", "noassertion"),
                f"{path}: model/data license missing or invalid; the software license is not a substitute",
            )
            mirror = release["mirror"]
            require(isinstance(mirror, dict), f"{path}: invalid mirror")
            repo, revision = mirror.get("repo"), mirror.get("revision")
            require(
                isinstance(repo, str)
                and repo.startswith("computational-metabolomics/")
                and component(repo.split("/", 1)[1]),
                f"{path}: invalid mirror repo",
            )
            require(
                component(version)
                and component(revision)
                and revision not in ("main", "master")
                and not re.fullmatch(r"[a-fA-F0-9]{7,40}", revision),
                f"{path}: invalid version or release revision",
            )
            target = (repo, revision)
            require(target not in targets, f"Duplicate mirror target: {repo}@{revision}")
            require(
                owners.setdefault(repo, path) == path,
                f"{path}: mirror repository belongs to another catalogue: {repo}",
            )
            targets.add(target)
            mirrored[version] = repo
            if "software_compatibility" in release:
                compatibility = release["software_compatibility"]
                require(
                    isinstance(compatibility, str)
                    and compatibility.strip()
                    and compatibility.isprintable()
                    and not any(char in compatibility for char in "|`"),
                    f"{path}: invalid software_compatibility",
                )
            assets = release.get("assets")
            require(isinstance(assets, dict) and assets, f"{path}: missing assets")
            filenames = set()
            for asset in assets.values():
                require(isinstance(asset, dict), f"{path}: invalid asset")
                filename = asset.get("filename")
                require(
                    component(filename) and filename not in filenames,
                    f"{path}: unsafe or duplicate filename {filename!r}",
                )
                filenames.add(filename)
                require(https_url(asset.get("url")), f"{path}: invalid asset URL")
                if "license" in asset or "license_url" in asset:
                    require(
                        isinstance(asset.get("license"), str)
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", asset["license"])
                        and asset["license"].lower()
                        not in ("unknown", "unspecified", "unlicensed", "none", "noassertion")
                        and https_url(asset.get("license_url")),
                        f"{path}: missing or invalid asset license/license_url for {filename}",
                    )
                if "doi" in asset:
                    require(valid_doi(asset["doi"]), f"{path}: invalid asset doi for {filename}")
                for algorithm, length in (("md5", 32), ("sha256", 64)):
                    require(
                        isinstance(asset.get(algorithm), str)
                        and re.fullmatch(rf"[a-fA-F0-9]{{{length}}}", asset[algorithm]),
                        f"{path}: missing or invalid {algorithm} for {filename}",
                    )
            releases.append((catalogue, version, release))
        if mirrored:
            require(len(set(mirrored.values())) == 1, f"{path}: a catalogue must use one mirror repository")
            current = catalogue.get("current_release", next(iter(mirrored)) if len(mirrored) == 1 else None)
            require(
                isinstance(current, str) and current in mirrored,
                f"{path}: current_release must name a mirrored release (required for multiple versions)",
            )
    # Publish historical releases first so the selected current card is always last.
    return sorted(releases, key=lambda entry: entry[1] == entry[0].get("current_release", entry[1]))


def markdown_table(headers, rows):
    """Render a Markdown table with escaped pipes and HTML line breaks."""

    def row(cells):
        return (
            "| "
            + " | ".join(
                str(cell).replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
                for cell in cells
            )
            + " |"
        )

    return [row(headers), row(["---"] * len(headers)), *(row(cells) for cells in rows)]


def card(catalogue, version, release, overview=True):
    """Render an overview or release-specific model card as UTF-8 bytes."""
    current = catalogue.get("current_release", version)
    selected = catalogue["releases"].items() if overview else [(version, release)]
    entries = [
        (key, item, release_metadata(catalogue, item))
        for key, item in sorted(selected, key=lambda entry: (entry[0] != current, entry[0]))
        if "mirror" in item
    ]
    licenses = {
        asset.get("license", metadata.get("hub_license", metadata["license"])).lower()
        for _, item, metadata in entries
        for asset in item["assets"].values()
    }
    lines = ["---"]
    if len(licenses) == 1:
        lines.append(f"license: {json.dumps(next(iter(licenses)))}")
    lines.extend(
        [
            f"tags: {json.dumps(catalogue['tags'])}",
            "---",
            "",
            f"# {catalogue['name']}" + ("" if overview else f" — {version}"),
            "",
            catalogue["description"],
        ]
    )
    if overview:
        mirror = catalogue["releases"][current]["mirror"]
        lines.extend(["", f"Current release: [{current}]({HUB}/{mirror['repo']}/tree/{mirror['revision']})."])
    attributions = {metadata["attribution"] for _, _, metadata in entries}
    if len(attributions) == 1:
        lines.extend(["", f"Attribution: {next(iter(attributions))}"])
    else:
        lines.extend(["", "Attribution:", ""])
        lines.extend(
            markdown_table(
                ["Version", "Attribution"], [[key, metadata["attribution"]] for key, _, metadata in entries]
            )
        )
    if "galaxy_data_manager" in catalogue:
        lines.extend(
            [
                "",
                f"These files are primarily provided for the [{catalogue['name']} Galaxy tool]"
                "(https://github.com/computational-metabolomics/galaxy-tools) and its "
                f"`{catalogue['galaxy_data_manager']}` data manager. They can also be imported into a history "
                "using [Galaxy's Upload tool](https://galaxyproject.org/news/2026-01-07-hf-integration/) "
                "where Hugging Face integration is available.",
            ]
        )
    lines.extend(["", "## Sources and documentation", ""])
    rows = []
    for key, _, metadata in entries:
        github = f"[GitHub and documentation]({metadata['software_url']})" if metadata.get("software_url") else "—"
        rows.append([key, github, f"[Original source]({metadata['source_url']})"])
    lines.extend(markdown_table(["Version", "GitHub and documentation", "Original source"], rows))
    lines.extend(["", "## Licensing", ""])
    software_licenses = any(metadata.get("software_license") for _, _, metadata in entries)
    notes = any(metadata.get("license_note") for _, _, metadata in entries)
    headers = (
        ["Version", "Model/data license"]
        + (["Software license"] if software_licenses else [])
        + (["Notes"] if notes else [])
    )
    rows = []
    for key, item, metadata in entries:
        license_text = f"[{metadata['license']}]({metadata['license_url']})"
        if any("license" in asset for asset in item["assets"].values()):
            license_text += " (default)"
        if metadata.get("hub_license") == "unknown":
            license_text += " (designation)"
        file_base = f"{HUB}/{item['mirror']['repo']}/blob/{item['mirror']['revision']}/{key}"
        for asset in sorted(item["assets"].values(), key=lambda entry: entry["filename"]):
            if "license" in asset:
                license_text += (
                    f"<br>[{asset['filename']}]({file_base}/{asset['filename']}): "
                    f"[{asset['license']}]({asset['license_url']})"
                )
        cells = [key, license_text]
        if software_licenses:
            cells.append(
                f"[{metadata['software_license']}]({metadata['software_license_url']})"
                if metadata.get("software_license")
                else "—"
            )
        if notes:
            cells.append(metadata.get("license_note", "—"))
        rows.append(cells)
    lines.extend(markdown_table(headers, rows))
    lines.extend(["", "## Versions and files", "", "Files are mirrored unchanged from upstream.", ""])
    rows = []
    for key, item, _ in entries:
        repo, revision = item["mirror"]["repo"], item["mirror"]["revision"]
        label = f"[{key}]({HUB}/{repo}/tree/{revision})" + (" (current)" if overview and key == current else "")
        compatibility = (
            f"`{item['software_compatibility']}`" if item.get("software_compatibility") else "Not specified"
        )
        file_base = f"{HUB}/{repo}/blob/{revision}/{key}"
        for asset in sorted(item["assets"].values(), key=lambda entry: entry["filename"]):
            rows.append(
                [
                    label,
                    compatibility,
                    f"[{key}/{asset['filename']}]({file_base}/{asset['filename']})",
                    f"`{asset['sha256'].lower()}`",
                ]
            )
    lines.extend(markdown_table(["Version", "Recorded software compatibility", "File", "SHA-256"], rows))
    return ("\n".join(lines) + "\n").encode()


def fetch(url, destination, expected):
    """Fetch bytes and check supplied hashes, retrying transient transport failures."""
    for attempt in range(3):
        hashes = {algorithm: hashlib.new(algorithm) for algorithm in expected}
        try:
            with urlopen(url, timeout=60) as response, destination.open("wb") as output:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(block)
                    for digest in hashes.values():
                        digest.update(block)
                # Sized HTTP reads can return EOF with declared bytes still missing.
                remaining = getattr(response, "length", None)
                if isinstance(remaining, int) and remaining > 0:
                    raise IncompleteRead(b"", remaining)
            for algorithm, digest in hashes.items():
                require(digest.hexdigest() == expected[algorithm].lower(), f"{algorithm} mismatch: {url}")
            return
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
            destination.unlink(missing_ok=True)
            if attempt == 2 or (isinstance(exc, HTTPError) and exc.code not in (500, 502, 503, 504)):
                raise
            logging.warning("Download attempt %s/3 failed (%s); retrying", attempt + 1, exc)
            time.sleep(2 ** (attempt + 1))
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def verify_remote(repo, revision, expected, directory):
    """Download public release files and verify their pinned hashes."""
    for index, (path, digests) in enumerate(expected.items()):
        fetch(f"{HUB}/{repo}/resolve/{revision}/{path}", directory / f"remote-{index}", digests)


def run(root, publish=False, local_dir=None, verify_only=False):
    """Verify and preview or publish releases without moving existing tags."""
    require(
        not verify_only or (not publish and local_dir is None),
        "--verify-only cannot be combined with --publish or --local-dir",
    )
    releases = discover(root)
    if not releases:
        print("No opted-in model releases found; nothing to verify or publish.")
        return
    token = os.environ.get("HF_TOKEN", "").strip() if publish else False
    require(not publish or token, "--publish requires an explicit HF_TOKEN")
    from huggingface_hub import CommitOperationAdd, HfApi
    from huggingface_hub.errors import RepositoryNotFoundError

    api = HfApi(endpoint=HUB, token=token)
    local_files = {}
    if local_dir is not None:
        local_dir = Path(local_dir).resolve()
        # Published releases are verified on the Hub, so only new releases are read from --local-dir.
        tags = {}
        for repo in {release["mirror"]["repo"] for _, _, release in releases}:
            try:
                tags[repo] = {tag.name for tag in api.list_repo_refs(repo_id=repo, repo_type="model").tags}
            except RepositoryNotFoundError:
                tags[repo] = set()
        for _, version, release in releases:
            if release["mirror"]["revision"] in tags[release["mirror"]["repo"]]:
                continue
            version_dir = local_dir / version
            require(not version_dir.is_symlink(), f"Expected a non-symlink directory: {version_dir}")
            for asset in release["assets"].values():
                source = (version_dir if version_dir.exists() else local_dir) / asset["filename"]
                require(
                    source.is_file() and not source.is_symlink(),
                    f"Expected a regular, non-symlink file for release {version}: {source}",
                )
                local_files[(release["mirror"]["repo"], version, asset["filename"])] = source
    for catalogue, version, release in releases:
        repo, revision = release["mirror"]["repo"], release["mirror"]["revision"]
        is_current = version == catalogue.get("current_release", version)
        content = card(catalogue, version, release, overview=is_current)
        asset_hashes = {
            f"{version}/{asset['filename']}": {algorithm: asset[algorithm] for algorithm in ("sha256", "md5")}
            for asset in release["assets"].values()
        }
        card_hash = {"README.md": {"sha256": hashlib.sha256(content).hexdigest()}}
        expected = {**asset_hashes, **card_hash}
        mode = "Verify" if verify_only else "Publish" if publish else "Dry run"
        print(f"{mode}: {repo}@{revision}", flush=True)
        for path in asset_hashes if verify_only else expected:
            print(f"  {path}")
        if not verify_only:
            print(content.decode())
        try:
            info = api.repo_info(repo_id=repo, repo_type="model", revision="main")
        except RepositoryNotFoundError:
            info = None
        existing = None
        if info is not None:
            require(not info.private and not info.gated, f"Mirror repository must be public and ungated: {repo}")
            refs = api.list_repo_refs(repo_id=repo, repo_type="model")
            require(
                all(branch.name != revision for branch in refs.branches),
                f"Release revision already names a branch: {repo}@{revision}",
            )
            existing = next((tag.target_commit for tag in refs.tags if tag.name == revision), None)
        require(not verify_only or existing, f"Missing published release tag: {repo}@{revision}")
        with tempfile.TemporaryDirectory(prefix="hf-mirror-") as temporary:
            directory = Path(temporary)
            if existing:
                verify_remote(repo, existing, asset_hashes, directory)
                if verify_only:
                    print(f"  Published assets verified. Commit: {existing}.", flush=True)
                    continue
                readme = directory / "main-card"
                current = None
                if is_current:
                    try:
                        fetch(f"{HUB}/{repo}/resolve/{info.sha}/README.md", readme, {})
                        current = readme.read_bytes()
                    except HTTPError as exc:
                        if exc.code != 404:
                            raise
                if not is_current or current == content:
                    print(f"  Existing release verified; no changes. Commit: {existing}.")
                elif publish:
                    commit = api.create_commit(
                        repo_id=repo,
                        repo_type="model",
                        revision="main",
                        operations=[CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=content)],
                        commit_message="Update model card",
                        parent_commit=info.sha,
                    )
                    verify_remote(repo, commit.oid, card_hash, directory)
                    print(f"  Updated model card on main; release tag unchanged. Commit: {commit.oid}.")
                else:
                    print("  Would update model card on main; release tag unchanged.")
                # The Hub copy is verified above; upstream downloads are only needed for new releases.
                continue
            elif info is not None:
                paths = {file.rfilename for file in info.siblings or []}
                conflicts = {
                    path: digest for path, digest in expected.items() if path != "README.md" and path in paths
                }
                verify_remote(repo, info.sha, conflicts, directory)
            files = {}
            for asset in release["assets"].values():
                destination = directory / asset["filename"]
                source = (
                    local_files[(repo, version, asset["filename"])].as_uri() if local_dir is not None else asset["url"]
                )
                fetch(source, destination, {algorithm: asset[algorithm] for algorithm in ("md5", "sha256")})
                files[f"{version}/{asset['filename']}"] = destination
            if not publish:
                continue
            operations = [
                CommitOperationAdd(path_in_repo=path, path_or_fileobj=str(file)) for path, file in files.items()
            ]
            operations.append(CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=content))
            if info is None:
                api.create_repo(repo_id=repo, repo_type="model", private=False, exist_ok=False)
                info = api.repo_info(repo_id=repo, repo_type="model", revision="main")
                require(not info.private and not info.gated, f"Mirror repository must be public and ungated: {repo}")
            commit = api.create_commit(
                repo_id=repo,
                repo_type="model",
                revision="main",
                operations=operations,
                commit_message=f"Mirror model release {revision}",
                parent_commit=info.sha,
            )
            verify_remote(repo, commit.oid, expected, directory)
            api.create_tag(repo_id=repo, repo_type="model", tag=revision, revision=commit.oid, exist_ok=False)
            print(f"  Verified and tagged commit {commit.oid}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--local-dir", type=Path, help="Use verified local files instead of upstream downloads")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Verify and preview (default)")
    modes.add_argument("--publish", action="store_true", help="Upload and tag verified releases")
    modes.add_argument("--verify-only", action="store_true", help="Require published releases and verify their files")
    args = parser.parse_args(argv)
    run(args.root, publish=args.publish, local_dir=args.local_dir, verify_only=args.verify_only)


if __name__ == "__main__":
    main()
