# sortling

- `sortling.py` stays a single file, standard library only, Python >= 3.9.
- Prompts, options and thresholds for built-in profiles live in `BUILTIN_PROFILE_SPECS` (JSON-style data). Routing logic is in `route()`.
- Rules vs the model: a rule (the `files` extension table) only when the extension alone decides the folder and the content isn't read: installers, archives, fonts, links, partial downloads. Anything about meaning (what an image shows, what a document is about) is the model's job, so fix it with a better question or option in `BUILTIN_PROFILE_SPECS`, never a filename or keyword rule. Rules can't say "unsure"; the model's probabilities are what send unsure files to `_review`.
- Test: `tests/make_fixtures.sh` builds the fixtures (gitignored; it skips sets that already exist, so `rm -rf tests/fixtures` after changing it), then `python3 tests/eval.py --backend local-d3 --misses`. eval.py always runs the repo's `sortling.py`.
- local-d3 gives the same answers on every run, so diff `--json` dry-run manifests before and after a change. A refactor should give identical manifests.
- Fixtures are synthetic (ImageMagick or picsum). Never commit the user's real files or put their names in `expected.json`.
