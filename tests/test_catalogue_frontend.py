import hashlib
import json
import re
import shutil
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from catalogue.build import build_catalogue
from catalogue.documentation import render_document


ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
APP = ROOT / "catalogue" / "static" / "app.js"
# Unmodified dist/cdn.min.js of @alpinejs/csp 3.17.4, from
# https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.17.4/dist/cdn.min.js; update it with the runtime.
ALPINE_CSP_SHA256 = "0d18d7f8d7910e2e0212f0f056b12f50bebc3abb7d88d2f7c7cb4c336fe4519a"


def call_argument(expression, name):
    """Extract a named call's JSON string argument from an Alpine expression."""
    match = re.search(re.escape(name) + r'\(("[^"]*")\)', expression or "")
    return json.loads(match.group(1)) if match else None


class FrontendDocument(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.elements = []
        self.payloads = []
        self._payload = None
        self.feed(path.read_text(encoding="utf8"))

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "script" and attributes.get("type") == "application/json":
            self._payload = []

    def handle_data(self, data):
        if self._payload is not None:
            self._payload.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._payload is not None:
            self.payloads.append("".join(self._payload))
            self._payload = None


class FrontendRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary_directory.name)
        cls.repository = cls.root / "repository"
        shutil.copytree(FIXTURES / "catalogue", cls.repository)
        shutil.copytree(
            FIXTURES / "catalogue-ui" / "tools", cls.repository / "tools", dirs_exist_ok=True,
        )
        cls.site = cls.root / "site"
        cls.sections = build_catalogue(cls.site, cls.repository / "tools", offline=True)
        cls.index = FrontendDocument(cls.site / "index.html")

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def test_search_payload_contains_all_search_fields_once(self):
        self.assertEqual(len(self.index.payloads), 1)
        entries = json.loads(self.index.payloads[0])
        self.assertEqual(len(entries), len(self.sections))
        for entry, section in zip(entries, self.sections):
            self.assertEqual(set(entry), {"slug", "kind", "search"})
            self.assertEqual(entry["slug"], section["slug"])
            self.assertEqual(entry["kind"], section["kind"])
            terms = [section["title"], section["description"] or ""]
            if section["main_dependency"]:
                terms.append(section["main_dependency"]["name"])
            terms.extend(wrapper["name"] for wrapper in section["wrappers"])
            terms.extend(requirement["name"] for wrapper in section["wrappers"]
                         for requirement in wrapper["requirements"])
            for term in terms:
                self.assertIn(term.lower(), entry["search"])
        for _, attributes in self.index.elements:
            self.assertFalse({"data-search", "data-kind", "data-slug"} & attributes.keys())

    def test_payload_escapes_script_end_tags_and_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "tools" / "unsafe_name"
            entry.mkdir(parents=True)
            name = '</script><script x-init="attack">& "quote" \'single\''
            (entry / "README.md").write_text("# Fixture\n\nSafe content.\n")
            (entry / ".shed.yml").write_text(json.dumps({"description": name}))
            build_catalogue(root / "site", root / "tools", offline=True)
            document = FrontendDocument(root / "site" / "index.html")
            self.assertEqual(len(document.payloads), 1)
            self.assertNotIn("<", document.payloads[0])
            self.assertIn(name.lower(), json.loads(document.payloads[0])[0]["search"])
            self.assertFalse(any(attrs.get("x-init") == "attack" for _, attrs in document.elements))

    def test_every_page_loads_local_runtime_after_components(self):
        for path in self.site.rglob("*.html"):
            with self.subTest(page=path.relative_to(self.site)):
                document = FrontendDocument(path)
                scripts = [attrs for tag, attrs in document.elements if tag == "script" and "src" in attrs]
                self.assertEqual(len(scripts), 2)
                self.assertTrue(urlsplit(scripts[0]["src"]).path.endswith("/app.js"))
                self.assertTrue(scripts[1]["src"].endswith("/vendor/alpine-csp/cdn.min.js"))
                for script in scripts:
                    self.assertIn("defer", script)
                    self.assertNotIn("://", script["src"])
                    self.assertTrue((path.parent / urlsplit(script["src"]).path).is_file())
        runtime = self.site / "static" / "vendor" / "alpine-csp" / "cdn.min.js"
        self.assertEqual(hashlib.sha256(runtime.read_bytes()).hexdigest(), ALPINE_CSP_SHA256)
        self.assertIn("MIT", runtime.with_name("LICENSE.md").read_text())

    def test_static_catalogue_retains_navigation_without_scripts(self):
        rows = [attrs for tag, attrs in self.index.elements if tag == "tr" and "tool-row" in attrs.get("class", "").split()]
        self.assertEqual(len(rows), len(self.sections))
        self.assertTrue(all("hidden" not in row for row in rows))
        links = {attrs["href"] for tag, attrs in self.index.elements if tag == "a" and "href" in attrs}
        for section in self.sections:
            self.assertIn(f"{section['slug']}/index.html", links)
        for section in self.sections:
            landing = FrontendDocument(self.site / section["slug"] / "index.html")
            links = {attrs["href"] for tag, attrs in landing.elements if tag == "a" and "href" in attrs}
            for wrapper in section["wrappers"]:
                self.assertIn(wrapper["url"], links)

    def test_readme_and_help_are_outside_alpine_processing(self):
        for path in self.site.rglob("*.html"):
            document = FrontendDocument(path)
            for _, attrs in document.elements:
                if attrs.get("id") in {"README", "tool-help"}:
                    self.assertIn("x-ignore", attrs)
                self.assertNotIn("x-html", attrs)
            # The fixture's documentation probes must not keep their directives.
            self.assertNotIn("compromised", path.read_text(encoding="utf8"))
        suite = self.site / "spectrum-workshop"
        self.assertIn("Documentation must remain inert.", (suite / "index.html").read_text())
        self.assertIn("Help must remain inert.", (suite / "wrappers" / "mass_filter.html").read_text())

    def test_template_expressions_use_component_members(self):
        # A lightweight wiring check; browser fixtures cover interaction behaviour.
        script = APP.read_text(encoding="utf8")
        components = set(re.findall(r'''Alpine\.data\(\s*["'](\w+)["']''', script))
        members = set(re.findall(r"^\s*(\w+)\s*(?::|\([^)]*\)\s*\{)", script, re.M))
        refs = set()
        for path in self.site.rglob("*.html"):
            for _, attrs in FrontendDocument(path).elements:
                for name, value in attrs.items():
                    if name == "x-ref":
                        refs.add(value)
                    elif name == "x-data":
                        self.assertIn(value, components)
                    elif name.startswith(("x-", "@", ":")) and name != "x-ignore":
                        code = re.sub(r"'[^']*'|\"[^\"]*\"", "", value)
                        for identifier in re.findall(r"[A-Za-z_]\w*", code):
                            with self.subTest(page=str(path.relative_to(self.site)), attribute=name):
                                self.assertIn(identifier, members | {"true", "false", "null"})
        self.assertEqual(set(re.findall(r"\$refs\.(\w+)", script)), refs)

    def test_rows_and_suite_controls_use_their_search_entry(self):
        filter_actions = {attrs.get("x-on:click") for tag, attrs in self.index.elements if tag == "button"}
        for kind in ("all", "tool", "data-manager"):
            self.assertIn(f"selectKind('{kind}')", filter_actions)
        slugs = [entry["slug"] for entry in json.loads(self.index.payloads[0])]
        remaining = iter(slugs)
        current = None
        controls = 0
        for tag, attrs in self.index.elements:
            classes = attrs.get("class", "").split()
            if tag == "tr" and "tool-row" in classes:
                current = next(remaining)
                self.assertEqual(call_argument(attrs.get("x-bind:hidden"), "isVisible"), current)
            elif tag == "tr" and "wrapper-row" in classes:
                self.assertIn("hidden", attrs)
                self.assertEqual(call_argument(attrs.get("x-bind:hidden"), "isExpanded"), current)
            elif "row-toggle" in classes:
                # The arrow is a mouse shortcut; "N wrappers" is the keyboard and screen-reader control.
                self.assertEqual((attrs.get("tabindex"), attrs.get("aria-hidden")), ("-1", "true"))
                self.assertNotIn("aria-expanded", attrs)
                self.assertEqual(call_argument(attrs.get("x-on:click"), "toggleSuite"), current)
                self.assertEqual(call_argument(attrs.get("x-bind:class"), "isExpanded"), current)
            elif "wrap-count" in classes:
                controls += 1
                self.assertEqual((tag, attrs.get("aria-expanded")), ("button", "false"))
                self.assertEqual(call_argument(attrs.get("x-on:click"), "toggleSuite"), current)
                self.assertEqual(call_argument(attrs.get("x-bind:aria-expanded"), "isExpanded"), current)
        self.assertIsNone(next(remaining, None))
        self.assertEqual(controls, sum(section["count"] > 1 for section in self.sections))

    def test_documentation_strips_alpine_shorthands_and_event_handlers(self):
        text = (
            '<span x-data="attack" x-init="attack" x-text="attack" '
            '@click="attack" :class="attack" onclick="attack">Inert text</span>'
        )
        rendered = str(render_document(text, "markdown", Path("README.md")))
        self.assertIn("Inert text", rendered)
        for attribute in ("x-data", "x-init", "x-text", "@click", ":class", "onclick"):
            self.assertNotIn(attribute, rendered)


if __name__ == "__main__":
    unittest.main()
