"""Conda and upstream release lookups, comparison and caching."""

from __future__ import annotations

import gzip
import os
import re
from urllib.parse import urlparse

import requests
from packaging.version import InvalidVersion, Version


def check_conda_package(
    package_name: str,
    version: str,
    channels: tuple[str, ...] = ("conda-forge", "bioconda", "defaults"),
) -> dict:
    for channel in channels:
        url = f"https://api.anaconda.org/package/{channel}/{package_name}"

        response = requests.get(url, timeout=20)

        if response.status_code == 404:
            continue

        response.raise_for_status()

        data = response.json()

        latest = data.get("latest_version")

        result = {
            "name": data.get("name"),
            "home": data.get("home"),
            "license": data.get("license"),
            "summary": data.get("summary"),
            "latest_version": latest,
            "channel": channel,
            "channel_url": f"https://anaconda.org/{channel}/{data.get('name', package_name)}",
            "out_of_date": None,
        }

        if version and latest:
            try:
                result["out_of_date"] = Version(str(version)) < Version(str(latest))
            except InvalidVersion:
                pass  # Incomparable versions are unknown, not an update signal.

        return result
    raise ValueError(f"Package '{package_name}' not found in any channel.")


def _clean_tag(tag: str) -> str | None:
    """Strip project prefixes; leave ambiguous build/architecture suffixes unknown."""
    tag = (tag or "").strip()
    # Match the first version token, never retry at a later architecture/build number.
    # Digits inside project names (ms2query, py3) are not token boundaries.
    match = re.search(
        r"(?:^|[-_\s])(?:v|r|release|version)?(\d.*)$", tag, re.IGNORECASE,
    )
    if match is None:
        match = re.search(r"(?<![\d.])(\d+\.\d.*)$", tag)
    if match:
        candidate = match.group(1)
        try:
            Version(candidate)
            return candidate
        except InvalidVersion:
            pass
    return None


def _best_version(names) -> str | None:
    """Return the highest parsed version, or None if no tag is a version."""
    best = None
    for n in names:
        cleaned = _clean_tag(n)
        if cleaned is None:
            continue
        try:
            v = Version(cleaned)
        except InvalidVersion:
            continue
        if best is None or v > best[0]:
            best = (v, cleaned)
    return best[1] if best else None


def github_latest(owner: str, repo: str) -> str | None:
    """Return the latest GitHub release/tag version, or None; use GITHUB_TOKEN when set."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
                         headers=headers, timeout=20)
        if r.status_code == 200:
            return _clean_tag(r.json().get("tag_name") or r.json().get("name") or "")
        # Try tags when no latest release is available.
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100",
                         headers=headers, timeout=20)
        if r.status_code == 200 and r.json():
            return _best_version([t.get("name", "") for t in r.json()])
    except requests.RequestException:
        pass
    return None


def bitbucket_latest(workspace: str, repo: str) -> str | None:
    """Latest tag of a Bitbucket repo, or None. Tolerant of errors."""
    try:
        r = requests.get(
            f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/refs/tags",
            params={"pagelen": 100}, timeout=20)
        if r.status_code == 200:
            values = r.json().get("values") or []
            return _best_version([v.get("name", "") for v in values])
    except requests.RequestException:
        pass
    return None


def pypi_latest(project: str) -> str | None:
    """Latest published PyPI version, or None. Tolerant of errors."""
    try:
        r = requests.get(f"https://pypi.org/pypi/{project}/json", timeout=20)
        if r.status_code == 200:
            return (r.json().get("info") or {}).get("version") or None
    except requests.RequestException:
        pass
    return None


_BIOCONDUCTOR_VERSIONS = None


def _parse_bioconductor_index(content: bytes) -> dict[str, str]:
    text = gzip.decompress(content).decode("utf8")
    versions = {}
    for record in text.split("\n\n"):
        package = re.search(r"^Package:\s*(\S+)", record, re.MULTILINE)
        version = re.search(r"^Version:\s*(\S+)", record, re.MULTILINE)
        if package and version:
            versions[package.group(1)] = version.group(1)
    return versions


def bioconductor_latest(project: str) -> str | None:
    """Latest package version in the current Bioconductor release."""
    global _BIOCONDUCTOR_VERSIONS
    if _BIOCONDUCTOR_VERSIONS is None:
        try:
            response = requests.get(
                "https://bioconductor.org/packages/release/bioc/"
                "src/contrib/PACKAGES.gz",
                timeout=20,
            )
            response.raise_for_status()
            _BIOCONDUCTOR_VERSIONS = _parse_bioconductor_index(
                response.content
            )
        except (OSError, UnicodeDecodeError, requests.RequestException):
            _BIOCONDUCTOR_VERSIONS = {}
    return _BIOCONDUCTOR_VERSIONS.get(project)


def resolve_upstream(entry, fetch_versions: bool = True) -> dict | None:
    """Resolve a source URL or mapping with optional repo/project/version overrides.
    Return display metadata with version=None when the version is unknown.
    """
    if isinstance(entry, str):
        entry = {"url": entry}
    url = entry.get("url", "")
    if not url:
        return None

    host = (urlparse(url).netloc or "").lower()
    parts = [p for p in urlparse(url).path.split("/") if p]
    repo = entry.get("repo")
    if repo:
        owner, _, name = repo.partition("/")
    else:
        owner, name = (parts[0], parts[1]) if len(parts) >= 2 else ("", "")

    version = entry.get("version")  # manual override skips the API
    if "github.com" in host:
        provider, label = "github", "GitHub"
        if fetch_versions and version is None and owner and name:
            version = github_latest(owner, name)
    elif "bitbucket.org" in host:
        provider, label = "bitbucket", "Bitbucket"
        if fetch_versions and version is None and owner and name:
            version = bitbucket_latest(owner, name)
    elif "pypi.org" in host:
        provider, label = "pypi", "PyPI"
        project = entry.get("project") or (
            parts[1] if len(parts) >= 2 and parts[0] == "project" else ""
        )
        if fetch_versions and version is None and project:
            version = pypi_latest(project)
    elif "bioconductor.org" in host:
        provider, label = "bioconductor", "Bioconductor"
        project = entry.get("project") or (
            parts[-1].removesuffix(".html") if parts else ""
        )
        if fetch_versions and version is None and project:
            version = bioconductor_latest(project)
    else:
        provider, label = "web", host or "source"

    return {
        "provider": provider,
        "label": entry.get("label", label),
        "url": url,
        "version": version,
        "compare_versions": entry.get("compare_versions", True),
    }


_CONDA_CACHE = {}
_UPSTREAM_CACHE = {}


def _empty_conda_info(name, channel=None):
    return {
        "name": name,
        "home": None,
        "license": None,
        "latest_version": None,
        "channel": channel,
        "channel_url": (
            f"https://anaconda.org/{channel}/{name}" if channel else None
        ),
        "out_of_date": None,
    }


def _conda_info(name, version, offline=False, preferred_channel=None):
    if offline:
        return _empty_conda_info(name, preferred_channel)
    key = (name, version, preferred_channel)
    if key not in _CONDA_CACHE:
        try:
            channels = tuple(
                dict.fromkeys(
                    (
                        preferred_channel,
                        "conda-forge",
                        "bioconda",
                        "defaults",
                    )
                )
            )
            channels = tuple(channel for channel in channels if channel)
            _CONDA_CACHE[key] = check_conda_package(
                name,
                version,
                channels=channels,
            )
        except Exception:
            _CONDA_CACHE[key] = _empty_conda_info(name, preferred_channel)
    return dict(_CONDA_CACHE[key])


def _resolve_upstream_cached(entry, offline=False):
    if offline:
        return resolve_upstream(entry, fetch_versions=False)
    key = entry if isinstance(entry, str) else str(sorted(entry.items()))
    if key not in _UPSTREAM_CACHE:
        try:
            _UPSTREAM_CACHE[key] = resolve_upstream(entry)
        except Exception:
            _UPSTREAM_CACHE[key] = None
    return _UPSTREAM_CACHE[key]


def _newer_than(pinned, candidate):
    if not pinned or not candidate:
        return None
    try:
        pinned_version = Version(str(pinned))
        candidate_version = Version(str(candidate))
    except InvalidVersion:
        return None
    return candidate_version > pinned_version


def _is_prerelease(version):
    try:
        return Version(str(version)).is_prerelease
    except InvalidVersion:
        return False


def dependency_status(states):
    """Combine package comparisons, preserving unknown and empty results."""
    states = list(states)
    if any(state is True for state in states):
        return True
    if not states or any(state is None for state in states):
        return None
    return False


def upstream_sources_for(metadata, main, main_name=None):
    """Attach entry sources only to its resolved main software package."""
    main_name = main_name or metadata.get("main_dependency")
    if not main_name or main["name"] != main_name:
        return None
    return metadata.get("upstream_sources")


def build_main_sources(main_dependency, upstream_entries, offline=False, versions=None):
    if not main_dependency:
        return []

    pins = list(versions) if versions is not None else [main_dependency.get("version")]
    sources = []
    if main_dependency.get("channel"):
        sources.append(
            {
                "label": main_dependency["channel"],
                "url": main_dependency.get("channel_url"),
                "version": main_dependency.get("latest"),
                "newer": main_dependency.get("out_of_date"),
            }
        )

    for entry in upstream_entries or []:
        upstream = _resolve_upstream_cached(entry, offline=offline)
        if upstream:
            sources.append(
                {
                    "label": upstream["label"],
                    "url": upstream["url"],
                    "version": upstream.get("version"),
                    "newer": (
                        None if upstream.get("compare_versions", True) is False
                        else dependency_status(_newer_than(pin, upstream.get("version")) for pin in pins)
                    ),
                }
            )
    return sources
