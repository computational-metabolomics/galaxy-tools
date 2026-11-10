# Catalogue browser checks

`spectrum_workshop` is a synthetic two-wrapper suite for checking the catalogue in a browser after
template or JavaScript changes. Never publish it.

Build it with the catalogue fixtures, from the repository root:

```sh
fixture_root=$(mktemp -d)
cp -R tests/fixtures/catalogue/. "$fixture_root/"
cp -R tests/fixtures/catalogue-ui/tools/. "$fixture_root/tools/"
python - "$fixture_root" <<'PY'
import sys
from pathlib import Path
from catalogue.build import build_catalogue

root = Path(sys.argv[1])
build_catalogue(root / "site", root / "tools", offline=True)
PY
python -m http.server 8765 --directory "$fixture_root/site"
```

Open <http://localhost:8765> and check that:

- search finds the title (`Spectrum workshop`), description (`fingerprints`), dependencies
  (`spectral-fixture`, `annotation-helper`) and wrapper name (`Peak annotator`), alone and with the
  type filters, including empty results;
- suites expand by hand and during search, and the search returns after visiting wrapper help;
- Tab reaches one `2 wrappers` control per suite;
- on narrow screens only the software table scrolls sideways;
- the page works with scripts disabled and stays in the light theme;
- README and help text, including the probe, stays visible and never runs as script;
- breadcrumb links return to the filtered catalogue.

`tests/test_catalogue_frontend.py` checks the probe text automatically.
