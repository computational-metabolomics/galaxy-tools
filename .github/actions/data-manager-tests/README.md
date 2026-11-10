# Data manager test action

This action tests Galaxy tools that need installed reference data. It runs the
matching data manager tests first to install the data, then runs the tool's
analysis tests against it.

## How CI works

The action runs in the [Build status](../../workflows/pr.yaml) and
[weekly tool test](../../workflows/ci.yaml) workflows. Both sets of tests use
`galaxyproject/planemo-ci-action@v1`. The existing tests and their native outputs
are preserved, and reports are combined into the usual CI artifacts.

Each test batch installs fresh reference data and shares it within that batch.
Source files are copied into a workspace. Links within the repository are
preserved; links pointing outside it continue to refer to the original files.

CI fails if a test is missing, skipped or fails unexpectedly. Expected-failure
cases must fail as specified.

### Which tests run

When a tool or its data manager changes, CI tests both. Lint runs on the changed
repositories. Changes to this action or either workflow run tests for all
tool and data manager pairs.

CI honours `.tt_skip` exclusions. If a selected tool needs an excluded manager,
the run fails before staging.

Pushes to `main` that change only `.github/` are skipped because the pull request
has already run the tests.

## Adding a tool

A tool that uses reference data must get all of its data tables from exactly
one data manager. Chained data managers and external tables, including Galaxy's
built-in genome tables, are not supported.

When adding a tool:

- Add native XML tests for successful runs using installed reference data, along
  with error cases where needed.
- Include the data manager in the same pull request unless it is already on
  `main`. It does not need to be published on the Tool Shed.
- Make sure the tool's `from_data_table` names match the names in
  `data_manager_conf.xml` and `tool_data_table_conf.xml.sample`.
- Add successful data manager tests that install every entry selected by the
  tool's analysis tests.

CI checks all tools. You do not need a separate mapping or special XML markers.

## Running locally

You need Planemo, Bash and `jq`. Run the example below from the repository root,
replacing the `tool` path with a wrapper that uses reference data.

The example keeps installed data and reports in a temporary workspace outside
the repository. It stops on failure and prints the workspace path on success.

```bash
(
    set -e

    tool='tools/<tool-name>/<wrapper>.xml'
    work=$(mktemp -d)
    stage="$work/staged"
    helper=.github/actions/data-manager-tests/scripts/prepare.py

    # Prepare the tool and its data manager.
    printf '%s\n' "$tool" > "$work/tools.txt"
    python "$helper" prepare --tool-list "$work/tools.txt" --workspace "$stage"
    manager=$(jq -r '.managers[0]' "$stage/selection.json")
    shared=(--tool_data_table "$stage/tool_data_table_conf.xml" --tool_data_path "$stage/installed")

    # Install reference data with the data manager tests.
    planemo test "$stage/$manager" "${shared[@]}" \
        --test_output "$work/managers.html" --test_output_json "$work/managers.json"
    python "$helper" verify-report --workspace "$stage" --kind managers \
        --report "$work/managers.json"

    # Run the analysis tests against the installed data.
    planemo test "$stage/$tool" "${shared[@]}" \
        --test_output "$work/tools.html" --test_output_json "$work/tools.json"
    python "$helper" verify-report --workspace "$stage" --kind tools \
        --report "$work/tools.json"

    printf 'Reports and installed data: %s\n' "$work"
)
```

### Helper-only checks

To check discovery, staging and report handling, run the helper's own tests.
They need Planemo installed, but do not start Galaxy or download models:

```bash
python -m unittest discover -s .github/actions/data-manager-tests/tests
```
