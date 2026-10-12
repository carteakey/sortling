# sortling plan

Working plan for sortling across sessions. Update it as items land. Last updated
2026-10-12 (v0.2.0, uncommitted at the time).

## Where things stand

**Done (v0.2.0):**

- **Single file:** `sortling.py`, standard library only, Python 3.9+ (syntax checked
  against 3.9; not run on a real 3.9 interpreter). Renamed from folderal with `git mv`.
- **Backends:**
  - ⚡ fast: System One decision models (local llama.cpp `/v1/systemone`, hosted TypeSafe Jev).
  - 🐢 thorough: any OpenAI-compatible chat endpoint. The Ollama backend is removed.
  - ⚡🐢 hybrid: fast on everything, thorough only on uncertain files. Use the
    `hybrid-local` config entry or `--second-pass NAME`.
- **Profiles:**
  - `default`: `docs/<category>`, `screenshots/<topic>`, `photos/<category>`, `memes/`.
  - `photos`: the lean profile for camera rolls and WhatsApp dumps.
  - Custom: from the wizard, a config entry, `--profile-file` (or `-` for stdin), or a
    per-folder `.sortling.json`.
- **Interactive wizard:**
  - Banner.
  - Folder presets: screenshots (macOS `defaults read`, Linux `~/Pictures/Screenshots`),
    Downloads, Desktop, or any path.
  - Profile choice, or create your own.
  - Backend choice with the ⚡/🐢/⚡🐢 explanation.
  - Hosted-upload warning.
  - Dry-run plan, confirm, apply, then the undo hint.
- **Config:** `~/.config/sortling/config.json`, with keys referenced by env-var name only.
- **Output and undo:** `--json` plan output for agents, `SORTLING.md` as the agent
  guide, and `--undo MANIFEST`.
- **Art:** placeholder sprites cropped from the character sheet into `assets/`. The
  README uses `assets/wizard.png` as the hero.

**Measured (RTX 4070 12 GB, llama-swap):**

| Test set | Backend | Result |
|---|---|---|
| 6 labelled docs/screenshots, neutral names | d3-flash, Clef-Flash | all categories right; meme kept, not deleted |
| same | 🐢 qwen38-flash-next-plat-vision | 6/6 incl. meme → `_delete`; 191 s incl. model load |
| 17 photos/memes/junk (`photos` profile) | d3 / Clef | 16/17, 15/17 |
| same (`default` profile) | d3 / Clef | 16/17, 15/17 |
| user's 14 real screenshots | d3 / Clef | 10 topic folders, 3 `other`, 0 `_review` / 10, 2, 1 |
| hybrid d3 → plat-vision, 17 photos | | 1 file re-checked, same answer; 188 s (model swap) |

Misses worth knowing: an accidentally dark photo scores junk 0.46 (below the
second-look band), and "woman in a canoe" flips between `people` and `places`.

## Next up (in order)

### 1. Commit and rename

- Commit v0.2.0 as one change ("folderal → sortling 0.2.0").
- Rename the GitHub repo `carteakey/folderal` → `carteakey/sortling` (the README's
  `curl` line already points there), then the local folder `~/repos/folderal`.
- The old Mac wrapper `paperless-init/PDFs/pdf_ai_organizer.py` calls `folderal.py`
  with removed flags (`--ollama-url`, `--model`); update or retire it.

### 2. Keep the test sets

The labelled fixtures from this session lived in a temporary scratchpad and are gone.
Re-create them as `tests/make_fixtures.sh` (ImageMagick + `pdftotext`, DejaVu fonts) plus
a small `tests/expected.json`, and a `tests/eval.py` that runs a backend and prints the
per-file comparison and score. Sets:

- **docs:**
  - Receipt JPG and boarding-pass screenshot (records → docs).
  - Terminal screenshot (→ `screenshots/code`).
  - Webp meme (→ `memes`).
  - Text-layer PDF of course notes, and an image-only scanned lab report PDF.
  - One exact duplicate.
  - The same files again with neutral names (`file01…`), so the filename can't leak
    the answer.
- **photos:** 10 picsum.photos stock photos (ids 237, 1025, 1011, 1039, 1047, 1080, 292,
  1062, 1059, 429), plus generated junk and memes:
  - A top/bottom-caption meme, a good-morning card, a "share with 10 friends" quote card.
  - A blurred photo and a darkened photo.

Don't commit anyone's real screenshots or photos.

### 3. wa-suite: replace unforwarder.py with sortling

`~/repos/wa-suite` (formerly `unforwarder`). Today `unforwarder/unforwarder.py` asks a
vision LLM via Ollama "is this a meme/forward…? yes or no", parses the text, and moves
files to `save/` or `review-n-delete/`; `pipeline.sh` and memento's flow feed it
`~/Pictures/WhatsApp/incoming/`.

- **Swap the call:** in `pipeline.sh` (and the preserve step), use
  `python3 sortling.py --root "$INCOMING_DIR" --profile photos --json` (`--apply` when
  `DRY_RUN=false`).
- **Map the folders:** `memes/` = the old `review-n-delete/`; `photos/*` = the old
  `save/`, which then goes to Immich / `FINAL_DIR`. Decide whether `FINAL_DIR` keeps the
  categories or flattens them (Immich doesn't care).
- **Drop the old dependencies:** Ollama, `tqdm`, `load_dotenv`. Keep a thin wrapper or
  remove `unforwarder.py`.
- **Resume:** unforwarder has a resume checkpoint. sortling re-runs skip already-moved
  files naturally (sorted folders are excluded), so no checkpoint is needed for
  `--apply`. A long dry run has no resume yet; see 7.
- **Work on a branch** in wa-suite.

### 4. carteakey.dev: `downloads-media-router` uses sortling

`.agents/skills/downloads-media-router/SKILL.md` routes `~/Downloads` / `inbox/` images
into vibes, ai-memes, infographics and NOTA. Today the agent runs Vision OCR and eyeballs
images to build the classification table.

- **Swap the classification step:** a custom profile (the example in `SORTLING.md` is
  exactly these four folders), run as `--json` dry run. The table comes from
  `records[].target_folder` with confidences, and `_review` items are flagged for a
  human look.
- **Keep the skill's execution steps:** slugs, `ai-memes.yaml` ids, changelog,
  commits. Never `--apply` there; the skill moves files itself.
- **Store the profile:** consider `inbox/.sortling.json` so the profile lives with the
  site.
- **The working tree there has many unrelated uncommitted changes:** use a branch,
  touch only the skill file.

### 5. Art integration (needs clean sprites)

Waiting on per-sprite PNGs with clean alpha (the sheet has red/yellow halos), at native
resolution on a fixed pixel grid. Requested components:

- **Every pose as its own PNG:** main, reading/confused, happy, carrying, stacking,
  overwhelmed, sleeping, three bins, wordmark.
- **The 8 heads**, plus a 16/32 px icon.
- **New poses:** a turtle companion (🐢 mode), a lightning-wand pose (⚡), a hybrid pose,
  and a trash/poof pose for `_delete`.
- **Optional:** a 3–4 frame sorting loop (GIF).

Then:

- **Terminal sprite:** render the wizard in the banner with truecolor half-block
  characters (▀ with fg/bg colors), embedded in `sortling.py` as palette + RLE (a few KB).
  Fall back to the ASCII wordmark on non-truecolor or non-UTF-8 terminals, or
  `NO_COLOR`.
- **Wizard states:**
  - sleeping while a model loads (precheck),
  - carrying during apply,
  - reading/confused for the `_review` summary,
  - happy at the end,
  - overwhelmed on errors.
- **Docs:** replace the placeholder crops in `assets/`; README sections per mode with
  their pose; a social preview image.

### 6. Accuracy and thresholds

- **Dark and black photos:** add an explicit noul "Is this photo too dark, blurry or
  accidental to keep?" for photos (or widen the second-look band to 0.4–0.85). Re-measure
  on the photos set.
- **Calibration check:** run the labelled sets on d3, Clef and hosted Jev (text-only), and
  record the probabilities of right vs wrong answers. Set the 0.6 / 0.4 / 0.85 defaults
  from data, not guesses.
- **Thorough mode with real probabilities:** for endpoints that return `logprobs`
  (llama.cpp, OpenAI), ask each choice as single-letter options and read `top_logprobs`
  instead of self-reported confidence. Reasoning models need thinking disabled for this
  (`chat_template_kwargs: {enable_thinking: false}` on llama.cpp). Mark the source in
  the manifest (`confidence_source: logprobs|self-reported`).

### 7. Features

- **Video:** the unforwarder v2 post lists meme videos as the open problem. llama.cpp's
  `/v1/systemone` takes images only, so sample 2–3 frames with `ffmpeg` (if installed)
  and send them as images. Add `.mp4/.mov/.webm/.mkv` as a `video` kind; `memes/`
  becomes the main video target. d3's own Python release reads video, if a native path
  is ever wanted.
- **Folders as units:** classify each top-level subfolder of `~/Downloads` (an unzipped
  project, a photo dump) from a listing of its contents, and move it whole. Off by
  default (`--folders`).
- **Resume for long runs:** per-file results cached by sha1 in `_sortling/cache.json`,
  so a re-run after an interruption or a threshold change doesn't re-ask the model.
- **Speed for d3:** it runs one pass per question, so the `default` profile on images is
  5 passes. Option: a two-stage request (kind + junk first, then only the relevant
  sub-question), or prefer Clef for images. Measure first.
- **macOS check:** the screenshots preset (`defaults read com.apple.screencapture
  location`), HEIC conversion via `sips`, and `qlmanage` PDF previews are written but
  untested on a Mac.
- **Scheduling:** a launchd/systemd timer recipe for the wa-suite pipeline (from the
  v2 post's "what's next").

### 8. Distribution

- **Release:** a GitHub release with `sortling.py` attached, and the `curl` one-liner
  pinned to a tag.
- **PyPI:** `sortling` is free on PyPI (checked 2026-10-12). Publish only if wanted;
  the single file stays the main path.
- **A post on carteakey.dev** (unforwarder v3 / sortling intro). The user writes it.
  Material:
  - decision models vs a yes/no LLM prompt,
  - the probabilities, and the leaderboard snapshot in `l3ms/docs/decision-models.md`,
  - the hybrid cascade.

## Decisions (and why)

- **One file, standard library only.** Download-and-run, like copyparty. That's why the
  config is JSON: Python can't write TOML without a dependency.
- **⚡ is the default and 🐢 isn't sold as "more accurate".** On the Jev Decision Index,
  prompted general LLMs score about the same as decision models, and only decision
  models give real probabilities, which the thresholds depend on.
- **`_review` is for documents only.** Unsure screenshots go to `screenshots/other` and
  unsure photos to `photos/other`: misfiling those costs nothing; misfiling a tax form does.
- **Memes are kept in `memes/`, not deleted.** Deletion needs junk ≥ 0.85 and still only
  moves to `_delete/`. Real deletes need explicit flags.
- **Records are documents whatever the format.** A screenshot of a receipt goes to
  `docs/finance`.
- **Photo categories stay coarse.** Immich and photo apps do faces and places better.
- **Hosted Jev is text-only:** TypeSafe's API docs don't mention images. Images are
  never sent to a text-only backend; they fall back to filename keywords instead.
- **Hybrid's second pass runs as one batch** after the first, so a 12 GB GPU swaps
  models once, not per file.

## How to test quickly

```bash
export LLAMA_SWAP_API_KEY=...                 # local router key
export XDG_CONFIG_HOME=/tmp/sortling-cfg      # keep tests out of the real config
python3 sortling.py --init-config && python3 sortling.py --list-backends
python3 sortling.py --root <fixtures> --json --no-progress --output-dir /tmp/sl-logs
python3 sortling.py --root <copy of fixtures> --apply --output-dir /tmp/sl-logs
python3 sortling.py --undo /tmp/sl-logs/sortling_manifest_<stamp>.json
```

Local backends used so far (llama-swap on :8080, l3ms repo):

- `vllm-d3-flash` (⚡, 11.5 GB).
- `cloudflare-clef-flash` (⚡, 8.9 GB, faster on images).
- `qwen38-flash-next-plat-vision` (🐢, slow to load).

Only one model fits the 12 GB card at a time. Each switch costs a load.
