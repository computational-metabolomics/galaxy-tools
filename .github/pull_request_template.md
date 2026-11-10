## Summary

<!-- Describe what this pull request changes and why. Link the related issue; a new tool or data manager must link its proposal. Note anything CI does not cover, such as workflows or checks you could not run. -->

## Galaxy tool checklist (for new or updated tools and data managers)

<!-- Delete this section if no tool or data manager changed. Check applicable items and explain exceptions in the summary. -->

- [ ] README and relevant wrapper sections (for example the help) are up to date; a tool README has its Main dependency, Conda channel and Upstream source lines.
- [ ] Wrapper IDs and `+galaxyN` versioning follow the contribution guide.
- [ ] The README's Changelog has an entry for the new version.
- [ ] Package requirements pin installable conda-forge or Bioconda versions.
- [ ] Tests cover the change, and test data provenance is documented.
- [ ] Upstream software and/or reference data licences are recorded; restrictions, if any, are flagged for maintainer review.
- [ ] `.shed.yml` ownership and repository configuration are correct.
- [ ] A tool that needs reference data is submitted with its data manager (or the manager is already on `main`).
- [ ] Data manager releases to publish on Hugging Face are declared with `mirror` in `assets.json`, with checksums, sources and licences. Merging publishes them.

See CONTRIBUTING.md for the detailed requirements.

## AI disclosure

- [ ] None
- [ ] Minor: suggestions, wording, explanations or small edits that I reviewed
- [ ] Moderate: AI drafted some of the code, wrapper, tests or documentation, which I reviewed and revised
- [ ] Substantial: AI drafted significant parts of the code, wrapper, tests or documentation

## Contributor acknowledgement (optional)

<!-- To request acknowledgement, give your preferred name, affiliation and, optionally, a link to your GitHub profile. -->
