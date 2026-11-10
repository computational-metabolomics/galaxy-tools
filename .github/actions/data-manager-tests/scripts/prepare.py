#!/usr/bin/env python3
"""Prepare native data-manager and wrapper tests for Planemo."""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from galaxy.tool_util.loader_directory import find_possible_tools_from_path
from galaxy.util.xml_macros import load
from lxml import etree
from planemo.io import filter_paths

PARSER = etree.XMLParser(strip_cdata=False, resolve_entities=False, no_network=True)


def repository(path: Path, root: Path) -> Path:
    """Find the enclosing Tool Shed repository within the repository root."""
    path = path.resolve()
    path.relative_to(root)
    for parent in path.parents:
        if parent == root:
            break
        if (parent / ".shed.yml").is_file():
            return parent
    raise ValueError(f"No Tool Shed repository for {path}")


def pairs(root: Path, tools):
    """Match each tool needing data tables to the unique manager supplying them."""
    providers = []
    for config in sorted(root.glob("data_managers/**/data_manager_conf.xml")):
        for manager in etree.parse(str(config), PARSER).findall("data_manager"):
            providers.append(
                (
                    config.parent / manager.get("tool_file"),
                    {table.get("name") for table in manager.findall("data_table")},
                )
            )
    result = {}
    for tool in dict.fromkeys(tools):
        path = (root / tool).resolve()
        path.relative_to(root)
        tree = load(path).getroot()
        if tree.tag != "tool" or tree.get("tool_type") == "manage_data":
            continue
        tables = {node.get("from_data_table") for node in tree.iter("options")}
        tables.discard(None)
        if not tables:
            continue
        matches = [manager for manager, supplied in providers if tables <= supplied]
        tool, names = path.relative_to(root), ", ".join(sorted(tables))
        if not matches:
            missing = sorted(tables - set().union(*(supplied for _, supplied in providers)))
            raise ValueError(
                f"{tool}: no single data manager in this repository supplies all of its "
                f"data tables ({names})"
                + (f"; not supplied by any: {', '.join(missing)}" if missing else "")
            )
        if len(matches) > 1:
            managers = ", ".join(str(match.resolve().relative_to(root)) for match in matches)
            raise ValueError(
                f"{tool}: data tables ({names}) are supplied by more than one data manager: "
                f"{managers}"
            )
        result[path] = matches[0].resolve()
    return result


def prepare(root: Path, tools, workspace: Path) -> dict:
    """Copy selected tools and managers into a workspace with shared empty tables."""
    skip = root / ".tt_skip"
    tools = [str((root / tool).resolve()) for tool in tools]
    if skip.is_file():
        tools = filter_paths(tools, cwd=str(root), exclude_from=[str(skip)])
    selected = pairs(root, tools)
    if skip.is_file():
        for tool, manager in selected.items():
            if not filter_paths([str(manager)], cwd=str(root), exclude_from=[str(skip)]):
                raise ValueError(
                    f"{repository(tool, root).relative_to(root)}: requires data manager "
                    f"{repository(manager, root).relative_to(root)}, excluded by .tt_skip"
                )
    selection = {"tools": [], "managers": []}
    for path in dict.fromkeys((root / tool).resolve() for tool in tools):
        tree = load(path).getroot()
        if tree.tag == "tool":
            kind = "managers" if tree.get("tool_type") == "manage_data" else "tools"
            selection[kind].append(str(path.relative_to(root)))
    selection["managers"] = list(
        dict.fromkeys(
            [
                *selection["managers"],
                *(str(path.relative_to(root)) for path in selected.values()),
            ]
        )
    )
    trees = {
        root / relative: load(root / relative)
        for paths in selection.values()
        for relative in paths
    }
    for path, tree in trees.items():
        tests = tree.findall("tests/test")
        positive = [
            t
            for t in tests
            if t.get("expect_failure", "false").lower() not in ("true", "1")
        ]
        if not tests or (path in selected and not positive):
            raise ValueError(f"No native tests or positive analysis tests: {path}")
        if path in selected.values() and not positive:
            raise ValueError(f"Need at least one positive installation test: {path}")
    repositories = {repository(path, root) for path in trees}
    workspace.mkdir(parents=True, exist_ok=False)
    installed = workspace / "installed"
    installed.mkdir()
    for source in sorted(repositories):
        staged = workspace / source.relative_to(root)
        shutil.copytree(source, staged, symlinks=True)
        # Links that leave the repository would dangle in the copy; point them at the original.
        for directory, dirnames, filenames in os.walk(staged):
            for link in (Path(directory, name) for name in dirnames + filenames):
                if link.is_symlink():
                    original = source / link.relative_to(staged)
                    target = Path(os.path.normpath(original.parent / os.readlink(original)))
                    if not target.is_relative_to(source) and target.exists():
                        link.unlink()
                        link.symlink_to(target)
        for tool in find_possible_tools_from_path(str(source), recursive=True):
            if Path(tool) not in trees:
                (workspace / Path(tool).relative_to(root)).unlink()
    tables = etree.Element("tables")
    names = set()
    for manager_repo in sorted(
        {repository(root / path, root) for path in selection["managers"]}
    ):
        schema = etree.parse(
            str(manager_repo / "tool_data_table_conf.xml.sample"), PARSER
        )
        for table in schema.findall("table"):
            name = table.get("name")
            if (
                not re.fullmatch(r"[A-Za-z0-9_]+", name or "")
                or name in names
                or table.find("file") is None
            ):
                raise ValueError(f"Missing or duplicate table declaration: {name}")
            names.add(name)
            location = installed / f"{name}.loc"
            location.touch()
            table.find("file").set("path", str(location))
            tables.append(table)
    etree.ElementTree(tables).write(str(workspace / "tool_data_table_conf.xml"))
    (workspace / "selection.json").write_text(json.dumps(selection))
    return selection


def write_outputs(selection: dict, workspace: Path, output: Path):
    """Write selected repositories and shared data-table options to GitHub outputs."""
    with output.open("a") as stream:
        for kind, paths in selection.items():
            repos = dict.fromkeys(
                repository(workspace / path, workspace) for path in paths
            )
            stream.write(f"{kind}<<TEST_PATHS\n")
            stream.writelines(f"{repo}\n" for repo in repos)
            stream.write("TEST_PATHS\n")
        options = (
            (
                f"--tool_data_table {workspace}/tool_data_table_conf.xml "
                f"--tool_data_path {workspace}/installed"
            )
            if selection["managers"]
            else ""
        )
        stream.write(f"planemo-options={options}\n")


def shed_repositories(root: Path, repositories: list[str]):
    """Keep discovered paths that are Tool Shed or Dockstore repositories."""
    # Drop Planemo's "." fallback and paths for removed repositories.
    return [
        repo for repo in repositories
        if any((root / repo / name).is_file() for name in (".shed.yml", ".dockstore.yml"))
    ]


def augment_repositories(root: Path, repositories: list[str], changed: list[str]):
    """Add affected tool/data-manager partners to the repositories to test."""
    repositories = shed_repositories(root, repositories)
    skip = root / ".tt_skip"
    shared = any(
        path.startswith(".github/actions/data-manager-tests/")
        or path in (".github/workflows/pr.yaml", ".github/workflows/ci.yaml")
        for path in changed
    )
    tools_dir = root / "tools"
    tools = (
        find_possible_tools_from_path(str(tools_dir), recursive=True)
        if tools_dir.is_dir() else []
    )
    for tool, manager in pairs(root, sorted(tools)).items():
        pair = [
            str(repository(path, root).relative_to(root)) for path in (tool, manager)
        ]
        if shared or any(
            repo in repositories or any(path.startswith(f"{repo}/") for path in changed)
            for repo in pair
        ):
            # Partners listed in .tt_skip stay excluded, as in Planemo's own discovery.
            repositories.extend(
                filter_paths(pair, cwd=str(root), exclude_from=[str(skip)]) if skip.is_file() else pair
            )
    return list(dict.fromkeys(repositories))


def verify_report(workspace: Path, kind: str, report_path: Path):
    """Require exactly one successful result for each selected native test."""
    selection = json.loads((workspace / "selection.json").read_text())
    report = json.loads(report_path.read_text())
    for relative in selection[kind]:
        tree = load(workspace / relative)
        tool_id = tree.getroot().get("id")
        expected = len(tree.findall("tests/test"))
        results = [
            test["data"]
            for test in report.get("tests", [])
            if test.get("data", {}).get("tool_id") == tool_id
        ]
        if (
            len(results) != expected
            or {result.get("test_index") for result in results} != set(range(expected))
            or any(result.get("status") != "success" for result in results)
        ):
            raise ValueError(f"Missing, skipped or failed tests: {tool_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, files in (
        ("prepare", ("tool-list", "workspace")),
        ("verify-report", ("workspace", "report")),
        ("augment", ("repository-list", "changed-files", "changed-output", "output")),
    ):
        command = commands.add_parser(name)
        for option in files:
            command.add_argument(f"--{option}", type=Path, required=True)
        if name != "verify-report":
            command.add_argument("--repository-root", type=Path, default=Path.cwd())
    commands.choices["prepare"].add_argument("--github-output", type=Path)
    commands.choices["verify-report"].add_argument(
        "--kind", choices=("managers", "tools"), required=True
    )
    args = parser.parse_args()
    try:
        if args.command == "verify-report":
            verify_report(args.workspace, args.kind, args.report)
        elif args.command == "augment":
            root = args.repository_root.resolve()
            discovered = args.repository_list.read_text().split()
            # Lint and the other checks use the changed repositories, as in IUC; tests add partners.
            args.changed_output.write_text(
                "".join(f"{repo}\n" for repo in shed_repositories(root, discovered))
            )
            repositories = augment_repositories(root, discovered, args.changed_files.read_text().split())
            args.output.write_text("".join(f"{repo}\n" for repo in repositories))
        else:
            workspace = args.workspace.resolve()
            selection = prepare(
                args.repository_root.resolve(),
                args.tool_list.read_text().split(),
                workspace,
            )
            if args.github_output:
                write_outputs(selection, workspace, args.github_output)
    except (OSError, ValueError, etree.XMLSyntaxError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
