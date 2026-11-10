"""Assemble catalogue sections and wrapper data without rendering pages."""

import json
import re
from urllib.parse import quote, urlsplit

from . import dependencies, ROOT, tools, toolshed

CHECKSUMS = (("md5", 32), ("sha256", 64))


def slug(name):
    value = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return value or "tool"


def _main_requirement_name(data, configured_name=None):
    names = {requirement["name"] for requirement in data["requirements"]}
    if configured_name in names:
        return configured_name
    if len(names) == 1:
        return next(iter(names))
    return data["id"] if data["id"] in names else None


def resolve_main_dependency(section_folder, metadata, tool_files):
    """Resolve and validate the suite's main package before any lookups."""
    main_name = metadata.get("main_dependency")
    names = {
        requirement["name"]
        for _, data in tool_files
        for requirement in data["requirements"]
    }
    if tool_files and main_name and main_name not in names:
        raise ValueError(
            f"{section_folder}: main_dependency {main_name!r} is not a package requirement of any wrapper"
        )
    if not main_name:
        selected = {
            _main_requirement_name(data)
            for _, data in tool_files
            if data["requirements"]
        }
        if len(selected) == 1:
            main_name = next(iter(selected))
    if not main_name and (metadata.get("conda_channel") or metadata.get("upstream_sources")):
        raise ValueError(
            f"{section_folder}: Conda channel and Upstream source hints require an unambiguous "
            "main dependency; add a Main dependency hint to README.md"
        )
    return main_name


def _package_summary(requirement):
    return {
        "name": requirement["name"],
        "version": requirement["version"],
        "latest": requirement.get("latest_version"),
        "out_of_date": requirement.get("out_of_date"),
        "channel": requirement.get("channel"),
        "channel_url": requirement.get("channel_url"),
    }


def build_wrapper(
    xml_path,
    data,
    main_name=None,
    main_channel=None,
    offline=False,
):
    requirements = [
        {
            **requirement,
            **dependencies._conda_info(
                requirement["name"],
                requirement["version"],
                offline=offline,
                preferred_channel=(
                    main_channel
                    if requirement["name"] == main_name
                    else None
                ),
            ),
            "name": requirement["name"],
        }
        for requirement in data["requirements"]
    ]
    wrapper_main_name = _main_requirement_name(data, main_name)
    main = next(
        (
            requirement
            for requirement in requirements
            if requirement["name"] == wrapper_main_name
        ),
        None,
    )
    version = tools.split_version(data["version"])
    return {
        "id": data["id"],
        "name": data["name"],
        "label": data["name"].rsplit(".", 1)[-1],
        "file": xml_path.name,
        "description": data["description"],
        "help": data["help"],
        "help_format": data["help_format"],
        "citations": data["citations"],
        "is_data_manager": data["is_data_manager"],
        "version_tool": version["tool"],
        "version_wrapper": version["wrapper"],
        "requirements": requirements,
        "has_requirements": bool(requirements),
        "main_dependency": _package_summary(main) if main else None,
        "out_of_date": dependencies.dependency_status(
            requirement.get("out_of_date") for requirement in requirements
        ),
    }


def consolidate_requirements(wrappers):
    consolidated = {}
    for wrapper in wrappers:
        for requirement in wrapper["requirements"]:
            key = (requirement["name"], requirement["version"])
            if key not in consolidated:
                consolidated[key] = {
                    **requirement,
                    "wrapper_labels": [],
                }
            consolidated[key]["wrapper_labels"].append(wrapper["label"])

    wrapper_count = len(wrappers)
    for requirement in consolidated.values():
        labels = requirement.pop("wrapper_labels")
        requirement["used_by"] = (
            "All wrappers"
            if len(labels) == wrapper_count
            else ", ".join(labels)
        )
    return list(consolidated.values())


def source_location(section_folder, source_ref):
    """Return the repository root, quoted section path and quoted ref for source links."""
    repo_root = ROOT if section_folder.resolve().is_relative_to(ROOT) else section_folder.parent.parent
    return repo_root, quote(section_folder.relative_to(repo_root).as_posix()), quote(source_ref, safe="")


def wrapper_page(section_slug, relative):
    """Return a wrapper page path relative to the site root."""
    return f"{section_slug}/wrappers/{relative.with_suffix('.html').as_posix()}"


def section_name(folder, metadata, tool_files):
    return metadata.get("readme_title") or (
        tool_files[0][1]["name"] if len(tool_files) == 1 else folder.name
    )


def check_release_files(path, catalogue):
    """Require an HTTPS URL and both checksums for every file of every release.

    Licences are not checked: a release that is not published on Hugging Face may have an
    unknown or restrictive licence, and the publisher checks the licences of published releases.
    """
    releases = catalogue.get("releases", {})
    if not isinstance(releases, dict):
        raise ValueError(f"{path}: releases must be an object")
    for release, details in releases.items():
        if not isinstance(details, dict):
            raise ValueError(f"{path}: release {release} must be an object")
        assets = details.get("assets", {})
        if not isinstance(assets, dict):
            raise ValueError(f"{path}: release {release}: assets must be an object")
        for key, asset in assets.items():
            filename = asset.get("filename") if isinstance(asset, dict) else None
            if not isinstance(filename, str) or not filename.strip():
                raise ValueError(f"{path}: release {release}: asset {key} needs a filename")
            url = asset.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
                raise ValueError(f"{path}: release {release}: {filename} needs an https url")
            for algorithm, length in CHECKSUMS:
                value = asset.get(algorithm)
                if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value):
                    raise ValueError(f"{path}: release {release}: missing or invalid {algorithm} for {filename}")


def manager_software_compatibility(folder, wrappers, repo_url, source_ref):
    """Keep declared software compatibility separate from installer requirements."""
    _, source_path, encoded_ref = source_location(folder, source_ref)
    software = []
    for path in sorted(folder.rglob("assets.json")):
        catalogue = json.loads(path.read_text(encoding="utf8"))
        check_release_files(path, catalogue)
        manager_id = catalogue.get("galaxy_data_manager")
        if not manager_id and len(wrappers) == 1:
            manager_id = wrappers[0]["id"]
        relative = quote(path.relative_to(folder).as_posix())
        for release, details in catalogue.get("releases", {}).items():
            compatibility = details.get("software_compatibility")
            if compatibility is not None and not isinstance(compatibility, str):
                raise ValueError(f"{path}: software_compatibility must be a string")
            software.append({
                "manager_id": manager_id,
                "release": release,
                "compatibility": (compatibility or "").strip(),
                "current": release == catalogue.get("current_release"),
                "source_url": f"{repo_url}/blob/{encoded_ref}/{source_path}/{relative}",
            })
    return sorted(software, key=lambda item: not item["current"])


def build_section(
    section_folder, metadata, tool_files, references,
    *, offline=False, repo_url, source_ref,
):
    toolshed_repositories = toolshed.repository_links(section_folder, tool_files, offline=offline)
    main_name = resolve_main_dependency(section_folder, metadata, tool_files)
    wrappers = [
        build_wrapper(
            path,
            data,
            main_name,
            metadata.get("conda_channel"),
            offline=offline,
        )
        for path, data in tool_files
    ]

    display_name = section_name(section_folder, metadata, tool_files)
    section_slug = slug(display_name)
    kind = "data-manager" if section_folder.parent.name == "data_managers" else "tool"
    software_compatibility = (
        manager_software_compatibility(section_folder, wrappers, repo_url, source_ref)
        if kind == "data-manager" else []
    )
    if wrappers:
        requirements = [requirement for wrapper in wrappers for requirement in wrapper["requirements"]]
        has_requirements = bool(requirements)
        out_of_date = dependencies.dependency_status(
            requirement.get("out_of_date") for requirement in requirements
        )

        tool_versions = {wrapper["version_tool"] for wrapper in wrappers}
        version_tool = (
            next(iter(tool_versions)) if len(tool_versions) == 1 else "multiple"
        )
        wrapper_versions = {wrapper["version_wrapper"] for wrapper in wrappers}
        version_wrapper = (
            next(iter(wrapper_versions)) if len(wrapper_versions) == 1 else ""
        )
        declared = [requirement for requirement in requirements if requirement["name"] == main_name]
        main_versions = {requirement["version"] for requirement in declared}
        main_dependency = (
            {
                **_package_summary(declared[0]),
                "version": next(iter(main_versions)) if len(main_versions) == 1 else "multiple",
                "out_of_date": dependencies.dependency_status(
                    requirement.get("out_of_date") for requirement in declared
                ),
            }
            if declared else None
        )
    else:
        has_requirements = False
        out_of_date = None
        version_tool = ""
        version_wrapper = ""
        main_dependency = (
            _package_summary({
                **dependencies._empty_conda_info(main_name, metadata.get("conda_channel")),
                "version": "",
            })
            if main_name
            else None
        )
        main_versions = {""}

    if main_dependency:
        main_dependency = {
            **main_dependency,
            "sources": dependencies.build_main_sources(
                main_dependency,
                dependencies.upstream_sources_for(metadata, main_dependency, main_name),
                offline=offline,
                versions=main_versions,
            ),
        }

    description = metadata.get("description") or (
        wrappers[0]["description"] if len(wrappers) == 1 else ""
    ) or metadata.get("readme_description", "")
    ids = [wrapper["id"] or wrapper["name"] for wrapper in wrappers]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{section_folder}: duplicate wrapper IDs")
    _, source_path, encoded_ref = source_location(section_folder, source_ref)
    for (path, data), wrapper in zip(tool_files, wrappers):
        relative = path.relative_to(section_folder)
        page = wrapper_page(section_slug, relative)
        wrapper.update({
            "file": relative.as_posix(),
            "source_url": f"{repo_url}/blob/{encoded_ref}/{source_path}/{quote(relative.as_posix())}",
            "url": quote(page.removeprefix(f"{section_slug}/")),
            "page_url": quote(page),
            "tables": references.get(path.resolve(), []),
            "software_compatibility": [
                item for item in software_compatibility if item["manager_id"] == wrapper["id"]
            ],
        })
        if wrapper["main_dependency"]:
            wrapper["main_dependency"]["sources"] = dependencies.build_main_sources(
                wrapper["main_dependency"],
                dependencies.upstream_sources_for(metadata, wrapper["main_dependency"], main_name),
                offline=offline,
            )

    search_terms = [display_name, description, main_name or ""]
    search_terms.extend(item["compatibility"] for item in software_compatibility)
    for wrapper in wrappers:
        search_terms.extend([wrapper["name"], wrapper["id"]])
        search_terms.extend(requirement["name"] for requirement in wrapper["requirements"])

    return {
        "title": display_name,
        "description": description,
        "slug": section_slug,
        "source_url": f"{repo_url}/tree/{encoded_ref}/{source_path}/",
        "toolshed_repositories": toolshed_repositories,
        "requirement_out_of_date": out_of_date,
        "has_requirements": has_requirements,
        "version_tool": version_tool,
        "version_wrapper": version_wrapper,
        "main_dependency": main_dependency,
        "software_compatibility": software_compatibility,
        "count": len(wrappers),
        "wrappers": wrappers,
        "kind": kind,
        "search_text": " ".join(search_terms).lower(),
    }
