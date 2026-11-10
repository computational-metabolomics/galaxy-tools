# CI workflows

This guide explains what CI checks on your pull request and what happens after it is merged.

The CI is based on [IUC's workflows](https://github.com/galaxyproject/tools-iuc/tree/main/.github/workflows)
for linting and testing Galaxy tools. This repository also includes:

- a [data manager test action](actions/data-manager-tests/README.md), which installs reference data
  before testing the tools that use it;
- a catalogue build, which checks each entry's README and Tool Shed metadata and publishes the
  catalogue on GitHub Pages;
- checks and publishing of data manager reference data on Hugging Face.

## On your pull request

When you open or update a pull request, CI lints and tests the tools and data managers you changed.
For tools that need reference data, it runs the matching data manager's tests first to install the
required data, then runs the tool's tests.

Changing either a tool or its data manager causes both to be tested. If no data manager supplies
a tool's required data tables, CI fails.

### Checks that must pass

Two checks must pass before a pull request can be merged:

- **Check workflow success**, from [Build status](workflows/pr.yaml), collects the results of the
  lint, test and reference data jobs.
- **Validate and build**, from [Validate and Deploy Catalogue](workflows/docs.yaml), runs the
  repository tests and lint and builds the catalogue.

Tool and data manager tests can be skipped when no relevant wrappers changed. A green check does
not, by itself, confirm that those tests ran.

### Reference data

If you change a data manager or the Hugging Face publisher, CI runs **Verify model mirrors**.
This validates every `mirror` entry in the repository, downloads unpublished files from their
source and published files from Hugging Face, and verifies their checksums.

### Preview the catalogue

CI builds the catalogue for every pull request. To preview it:

1. Open the **Validate and Deploy Catalogue** run from the list of checks.
2. Download the `catalogue-preview` artifact from its summary page and unzip it into
   `catalogue-preview/`.
3. Run the following command, then open `http://localhost:8000` in your browser:

   ```bash
   python -m http.server --directory catalogue-preview
   ```

On pull requests the catalogue is built offline. The build checks the required README and Tool
Shed files, the format of catalogue hint lines, and links within the repository. It also checks
that every file in `assets.json` has an `https` URL and well-formed checksums. It does not check
external URLs.

### Reports

Test reports and mirror logs are kept as artifacts. Version reports appear in the run summary.

### Run all tool tests

To test every tool, put `/run-all-tool-tests` on the first line of a new comment on a pull request.
You need write access. **This tests the default branch (`main`), not your pull request's branch.**

## After merging

Nothing is published from a pull request. Merging creates a push to `main`, and that push starts
publishing. Pull request runs, manual runs and pushes to other branches never upload to the
Tool Shed or Hugging Face, so you can update your pull request as often as you like.

- **Tool Shed:** once lint and tests pass, the changed tools and data managers are uploaded to the
  Test Tool Shed and then the Main Tool Shed. Every data manager release with a `mirror` entry
  must already be published on Hugging Face. A Test Tool Shed failure does not stop the Main Tool
  Shed upload. The result is reported on the merged pull request.
- **Hugging Face:** data manager releases with a `mirror` entry are published; releases without
  one are skipped. See [reference data on Hugging Face](../CONTRIBUTING.md#reference-data-on-hugging-face).
- **Catalogue:** the catalogue on GitHub Pages is rebuilt and published, with a screenshot for
  the README.

## Workflows

| Workflow | Runs on | What it does |
| --- | --- | --- |
| [Build status](workflows/pr.yaml) | Pull requests; selected pushes to `main` | Lints and tests changed tools and data managers; checks Hugging Face reference data; uploads to the Tool Shed after a merge |
| [Weekly global Tool Linting and Tests](workflows/ci.yaml) | Mondays 00:30 UTC, manual runs, `/run-all-tool-tests` | Lints and tests all tools |
| [Validate and Deploy Catalogue](workflows/docs.yaml) | Pull requests, pushes to `main`, Mondays 00:30 UTC, manual runs | Runs repository tests and lint, builds the catalogue and, from `main`, publishes it on GitHub Pages with a screenshot for the README |
| [Model mirror verification](workflows/hf-mirror.yml) | Relevant pushes to `main`, manual runs | Tests the Hugging Face publisher, checks reference data and publishes it after a merge |
| [Slash Command Dispatch](workflows/slash.yaml) | `/run-all-tool-tests` comments | Starts the weekly tool tests |

The Actions tab shows these names, except for Build status: it keeps IUC's name there,
*Galaxy Tool Linting and Tests for push and PR*. This guide and the README badge call it
Build status.
