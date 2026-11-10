"""Render repository documentation without executing tool or template code."""

import posixpath
import re
import shutil
from html import escape
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import bleach
import markdown
from docutils.core import publish_parts
from docutils.utils import SystemMessage
from lxml import etree
from markdown.extensions.toc import TocExtension
from markupsafe import Markup


DOCUMENT_TAGS = bleach.sanitizer.ALLOWED_TAGS.union(
    {
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "img",
        "nav",
        "p",
        "pre",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "caption",
        "colgroup",
        "col",
        "div",
        "span",
        "section",
        "dl",
        "dt",
        "dd",
        "sup",
        "sub",
        "blockquote",
        # Docutils wraps notes and warnings in <aside class="admonition ...">.
        "aside",
        # Docutils' native MathML output keeps equations available offline.
        "math",
        "mrow",
        "mi",
        "mn",
        "mo",
        "msup",
        "msub",
        "msubsup",
        "mfrac",
        "msqrt",
        "mroot",
        "mtext",
        "mspace",
        "mtable",
        "mtr",
        "mtd",
        "mover",
        "munder",
        "munderover",
        "mfenced",
        "mmultiscripts",
        "mprescripts",
        "none",
    }
)
ADMONITION_CLASSES = {
    "admonition", "attention", "caution", "danger", "error", "hint", "important", "note", "tip",
    "warning",
}


def _admonition_attribute(tag, name, value):
    """Allow ids, and only docutils' admonition classes, on asides and paragraphs."""
    if name == "id":
        return True
    return name == "class" and all(
        cls in ADMONITION_CLASSES or cls.startswith("admonition-") for cls in value.split()
    )


IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


def validate_svg(path):
    """Only copy static SVGs, including when their XML uses a non-UTF-8 encoding."""
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        tree = etree.parse(str(path), parser)
    except etree.XMLSyntaxError as error:
        raise ValueError(f"{path}: invalid SVG image: {error}") from error
    forbidden = {"script", "foreignobject", "animate", "animatemotion", "animatetransform", "set", "discard"}
    unsafe = bool(tree.docinfo.doctype or tree.xpath("//processing-instruction()"))
    for element in tree.getroot().iter():
        if not isinstance(element.tag, str):
            continue
        unsafe |= etree.QName(element).localname.lower() in forbidden
        for name, value in element.attrib.items():
            unsafe |= etree.QName(name).localname.lower().startswith("on")
            unsafe |= "javascript:" in re.sub(r"\s+", "", value).lower()
    if unsafe:
        raise ValueError(f"{path}: SVG image must not contain scripts, event handlers, DTDs or other active content")
    if tree.getroot().tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError(f"{path}: image must have an SVG root element")


def _github_slugify():
    """Return a heading-id function that matches GitHub's anchors, including -1, -2 for repeats."""
    used = {}

    def slugify(value, separator):
        base = re.sub(r"[^\w\- ]", "", value.strip().lower()).replace(" ", "-")
        slug = base
        while slug in used:
            used[base] += 1
            slug = f"{base}-{used[base]}"
        used[slug] = 0
        return slug

    return slugify


DOCUMENT_ATTRIBUTES = {
    "*": ["id"],
    "aside": _admonition_attribute,
    "p": _admonition_attribute,
    "a": ["href", "name", "title"],
    "img": ["alt", "src", "title", "width", "height"],
    "td": ["align", "colspan", "rowspan"],
    "th": ["align", "colspan", "rowspan"],
    "math": ["xmlns", "display"],
    "mo": ["stretchy", "fence", "separator", "form"],
    "mspace": ["width"],
    "mtable": ["columnalign", "rowspacing", "columnspacing"],
}


class _DocumentHTML(HTMLParser):
    """Rewrite document headings and repository-relative links after sanitizing."""

    def __init__(self, source, heading_offset=0, context=None, id_prefix=""):
        super().__init__(convert_charrefs=False)
        self.source = Path(source)
        self.heading_offset = heading_offset
        self.context = context
        self.id_prefix = id_prefix
        self.output = []
        self.ids = set()
        self.fragments = []

    def _tag(self, tag):
        if re.fullmatch(r"h[1-6]", tag):
            return f"h{min(6, int(tag[1]) + self.heading_offset)}"
        return tag

    def _url(self, value, image=False):
        if value.startswith("#"):
            # "#" and "#top" go to the top of the page without a matching id.
            if value[1:].lower() in ("", "top"):
                return value
            self.fragments.append(value[1:])
            return "#" + self.id_prefix + value[1:]
        if not self.context:
            return value
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path:
            return value
        path = unquote(parts.path)
        for prefix in ("$PATH_TO_IMAGES/", "${PATH_TO_IMAGES}/"):
            if path.startswith(prefix):
                path = "static/images/" + path[len(prefix):]
                break
        if path.startswith("$__tool_directory__/"):
            path = path[len("$__tool_directory__/"):]
        root, site, output, page_links, repo_url, repo_root, source_ref = self.context
        # As on GitHub, a leading "/" refers to the repository root.
        base = repo_root if path.startswith("/") else self.source.parent
        target = (base / path.lstrip("/")).resolve()
        if not target.is_relative_to(repo_root.resolve()):
            raise ValueError(
                f"{self.source}: documentation link escapes repository: {value}"
            )
        if not target.exists():
            raise ValueError(f"{self.source}: missing documentation resource: {value}")
        if target in page_links:
            destination = page_links[target]
            url = quote(
                posixpath.relpath(destination.as_posix(), output.parent.as_posix())
            )
            if target.suffix == ".xml" and parts.fragment and parts.fragment.lower() != "top":
                parts = parts._replace(fragment="help-" + parts.fragment)
            elif target.name.lower() == "readme.md" and parts.fragment and parts.fragment.lower() != "top":
                parts = parts._replace(fragment="readme-" + parts.fragment)
        elif image:
            if not target.is_file() or not target.is_relative_to(root.resolve()):
                raise ValueError(
                    f"{self.source}: image must be a file within its tool folder: {value}"
                )
            if target.suffix.lower() not in IMAGE_SUFFIXES:
                raise ValueError(
                    f"{self.source}: image must be a GIF, JPEG, PNG, SVG or WebP file: {value}"
                )
            if target.suffix.lower() == ".svg":
                validate_svg(target)
            # Keep tools/ and data_managers/ entries with the same folder name apart.
            destination = (
                site / "assets" / root.resolve().relative_to(repo_root.resolve())
                / target.relative_to(root.resolve())
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, destination)
            url = quote(
                posixpath.relpath(destination.as_posix(), output.parent.as_posix())
            )
        else:
            relative = quote(target.relative_to(repo_root.resolve()).as_posix())
            kind = "tree" if target.is_dir() else "blob"
            url = f"{repo_url}/{kind}/{quote(source_ref, safe='')}/{relative}"
        return urlunsplit(("", "", url, parts.query, parts.fragment))

    def handle_starttag(self, tag, attrs):
        rewritten = []
        external_link = False
        for key, value in attrs:
            if (tag, key) in {("a", "href"), ("img", "src")}:
                value = self._url(value, image=tag == "img")
                if tag == "a":
                    parts = urlsplit(value)
                    external_link = parts.scheme in {"http", "https"} or (not parts.scheme and bool(parts.netloc))
            elif key == "id" or (tag, key) == ("a", "name"):
                # Browsers also follow fragments to <a name>, as GitHub does.
                value = self.id_prefix + value
                self.ids.add(value)
            rewritten.append(f' {key}="{escape(value or "", quote=True)}"')
        if external_link:
            rewritten.append(' target="_blank" rel="noopener noreferrer"')
        self.output.append(f"<{self._tag(tag)}{''.join(rewritten)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.output.append(f"</{self._tag(tag)}>")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")


def render_document(
    text, format, source, *, context=None, heading_offset=0, id_prefix="", title_anchor=False
):
    if format == "markdown":
        rendered = markdown.markdown(
            text, extensions=["tables", "fenced_code", TocExtension(slugify=_github_slugify())]
        )
        if title_anchor:
            # The page heading shows the title; keep its anchor, which GitHub also counts
            # when numbering repeated headings.
            rendered = re.sub(r'\A\s*<h1 id="([^"]*)">.*?</h1>', r'<span id="\1"></span>', rendered, count=1, flags=re.S)
    elif format == "restructuredtext":
        warnings = StringIO()
        try:
            rendered = publish_parts(
                text,
                source_path=str(source),
                writer="html5",
                settings_overrides={
                    "raw_enabled": False,
                    "file_insertion_enabled": False,
                    "_disable_config": True,
                    "halt_level": 2,
                    "report_level": 2,
                    "warning_stream": warnings,
                    "doctitle_xform": False,
                    "initial_header_level": 1,
                    "math_output": "MathML",
                    "syntax_highlight": "none",
                },
            )["fragment"]
        except SystemMessage as error:
            raise ValueError(
                f"{source}: invalid reStructuredText help: {error}"
            ) from error
    else:
        raise ValueError(f"{source}: unsupported help format {format!r}")
    sanitized = bleach.clean(
        rendered,
        tags=DOCUMENT_TAGS,
        attributes=DOCUMENT_ATTRIBUTES,
        protocols=bleach.sanitizer.ALLOWED_PROTOCOLS,
        strip=True,
    )
    document = _DocumentHTML(
        source, heading_offset=heading_offset, context=context, id_prefix=id_prefix
    )
    document.feed(sanitized)
    document.close()
    # Browsers also match a fragment after percent-decoding it.
    missing = [
        fragment for fragment in document.fragments
        if not {id_prefix + fragment, id_prefix + unquote(fragment)} & document.ids
    ]
    if missing:
        raise ValueError(
            f"{source}: links to missing headings or anchors: {', '.join('#' + f for f in missing)}"
        )
    return Markup("".join(document.output))


class _PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.ids.add(attrs["name"])
        key = "href" if tag in {"a", "link"} else "src" if tag in {"img", "script"} else None
        if key and attrs.get(key):
            self.links.append(attrs[key])


def validate_site_links(site):
    """Check destinations after rendering, so links can point to later pages."""
    site = site.resolve()
    pages = {}
    for path in site.rglob("*.html"):
        page = _PageLinks()
        page.feed(path.read_text(encoding="utf8"))
        pages[path] = page
    for source, page in pages.items():
        for link in page.links:
            parts = urlsplit(link)
            if parts.scheme or parts.netloc:
                continue
            path = unquote(parts.path)
            if path.startswith("/"):
                target = site / path.lstrip("/")
            else:
                target = source.parent / path if path else source
            target = target.resolve()
            if target.is_dir():
                target /= "index.html"
            if not target.is_relative_to(site) or not target.is_file():
                raise ValueError(f"{source}: missing or external site destination: {link}")
            fragment = unquote(parts.fragment)
            if target in pages and fragment and fragment.lower() != "top" and fragment not in pages[target].ids:
                raise ValueError(f"{source}: link to missing heading or anchor: {link}")
