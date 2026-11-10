"""Summarise existing catalogue lookups without updating tools or reference data."""

from html import escape

from packaging.version import InvalidVersion, Version


def _cell(value):
    return (
        escape(str(value if value is not None else "Unknown"))
        .replace("|", "&#124;")
        .replace("\n", " ")
    )


def _status(newer, available):
    if not available:
        return "Unknown"
    status = (
        "Unknown" if newer is None else
        "Newer available" if newer else "No newer version found"
    )
    try:
        if Version(str(available)).is_prerelease:
            status += " (pre-release)"
    except InvalidVersion:
        return "Unknown"
    return status


def write_version_report(sections, path):
    """Write a Markdown summary, deduplicating shared requirements within suites."""
    packages, upstream = set(), set()
    for section in sections:
        for wrapper in section["wrappers"]:
            for requirement in wrapper["requirements"]:
                packages.add(
                    (
                        section["title"],
                        requirement["name"],
                        requirement["version"] or "Unpinned",
                        requirement.get("channel"),
                        requirement.get("latest_version"),
                        _status(
                            requirement.get("out_of_date"),
                            requirement.get("latest_version"),
                        ),
                    )
                )
        main_dependencies = [
            wrapper.get("main_dependency") for wrapper in section["wrappers"]
        ] or [section.get("main_dependency")]
        for main in main_dependencies:
            main = main or {}
            for source in main.get("sources", []):
                if source["url"] == main.get("channel_url"):
                    continue
                upstream.add(
                    (
                        section["title"],
                        main["name"],
                        main["version"] or "Unpinned",
                        source["label"],
                        source.get("version"),
                        _status(source.get("newer"), source.get("version")),
                    )
                )

    lines = [
        "## Catalogue version report",
        "",
        "Read-only lookups, separate from Planemo autoupdate. Unknown includes offline, failed "
        "or incomparable results. Conda availability does not establish platform support or "
        "a solvable environment; an upstream release may not yet be packaged. "
        "Data-manager package versions describe the installer dependencies, not model/data releases.",
        "",
    ]
    for title, rows, provider in (
        ("Conda packages (all wrapper requirements)", packages, "Channel"),
        ("Upstream releases/tags (main software)", upstream, "Source"),
    ):
        lines.extend(
            [
                f"### {title}",
                "",
                f"| Catalogue entry | Package | Pinned | {provider} | Reported latest | Result |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            "| " + " | ".join(map(_cell, row)) + " |" for row in sorted(rows, key=str)
        )
        if not rows:
            lines.append("| No lookups configured | — | — | — | — | Unknown |")
        lines.append("")
    lines.append(
        "Updates require review and tests. Check model/data-manager compatibility before "
        "changing software versions; this report does not replace models, assets or expected results.\n"
    )
    path.write_text("\n".join(lines), encoding="utf8")
