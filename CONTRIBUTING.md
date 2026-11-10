# Contributing

This guide is for contributing to this repository: its Galaxy tools, data managers and workflows,
and the catalogue, CI and documentation around them.

Thank you for contributing! Bug reports, testing, reviews and ideas are as welcome as code and
documentation, and you do not need to be a Galaxy developer to help. Please follow our
[Code of Conduct](CODE_OF_CONDUCT.md) in issues, pull requests and reviews.

## Before you start

- Use the issue forms: [**Propose a Galaxy tool or data manager**](../../issues/new?template=new-tool.yml),
  [**Existing Galaxy tool or data manager issue**](../../issues/new?template=existing-tool.yml) or
  [**Other repository issue**](../../issues/new?template=other.yml).
- Check for existing work in the [Main](https://toolshed.g2.bx.psu.edu/) and
  [Test](https://testtoolshed.g2.bx.psu.edu/) Tool Sheds, [tools-iuc](https://github.com/galaxyproject/tools-iuc)
  and other community repositories, such as
  [Workflow4Metabolomics](https://github.com/workflow4metabolomics/tools-metabolomics),
  [RECETOX](https://github.com/RECETOX/galaxytools),
  [GalaxyP](https://github.com/galaxyproteomics/tools-galaxyp) and
  [bgruening/galaxytools](https://github.com/bgruening/galaxytools).
- Before starting substantial work, open an issue so that maintainers can agree the scope: a proposal
  for a new tool or data manager, or an **Other repository issue** for a workflow or for catalogue, CI
  or documentation changes.
- New to Galaxy tool development? Start with the Galaxy Training Network
  [tool development tutorial](https://training.galaxyproject.org/training-material/topics/dev/tutorials/tool-from-scratch/tutorial.html)
  and the [Intergalactic Utilities Commission (IUC) standards](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/tool_xml.html).

## How to contribute

1. Fork the repository and create a branch from `main`, for example `add-<tool-name>` or
   `fix-catalogue-links`.
2. Make your changes, following the section below for your kind of change.
3. Test them locally, as that section describes.
4. Open a pull request against `main`, as a draft while work is in progress. Complete the template
   and link the issue; for a tool, include any data manager it needs. The template's Galaxy tool
   checklist applies only to tool and data manager changes.
5. When it is complete, mark it ready for review. CI must pass and a maintainer must approve it;
   see [CI workflows](.github/README_CI.md).

Contributor acknowledgement is optional; see the README's [Contributors](README.md#contributors)
section.

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

## Galaxy tools, data managers and workflows

### Galaxy tools

Follow the [IUC tool standards](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/tool_xml.html).
The key rules are:

- **IDs and versions:** use a unique tool ID and the version `upstream-version+galaxyN`. Start at
  `+galaxy0`, increase `N` for wrapper-only changes and reset it when the upstream version changes
  ([IUC tool versions](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/tool_xml.html#tool-versions)).
  Record every new version in the README's Changelog section.
- **Requirements:** pin package versions from Bioconda or conda-forge, and do not commit binaries or
  environments. To add a missing package, see the [Bioconda guide](https://bioconda.github.io/contributor/index.html).
- **Tests:** test representative inputs and outputs with small test data, and record where it comes
  from, with its licence when it is taken from a third party
  ([IUC tests](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/tool_xml.html#tests)).
- **Citations:** add the DOI to the wrapper XML or macros
  ([IUC checklist](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/integration_checklist.html#before-toolshed)).
- **`.shed.yml`:** required for Tool Shed ownership and metadata
  ([IUC `.shed.yml` guide](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/shed_yml.html)).
  Suites and automatically generated repository configurations do not need a top-level `name`.

### Data managers

Follow Galaxy's [data manager guide](https://docs.galaxyproject.org/en/master/dev/data_managers.html)
and the [IUC data manager practices](https://galaxy-iuc-standards.readthedocs.io/en/latest/best_practices/data_managers.html).

- Submit a data manager in the same pull request as the tool that needs its reference data, unless
  it is already on `main`. CI pairs them and tests both.
- Exactly one data manager must supply all the data tables a tool uses. Chained data managers and
  external tables, including Galaxy's built-in genome tables, are not supported. See the
  [reference data checklist](.github/actions/data-manager-tests/README.md#adding-a-tool).
- Keep large reference files out of test data.

#### Reference data on Hugging Face

Publishing reference data on [Hugging Face](https://huggingface.co/computational-metabolomics) is new
and still experimental. It aims to make reference data more FAIR (findable, accessible, interoperable
and reusable): each published release is a versioned copy with its sources and licence, which stays
available when an upstream link changes.

- Releases are recorded in `data_manager/assets.json`, which is optional. It lists each release's
  files with their sources and licences. The catalogue shows the releases, and the data manager can
  use the file as its download manifest.
- Every file in every release needs an `https` URL and both an `md5` and a `sha256` checksum. The
  catalogue build checks this.
- To publish a release, add a `mirror` entry to it, with a `repo` under `computational-metabolomics/`
  and a release tag as `revision`, and record its model or data licence. Releases without a `mirror`
  entry are not published and can have any licence, which suits data that cannot go on Hugging Face.
- Merging to `main` [publishes](.github/README_CI.md#after-merging) the release, so maintainers check
  its provenance and redistribution terms first. The data manager's catalogue page then links to the
  Hugging Face repository, and the data manager can fall back to that copy when an upstream download
  fails.
- The pull request's mirror check reports any problem with a `mirror` entry, for example a missing
  field or a tag that looks like a commit hash. The example below shows a complete entry.

<details>
<summary>Minimal <code>assets.json</code> with one published release</summary>

```json
{
  "name": "Example reference data",
  "description": "What the files contain and which tool uses them.",
  "tags": ["galaxy", "metabolomics"],
  "attribution": "Example project authors.",
  "source_url": "https://example.org/reference-data",
  "license": "cc-by-4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "current_release": "1.0.0",
  "releases": {
    "1.0.0": {
      "software_compatibility": "example-tool 2.x",
      "mirror": {
        "repo": "computational-metabolomics/example-reference-data",
        "revision": "v1.0.0"
      },
      "assets": {
        "data": {
          "filename": "reference.tsv",
          "url": "https://example.org/reference-data/1.0.0/reference.tsv",
          "md5": "d41d8cd98f00b204e9800998ecf8427e",
          "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        }
      }
    }
  }
}
```

</details>

### Tool and data manager READMEs

Each tool and data manager folder needs its own `README.md`, including a planned entry without a
wrapper. Start from the [tool template](.github/readme-templates/tool.md) or the
[data manager template](.github/readme-templates/data-manager.md). Both use the same headings, in
this order: Overview, Usage, Installation and configuration (only if needed), Testing, Citations
(when published), Contributors, Changelog, and Sources and licence. The Changelog has an entry for
every new wrapper version, newest first.

The catalogue builds the folder's page from the README and from these files:

| File | What the catalogue uses |
| --- | --- |
| `README.md` in the folder | The title (first heading), overview and other sections. Use the template headings; the templates explain what goes in each section and how to format references and credits. |
| `.shed.yml` | The description, homepage and Tool Shed links. |
| Wrapper XML and macros | Names, versions, requirements, help and citations. Keep parameter details and user help here, not in the README. |
| `data_manager/assets.json` | A data manager's releases and, for each, the software versions it works with (`software_compatibility`). If a folder has more than one data manager, set `galaxy_data_manager` to the ID of the one the file describes; with one, this is automatic. |

Every tool README must include these three catalogue hint lines, as separate paragraphs outside
lists and code blocks; the [tool template](.github/readme-templates/tool.md) has them at the end of
the Overview. The catalogue reads them for the software summary and release checks, and hides them
on the page.
Without them, the catalogue has to guess the main package from the wrapper requirements, which only
works in simple cases, and it cannot check upstream releases.

```markdown
Main dependency: package-name

Conda channel: bioconda

Upstream source: [Project](https://github.com/organisation/project)
```

- `Main dependency` must match a package in the wrapper XML, when there is one.
- `Upstream source` enables release checks for GitHub, Bitbucket, PyPI and Bioconductor; repeat it for
  several sources.

### Local validation

New to Planemo? Start with its [Quick Start](https://planemo.readthedocs.io/en/stable/readme.html)
and the [Building Galaxy Tools Using Planemo](https://planemo.readthedocs.io/en/stable/writing_standalone.html)
tutorial. Then [install Planemo](https://planemo.readthedocs.io/en/stable/installation.html) and run
from the repository root:

```bash
planemo lint "tools/<tool-name>"
planemo test "tools/<tool-name>"
```

- For a data manager, use `data_managers/<manager-name>`.
- Test a tool that needs reference data together with its data manager, using the
  [manager-first example](.github/actions/data-manager-tests/README.md#running-locally).
- CI repeats these checks for changed wrappers; in your pull request, note anything it does not
  cover.

### Galaxy workflows

Follow the [IWC guide](https://github.com/galaxyproject/iwc/blob/main/workflows/README.md) and the
Galaxy Training Network's [workflow best practices](https://training.galaxyproject.org/training-material/faqs/galaxy/workflows_best_practices.html).

- Before adding a workflow, discuss it in an **Other repository issue**.
- Check it with `planemo workflow_lint` and test it with `planemo test`.
- CI does not lint or test workflows yet, so give the commands and results in your pull request.

## Catalogue, CI and documentation

Changes to the catalogue, the CI or the documentation follow the same pull request steps; the CI is
described in [CI workflows](.github/README_CI.md). For code or CI changes, run the repository tests
and lint from the repository root, ideally in the Python environment from
[Build the catalogue locally](README.md#build-the-catalogue-locally):

```bash
python -m pip install -r requirements.txt flake8==7.4.1 flake8-import-order==0.19.2
python -m unittest discover -s tests
flake8 catalogue tests .github/scripts .github/actions/data-manager-tests
```

Changes to the data manager test action also need its
[own checks](.github/actions/data-manager-tests/README.md#running-locally). When you change
documentation, keep the headings that other files link to, or update those links.

### Catalogue

The catalogue code is in `catalogue/`, with page templates in `catalogue/templates/` and styles and
scripts in `catalogue/static/`. To build and view it locally, see
[Build the catalogue locally](README.md#build-the-catalogue-locally) in the README.

- For template or JavaScript changes, also run the
  [browser checks](tests/fixtures/catalogue-ui/README.md).
- In templates, render README and help HTML inside `x-ignore`, and never use `x-html`.
- To upgrade Alpine, replace `cdn.min.js` and `LICENSE.md` in `catalogue/static/vendor/alpine-csp/`
  with the files from one `@alpinejs/csp` release (keep the CSP build, not the standard one), and
  update the checksum in `tests/test_catalogue_frontend.py`.
- Do not commit `site/`, caches or test reports.

## Licences

- Wrapper code is [MIT](LICENSE) by default. Keep an imported wrapper's licence, and add a `LICENSE`
  file in its folder when its terms differ.
- Record the licences of upstream software and reference data, and of any test data taken from a
  third party; they can differ from the wrapper licence.
- Flag unclear or restrictive terms in the issue and pull request. An unknown licence does not permit
  redistribution.

## AI-assisted contributions

Disclose any AI assistance under **AI disclosure** in the pull request template: none, minor,
moderate or substantial. You remain responsible for the accuracy of what you submit.
