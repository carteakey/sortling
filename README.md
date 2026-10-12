# sortling

<p align="center"><img src="assets/poses/sortling_idle.png" alt="sortling, a small pixel-art wizard with a star-topped hat, raising a sparkling wand" width="240"></p>

```
                   _    _  _
 ___   ___   _ __ | |_ | |(_) _ __    __ _
/ __| / _ \ | '__|| __|| || || '_ \  / _` |
\__ \| (_) || |   | |_ | || || | | || (_| |
|___/ \___/ |_|    \__||_||_||_| |_| \__, |
                                     |___/
```

Tidy screenshots, downloads and documents with decision models. It's one Python file
using only the standard library: download it and run it.

```bash
curl -O https://raw.githubusercontent.com/carteakey/sortling/main/sortling.py
python3 sortling.py
```

Run with no arguments and the wizard greets you, then asks three things. The menus
take arrow keys and Enter, and typing filters long lists:

1. **What to tidy:** your screenshots folder (on macOS it reads the real location from
   `defaults read com.apple.screencapture location`, which defaults to the Desktop),
   Downloads, Desktop, or any folder.
2. **How to sort:** the built-in layout, or your own folders, each with a one-line
   description. You can save custom profiles for next time.
3. **Which model:** ⚡ fast, 🐢 thorough or ⚡🐢 hybrid (below). Hosted models warn
   before anything is uploaded. "⚙ set up models…" at the bottom adds new ones.

Then it shows the plan ("12 → screenshots/code, 3 → _review …") and moves nothing until
you say yes. Every applied run prints an undo command.

While it works, the wizard's head sits next to the progress bar and reacts to each
file, Doom-guy style:

- **Happy** at an easy one, **confused** when unsure, **surprised** at junk.
- **Sunglasses** for memes, **heart eyes** for pets, **glasses** for a duplicate.
- **A magnifier** for a receipt or ticket photo filed as a document.
- **Sleepy** while a model loads, **dizzy** when there's no model, **sad** when the model
  stops answering.
- **Star-eyed** when everything's sorted.
- **Between files** it glances around, blinks, and peers at the paperwork.

It needs a UTF-8 terminal at least 72×18 (truecolor if available, 256 colors otherwise).
Turn it off with `--no-face` or `SORTLING_FACE=0`.

## ⚡ fast, 🐢 thorough, ⚡🐢 hybrid

| | ⚡ fast | 🐢 thorough |
|---|---|---|
| Model | a **decision model** over the System One API: local llama.cpp (`/v1/systemone`) or hosted TypeSafe Jev | **any OpenAI-compatible chat endpoint**: llama-swap, Ollama, LM Studio, OpenAI, OpenRouter… |
| Speed | one pass per file, about 0.1–2 s | a full LLM generation per file |
| Confidence | real probabilities, so the `_review` / `_delete` thresholds mean something | self-reported by the model, so the thresholds are rougher |
| Extra | — | a one-line explanation per file in the manifest |

Fast is the default. Thorough isn't automatically more accurate: on decision benchmarks,
general LLMs prompted to decide score about the same as dedicated decision models. Use it
when you want explanations or only have a chat model.

**Hybrid** runs fast on everything, then sends only the files fast was unsure about
(headed for `_review` or an `other` folder, or junk between 0.5 and 0.85) to a thorough
backend, whose answer wins. The second pass runs as one batch at the end, so a single-GPU
box swaps models once. Use the `hybrid-local` backend, or add `--second-pass <backend>`
to any run. Each record's `second_pass` field shows what changed.

## Profiles

<p align="center"><img src="assets/poses/sortling_sorting.png" alt="the wizard dropping colored cubes into document, image and leaf bins" width="360"></p>

### `default`

Lowercase, one tree per kind of file. System folders are underscore-prefixed so they
sort first:

```
docs/          personal/ medical/ legal/ finance/ work/ travel/ learning/ (+ learning/archive/)
screenshots/   code/ chats/ reading/ apps/ social/ shopping/ maps/ media/ other/
photos/        people/ places/ food/ pets/ events/ other/
memes/         memes, forwards, good-morning and festival cards, quote images
_review/       uncertain documents only, where misfiling matters
_delete/       junk (only at junk probability >= 0.85)
_duplicates/   exact duplicates
_sortling/     manifests (CSV + JSON) for every run
```

- **Records are documents, whatever the format.** A screenshot of a receipt, booking or
  order confirmation, or a photo of a paper form, goes to `docs/<category>` next to the PDFs.
- **Other screenshots are filed by topic.** Unsure ones go to `screenshots/other`, not
  `_review`: a misfiled screenshot costs little.
- **Camera photos get a few coarse folders on purpose.** Photo apps do faces and places better.
- **Memes are kept, not deleted.** `memes/` is the bin you review and empty; only real junk
  (blurry, accidental, black shots) goes to `_delete`.
- **Scanned PDFs work.** When a PDF has no real text layer, its first page is rendered
  (PyMuPDF, `pdftoppm` or macOS `qlmanage`) and read as an image.

### `photos`

For camera rolls and chat-app dumps (the old unforwarder job): `photos/<category>`,
`memes/`, `screenshots/`, `docs/` (not sub-sorted). It asks 3 questions per image instead
of 5, so it's faster.

### Your own

Name the folders and describe what goes in each. The description is what the model
reads. Custom profiles are flat: `<your-folder>/` for each, plus `_review`, `_delete`
and `_duplicates`. Define one in any of three ways:

- **Interactively:** "create your own…" in the wizard (and save it to the config).
- **From a file or stdin:** `--profile-file my.json`, or `--profile-file -`:
  `{"folders": {"money": "receipts, invoices, bills", "school": "course notes"}}`.
- **Per folder:** save that JSON as `<folder>/.sortling.json`, and the folder always sorts
  its own way. `{"profile": "photos"}` there picks a built-in.

Order of precedence: `--profile-file`, `--profile`, `.sortling.json`, then `default`.

### Tuning a built-in

The built-in profiles are plain JSON data inside `sortling.py`: the questions the model
is asked, the options and their descriptions, and the thresholds.
`python3 sortling.py --show-profile default` prints all of it. With no name,
`--show-profile` prints the profile a run would use, after your changes.

To change a built-in, set only the keys you want to change. Everything else stays
built-in and keeps improving with updates. Nested objects merge, `null` removes an
option, and keys starting with `_` are notes:

```json
{
  "profiles": {
    "default": {
      "thresholds": {"min_confidence": 0.55},
      "questions": {
        "category": {"criteria": {"learning": "courses, papers, cheat sheets, charts and figures"}},
        "topic": {"criteria": {"shopping": null, "recipes": "recipes and cooking"}}
      }
    },
    "strict": {"profile": "default", "thresholds": {"delete_min_prob": 0.95}}
  }
}
```

- A config entry named after a built-in (`default`, `photos`) tunes that built-in.
- `{"profile": "<name>", ...}` makes a tuned copy under a new name. It also works in
  `--profile-file` and in `.sortling.json`.
- New screenshot topics, photo categories and doc categories become folders
  (`screenshots/recipes/`). The four image kinds are fixed (you can reword them), and the
  catch-alls the sorting depends on (`other`, `unknown`) can't be removed. sortling
  reports which key is wrong.
- Command-line threshold flags beat the profile's thresholds. Custom folder profiles can
  set `thresholds` too.

## For agents and scripts

`--json` prints the plan (every file, its folder, the probabilities and a per-folder
summary) as JSON on stdout. [SORTLING.md](SORTLING.md) is a guide to hand an agent: how
to run sortling with custom categories, read the output, and the safety rules
(dry run first, confirm, keep the undo command).

## Setting up models

`python3 sortling.py --setup` (or "⚙ set up models…" in the wizard) adds and manages
models interactively:

- **Pick a kind:** a local decision model (llama.cpp / llama-swap), TypeSafe Jev, a
  local chat model (llama-swap, Ollama, LM Studio, vLLM), a hosted chat model (OpenAI,
  OpenRouter, any OpenAI-compatible API), or a hybrid of two you already have.
- **Pick the model from a list:** sortling asks the server's `/v1/models`, so you
  choose instead of typing ids. Behind llama-swap it finds the right
  `/upstream/<model>/v1/systemone` address by testing it.
- **Test, save, make default:** each model is tried before it's saved. Built-in
  entries you don't use can be hidden.
- **Keys stay in environment variables:** setup asks for the variable's name
  (`LLAMA_SWAP_API_KEY`, `OPENAI_API_KEY`…), never the key.

## Config

`~/.config/sortling/config.json` (or `$XDG_CONFIG_HOME`). Create a starter file with
`python3 sortling.py --init-config`; `--list-backends` shows what's configured. API keys
are **never** stored in the config, only the name of the environment variable that
holds each one:

```json
{
  "default_backend": "local-d3",
  "backends": {
    "local-d3":  {"type": "systemone", "url": "http://localhost:8080/upstream/vllm-d3-flash/v1/systemone", "api_key_env": "LLAMA_SWAP_API_KEY"},
    "jev":       {"type": "systemone", "url": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest", "api_key_env": "TYPESAFE_API_KEY", "vision": false},
    "local-llm": {"type": "openai", "url": "http://localhost:8080/v1", "model": "your-vision-model", "api_key_env": "LLAMA_SWAP_API_KEY"}
  },
  "profiles": {
    "my-folders": {"folders": {"money": "receipts, invoices, bills", "school": "course notes and lectures"}}
  }
}
```

- `type`: `systemone` (⚡) or `openai` (🐢, `url` is the `/v1` base).
- `vision: false`: text-only models. Images are then sorted by filename keywords only,
  never sent to a model that can't see them. Hosted Jev is text-only.
- `hosted`: auto-detected from the URL (anything not localhost or a private address).
  Hosted backends get an upload warning in interactive mode.

Local decision models run on llama.cpp: any GGUF whose metadata has
`<arch>.decision.type` (d3, Clef, d1, Kev…), served with `--mmproj` for images.

## Scripted use

```bash
python3 sortling.py --root ~/Downloads                         # dry run, default backend
python3 sortling.py --root ~/Downloads --backend local-clef --apply
python3 sortling.py --root ~/Pictures/Screenshots --types image --profile my-folders
python3 sortling.py --undo ~/Downloads/_sortling/sortling_manifest_<stamp>.json
```

Useful flags:

- `--apply`: move files (default is a dry run that only writes the manifest)
- `--undo MANIFEST`: move an applied run's files back (deleted files can't return)
- `--types pdf,image`, `--recursive-source`
- `--profile NAME`, `--profile-file JSON|-`, `--backend NAME`, `--second-pass NAME`, `--url URL`
- `--json`: the plan as JSON on stdout
- `--min-confidence` (0.6), `--screenshot-min-confidence` (0.4), `--delete-min-prob` (0.85):
  override the profile's thresholds for one run
- `--show-profile [NAME]`: print a profile's questions, options and thresholds as JSON
- `--delete-exact-duplicates`, `--hard-delete-delete-candidates`: real deletes, off by default
- `--disable-llm`: filename keywords only
- `--setup`: add, test and choose models, then exit
- `--vision-mode off|auto|always`, `--timeout-s`, `--no-progress`, `--no-banner`, `--no-face`
- `SORTLING_MENUS=numbers`: numbered prompts instead of arrow-key menus (automatic
  when input isn't a terminal)

If a model is unreachable, rejects the key, or keeps timing out, sortling falls back to
filename keywords and records why in `PRECHECK` and in each record's `classifier`.

## Upgrading from folderal

- **Command rename:** the script is now `sortling.py`. `--ollama-url`, `--model` and
  `--strict-model` are gone; Ollama works as a 🐢 `openai` backend (`url:
  http://localhost:11434/v1`).
- **Old folders are safe:** the numbered folders (`10_Personal_Keep` … `99_Duplicates`)
  are still recognized as already-sorted, so they're never re-processed. Moving them into
  the new layout is manual.
- **Manifests have moved:** they're now in `_sortling/` (was `_pdf_ai_logs/`).

## Tests

Labelled fixtures check that a change to prompts, categories, thresholds or models doesn't
quietly misfile things. The images are generated, not committed:

```bash
tests/make_fixtures.sh                              # builds tests/fixtures/ (ImageMagick, DejaVu fonts, curl)
python3 tests/eval.py --backend local-d3 --backend local-clef --misses
```

There are three sets: documents and screenshots, the same files with neutral names
(so filenames can't give the answer away), and photos with memes and junk.
Expected folders are in `tests/expected.json`.

## Art

The pixel wizard lives in [`assets/`](assets/):

- **`poses/`:** `sortling_idle`, `sortling_thinking`, `sortling_sorting`, `sortling_moving`,
  `sortling_sleeping`, `sortling_overwhelmed`, `sortling_success`, `sortling_deleting`,
  `sortling_clearing`.
- **`faces/`:** 16 expression heads: smile, happy, surprised, confused, nerd, content,
  love, sleepy, dizzy, sad, angry, cool, searching, crying, starstruck, wink.
- **`character-sheet.png`:** the original concept sheet.

<p align="center">
  <img src="assets/poses/sortling_thinking.png" alt="the wizard reading a scroll, puzzled" width="150">
  <img src="assets/poses/sortling_moving.png" alt="the wizard carrying a stack of colored crates" width="150">
  <img src="assets/poses/sortling_sleeping.png" alt="the wizard asleep against a pile of boxes" width="190">
  <img src="assets/poses/sortling_success.png" alt="the wizard cheering beside a folder with a check mark" width="190">
</p>

The terminal face (from `assets/faces/head-*.png`, 28×23 px) and the banner hero (from
`assets/poses/sortling_idle.png`, 38 px wide) are packed into `sortling.py` by
`python3 tools/encode_faces.py` (needs ImageMagick; about 8 KB together). The art is
area-averaged when shrunk, so eyes and mouths don't lose pixels. Re-run it
after changing the heads; `SORTLING_FACE=force` draws the face even without a terminal,
for checking.
