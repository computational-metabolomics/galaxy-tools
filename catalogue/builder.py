"""Coordinate discovery, assembly, rendering and final link validation."""

from . import DOCS, metadata, references, rendering, REPO_URL, SITE, tools
from .documentation import validate_site_links
from .sections import build_section, resolve_main_dependency


def build_catalogue(site_dir=SITE, docs_dir=DOCS, offline=False, repo_url=REPO_URL, source_ref="main"):
    repo_url = repo_url.rstrip("/")
    if not metadata._is_http_url(repo_url):
        raise ValueError("repo_url must be an absolute http(s) URL")
    if not source_ref.strip():
        raise ValueError("source_ref must not be empty")
    badges = metadata.load_badges(repo_url, source_ref)
    roots = (docs_dir, docs_dir.parent / "data_managers")
    entries = [
        (folder, metadata.load_metadata(folder), list(tools.discover_tools(folder)))
        for root in roots if root.is_dir()
        for folder in sorted(root.iterdir()) if folder.is_dir()
    ]
    for folder, entry_metadata, tool_files in entries:
        if tool_files and not (folder / ".shed.yml").is_file():
            raise ValueError(f"{folder / '.shed.yml'}: file is required for implemented entries")
        resolve_main_dependency(folder, entry_metadata, tool_files)
    table_references = references.reference_tables(entries)
    sections = []
    for folder, entry_metadata, tool_files in entries:
        section = build_section(
            folder, entry_metadata, tool_files, table_references,
            offline=offline, repo_url=repo_url, source_ref=source_ref,
        )
        sections.append(section)

    slugs = [section["slug"] for section in sections]
    duplicates = sorted({value for value in slugs if slugs.count(value) > 1})
    if duplicates:
        raise ValueError(f"Duplicate catalogue slugs: {', '.join(duplicates)}")

    references.link_manager_software(sections)
    rendering.clean(site_dir)
    for section, (folder, entry_metadata, tool_files) in zip(sections, entries):
        rendering.render_section(
            section, folder, entry_metadata, tool_files, badges, site_dir,
            offline=offline, repo_url=repo_url, source_ref=source_ref,
        )
    sections.sort(key=lambda section: section["title"].casefold())
    rendering.render_index_page(sections, badges, site_dir, offline=offline, repo_url=repo_url)
    validate_site_links(site_dir)
    return sections
