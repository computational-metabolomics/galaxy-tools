import unittest

from catalogue.rendering import add_document_links
from lxml import html
from markupsafe import Markup


class DocumentResourceLinksTests(unittest.TestCase):
    def document(self, rendered, links):
        result = add_document_links(Markup(rendered), links)
        self.assertIsInstance(result, Markup)
        return html.fragment_fromstring(str(result), create_parent="div")

    def test_existing_contextual_links_are_preserved_without_duplicate_resources(self):
        rendered = (
            '<h2 id="readme-sources">Sources and licence</h2>'
            '<p id="licence">See the <a href="https://example.org/project/">project</a>'
            ' for the upstream licence.</p>'
        )
        result = add_document_links(Markup(rendered), [
            {"name": "Project homepage", "url": "https://example.org/project"},
        ])
        self.assertEqual(result, rendered)

    def test_missing_links_go_at_end_of_existing_sources_section(self):
        document = self.document(
            '<h2 id="sources">Sources and licence</h2>'
            '<p id="credit">Original authors and <a href="#details">context</a>.</p>'
            '<h3 id="history">History</h3><p>Original repository.</p>'
            '<h2 id="details">Further details</h2><p>Other information.</p>',
            [{"name": "Source code", "url": "https://example.org/source"}],
        )
        self.assertEqual([child.tag for child in document], ["h2", "p", "h3", "p", "ul", "h2", "p"])
        self.assertEqual(document.xpath("./h2/@id"), ["sources", "details"])
        self.assertEqual(document.xpath(".//p[@id='credit']/a/@href"), ["#details"])
        self.assertEqual(document.xpath(".//p[@id='credit']")[0].text_content(), "Original authors and context.")
        anchor = document.xpath("./ul/li/a")[0]
        self.assertEqual(anchor.text, "Source code")
        self.assertEqual(anchor.get("target"), "_blank")
        self.assertEqual(anchor.get("rel"), "noopener noreferrer")

    def test_fallback_heading_has_unique_id_and_keeps_existing_anchors(self):
        document = self.document(
            '<span id="sources-and-licence"></span><a name="sources-and-licence-1"></a>'
            '<h2 id="usage">Usage</h2><p>Instructions.</p>',
            [{"name": "Source code", "url": "https://example.org/source"}],
        )
        self.assertEqual(document.xpath("./h2/@id"), ["usage", "sources-and-licence-2"])
        self.assertEqual(document.xpath("./a/@name"), ["sources-and-licence-1"])
        self.assertEqual(document[-2].text, "Sources and licence")
        self.assertEqual(document[-1].tag, "ul")

    def test_empty_document_and_repeated_requested_links(self):
        document = self.document("", [
            {"name": "Source code", "url": "https://example.org/source/"},
            {"name": "Same source", "url": "https://example.org/source"},
        ])
        self.assertEqual(document.xpath("./h2/text()"), ["Sources and licence"])
        self.assertEqual(document.xpath("./ul/li/a/text()"), ["Source code"])

    def test_unsafe_urls_are_ignored_and_labels_cannot_inject_html(self):
        name = '<img src=x onerror="attack()"> & "quoted"'
        url = 'https://example.org/?label="quoted"&value=1'
        document = self.document("", [
            {"name": "Script", "url": "javascript:alert(1)"},
            {"name": "Relative", "url": "/source"},
            {"name": "Protocol relative", "url": "//example.org/source"},
            {"name": name, "url": url},
        ])
        self.assertFalse(document.xpath(".//img | .//script"))
        self.assertEqual(document.xpath(".//a/text()"), [name])
        self.assertEqual(document.xpath(".//a/@href"), [url])
        self.assertEqual(document.xpath(".//a/@onclick | .//a/@onerror"), [])
        self.assertEqual(add_document_links(Markup(""), [{"name": "Unsafe", "url": "javascript:alert(1)"}]), "")


if __name__ == "__main__":
    unittest.main()
