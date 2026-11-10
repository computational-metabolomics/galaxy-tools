#!/usr/bin/env python3
"""Take a screenshot of the built catalogue so the README can show it.

Serve the generated site on a local port, capture its front page with headless
Chromium and reduce the colours so the PNG stays small. Playwright and Pillow are
installed by the Catalogue workflow only; they are not catalogue dependencies.
"""

import argparse
import functools
import http.server
import io
import threading
from pathlib import Path

# The README shows this image small, so keep the capture narrow, at low resolution and with
# few colours (about 30 KB).
VIEWPORT = {"width": 800, "height": 560}
COLOURS = 64


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def capture(site: Path) -> bytes:
    from playwright.sync_api import sync_playwright

    handler = functools.partial(QuietHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
                page.goto(f"http://127.0.0.1:{server.server_port}/index.html", wait_until="networkidle")
                page.wait_for_selector("#browse-heading")
                return page.screenshot()
            finally:
                browser.close()
    finally:
        server.shutdown()


def screenshot(site: Path, output: Path):
    from PIL import Image

    if not (site / "index.html").is_file():
        raise SystemExit(f"No index.html in {site}; build the catalogue first")
    image = Image.open(io.BytesIO(capture(site))).convert("RGB")
    reduced = image.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    output.parent.mkdir(parents=True, exist_ok=True)
    reduced.save(output, optimize=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="generated catalogue directory")
    parser.add_argument("output", type=Path, help="PNG file to write")
    args = parser.parse_args(argv)
    screenshot(args.site, args.output)
    print(f"Wrote {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
