# Folderal

Local-first AI organizer for personal files.

Current scope: PDF organization with content extraction, optional first-page vision context, Ollama-based classification, duplicate handling, and safe dry-run/apply modes.

## Features

- Content-aware PDF classification (not filename-only)
- Optional vision snapshot (`--vision-mode auto|always|off`)
- Exact duplicate detection + handling
- Progress bars across pipeline stages
- Resilient fallback behavior when LLM/tools are unavailable
- Dry-run logs (`CSV` + `JSON`)

## Quick start

```bash
cd /Users/kchauhan/repos/folderal
python3 folderal.py --root /Users/kchauhan/Downloads/paperless-init/PDFs --vision-mode auto --write-review-note
```

Apply changes:

```bash
python3 folderal.py --root /Users/kchauhan/Downloads/paperless-init/PDFs --apply --delete-exact-duplicates --vision-mode auto --write-review-note
```

## Common flags

- `--apply`: execute moves/deletes (default is dry-run)
- `--delete-exact-duplicates`: delete exact duplicate copies
- `--hard-delete-delete-candidates`: hard-delete low-value docs (instead of moving to `98_Delete`)
- `--disable-llm`: skip Ollama and use fallback keyword classifier
- `--model`: set preferred Ollama model
- `--strict-model`: do not auto-fallback to another available model
- `--timeout-s`: Ollama timeout
- `--retry-timeouts`: per-file timeout retries
- `--max-consecutive-timeouts`: disable LLM globally after repeated timeouts
- `--no-progress`: disable progress bars

## Compatibility

The old script path in `paperless-init/PDFs/pdf_ai_organizer.py` can call this repo script via a wrapper, so existing commands continue to work.
