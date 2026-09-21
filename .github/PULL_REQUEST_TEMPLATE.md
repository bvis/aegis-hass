<!--
Thanks for the contribution! A few minutes spent filling this in helps reviews
move quickly.
-->

## Summary

<!--
What does this PR change and why? One or two paragraphs is enough. Link any
related issue with `Relates to #N` (avoid `Fixes #N` / `Closes #N` — let the
maintainer close issues manually after release validation).
-->

## Test plan

<!--
Tick what applies, add anything project-specific. CI runs lint, format,
typecheck, unit tests, dead-code analysis and the dependency-floor checks on
every pull request — keep it green.
-->

- [ ] `make check` passes inside a freshly built dev image (`make build-docker`)
- [ ] New behaviour covered by unit tests in `tests/unit/`
- [ ] Coverage stays at or above the 80% threshold
- [ ] User-facing strings updated in `strings.json` and every file under `translations/` (a test pins key parity)
- [ ] `README.md` / `CHANGELOG.md` updated when the change is user-visible

## Notes for the reviewer

<!--
Anything the reviewer should know: gotchas, areas you want a closer look at,
follow-ups deliberately deferred, manual validation done on real hardware, etc.
-->
