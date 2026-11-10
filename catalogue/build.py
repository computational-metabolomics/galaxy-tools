#!/usr/bin/env python3
"""Compatibility entry point for script and module invocations."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    # A direct script run puts catalogue/ first on sys.path; add the repository root for the package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from catalogue.builder import build_catalogue
    from catalogue.cli import main
else:
    from .builder import build_catalogue
    from .cli import main

__all__ = ["build_catalogue", "main"]


if __name__ == "__main__":
    main()
