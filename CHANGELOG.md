# Changelog

## 0.3.0 (2026-10-12)

Sorts every file type, reads PDFs with nothing installed, and is more accurate on real
screenshots and photos.

**New**

- **Every file type.** Office and text documents (docx, pptx, xlsx, odt/odp/ods, rtf, txt,
  md) have their text read and get the same questions as PDFs, so a Word invoice lands in
  `docs/finance`. Everything else is sorted by extension into `files/` (`archives`,
  `installers`, `audio`, `video`, `ebooks`, `fonts`, `code`, `data`, `documents`, `3d`,
  `torrents`) without asking a model. Unknown extensions, hidden files and unfinished
  downloads (`.crdownload`, `.part`) are left alone. The `photos` profile still only sorts
  pictures and PDFs. `--types` takes `pdf,image,doc,file` (default: all).
- **A built-in PDF reader.** Without `pypdf` or `pdftotext` (a stock Mac, for example),
  sortling reads PDFs itself: it unpacks compressed text, walks the pages in order and
  decodes each font's character map. It handles LaTeX, Adobe and browser "Save as PDF"
  output.
- **Profiles as data.** The built-in prompts, options, thresholds and extension rules are
  plain JSON-style data. Tune them from your config or a folder's `.sortling.json` by
  writing only the keys you change. `--show-profile` prints exactly what the model is
  asked.
- **Per-file timing.** Each record has the model's time, and the manifest has a latency
  summary. When a file takes far longer than usual, sortling says the model may be
  reloading on a shared server.

**More accurate**

- **Records.** Images get a direct "is this a record worth keeping?" question, so an
  order-confirmation or ticket screenshot is filed as a document. Models had been calling
  it just "a screenshot".
- **Dark and blurry photos.** A direct "is this photo unusable?" question sends them to
  `_delete`. The junk question scored an accidental dark shot only 0.46; this one scores
  0.82-0.90, and good photos at most 0.04.
- **Screenshot topics.** The confidence needed for a topic folder dropped from 0.4 to 0.3.
  On real screenshots the right topic often wins with only 0.33-0.37.
- **Charts, figures and cheat sheets** are filed as documents, not memes.

On the test sets (85 files: 70 synthetic ones in `tests/` plus 15 real screenshots kept
out of the repo) Clef-Flash gets 85/85 and
d3-flash 82/85. Before the record, unusable-photo and threshold changes, the same models
scored 60/65 and 59/65 on the 65 files that existed then.

**Fixes**

- **Missing files:** a file that vanishes mid-run (a download finishing, a sync) is
  skipped instead of crashing the run.
- **Tailscale:** Tailscale and CGNAT addresses (and `*.ts.net`) count as local, so your
  own server no longer gets an upload warning.
- **PDF warning:** a one-line warning when scanned PDFs can't be rendered (no PyMuPDF,
  `pdftoppm` or `qlmanage`).
- **Speed:** files over 64 MB are fingerprinted from their size and their first and last
  8 MB for the duplicate check, so big videos and installers aren't hashed in full.
- **Tests:** `tests/eval.py` runs one backend at a time, so a single-GPU server doesn't
  reload models between test sets.

## 0.2.0 (2026-10-12)

The first sortling, renamed from folderal: one standard-library Python file with a
wizard (arrow-key menus, model setup, the banner and the Doom-guy face), ⚡ fast decision
models, 🐢 thorough chat models and ⚡🐢 hybrid, the `default` and `photos` profiles plus
custom ones, `--json` and `SORTLING.md` for agents, and `--undo`.
