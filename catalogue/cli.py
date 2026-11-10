"""Command-line options and version-report output for the catalogue builder."""

import argparse
from pathlib import Path

from . import REPO_URL
from .builder import build_catalogue
from .version_report import write_version_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the catalogue website")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip remote conda and upstream version lookups",
    )
    parser.add_argument("--version-report", type=Path, help="also write a Markdown version report")
    parser.add_argument("--repo-url", default=REPO_URL, help="repository URL used for source links and branding")
    parser.add_argument("--source-ref", default="main", help="branch, tag or commit used for source links")
    arguments = parser.parse_args(argv)
    sections = build_catalogue(
        offline=arguments.offline, repo_url=arguments.repo_url, source_ref=arguments.source_ref,
    )
    if arguments.version_report:
        write_version_report(sections, arguments.version_report)
    print("Catalogue generated.")


if __name__ == "__main__":
    main()
