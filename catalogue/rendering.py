"""Render catalogue pages and copy their static assets."""

import json
import re
import shutil
from copy import deepcopy
from datetime import date
from hashlib import sha256
from urllib.parse import quote, unquote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from lxml import html
from markupsafe import escape, Markup

from . import dependencies, REPO_URL, sections, STATIC, TEMPLATES
from .documentation import render_document
from .metadata import _is_http_url, README_HINT


env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(("html", "xml")),
)
env.tests["prerelease"] = dependencies._is_prerelease
env.tests["http_url"] = _is_http_url
env.filters["dependency_status"] = dependencies.dependency_status
index_template = env.get_template("index.html")
tool_template = env.get_template("tool.html")


def detail_software_rows(requirements, main_dependency, related_software=(), is_data_manager=False):
    """Combine installed packages and version-matched source details for one table."""
    rows = {}
    packages = list(related_software) if is_data_manager else ([main_dependency] if main_dependency else [])
    for requirement in requirements:
        if is_data_manager and requirement["name"].casefold() == "python":
            continue
        key = (requirement["name"], requirement.get("version"))
        if key not in rows:
            rows[key] = deepcopy(requirement)
            rows[key]["role"] = "Installer dependency" if is_data_manager else "Dependency"
        else:
            row = rows[key]
            for field, value in requirement.items():
                if not row.get(field):
                    row[field] = deepcopy(value)
                elif field in {"license", "used_by"} and value and value != row[field]:
                    if field == "used_by" and "All wrappers" in (row[field], value):
                        row[field] = "All wrappers"
                    else:
                        row[field] = "; ".join(dict.fromkeys((row[field], value)))

    ordered_keys = []
    for package in packages:
        if is_data_manager and package["name"].casefold() == "python":
            continue
        if package.get("version") == "multiple" and any(name == package["name"] for name, _ in rows):
            continue  # A suite summary is not an additional pinned requirement.
        key = (package["name"], package.get("version"))
        if key not in rows:
            rows[key] = deepcopy(package)
            rows[key]["role"] = "Related software" if is_data_manager else "Software"
        row = rows[key]
        for field, value in package.items():
            if field == "sources":
                row.setdefault("sources", [])
                row["sources"].extend(deepcopy(source) for source in value if source not in row["sources"])
            elif field not in row:
                row[field] = deepcopy(value)
        if not is_data_manager:
            row["role"] = "Software"
        if key not in ordered_keys:
            ordered_keys.append(key)

    for key, row in rows.items():
        row.setdefault("latest_version", row.get("latest"))
        if not row.get("sources") and row.get("channel_url"):
            row["sources"] = [{
                "label": row.get("channel"), "url": row["channel_url"],
                "version": row.get("latest_version"), "newer": row.get("out_of_date"),
            }]
        row.setdefault("sources", [])
        if key not in ordered_keys:
            ordered_keys.append(key)
    return [rows[key] for key in ordered_keys]


env.globals["detail_software_rows"] = detail_software_rows


def render_readme(path, context=None):
    text = path.read_text(encoding="utf8")
    rendered = render_document(
        text, "markdown", path, context=context, id_prefix="readme-",
        title_anchor=bool(re.match(r"\A\s*#\s+[^\n]+", text)),
    )
    document = html.fragment_fromstring(str(rendered), create_parent="div")
    # The generated software summary already presents these catalogue hints.
    for paragraph in document.findall("p"):
        if README_HINT.match(paragraph.text_content().strip()):
            paragraph.drop_tree()
    return _inner_html(document)


def _inner_html(document):
    return escape(document.text or "") + Markup("".join(
        html.tostring(child, encoding="unicode") for child in document
    ))


def add_document_links(rendered, links):
    """Add missing resource links to a rendered document's sources section."""
    document = html.fragment_fromstring(str(rendered), create_parent="div")
    linked = {url.rstrip("/") for url in document.xpath(".//a/@href")}
    missing = []
    for link in links:
        url = link["url"]
        if not _is_http_url(url) or url.rstrip("/") in linked:
            continue
        missing.append(link)
        linked.add(url.rstrip("/"))
    if not missing:
        return Markup(rendered)

    children = list(document)
    source_index = next((
        index for index, child in enumerate(children)
        if child.tag == "h2" and child.text_content().strip().casefold() == "sources and licence"
    ), None)
    if source_index is None:
        used_ids = set(document.xpath(".//@id | .//a/@name"))
        heading_id = "sources-and-licence"
        suffix = 1
        while heading_id in used_ids:
            heading_id = f"sources-and-licence-{suffix}"
            suffix += 1
        heading = html.Element("h2", id=heading_id)
        heading.text = "Sources and licence"
        document.append(heading)
        insert_at = len(document)
    else:
        insert_at = next((
            index for index in range(source_index + 1, len(children))
            if children[index].tag == "h2"
        ), len(children))

    resource_list = html.Element("ul")
    for link in missing:
        item = html.Element("li")
        anchor = html.Element("a", href=link["url"], target="_blank", rel="noopener noreferrer")
        anchor.text = str(link["name"])
        item.append(anchor)
        resource_list.append(item)
    document.insert(insert_at, resource_list)
    return _inner_html(document)


def _split_readme_overview(rendered):
    """Use a leading Overview as the introduction, retaining document anchors."""
    document = html.fragment_fromstring(str(rendered), create_parent="div")
    children = list(document)
    for index, heading in enumerate(children):
        if heading.tag == "span" and not heading.text_content().strip():
            continue  # The README title is rendered as an anchor.
        if heading.tag != "h2" or heading.text_content().strip().casefold() != "overview":
            break
        if heading.xpath(".//*[@id or @name or @href]"):
            break  # Keep custom heading links and anchors in the original body.
        end = next((i for i in range(index + 1, len(children)) if children[i].tag == "h2"), len(children))
        if not any(child.text_content().strip() or len(child) for child in children[index + 1:end]):
            break
        introduction = html.Element("div")
        # The page title introduces this text; keep the Overview link target.
        heading.tag = "span"
        heading.text = ""
        for child in list(heading):
            heading.remove(child)
        for child in children[:end]:
            introduction.append(child)
        return _inner_html(introduction), _inner_html(document)
    return Markup(""), rendered


def _split_readme_usage(rendered):
    """Move a leading Usage section above generated details without rewriting it."""
    document = html.fragment_fromstring(str(rendered), create_parent="div")
    children = list(document)
    for index, heading in enumerate(children):
        if heading.tag == "span" and not heading.text_content().strip():
            continue
        if heading.tag != "h2" or heading.text_content().strip().casefold() != "usage":
            break
        end = next((i for i in range(index + 1, len(children)) if children[i].tag == "h2"), len(children))
        if not any(child.text_content().strip() or len(child) for child in children[index + 1:end]):
            break
        usage = html.Element("div")
        for child in children[:end]:
            usage.append(child)
        return _inner_html(usage), _inner_html(document)
    return Markup(""), rendered


def _readme_citation_details(rendered):
    """Reuse documented references on wrapper pages, matched by their DOI."""
    document = html.fragment_fromstring(str(rendered), create_parent="div")
    references = {}
    in_references = False
    for child in document:
        if child.tag == "h2":
            in_references = child.text_content().strip().casefold() in {"acknowledgements", "citations", "references"}
        elif in_references and child.tag in {"ul", "ol"}:
            for item in child.findall("li"):
                links = item.xpath(".//a/@href")
                if item.xpath(".//@id | .//@name | .//li") or any(not _is_http_url(link) for link in links):
                    continue  # Relative links and anchors belong to the README page.
                dois = {
                    unquote(urlsplit(link).path.lstrip("/")).casefold()
                    for link in links if urlsplit(link).hostname in {"doi.org", "dx.doi.org"}
                }
                if len(dois) == 1:
                    doi, = dois
                    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
                        references.setdefault(doi, _inner_html(item))
    return references


def _unlinked_assets(asset_links, rendered):
    if not asset_links:
        return []
    document = html.fragment_fromstring(str(rendered), create_parent="div")
    linked = {url.rstrip("/") for url in document.xpath(".//a/@href")}
    return [link for link in asset_links if link["url"].rstrip("/") not in linked]


def clean(site_dir):
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)
    shutil.copytree(STATIC, site_dir / "static")
    (site_dir / ".nojekyll").write_text("", encoding="utf8")


def _shared_context(badges, repo_url, offline):
    return {
        "asset_version": sha256((STATIC / "style.css").read_bytes() + (STATIC / "app.js").read_bytes()).hexdigest()[:12],
        "now": date.today(),
        "offline": offline,
        "repo_url": repo_url,
        "repo_name": urlsplit(repo_url).path.strip("/").replace("/", " / "),
        "badges": badges,
    }


def render_section(
    section, section_folder, metadata, tool_files, badges, site_dir,
    offline=False, repo_url=REPO_URL, source_ref="main",
):
    wrappers = section["wrappers"]
    display_name = section["title"]
    section_slug = section["slug"]
    kind = section["kind"]
    description = section["description"]
    version_tool = section["version_tool"]
    version_wrapper = section["version_wrapper"]
    main_dependency = section["main_dependency"]
    has_requirements = section["has_requirements"]
    out_of_date = section["requirement_out_of_date"]
    repo_root, source_path, encoded_ref = sections.source_location(section_folder, source_ref)
    asset_links = []
    if kind == "data-manager":
        for path in sorted(section_folder.rglob("assets.json")):
            relative = quote(path.relative_to(repo_root).as_posix())
            asset_links.append({"name": "Asset catalogue", "url": f"{repo_url}/blob/{encoded_ref}/{relative}"})
            catalogue = json.loads(path.read_text(encoding="utf8"))
            repositories = {
                release["mirror"]["repo"] for release in catalogue.get("releases", {}).values()
                if release.get("mirror")
            }
            asset_links.extend(
                {"name": f"Model card: {repo}", "url": f"https://huggingface.co/{repo}"}
                for repo in sorted(repositories)
            )

    source_link = section.get("source_url") or f"{repo_url}/tree/{encoded_ref}/{source_path}/"
    destination = site_dir / section_slug
    destination.mkdir(parents=True, exist_ok=True)
    page_links = {(section_folder / "README.md").resolve(): destination / "index.html"}
    for path, _ in tool_files:
        page_links[path.resolve()] = site_dir / sections.wrapper_page(section_slug, path.relative_to(section_folder))
    license_name = "Entry licence" if (section_folder / "LICENSE").is_file() else "MIT"
    license_url = (
        f"{repo_url}/blob/{encoded_ref}/{source_path}/LICENSE" if (section_folder / "LICENSE").is_file()
        else f"{repo_url}/blob/{encoded_ref}/LICENSE"
    )
    resource_links = [
        {"name": f"Wrapper licence: {license_name}", "url": license_url},
        {"name": "Wrapper source on GitHub", "url": source_link},
    ]
    if metadata.get("homepage"):
        resource_links.append({"name": "Project homepage", "url": metadata["homepage"]})
    readme_html = render_readme(section_folder / "README.md", context=(
        section_folder, site_dir, destination / "index.html", page_links, repo_url, repo_root, source_ref,
    ))
    readme_html = add_document_links(readme_html, resource_links)
    common = {
        **_shared_context(badges, repo_url, offline),
        "license": license_name, "license_url": license_url,
        "kind": kind, "asset_links": asset_links,
        "homepage": metadata.get("homepage"),
        "toolshed_repositories": section.get("toolshed_repositories", []),
        "citation_details": _readme_citation_details(readme_html),
    }
    for (path, data), wrapper in zip(tool_files, wrappers):
        output = page_links[path.resolve()]
        output.parent.mkdir(parents=True, exist_ok=True)
        prefix = "../" * len(output.parent.relative_to(site_dir).parts)
        parent_url = "../" * len(output.parent.relative_to(destination).parts) + "index.html"
        context = (section_folder, site_dir, output, page_links, repo_url, repo_root, source_ref)
        help_html = render_document(
            wrapper["help"], wrapper["help_format"], path,
            context=context, heading_offset=2, id_prefix="help-",
        )
        help_html = add_document_links(help_html, resource_links)
        output.write_text(tool_template.render(
            **{**common, "asset_links": _unlinked_assets(asset_links, help_html)},
            title=wrapper["name"], description=wrapper["description"],
            wrapper=wrapper, help_html=help_html, wrappers=[wrapper],
            version_tool=wrapper["version_tool"], version_wrapper=wrapper["version_wrapper"],
            main_dependency=wrapper["main_dependency"], has_requirements=wrapper["has_requirements"],
            software_compatibility=wrapper["software_compatibility"],
            tool_software=wrapper.get("tool_software", []),
            requirement_out_of_date=wrapper["out_of_date"], source_code_link=wrapper["source_url"],
            source_code_text=wrapper["file"], parent_url=parent_url, suite_title=display_name,
            home_url=prefix + "index.html", static_base=prefix + "static",
            site_prefix=prefix,
        ), encoding="utf8")
    suite_requirements = (
        sections.consolidate_requirements(wrappers) if len(wrappers) > 1 else []
    )
    entry_assets = _unlinked_assets(asset_links, readme_html)
    overview_html, readme_html = _split_readme_overview(readme_html)
    usage_html = Markup("")
    if kind == "tool":
        usage_html, readme_html = _split_readme_usage(readme_html)
    (destination / "index.html").write_text(
        tool_template.render(
            **{**common, "asset_links": entry_assets},
            title=display_name,
            description=description,
            overview_html=overview_html,
            usage_html=usage_html,
            readme_html=readme_html,
            version_tool=version_tool,
            version_wrapper=version_wrapper,
            wrappers=wrappers,
            suite_requirements=suite_requirements,
            main_dependency=main_dependency,
            software_compatibility=section["software_compatibility"],
            tool_software=section.get("tool_software", []),
            has_requirements=has_requirements,
            requirement_out_of_date=out_of_date,
            source_code_link=source_link,
            source_code_text=section_folder.relative_to(repo_root).as_posix(),
            site_prefix="../",
        ),
        encoding="utf8",
    )


def render_index_page(sections, badges, site_dir, offline=False, repo_url=REPO_URL):
    (site_dir / "index.html").write_text(
        index_template.render(
            **_shared_context(badges, repo_url, offline), sections=sections,
        ),
        encoding="utf8",
    )
