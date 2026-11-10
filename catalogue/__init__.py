"""Static catalogue site builder."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "tools"
SITE = ROOT / "site"
TEMPLATES = ROOT / "catalogue" / "templates"
STATIC = ROOT / "catalogue" / "static"
BADGES = ROOT / "catalogue" / "badges.yaml"
REPO_URL = "https://github.com/computational-metabolomics/galaxy-tools"
