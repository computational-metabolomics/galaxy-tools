"""Read catalogue information from README and Tool Shed files."""

import re
from urllib.parse import quote, urlsplit

import markdown
import yaml
from lxml import html

from . import BADGES, REPO_URL


README_HINT = re.compile(r"^(Main dependency|Conda channel|Upstream source):\s*(.*)$", re.I | re.S)


def load_yaml(path):
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as error:
        raise ValueError(f"{path}: invalid YAML: {error}") from error
    return {} if data is None else data


def _readme_information(path):
    source = path.read_text(encoding="utf8")
    document = html.fragment_fromstring(
        markdown.markdown(source, extensions=["fenced_code", "tables"]),
        create_parent="div",
    )
    title = ""
    if re.match(r"\A\s*#\s+[^\n]+", source) and len(document) and document[0].tag == "h1":
        title = "".join(document[0].itertext()).strip()
    information = {"readme_title": title, "readme_description": ""}
    fields = {"main dependency": "main_dependency", "conda channel": "conda_channel"}
    for paragraph in document:
        if paragraph.tag != "p":
            continue
        text = "".join(paragraph.itertext()).strip()
        hint = README_HINT.match(text)
        if hint is None:
            if not information["readme_description"] and text:
                information["readme_description"] = " ".join(text.split())
            continue
        label, value = hint.groups()
        label = label.lower()
        value = value.strip()
        if label in fields:
            field = fields[label]
            if field in information:
                raise ValueError(f"{path}: duplicate '{label}' hint")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
                raise ValueError(f"{path}: '{label}' must contain one package or channel name")
            information[field] = value
        else:
            links = paragraph.findall("a")
            if links:
                if (
                    len(links) != 1 or len(paragraph) != 1
                    or (links[0].tail or "").strip()
                    or not re.fullmatch(r"Upstream source:\s*", paragraph.text or "", re.I)
                ):
                    raise ValueError(f"{path}: 'upstream source' must contain one URL or Markdown link")
                value = links[0].get("href", "")
            if not _is_http_url(value):
                raise ValueError(f"{path}: 'upstream source' must be an absolute http(s) URL")
            information.setdefault("upstream_sources", []).append(value)
    return information


def _shed_information(path):
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: content must be a YAML mapping")
    repositories = data.get("repositories", {})
    suite = data.get("suite", {})
    if repositories is None:
        repositories = {}
    if suite is None or suite is False:
        suite = {}
    if not isinstance(repositories, dict) or not isinstance(suite, dict):
        raise ValueError(f"{path}: 'repositories' and 'suite' must be YAML mappings")
    records = [("", data), ("suite.", suite)] + [
        (f"repositories.{name}.", repository) for name, repository in repositories.items()
    ]
    for prefix, record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{path}: '{prefix.rstrip('.')}' must be a YAML mapping")
        for field in ("description", "homepage_url"):
            if field not in record:
                continue
            value = record[field]
            if not isinstance(value, str):
                raise ValueError(f"{path}: '{prefix}{field}' must be a string")
            if field == "homepage_url" and value and not _is_http_url(value):
                raise ValueError(f"{path}: '{prefix}{field}' must be an absolute http(s) URL")

    def effective(field):
        if data.get(field, "").strip():
            return data[field].strip()
        values = {record.get(field, "").strip() for record in repositories.values()} - {""}
        if len(values) == 1:
            return values.pop()
        return suite.get(field, "").strip()

    return {"description": effective("description"), "homepage": effective("homepage_url")}


def load_metadata(folder):
    legacy = folder / "metadata.yaml"
    if legacy.exists():
        raise ValueError(
            f"{legacy}: no longer supported; move the overview and dependency hints to README.md, "
            "repository details to .shed.yml, and licensing/provenance to README.md or LICENSE; "
            "then remove metadata.yaml"
        )
    readme = folder / "README.md"
    if not readme.is_file():
        raise ValueError(f"{readme}: file is required")
    return {**_readme_information(readme), **_shed_information(folder / ".shed.yml")}


def load_badges(repo_url=REPO_URL, source_ref="main"):
    data = load_yaml(BADGES)
    badges = data.get("badges", []) if isinstance(data, dict) else []
    return [
        {**badge, "url": badge["url"] if _is_http_url(badge["url"])
         else f"{repo_url}/blob/{quote(source_ref, safe='')}/{quote(badge['url'])}"}
        for badge in badges
    ]


def _is_http_url(value):
    if not isinstance(value, str) or re.search(r"\s", value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme in ("http", "https") and parts.hostname)
