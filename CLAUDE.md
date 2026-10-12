# sortling

- `sortling.py` stays a single file, standard library only, Python >= 3.9.
- Prompts, options and thresholds for built-in profiles live in `BUILTIN_PROFILE_SPECS` (JSON-style data). Routing logic is in `route()`.
- Test: `tests/make_fixtures.sh` builds the fixtures (gitignored), then `python3 tests/eval.py --backend local-d3 --misses`. eval.py always runs the repo's `sortling.py`.
- local-d3 gives the same answers on every run, so diff `--json` dry-run manifests before and after a change. A refactor should give identical manifests.
- Fixtures are synthetic (ImageMagick or picsum). Never commit the user's real files or put their names in `expected.json`.
