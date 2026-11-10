# Galaxy Tools for Metabolomics

[![Browse catalogue](catalogue/static/badges/catalogue.svg)](https://computational-metabolomics.github.io/galaxy-tools/)
[![usegalaxy.eu](catalogue/static/badges/galaxy-eu.svg)](https://usegalaxy.eu)
[![Galaxy Tool Shed](catalogue/static/badges/galaxy-toolshed.svg)](https://toolshed.g2.bx.psu.edu)
[![Licence MIT](catalogue/static/badges/mit.svg)](LICENSE)
[![University of Birmingham](catalogue/static/badges/uob.svg)](https://www.birmingham.ac.uk)
[![BioFAIR](catalogue/static/badges/biofair.svg)](https://biofair.uk)
[![Build status](https://img.shields.io/github/actions/workflow/status/computational-metabolomics/galaxy-tools/pr.yaml?label=Build%20status&labelColor=2c3143&style=flat&branch=main)](../../actions/workflows/pr.yaml)

This repository contains a wide range of Galaxy tools, data managers and workflows for
metabolomics and related fields, developed and maintained by the
[Computational Metabolomics Research Group](https://github.com/computational-metabolomics) at
the [University of Birmingham](https://www.birmingham.ac.uk/) and collaborators.

<a href="https://computational-metabolomics.github.io/galaxy-tools/"><img align="right" width="320" src="https://computational-metabolomics.github.io/galaxy-tools/catalogue-preview.png" alt="Screenshot of the Galaxy tool catalogue"></a>

Browse the [catalogue](https://computational-metabolomics.github.io/galaxy-tools/) to explore the tools and data managers. Search them, filter by type, and view their dependencies, version information and help. Catalogue pages are generated from the same macro-expanded XML that Galaxy reads, and software versions are monitored automatically to suggest when a wrapper requires an update.

We highly recommend installing tools and data managers through the
[Galaxy Tool Shed](https://toolshed.g2.bx.psu.edu/), when available, rather than
manually from this repository.

Most of our tools are also available on public Galaxy instances such as [Workflow4Metabolomics](https://workflow4metabolomics.usegalaxy.fr/) and [Galaxy Europe](https://usegalaxy.eu/).
<br clear="right">

## Repository layout

| Path | Contents |
| --- | --- |
| [`tools/<tool-name>/`](tools/) | One directory per Galaxy tool or suite: `README.md`, wrapper XML files, `.shed.yml` and small test data in `test-data/`. |
| [`data_managers/<manager-name>/`](data_managers/) | One directory per data manager: `README.md`, the wrapper XML and its data table configuration, `.shed.yml` and, optionally, `data_manager/assets.json` for reference data releases. |
| [`workflows/<workflow-name>/`](workflows/) | One directory per Galaxy workflow, laid out as in the [Intergalactic Workflow Commission (IWC)](https://github.com/galaxyproject/iwc): `README.md`, the workflow (`.ga` or `.gxwf.yml`), a Planemo test file (`<workflow-name>-tests.yml`) and `test-data/`. |
| [`catalogue/`](catalogue/) | The code that builds the catalogue website. |
| [`.github/`](.github/) | CI workflows (most based on [IUC's](https://github.com/galaxyproject/tools-iuc/tree/main/.github/workflows)), the data manager test action, the Hugging Face publisher, and the issue, pull request and README templates. |
| [`tests/`](tests/) | Tests for the catalogue, the CI workflows and the Hugging Face publisher. `tests/fixtures/` holds test fixtures, not published tools. |
| [`assets/images/`](assets/images/) | Images used in the README, with the sources of the logos. |

## Contributing

Contributions are welcome. Please follow our [Code of Conduct](CODE_OF_CONDUCT.md) and read
[CONTRIBUTING.md](CONTRIBUTING.md), which explains the standards for tools, data managers and
workflows, their READMEs, licences and [local validation](CONTRIBUTING.md#local-validation).

- **Propose a new tool or data manager:** before starting substantial work,
  [open a proposal issue](../../issues/new?template=new-tool.yml). Working code is not required.
- **Report an issue:** use the [**Existing Galaxy tool or data manager issue**](../../issues/new?template=existing-tool.yml)
  form, or the [**Other repository issue**](../../issues/new?template=other.yml) form for a workflow,
  the catalogue, documentation or CI.
- **Submit a pull request:** branch from `main`, link the issue (for example with `Closes #123`) and
  include any data manager a tool needs. CI lints and tests the changes; after a merge it publishes
  them to the Galaxy Tool Shed and opted-in reference data to Hugging Face
  (see [CI workflows](.github/README_CI.md)).

### Build the catalogue locally

Use Python 3.11 or later, and ensure that `python` refers to a compatible interpreter. Run the following commands in Bash from the repository root.

The example assumes a Git checkout. For a ZIP download, replace `$(git rev-parse HEAD)` with the branch or commit the archive came from. Use `main` only for an archive downloaded from that branch.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python catalogue/build.py --offline \
    --source-ref "$(git rev-parse HEAD)"
python -m http.server --directory site
```

Open [localhost:8000](http://localhost:8000) in your browser. Do not commit the generated `site/` directory.

Source links point to the main repository at the given source reference; add `--repo-url` with your fork's URL to link to the fork instead. The `--offline` option skips remote version and Tool Shed lookups; installing dependencies may still require internet access. Omit `--offline` for live lookups, or run `python catalogue/build.py --help` for version reports and other options.

The frontend uses Jinja templates, custom CSS and locally bundled Alpine.js, with no frontend build step or CSS framework. Edit `catalogue/templates/` for page layout, `catalogue/static/style.css` for styling and `catalogue/static/app.js` for interactions.

## Contributors

Thank you to everyone contributing code, documentation, testing, reviews and ideas.

Main contributors:

- **Ralf J. M. Weber** — [University of Birmingham](https://www.birmingham.ac.uk/) (UK): Galaxy
  tools and data managers, catalogue, CI workflows, Hugging Face publishing, documentation
- **Neil Butcher** — [University of Birmingham](https://www.birmingham.ac.uk/) (UK): Galaxy tools,
  CI workflows, documentation

Authors, contributors and maintainers are also credited in the README of the specific Galaxy
tool or data manager. To request acknowledgement, include your preferred name, affiliation and
an optional GitHub profile link in your pull request.

## Acknowledgements

We thank the [Workflow4Metabolomics](https://workflow4metabolomics.org/) team and the wider [Galaxy community](https://galaxyproject.org/) for their collaboration, shared expertise and support over the years.

<p align="center">
  <a href="https://workflow4metabolomics.org/"><img src="assets/images/workflow4metabolomics-logo.jpg" alt="Workflow4Metabolomics (W4M)" width="340"></a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://galaxyproject.org/"><img src="assets/images/galaxy-logo.svg" alt="Galaxy community" width="220"></a>
</p>

This work has benefited from direct or indirect support from [BioFAIR Pathfinder](https://biofair.uk/), the [European Union's Horizon 2020 programme](https://research-and-innovation.ec.europa.eu/funding/funding-opportunities/funding-programmes-and-open-calls/horizon-2020_en) and [ELIXIR](https://elixir-europe.org/).

<p align="center">
  <a href="https://research-and-innovation.ec.europa.eu/funding/funding-opportunities/funding-programmes-and-open-calls/horizon-2020_en"><img src="assets/images/european-union-emblem.jpg" alt="European Union" width="180"></a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://biofair.uk/"><img src="assets/images/biofair-logo.png" alt="BioFAIR" width="160"></a>
  &nbsp;&nbsp;&nbsp;
  <a href="https://elixir-europe.org/"><img src="assets/images/elixir-logo.svg" alt="ELIXIR" width="120"></a>
</p>

## Citation and licence

### Citation

See [CITATION.cff](CITATION.cff) for information on citing this work.

### Licence

Wrapper code defaults to the [MIT Licence](LICENSE). Upstream software, models and test data may have separate terms, documented with each contribution.
