#!/usr/bin/env python3
"""
Folderal: local-first AI file organizer.

Current implementation focuses on PDF organization with:
- content extraction + optional vision snapshots
- Ollama classification with safe fallbacks
- duplicate detection, dry-run/apply modes, and progress bars
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


TARGET_FOLDERS: Tuple[str, ...] = (
    "10_Personal_Keep",
    "20_Finance_Keep",
    "30_Work_Archive",
    "40_Learning_Core",
    "41_Learning_Archive",
    "90_Review",
    "98_Delete",
    "99_Duplicates",
)

STANDARDIZED_CATEGORIES: Tuple[str, ...] = (
    "personal",
    "medical",
    "finance",
    "work",
    "learning",
    "travel",
    "legal",
    "spam",
    "unknown",
)

KEEP_DECISIONS: Tuple[str, ...] = (
    "keep",
    "archive",
    "review",
    "delete_candidate",
)


CATEGORY_ALIASES: Dict[str, str] = {
    "personal_records": "personal",
    "identity": "personal",
    "health": "medical",
    "medical_records": "medical",
    "financial": "finance",
    "banking": "finance",
    "career": "work",
    "employment": "work",
    "education": "learning",
    "study": "learning",
    "learning_reference": "learning",
    "property": "legal",
    "legal_property": "legal",
    "junk": "spam",
    "marketing": "spam",
    "unclear": "unknown",
    "misc": "unknown",
}

DECISION_ALIASES: Dict[str, str] = {
    "keep_archive": "archive",
    "archive_keep": "archive",
    "delete": "delete_candidate",
    "remove": "delete_candidate",
    "maybe_delete": "delete_candidate",
    "manual_review": "review",
    "needs_review": "review",
}

FOLDER_ALIASES: Dict[str, str] = {
    "personal": "10_Personal_Keep",
    "finance": "20_Finance_Keep",
    "work": "30_Work_Archive",
    "learning_core": "40_Learning_Core",
    "learning_archive": "41_Learning_Archive",
    "review": "90_Review",
    "delete": "98_Delete",
    "duplicates": "99_Duplicates",
}


@dataclass
class DocRecord:
    file: str
    source_relpath: str
    sha1: str
    duplicate_of: str
    extractor: str
    text_chars: int
    used_vision: bool
    llm_status: str
    standardized_category: str
    keep_decision: str
    target_folder: str
    confidence: float
    reason: str
    action: str
    result: str = ""
    error: str = ""
    # Enhanced metadata fields
    file_size: int = 0
    page_count: int = 0
    modified_time: str = ""
    created_time: str = ""


class ProgressBar:
    def __init__(
        self,
        total: int,
        label: str,
        enabled: bool = True,
        width: int = 30,
        stream=None,
    ) -> None:
        self.total = max(int(total), 1)
        self.label = label
        self.enabled = enabled and total > 0
        self.width = max(10, int(width))
        self.stream = stream or sys.stderr
        self.dynamic = bool(
            getattr(self.stream, "isatty", lambda: False)()
            and os.environ.get("TERM", "").lower() not in {"", "dumb"}
        )
        self._last_non_dynamic_done = -1
        if self.enabled:
            self.update(0, "")

    def _format(self, done: int, detail: str = "") -> str:
        ratio = min(max(done / self.total, 0.0), 1.0)
        filled = int(self.width * ratio)
        bar = "#" * filled + "-" * (self.width - filled)
        msg = f"{self.label} [{bar}] {done}/{self.total} {ratio * 100:5.1f}%"
        if detail:
            msg += f" | {detail[:52]}"
        return msg

    def update(self, done: int, detail: str = "") -> None:
        if not self.enabled:
            return
        done = min(max(int(done), 0), self.total)
        msg = self._format(done, detail)
        if self.dynamic:
            self.stream.write("\r" + msg)
            self.stream.flush()
            return

        step = max(1, self.total // 20)
        if done in {0, self.total} or done - self._last_non_dynamic_done >= step:
            self.stream.write(msg + "\n")
            self.stream.flush()
            self._last_non_dynamic_done = done

    def finish(self, detail: str = "done") -> None:
        if not self.enabled:
            return
        self.update(self.total, detail)
        if self.dynamic:
            self.stream.write("\n")
            self.stream.flush()


def stage_note(message: str, enabled: bool) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extract_ascii_pdf_strings(path: Path, max_chars: int) -> Tuple[str, str]:
    try:
        raw = path.read_bytes()[: 6 * 1024 * 1024]
    except Exception as exc:
        return "", f"ascii-scan:{exc}"

    matches = re.findall(rb"[ -~]{6,}", raw)
    if not matches:
        return "", "ascii-scan:no-text"

    out: List[str] = []
    total = 0
    for m in matches:
        s = m.decode("ascii", errors="ignore").strip()
        if not s:
            continue
        out.append(s)
        total += len(s) + 1
        if total >= max_chars:
            break

    joined = "\n".join(out).strip()
    if not joined:
        return "", "ascii-scan:no-text"
    return joined[:max_chars], "ascii-scan"


def list_source_pdfs(root: Path, recursive_source: bool) -> List[Path]:
    if recursive_source:
        return sorted(
            p
            for p in root.rglob("*.pdf")
            if p.is_file() and not is_in_target_folder(root, p)
        )
    return sorted(
        p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )


def is_in_target_folder(root: Path, file_path: Path) -> bool:
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] in TARGET_FOLDERS


def all_pdfs_for_duplicate_scan(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def file_penalty(
    path: Path, source_set: set[Path], root: Path
) -> Tuple[int, int, int, int, str]:
    rel = str(path.relative_to(root))
    n = path.name.lower()
    in_source = 1 if path in source_set else 0
    in_deleteish = (
        1 if rel.startswith("98_Delete/") or rel.startswith("99_Duplicates/") else 0
    )
    copyish = 1 if (" copy" in n or re.search(r"\(\d+\)\.pdf$", n) is not None) else 0
    numericish = 1 if re.match(r"^\d{7,}", n) else 0
    return (in_source, in_deleteish, copyish + numericish, len(n), n)


def build_duplicate_lookup(
    root: Path,
    source_files: Sequence[Path],
    show_progress: bool = False,
    progress_width: int = 30,
) -> Dict[Path, Path]:
    source_set = set(source_files)
    hash_groups: Dict[str, List[Path]] = {}
    scan_files = all_pdfs_for_duplicate_scan(root)
    progress = ProgressBar(
        total=len(scan_files),
        label="Hash scan",
        enabled=show_progress,
        width=progress_width,
    )
    for idx, p in enumerate(scan_files, start=1):
        digest = sha1_file(p)
        hash_groups.setdefault(digest, []).append(p)
        progress.update(idx, p.name)
    progress.finish("hash complete")

    duplicate_of: Dict[Path, Path] = {}
    for _, group in hash_groups.items():
        if len(group) < 2:
            continue
        primary = sorted(group, key=lambda p: file_penalty(p, source_set, root))[0]
        for p in group:
            if p != primary and p in source_set:
                duplicate_of[p] = primary
    return duplicate_of


def extract_text_from_pdf(
    path: Path, max_pages: int, max_chars: int
) -> Tuple[str, str]:
    errors: List[str] = []

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        chunks: List[str] = []
        for i in range(min(len(reader.pages), max_pages)):
            text = reader.pages[i].extract_text() or ""
            if text.strip():
                chunks.append(text.strip())
            joined = "\n\n".join(chunks)
            if len(joined) >= max_chars:
                return joined[:max_chars], "pypdf"
        joined = "\n\n".join(chunks)
        if joined.strip():
            return joined[:max_chars], "pypdf"
        errors.append("pypdf:no-text")
    except ImportError:
        errors.append("pypdf:missing")
    except Exception as exc:
        errors.append(f"pypdf:{exc}")

    try:
        from PyPDF2 import PdfReader as PyPdf2Reader  # type: ignore

        reader = PyPdf2Reader(str(path))
        chunks = []
        for i in range(min(len(reader.pages), max_pages)):
            text = reader.pages[i].extract_text() or ""
            if text.strip():
                chunks.append(text.strip())
            joined = "\n\n".join(chunks)
            if len(joined) >= max_chars:
                return joined[:max_chars], "PyPDF2"
        joined = "\n\n".join(chunks)
        if joined.strip():
            return joined[:max_chars], "PyPDF2"
        errors.append("PyPDF2:no-text")
    except ImportError:
        errors.append("PyPDF2:missing")
    except Exception as exc:
        errors.append(f"PyPDF2:{exc}")

    if shutil.which("pdftotext"):
        try:
            cmd = [
                "pdftotext",
                "-f",
                "1",
                "-l",
                str(max_pages),
                "-layout",
                str(path),
                "-",
            ]
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout[:max_chars], "pdftotext"
            errors.append(f"pdftotext:rc{proc.returncode}")
        except Exception as exc:
            errors.append(f"pdftotext:{exc}")
    else:
        errors.append("pdftotext:missing")

    text, source = extract_ascii_pdf_strings(path, max_chars=max_chars)
    if text.strip():
        return text[:max_chars], source
    errors.append(source)

    return "", ";".join(errors)


def render_first_page_png_base64(
    path: Path, max_dim: int = 1100
) -> Tuple[Optional[str], str]:
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(path))
        if doc.page_count < 1:
            doc.close()
            return None, "fitz:no-pages"
        page = doc.load_page(0)
        rect = page.rect
        width = max(rect.width, 1.0)
        height = max(rect.height, 1.0)
        scale = min(max_dim / width, max_dim / height)
        scale = max(0.7, min(scale, 2.0))
        matrix = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        raw = pix.tobytes("png")
        doc.close()
        return base64.b64encode(raw).decode("ascii"), "fitz"
    except ImportError:
        pass
    except Exception as exc:
        return None, f"fitz:{exc}"

    if shutil.which("qlmanage"):
        try:
            with tempfile.TemporaryDirectory(prefix="pdf_preview_") as tmp:
                cmd = [
                    "qlmanage",
                    "-t",
                    "-s",
                    str(max_dim),
                    "-o",
                    tmp,
                    str(path),
                ]
                proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
                if proc.returncode != 0:
                    return None, f"qlmanage:rc{proc.returncode}"
                preview_files = sorted(Path(tmp).glob("*.png"))
                if not preview_files:
                    return None, "qlmanage:no-preview"
                raw = preview_files[0].read_bytes()
                return base64.b64encode(raw).decode("ascii"), "qlmanage"
        except Exception as exc:
            return None, f"qlmanage:{exc}"

    return None, "fitz:missing;qlmanage:missing"


def build_system_prompt() -> str:
    categories = ", ".join(STANDARDIZED_CATEGORIES)
    decisions = ", ".join(KEEP_DECISIONS)
    folders = ", ".join(TARGET_FOLDERS[:-1])
    return (
        "You classify personal document PDFs for archival organization.\n"
        "Output ONLY strict JSON with this schema:\n"
        "{\n"
        '  "standardized_category": "<category>",\n'
        '  "keep_decision": "<decision>",\n'
        '  "target_folder": "<folder>",\n'
        '  "confidence": <0_to_1_float>,\n'
        '  "reason": "<max 120 chars>"\n'
        "}\n"
        f"Allowed standardized_category: {categories}\n"
        f"Allowed keep_decision: {decisions}\n"
        f"Allowed target_folder: {folders}\n"
        "Rules:\n"
        "- Prefer review when uncertain.\n"
        "- Use delete_candidate only for obvious junk, spam, or duplicate-like low-value docs.\n"
        "- Medical, legal, identity, employment, financial statements should usually be keep/archive.\n"
        "- Learning/reference docs can be core or archive depending on long-term value.\n"
    )


def build_user_prompt(filename: str, text_excerpt: str) -> str:
    clipped = text_excerpt[:7000] if text_excerpt else ""
    return (
        f"Filename: {filename}\n"
        "Task: categorize this PDF for personal document organization.\n"
        "Text excerpt (possibly OCR/extraction noisy):\n"
        f"{clipped if clipped else '[no extracted text available]'}"
    )


def ollama_chat(
    ollama_url: str,
    model: str,
    messages: List[dict],
    timeout_s: int,
    temperature: float = 0.1,
) -> str:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": messages,
        "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data.get("message", {}).get("content", "")
    if not isinstance(content, str):
        raise ValueError("Ollama response did not contain string content")
    return content


def fetch_ollama_models(ollama_url: str, timeout_s: int) -> List[str]:
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/tags",
        data=None,
        headers={"Content-Type": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    names: List[str] = []
    for item in data.get("models", []):
        name = str(item.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def choose_best_available_model(names: Sequence[str], prefer_vision: bool) -> str:
    if not names:
        raise ValueError("no models available")
    if not prefer_vision:
        return names[0]
    hints = ("vl", "vision", "llava", "qwen2.5vl", "minicpm-v", "gemma3")
    lowered = [(n, n.lower()) for n in names]
    for original, low in lowered:
        if any(h in low for h in hints):
            return original
    return names[0]


def is_timeout_like_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            return True
        if reason and "timed out" in str(reason).lower():
            return True
    txt = str(exc).lower()
    return "timed out" in txt or "timeout" in txt


def extract_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    fenced = re.search(
        r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found")
    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : idx + 1])
    raise ValueError("Unbalanced JSON object in model output")


def normalize_category(value: str) -> str:
    v = CATEGORY_ALIASES.get(
        (value or "").strip().lower(), (value or "").strip().lower()
    )
    return v if v in STANDARDIZED_CATEGORIES else "unknown"


def normalize_decision(value: str) -> str:
    v = DECISION_ALIASES.get(
        (value or "").strip().lower(), (value or "").strip().lower()
    )
    return v if v in KEEP_DECISIONS else "review"


def resolve_target_folder(category: str, decision: str, folder_hint: str) -> str:
    hint = FOLDER_ALIASES.get(
        (folder_hint or "").strip().lower(), (folder_hint or "").strip()
    )
    if hint in TARGET_FOLDERS:
        return hint
    if decision == "delete_candidate":
        return "98_Delete"
    if decision == "review":
        return "90_Review"
    if category in {"personal", "medical", "legal"}:
        return "10_Personal_Keep"
    if category == "finance":
        return "20_Finance_Keep"
    if category == "work":
        return "30_Work_Archive"
    if category == "learning":
        return "40_Learning_Core" if decision == "keep" else "41_Learning_Archive"
    return "90_Review"


def to_float_0_1(value: object) -> float:
    try:
        f = float(value)  # type: ignore
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


def classify_with_llm(
    ollama_url: str,
    model: str,
    timeout_s: int,
    filename: str,
    text_excerpt: str,
    image_b64: Optional[str],
) -> Tuple[str, str, str, float, str]:
    user_msg = {"role": "user", "content": build_user_prompt(filename, text_excerpt)}
    if image_b64:
        user_msg["images"] = [image_b64]
    raw = ollama_chat(
        ollama_url=ollama_url,
        model=model,
        timeout_s=timeout_s,
        messages=[{"role": "system", "content": build_system_prompt()}, user_msg],
    )
    parsed = extract_json_object(raw)
    category = normalize_category(str(parsed.get("standardized_category", "")))
    decision = normalize_decision(str(parsed.get("keep_decision", "")))
    folder = resolve_target_folder(
        category, decision, str(parsed.get("target_folder", ""))
    )
    confidence = to_float_0_1(parsed.get("confidence"))
    reason = str(parsed.get("reason", "")).strip()[:200] or "llm-classification"
    return category, decision, folder, confidence, reason


def fallback_classify(
    filename: str, text_excerpt: str
) -> Tuple[str, str, str, float, str]:
    hay = f"{filename} {text_excerpt}".lower()

    def has(*tokens: str) -> bool:
        return any(tok in hay for tok in tokens)

    if has("congrats on winning", "contest", "promo", "unsubscribe", "marketing"):
        return ("spam", "delete_candidate", "98_Delete", 0.65, "keyword:spam")
    if has("study permit", "passport", "oci", "visa", "pr card"):
        return ("personal", "keep", "10_Personal_Keep", 0.78, "keyword:identity")
    if has(
        "invoice", "statement", "receipt", "tangerine", "cibc", "tax", "w8", "payment"
    ):
        return ("finance", "keep", "20_Finance_Keep", 0.73, "keyword:finance")
    if has("resume", "employment", "offer letter", "reference check", "certificate"):
        return ("personal", "keep", "10_Personal_Keep", 0.74, "keyword:personal")
    if has("blood report", "prescription", "health", "medical", "lab"):
        return ("medical", "keep", "10_Personal_Keep", 0.75, "keyword:medical")
    if has("oracle", "supply chain", "ebs", "oaf", "doc id", "plsql"):
        return ("work", "archive", "30_Work_Archive", 0.72, "keyword:work")
    if has(
        "lecture", "cheat sheet", "numpy", "pandas", "machine learning", "deep learning"
    ):
        return ("learning", "archive", "41_Learning_Archive", 0.71, "keyword:learning")
    if has("flight", "booking", "ticket", "map", "transit"):
        return ("travel", "review", "90_Review", 0.61, "keyword:travel")
    return ("unknown", "review", "90_Review", 0.35, "fallback:uncertain")


def quick_filename_check(filename: str) -> Tuple[str, str, str, float, str]:
    """
    Quick filename-based classification to avoid expensive processing
    Returns: (category, decision, folder, confidence, reason)
    """
    filename_lower = filename.lower()

    # Obvious spam/junk - high confidence
    if any(
        indicator in filename_lower
        for indicator in ["spam", "promo", "unsubscribe", "advertisement"]
    ):
        return ("spam", "delete_candidate", "98_Delete", 0.9, "filename:spam-indicator")

    # Obvious personal/identity documents
    if any(
        indicator in filename_lower
        for indicator in [
            "passport",
            "visa",
            "birth certificate",
            "marriage certificate",
            "social security",
        ]
    ):
        return ("personal", "keep", "10_Personal_Keep", 0.85, "filename:identity-doc")

    # Obvious financial documents
    if any(
        indicator in filename_lower
        for indicator in ["invoice", "statement", "receipt", "tax", "bill", "payment"]
    ):
        return ("finance", "keep", "20_Finance_Keep", 0.8, "filename:financial-doc")

    # Obvious work documents
    if any(
        indicator in filename_lower
        for indicator in ["resume", "cv", "contract", "agreement", "proposal", "report"]
    ):
        # Check if it's more likely personal resume vs work report
        if any(pers in filename_lower for pers in ["resume", "cv"]):
            return ("personal", "keep", "10_Personal_Keep", 0.75, "filename:resume")
        else:
            return ("work", "archive", "30_Work_Archive", 0.75, "filename:work-doc")

    # Obvious medical documents
    if any(
        indicator in filename_lower
        for indicator in [
            "prescription",
            "medical",
            "health",
            "hospital",
            "clinic",
            "doctor",
        ]
    ):
        return ("medical", "keep", "10_Personal_Keep", 0.8, "filename:medical-doc")

    # Obvious learning documents
    if any(
        indicator in filename_lower
        for indicator in [
            "lecture",
            "notes",
            "homework",
            "assignment",
            "syllabus",
            "textbook",
        ]
    ):
        return (
            "learning",
            "archive",
            "41_Learning_Archive",
            0.75,
            "filename:learning-doc",
        )

    # Low confidence - return unknown to proceed with normal processing
    return ("unknown", "review", "90_Review", 0.3, "filename:no-clear-indicators")


def ensure_target_dirs(root: Path) -> None:
    for folder in TARGET_FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)


def unique_destination(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def assign_action(
    record: DocRecord,
    delete_exact_duplicates: bool,
    hard_delete_delete_candidates: bool,
) -> None:
    if record.duplicate_of:
        if delete_exact_duplicates:
            record.action = "DELETE_EXACT_DUPLICATE"
        else:
            record.target_folder = "99_Duplicates"
            record.action = "MOVE_TO_DUPLICATES"
        return
    if record.keep_decision == "delete_candidate" and hard_delete_delete_candidates:
        record.action = "DELETE_CANDIDATE"
        return
    record.action = "MOVE"


def apply_record(root: Path, rec: DocRecord) -> None:
    src = root / rec.source_relpath
    if not src.exists():
        rec.error = "source-missing"
        rec.result = "ERROR"
        return
    if rec.action in {"DELETE_EXACT_DUPLICATE", "DELETE_CANDIDATE"}:
        src.unlink()
        rec.result = "DELETED"
        return
    dest_dir = root / rec.target_folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_destination(dest_dir / src.name)
    shutil.move(str(src), str(dest))
    rec.result = f"MOVED:{dest.relative_to(root)}"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_logs(
    output_dir: Path, records: Sequence[DocRecord], dry_run: bool
) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    csv_path = output_dir / f"pdf_ai_manifest_{stamp}.csv"
    json_path = output_dir / f"pdf_ai_manifest_{stamp}.json"
    fieldnames = (
        list(asdict(records[0]).keys())
        if records
        else list(
            asdict(
                DocRecord(
                    file="",
                    source_relpath="",
                    sha1="",
                    duplicate_of="",
                    extractor="",
                    text_chars=0,
                    used_vision=False,
                    llm_status="",
                    standardized_category="",
                    keep_decision="",
                    target_folder="",
                    confidence=0.0,
                    reason="",
                    action="",
                    file_size=0,
                    page_count=0,
                    modified_time="",
                    created_time="",
                )
            ).keys()
        )
    )
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            writer.writerow(asdict(rec))
    payload = {
        "generated_at": datetime.now().isoformat(),
        "mode": "dry_run" if dry_run else "apply",
        "records": [asdict(r) for r in records],
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return csv_path, json_path


def maybe_write_review_note(
    root: Path, records: Sequence[DocRecord], apply: bool
) -> Optional[Path]:
    review_dir = root / "90_Review"
    review_dir.mkdir(parents=True, exist_ok=True)
    note = review_dir / "REVIEW_LIST.txt"
    if apply:
        files = sorted(p.name for p in review_dir.glob("*.pdf"))
        lines = [
            "Manual review list (current snapshot)",
            f"Total files in 90_Review: {len(files)}",
            "",
        ]
        lines.extend(f"- {name}" for name in files)
    else:
        candidates = sorted(
            rec.file for rec in records if rec.target_folder == "90_Review"
        )
        lines = [
            "Manual review list (dry-run projection)",
            f"Would route to 90_Review: {len(candidates)}",
            "",
        ]
        lines.extend(f"- {name}" for name in candidates)
    with note.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return note


def summarize(records: Sequence[DocRecord]) -> str:
    by_action: Dict[str, int] = {}
    by_folder: Dict[str, int] = {}
    by_llm: Dict[str, int] = {}
    for rec in records:
        by_action[rec.action] = by_action.get(rec.action, 0) + 1
        by_folder[rec.target_folder] = by_folder.get(rec.target_folder, 0) + 1
        by_llm[rec.llm_status] = by_llm.get(rec.llm_status, 0) + 1
    parts = ["Action counts:"]
    for k, v in sorted(by_action.items()):
        parts.append(f"- {k}: {v}")
    parts.append("Target folder counts:")
    for k, v in sorted(by_folder.items()):
        parts.append(f"- {k}: {v}")
    parts.append("Classifier source:")
    for k, v in sorted(by_llm.items()):
        parts.append(f"- {k}: {v}")
    return "\n".join(parts)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Folderal: AI PDF organizer with Ollama + optional vision"
    )
    p.add_argument(
        "--root", default=".", help="Root directory containing PDFs and target folders"
    )
    p.add_argument(
        "--apply", action="store_true", help="Apply moves/deletes. Default is dry-run"
    )
    p.add_argument(
        "--recursive-source",
        action="store_true",
        help="Process PDFs recursively outside target folders",
    )
    p.add_argument(
        "--delete-exact-duplicates",
        action="store_true",
        help="Delete exact duplicate copies",
    )
    p.add_argument(
        "--hard-delete-delete-candidates",
        action="store_true",
        help="Hard-delete delete_candidate files instead of moving to 98_Delete",
    )
    p.add_argument(
        "--ollama-url", default="http://localhost:11434", help="Ollama base URL"
    )
    p.add_argument("--model", default="qwen2.5:14b", help="Ollama model name")
    p.add_argument(
        "--disable-llm",
        action="store_true",
        help="Skip Ollama and use local fallback classifier",
    )
    p.add_argument(
        "--strict-model",
        action="store_true",
        help="Do not auto-switch model when unavailable",
    )
    p.add_argument(
        "--timeout-s", type=int, default=120, help="Ollama request timeout (seconds)"
    )
    p.add_argument(
        "--max-pages", type=int, default=3, help="Max PDF pages for text extraction"
    )
    p.add_argument(
        "--max-chars", type=int, default=7000, help="Max text chars sent to model"
    )
    p.add_argument(
        "--vision-mode",
        choices=("off", "auto", "always"),
        default="auto",
        help="Use first-page snapshot images in model prompt",
    )
    p.add_argument(
        "--vision-min-text-chars",
        type=int,
        default=400,
        help="In auto mode, use vision when text chars are below this threshold",
    )
    p.add_argument(
        "--output-dir", default="_pdf_ai_logs", help="Directory for manifest outputs"
    )
    p.add_argument(
        "--write-review-note",
        action="store_true",
        help="Update 90_Review/REVIEW_LIST.txt",
    )
    p.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars and stage logs",
    )
    p.add_argument("--progress-width", type=int, default=32, help="Progress bar width")
    p.add_argument(
        "--max-consecutive-timeouts",
        type=int,
        default=3,
        help="Disable LLM after this many consecutive timeout-like failures",
    )
    p.add_argument(
        "--retry-timeouts",
        type=int,
        default=1,
        help="Per-file retries when timeout-like LLM errors occur",
    )
    p.add_argument(
        "--retry-backoff-sec",
        type=float,
        default=1.2,
        help="Base backoff seconds before retrying timeout-like failures",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    progress_enabled = not args.no_progress
    if not root.exists() or not root.is_dir():
        print(f"error: invalid root directory: {root}", file=sys.stderr)
        return 2

    source_files = list_source_pdfs(root, recursive_source=args.recursive_source)
    if not source_files:
        print("No source PDFs found to process.")
        return 0

    stage_note("Stage 1/3: scanning duplicates", progress_enabled)
    effective_model = args.model
    llm_enabled = not args.disable_llm
    llm_global_status = "llm"
    consecutive_timeout_failures = 0
    retry_timeouts = max(0, int(args.retry_timeouts))
    timeout_cutoff = max(1, int(args.max_consecutive_timeouts))

    if llm_enabled:
        try:
            available_models = fetch_ollama_models(
                ollama_url=args.ollama_url,
                timeout_s=max(5, min(args.timeout_s, 15)),
            )
            if not available_models:
                llm_enabled = False
                llm_global_status = "fallback:ollama-no-models"
            elif effective_model not in available_models:
                if args.strict_model:
                    llm_enabled = False
                    llm_global_status = f"fallback:model-not-found:{effective_model}"
                else:
                    chosen = choose_best_available_model(
                        names=available_models,
                        prefer_vision=(args.vision_mode != "off"),
                    )
                    llm_global_status = f"model-fallback:{effective_model}->{chosen}"
                    effective_model = chosen
        except Exception as exc:
            llm_enabled = False
            llm_global_status = f"fallback:ollama-unreachable:{type(exc).__name__}"

    duplicate_of = build_duplicate_lookup(
        root=root,
        source_files=source_files,
        show_progress=progress_enabled,
        progress_width=args.progress_width,
    )
    records: List[DocRecord] = []
    stage_note("Stage 2/3: extracting + classifying PDFs", progress_enabled)
    classify_progress = ProgressBar(
        total=len(source_files),
        label="Classify",
        enabled=progress_enabled,
        width=args.progress_width,
    )

    for idx, path in enumerate(source_files, start=1):
        rel = str(path.relative_to(root))
        digest = sha1_file(path)
        dup_primary = (
            str(duplicate_of[path].relative_to(root)) if path in duplicate_of else ""
        )

        # Guard clause: quick filename-based checks to avoid expensive processing
        quick_category, quick_decision, quick_folder, quick_confidence, quick_reason = (
            quick_filename_check(path.name)
        )
        if quick_confidence > 0.8:  # High confidence quick check
            text_excerpt = ""  # Skip text extraction
            extractor = "quick-filename-check"
            text_chars = 0
            use_vision = False
            image_b64 = None
            vision_source = ""
            llm_status = f"quick-{quick_reason}"
            category, decision, folder, confidence, reason = (
                quick_category,
                quick_decision,
                quick_folder,
                quick_confidence,
                quick_reason,
            )
        else:
            # Proceed with normal processing
            text_excerpt, extractor = extract_text_from_pdf(
                path=path, max_pages=args.max_pages, max_chars=args.max_chars
            )
            text_chars = len(text_excerpt)
            use_vision = args.vision_mode == "always" or (
                args.vision_mode == "auto" and text_chars < args.vision_min_text_chars
            )

            image_b64: Optional[str] = None
            vision_source = ""
            if use_vision:
                image_b64, vision_source = render_first_page_png_base64(path)
                if image_b64 is None and args.vision_mode == "always":
                    extractor = f"{extractor};{vision_source}"

            if llm_enabled:
                llm_status = (
                    llm_global_status
                    if llm_global_status.startswith("model-fallback:")
                    else "llm"
                )
            llm_exc: Optional[Exception] = None
            for attempt in range(retry_timeouts + 1):
                try:
                    category, decision, folder, confidence, reason = classify_with_llm(
                        ollama_url=args.ollama_url,
                        model=effective_model,
                        timeout_s=args.timeout_s,
                        filename=path.name,
                        text_excerpt=text_excerpt,
                        image_b64=image_b64,
                    )
                    llm_exc = None
                    consecutive_timeout_failures = 0
                    if image_b64 is not None and vision_source:
                        reason = f"{reason} | vision:{vision_source}"
                    break
                except Exception as exc:
                    llm_exc = exc
                    if is_timeout_like_error(exc) and attempt < retry_timeouts:
                        backoff = max(0.0, float(args.retry_backoff_sec)) * (
                            attempt + 1
                        )
                        if backoff > 0:
                            time.sleep(backoff)
                        continue
                    break

            if llm_exc is not None:
                llm_status = f"fallback:{type(llm_exc).__name__}"
                if is_timeout_like_error(llm_exc):
                    consecutive_timeout_failures += 1
                    if consecutive_timeout_failures >= timeout_cutoff:
                        llm_enabled = False
                        llm_global_status = "fallback:llm-timeout-global"
                category, decision, folder, confidence, reason = fallback_classify(
                    filename=path.name,
                    text_excerpt=text_excerpt,
                )
                reason = f"{reason} | llm_error:{llm_exc}"
            else:
                llm_status = llm_global_status
                category, decision, folder, confidence, reason = fallback_classify(
                    filename=path.name,
                    text_excerpt=text_excerpt,
                )
                if vision_source:
                    reason = f"{reason} | vision:{vision_source}"

            rec = DocRecord(
                file=path.name,
                source_relpath=rel,
                sha1=digest,
                duplicate_of=dup_primary,
                extractor=extractor,
                text_chars=text_chars,
                used_vision=image_b64 is not None,
                llm_status=llm_status,
                standardized_category=category,
                keep_decision=decision,
                target_folder=folder,
                confidence=confidence,
                reason=reason[:220],
                action="",
                file_size=path.stat().st_size,
                page_count=0,  # TODO: Extract actual page count if needed
                modified_time=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                created_time=datetime.fromtimestamp(path.stat().st_ctime).isoformat(),
            )
            assign_action(
                record=rec,
                delete_exact_duplicates=args.delete_exact_duplicates,
                hard_delete_delete_candidates=args.hard_delete_delete_candidates,
            )
            records.append(rec)
            classify_progress.update(idx, f"{path.name} -> {rec.target_folder}")
    classify_progress.finish("classification complete")

    if args.apply:
        stage_note("Stage 3/3: applying actions", progress_enabled)
        ensure_target_dirs(root)
        apply_progress = ProgressBar(
            total=len(records),
            label="Apply",
            enabled=progress_enabled,
            width=args.progress_width,
        )
        for idx, rec in enumerate(records, start=1):
            try:
                apply_record(root, rec)
            except Exception as exc:
                rec.error = str(exc)
                rec.result = "ERROR"
            apply_progress.update(idx, f"{rec.action} {rec.file}")
        apply_progress.finish("apply complete")
    else:
        stage_note("Stage 3/3: dry-run (no file changes)", progress_enabled)
        for rec in records:
            rec.result = "DRY_RUN"

    output_dir = (root / args.output_dir).resolve()
    csv_path, json_path = write_logs(
        output_dir=output_dir, records=records, dry_run=not args.apply
    )
    review_note = (
        maybe_write_review_note(root=root, records=records, apply=args.apply)
        if args.write_review_note
        else None
    )

    mode = "APPLY" if args.apply else "DRY_RUN"
    print(f"MODE: {mode}")
    print(f"ROOT: {root}")
    print(f"SOURCE_PDFS: {len(source_files)}")
    print(f"MODEL: {effective_model}")
    print(f"LLM_PRECHECK: {llm_global_status}")
    print(summarize(records))
    print(f"MANIFEST_CSV: {csv_path}")
    print(f"MANIFEST_JSON: {json_path}")
    if review_note:
        print(f"REVIEW_NOTE: {review_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
