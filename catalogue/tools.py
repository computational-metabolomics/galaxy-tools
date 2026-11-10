"""Discover and parse Galaxy wrappers, including XML macros."""

import re
import xml.etree.ElementTree as ET

from galaxy.util.xml_macros import load as load_tool_xml

TOOL_XML = re.compile(r"<tool\s")


def split_version(raw):
    raw = (raw or "").strip()
    tool, separator, wrapper = raw.partition("+")
    return {"tool": tool or raw, "wrapper": wrapper if separator else ""}


def parse_tool(xml_path):
    try:
        if ET.parse(xml_path).getroot().tag != "tool":
            return None
        root = load_tool_xml(xml_path).getroot()
    except Exception as error:
        raise ValueError(f"{xml_path}: could not parse tool or expand macros: {error}") from error
    if root.find(".//expand") is not None:
        raise ValueError(f"{xml_path}: unresolved XML macro expansion")

    requirements = [
        {
            "name": (requirement.text or "").strip(),
            "version": requirement.get("version", ""),
        }
        for requirement in root.findall("./requirements/requirement")
        if requirement.get("type", "package") == "package"
    ]
    description = root.find("description")
    help_element = root.find("help")
    if help_element is None or not (help_element.text or "").strip():
        raise ValueError(f"{xml_path}: a nonempty help element is required")
    return {
        "id": root.get("id", ""),
        "name": root.get("name", xml_path.stem),
        "version": root.get("version", ""),
        "is_data_manager": root.get("tool_type") == "manage_data",
        "data_tables": sorted({
            node.get("from_data_table")
            for node in root.findall(".//options[@from_data_table]")
        }),
        "description": (
            (description.text or "").strip() if description is not None else ""
        ),
        "requirements": requirements,
        "help": help_element.text,
        "help_format": help_element.get("format", "restructuredtext"),
        "citations": [
            {"type": citation.get("type"), "text": (citation.text or "").strip()}
            for citation in root.findall("./citations/citation")
        ],
    }


def looks_like_tool(path):
    """Apply Galaxy's quick tool check, so XML test data is not parsed as a wrapper.

    As in Galaxy, unreadable paths such as broken links or folders named *.xml are skipped.
    """
    try:
        with path.open(encoding="utf8") as handle:
            return bool(TOOL_XML.search(handle.read(5 * 1024)))
    except (OSError, UnicodeDecodeError):
        return False


def discover_tools(folder):
    for path in sorted(folder.rglob("*.xml")):
        data = parse_tool(path) if looks_like_tool(path) else None
        if data is not None:
            yield path, data
