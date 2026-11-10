"""Resolve Tool Shed repositories declared by an entry's publication metadata."""

import re
from functools import lru_cache

import requests
from jinja2 import nodes, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .metadata import load_yaml


TOOL_SHED_URL = "https://toolshed.g2.bx.psu.edu"
_COMPONENT = re.compile(r"[A-Za-z0-9_-]+")
_VARIABLES = {"tool_id", "tool_name", "description"}
_TEMPLATE_NODES = (
    nodes.Template, nodes.Output, nodes.TemplateData, nodes.Name, nodes.Const,
    nodes.CondExpr, nodes.Compare, nodes.Operand, nodes.And, nodes.Or, nodes.Not,
    nodes.Concat,
)


def _component(value, path, field):
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value):
        raise ValueError(f"{path}: '{field}' must contain only letters, numbers, underscores or hyphens")
    return value


def _name_template(source, path):
    if not isinstance(source, str) or not source:
        raise ValueError(f"{path}: 'auto_tool_repositories.name_template' must be a non-empty string")
    environment = SandboxedEnvironment(undefined=StrictUndefined)
    environment.globals.clear()
    environment.filters.clear()
    environment.tests.clear()
    try:
        parsed = environment.parse(source)
        for node in (parsed, *parsed.find_all(nodes.Node)):
            if not isinstance(node, _TEMPLATE_NODES):
                raise ValueError("only strings, tool variables and simple conditional expressions are supported")
            if isinstance(node, nodes.Name) and node.name not in _VARIABLES:
                raise ValueError(f"unknown tool variable '{node.name}'")
            if isinstance(node, nodes.Operand) and node.op not in {"eq", "ne"}:
                raise ValueError("only equality comparisons are supported")
        return environment.from_string(source)
    except (TemplateError, ValueError) as error:
        raise ValueError(f"{path}: invalid 'auto_tool_repositories.name_template': {error}") from error


@lru_cache(maxsize=128)
def _published_repositories(owner):
    """Return published names, or None when the remote lookup is inconclusive."""
    try:
        response = requests.get(
            f"{TOOL_SHED_URL}/api/repositories", params={"owner": owner}, timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, list):
        return None
    published = set()
    for repository in data:
        if not isinstance(repository, dict) or not isinstance(repository.get("name"), str):
            return None
        if repository.get("owner", owner) != owner:
            continue
        deleted = repository.get("deleted", False)
        if not isinstance(deleted, bool):
            return None
        if not deleted:
            published.add(repository["name"])
    return frozenset(published)


def repository_links(folder, tool_files, offline=False):
    """Return configured suite/repository links and their publication status.

    Unknown owners or inferred folder names are never used to construct links.
    Offline rendering retains configured links but does not claim publication.
    """
    path = folder / ".shed.yml"
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: content must be a YAML mapping")
    repositories = data.get("repositories") or {}
    suite = data.get("suite") or {}
    automatic = data.get("auto_tool_repositories") or {}
    for field, value in (("repositories", repositories), ("suite", suite), ("auto_tool_repositories", automatic)):
        if not isinstance(value, dict):
            raise ValueError(f"{path}: '{field}' must be a YAML mapping")
    if repositories and automatic:
        raise ValueError(f"{path}: 'repositories' and 'auto_tool_repositories' cannot both be configured")
    owner = data.get("owner")
    candidates = {}

    def add(name, candidate_owner, kind, field):
        if not candidate_owner:
            return
        candidate_owner = _component(candidate_owner, path, f"{field}.owner")
        name = _component(name, path, f"{field}.name")
        key = candidate_owner, name
        candidates.setdefault(key, {
            "owner": candidate_owner, "name": name, "kind": kind,
            "url": f"{TOOL_SHED_URL}/view/{candidate_owner}/{name}",
        })

    if suite:
        add(suite.get("name"), owner, "suite", "suite")
    for name, repository in repositories.items():
        if not isinstance(repository, dict):
            raise ValueError(f"{path}: 'repositories.{name}' must be a YAML mapping")
        add(name, repository.get("owner") or owner, "repository", f"repositories.{name}")
    if automatic and owner:
        template = _name_template(automatic.get("name_template"), path)
        for _, tool in tool_files:
            variables = {
                "tool_id": str(tool.get("id") or "").lower(),
                "tool_name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
            }
            try:
                name = template.render(**variables)
            except TemplateError as error:
                raise ValueError(f"{path}: cannot render 'auto_tool_repositories.name_template': {error}") from error
            add(name, owner, "repository", "auto_tool_repositories")
    elif not repositories and not suite and data.get("name"):
        add(data["name"], owner, "repository", "repository")
    result = []
    for candidate in candidates.values():
        published = None if offline else _published_repositories(candidate["owner"])
        status = "unknown" if published is None else "published" if candidate["name"] in published else "missing"
        result.append({**candidate, "status": status})
    return result
