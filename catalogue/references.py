"""Relationships between Galaxy data-table providers and consumers."""

import xml.etree.ElementTree as ET
from urllib.parse import quote

from .sections import section_name, slug, wrapper_page


def link_manager_software(sections):
    """Show consuming tools' software without treating it as an installer requirement."""
    consumers = {
        wrapper["page_url"]: wrapper
        for section in sections if section["kind"] == "tool"
        for wrapper in section["wrappers"]
    }

    def software_for(wrappers):
        packages = {}
        for wrapper in wrappers:
            for table in wrapper["tables"]:
                for link in table["links"]:
                    consumer = consumers.get(link["page_url"])
                    main = consumer.get("main_dependency") if consumer else None
                    if not main or main["name"] == "python":
                        continue
                    key = (main["name"], main["version"])
                    package = packages.setdefault(key, {
                        "name": main["name"], "version": main["version"], "sources": [], "tools": [],
                    })
                    if link not in package["tools"]:
                        package["tools"].append(dict(link))
                    for source in main.get("sources", []):
                        if source not in package["sources"]:
                            package["sources"].append(dict(source))
        return list(packages.values())

    for section in sections:
        if section["kind"] == "data-manager":
            for wrapper in section["wrappers"]:
                wrapper["tool_software"] = software_for([wrapper])
            section["tool_software"] = software_for(section["wrappers"])


def reference_tables(entries):
    """Link providers and consumers by their native Galaxy table declarations."""
    wrappers = {}
    for folder, metadata, tool_files in entries:
        section_slug = slug(section_name(folder, metadata, tool_files))
        for path, data in tool_files:
            wrappers[path.resolve()] = {
                "name": data["name"],
                "page_url": quote(wrapper_page(section_slug, path.relative_to(folder))),
                "is_data_manager": data["is_data_manager"],
                "tables": set(data["data_tables"]),
            }
        for config in folder.rglob("data_manager_conf.xml"):
            for manager in ET.parse(config).getroot().findall("data_manager"):
                path = (config.parent / manager.get("tool_file", "")).resolve()
                if path not in wrappers or not wrappers[path]["is_data_manager"]:
                    raise ValueError(f"{config}: unknown data-manager wrapper: {path}")
                wrappers[path]["tables"] = {
                    table.attrib["name"] for table in manager.findall("data_table")
                }
    return {
        path: [
            {"name": table, "links": [
                {"name": other["name"], "page_url": other["page_url"]}
                for other in wrappers.values()
                if other["is_data_manager"] != wrapper["is_data_manager"]
                and table in other["tables"]
            ]}
            for table in sorted(wrapper["tables"])
        ]
        for path, wrapper in wrappers.items()
    }
