#!/usr/bin/env bash
# Keep the changed repositories for lint, add affected tool/data-manager
# partners for tests, and emit test chunks.
set -euo pipefail

discovered="${RUNNER_TEMP}/discovered-repositories.txt"
changed="${RUNNER_TEMP}/changed-files.txt"
changed_repositories="${RUNNER_TEMP}/changed-repositories.txt"
augmented="${RUNNER_TEMP}/augmented-repositories.txt"
all_tools="${RUNNER_TEMP}/augmented-tools.txt"
printf '%s\n' "$DISCOVERED_REPOSITORIES" > "$discovered"
if [[ -n "$COMMIT_RANGE" ]]; then
    git diff --name-only "$COMMIT_RANGE" > "$changed"
else
    : > "$changed"
fi
python .github/actions/data-manager-tests/scripts/prepare.py augment \
    --repository-list "$discovered" \
    --changed-files "$changed" \
    --changed-output "$changed_repositories" \
    --output "$augmented"
repositories=()
while IFS= read -r repository; do
    repositories+=("$repository")
done < "$augmented"
if (( ${#repositories[@]} == 0 )); then
    chunk_count=1
else
    planemo ci_find_tools --output "$all_tools" "${repositories[@]}"
    tool_count=$(wc -l < "$all_tools")
    chunk_count=$(( tool_count < MAX_CHUNKS ? tool_count : MAX_CHUNKS ))
    (( chunk_count > 0 )) || chunk_count=1
fi
chunk_list=$(seq 0 $((chunk_count - 1)) | jq --slurp --compact-output .)
{
    echo 'repository-list<<CHANGED_REPOSITORIES'
    cat "$changed_repositories"
    echo 'CHANGED_REPOSITORIES'
    echo 'test-repository-list<<TEST_REPOSITORIES'
    cat "$augmented"
    echo 'TEST_REPOSITORIES'
    echo "chunk-count=$chunk_count"
    echo "chunk-list=$chunk_list"
} >> "$GITHUB_OUTPUT"
echo "Changed repositories (lint and other checks):"
cat "$changed_repositories"
echo "Repositories to test (with partners):"
cat "$augmented"
