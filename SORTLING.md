# SORTLING.md: instructions for agents

You can use `sortling.py` to sort a folder of files (PDFs, images, Office and text documents, and
anything else by extension) into folders with
a local or hosted classification model. Read this whole file before running it. The
script is one Python 3.9+ file with no dependencies; no install step is needed.

## Rules

1. **Dry run first, always.** Run without `--apply`, show the user the plan, and get an
   explicit yes before running again with `--apply`.
2. **Never pass `--delete-exact-duplicates` or `--hard-delete-delete-candidates`** unless
   the user asks for real deletion in those words. By default nothing is deleted:
   junk moves to `_delete/` and duplicates to `_duplicates/`.
3. **Keep the undo command.** Every applied run prints
   `UNDO: python3 sortling.py --undo <manifest.json>`; give it to the user.
4. **Don't send files to a hosted model without permission.** Backends with hosted
   URLs upload file text and images. Check with `--list-backends` (☁ means hosted); local
   ones are marked ⌂.
5. **Don't open or describe the user's files yourself** to "help" the model. sortling
   reads them; you read its JSON.

## Run it

```bash
python3 sortling.py --root <folder> --json --no-progress                 # plan, JSON on stdout
python3 sortling.py --root <folder> --json --no-progress --apply         # after the user agrees
python3 sortling.py --undo <manifest.json>                               # put everything back
```

- **Pick files:** `--types pdf,image,doc,file` (default all: `doc` = Office/text files that are read,
  `file` = sorted by extension without a model), `--recursive-source` (subfolders too).
  Hidden files, unknown extensions and unfinished downloads are never touched.
- **Pick a profile:** `--profile default|photos|<saved name>`, or
  `--profile-file <file.json>` (`-` reads stdin).
- **Pick a backend:** `--backend <name>` (see `--list-backends`). Add `--second-pass <name>`
  for a hybrid run.
- **Thresholds** (rarely needed): `--min-confidence 0.6`, `--screenshot-min-confidence 0.3`,
  `--delete-min-prob 0.85`. These override the profile's own thresholds.
- **See what the model is asked:** `--show-profile [name]` prints the profile as JSON.

Exit code 0 is success, 2 is a usage or config error. If no model is reachable, sortling
still runs on filename keywords; `classifier` in the output says so. Tell the user
rather than presenting keyword results as model results.

## Custom categories

Write the categories as JSON and pipe them in. Each folder needs a description, and the
description is what the model reads, so make it concrete ("receipts, invoices, bank
statements", not "money stuff"):

```bash
cat <<'EOF' | python3 sortling.py --root ~/Downloads --profile-file - --json --no-progress
{
  "name": "inbox",
  "folders": {
    "vibes":        "general memes, aesthetic illustrations, internet culture, quotes",
    "ai-memes":     "memes about AI models, agents, prompt engineering, AI labs",
    "infographics": "architecture diagrams, benchmarks, cost charts, model timelines",
    "nota":         "personal documents, receipts, private photos"
  }
}
EOF
```

- At least 2 folders. Names are lowercased and slugified (`AI Memes` becomes `ai-memes`).
- Optional `"extensions": {"stl": "3d-prints"}` sorts files by extension into your folders
  without asking the model. Other extension-only files are left alone.
- Reserved names: `_review`, `_delete`, `_duplicates`, `none`.
- A file goes to `_review/` when no folder clearly fits (probability below 0.6), and to
  `_delete/` only when the model is at least 85% sure it's junk.
- To make a folder always sort the same way, save the JSON as `<folder>/.sortling.json`.
  It's used automatically when no `--profile` or `--profile-file` is given.
  `{"profile": "photos"}` there selects a built-in profile. Extra keys tune it, e.g.
  `{"profile": "default", "thresholds": {"min_confidence": 0.5}}` (see the README).

## Built-in profiles

- `default`: `docs/<personal|medical|legal|finance|work|travel|learning>`,
  `screenshots/<code|chats|reading|apps|social|shopping|maps|media|other>`,
  `photos/<people|places|food|pets|events|other>`, `memes/`.
- `photos`: for camera rolls and chat-app dumps. `photos/<category>`, `memes/`,
  `screenshots/`, `docs/`. Fewer questions, so faster.

## Reading the JSON

```json
{
  "mode": "dry_run",
  "root": "/home/me/Downloads",
  "profile": "inbox",
  "classifier": "fast:local-d3",
  "second_pass": "",
  "summary": {"files": 12, "by_folder": {"ai-memes": 4, "vibes": 6, "_review": 2}},
  "manifest_json": "/home/me/Downloads/_sortling/sortling_manifest_20261012_151500.json",
  "records": [
    {
      "file": "claude-rate-limit.png",
      "kind": "image",
      "target_folder": "ai-memes",
      "action": "MOVE",
      "confidence": 0.93,
      "classifier": "fast:local-d3",
      "reason": "decision:ai-memes=0.93,junk=0.04 | vision:file",
      "duplicate_of": "",
      "second_pass": "",
      "result": "DRY_RUN"
    }
  ]
}
```

- **`target_folder`** is where the file goes, relative to `root`. `action` is `MOVE`,
  `MOVE_TO_DUPLICATES`, or one of the delete actions (only if you passed the delete flags).
- **`confidence`** is a real probability for ⚡ fast backends, and self-reported for
  🐢 thorough ones. Treat 🐢 numbers as rough.
- **`reason`** shows every probability the model gave. With a thorough backend it
  ends with a one-line description of the file.
- **`classifier`** starting with `fallback:` means no model answered: filename keywords only.
- **After `--apply`,** `result` is `MOVED:<new relative path>`, `DELETED` or `ERROR`.

When summarizing for the user, give the counts per folder, list the `_review` files,
and mention anything going to `_delete`.

## Using it as a step in a bigger workflow

If your workflow has its own destinations (a site's gallery, a data file, a CMS),
use sortling for the **classification only**:

- **Run the dry run** with your categories as a custom profile.
- **Read `records[].target_folder`** as the label for each file.
- **Do your own moves or imports** and skip `--apply`.

The dry run moves nothing and writes only the manifest under `<root>/_sortling/`
(override with `--output-dir`).
