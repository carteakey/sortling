#!/usr/bin/env python3
"""
sortling: tidy screenshots, downloads and documents with decision models.

One file, standard library only. Download it and run it:

    python3 sortling.py                     # interactive: pick a folder, a profile, a model
    python3 sortling.py --root ~/Downloads  # scripted dry run with the default backend
    python3 sortling.py --undo <manifest.json>

Modes:
  fast      System One decision models (llama.cpp /v1/systemone, TypeSafe Jev).
            One quick pass per file with real probabilities.
  thorough  Any OpenAI-compatible chat endpoint. Explains each file; slower;
            confidence is self-reported by the model.

Config: $XDG_CONFIG_HOME/sortling/config.json (see --init-config). API keys are never
stored there, only the names of environment variables that hold them.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__version__ = "0.3.0"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

# system folders are underscore-prefixed so they sort first
REVIEW_FOLDER = "_review"
DELETE_FOLDER = "_delete"
DUPLICATES_FOLDER = "_duplicates"
SYSTEM_FOLDERS: Tuple[str, ...] = (REVIEW_FOLDER, DELETE_FOLDER, DUPLICATES_FOLDER)
# folderal's numbered layout; still treated as already-sorted so old trees aren't re-processed
LEGACY_TARGET_FOLDERS: Tuple[str, ...] = (
    "10_Personal_Keep",
    "20_Finance_Keep",
    "30_Work_Archive",
    "40_Learning_Core",
    "41_Learning_Archive",
    "90_Review",
    "98_Delete",
    "99_Duplicates",
)

DOCS_FOLDER = "docs"
LEARNING_ARCHIVE_FOLDER = "docs/learning/archive"
PHOTOS_FOLDER = "photos"
MEMES_FOLDER = "memes"
FILES_FOLDER = "files"
DEFAULT_PROFILE_ROOTS: Tuple[str, ...] = (DOCS_FOLDER, "screenshots", PHOTOS_FOLDER, MEMES_FOLDER, FILES_FOLDER)
NONE_OPTION = "none"

# Built-in profiles as plain JSON data: the questions the model is asked, their options and the
# thresholds. A config "profiles" entry with a built-in's name, or {"profile": "<built-in>", ...}
# in a saved profile or .sortling.json, deep-merges over these (null removes an option).
# Routing stays in code: the option names it relies on can be reworded but not removed.
BUILTIN_PROFILE_SPECS: Dict[str, dict] = {
    "default": {
        "description": "docs/<category>, screenshots/<topic>, photos/<category>, memes/",
        "thresholds": {
            # below this, the image kind or document category is unsure: _review
            "min_confidence": 0.6,
            # below this topic probability, screenshots go to screenshots/other. 0.3, not 0.4: on real
            # screenshots the right topic often wins with only 0.33-0.37 (eval set screens-real)
            "screenshot_min_confidence": 0.3,
            # a "yes, it's a record" this sure files a screenshot or photo as a document
            "record_min_prob": 0.5,
            # an "unusable" (too dark, blurry, accidental) photo this sure goes to _delete. The junk
            # question misses these (0.43-0.46 on a dark shot); this one scores 0.82-0.97 against
            # at most 0.04 for good photos
            "unusable_min_prob": 0.75,
            # junk probability needed for _delete
            "delete_min_prob": 0.85,
        },
        "questions": {
            # images first split into documents, screenshots, photos and memes, each with its own tree
            "kind": {
                "instructions": "What kind of image is this?",
                "criteria": {
                    "screenshot": "a capture of a screen: app, website, chat, code, map, social feed, game, or a frame from a video or livestream",
                    "photo": "a real camera photo of people, places, food, pets, objects or scenery",
                    "meme": (
                        "a meme, joke or reaction image, good morning or festival wishes card, "
                        "motivational or religious quote image, or other picture made to amuse or greet (not a chart)"
                    ),
                    "document": (
                        "a scan or photo of a paper document, a screenshot of a record such as a receipt, "
                        "order confirmation, booking, ticket, bill, ID or statement, "
                        "or a chart, graph, infographic, paper figure, slide or cheat sheet"
                    ),
                },
            },
            # concrete yes/no questions answer more reliably than an abstract keep/archive/delete choice
            "junk": {
                "instructions": (
                    "Is this junk with no lasting value, such as an ad, spam, or a blurry, "
                    "accidental, blank or black photo or screenshot?"
                ),
            },
            # records: models call an order-confirmation screenshot a "screenshot" even when the kind
            # description names it, so a direct yes/no question decides instead
            "record": {
                "instructions": (
                    "Is this a record worth keeping: a receipt, an order, booking or payment confirmation, "
                    "a ticket, a bill, an ID or a statement?"
                ),
            },
            # photos: a direct quality question, since models don't think of a dark photo as "junk"
            "unusable": {
                "instructions": "Is this photo unusable: too dark, too blurry, or taken by accident, so you can barely see what's in it?",
            },
            # documents: docs/<category>
            "category": {
                "instructions": "If this is a document, record, chart or figure, which category is it?",
                "criteria": {
                    "personal": "identity documents, personal records, resumes, certificates, personal letters or chats",
                    "medical": "health records, lab reports, prescriptions, insurance claims for care",
                    "finance": "bank or card statements, invoices, receipts, bills, taxes, payments, payslips",
                    "work": "employer documents, job-related technical material, work chats, dashboards or tickets",
                    "learning": "courses, lecture notes, tutorials, papers, cheat sheets, charts, infographics, figures, code or reference material",
                    "travel": "bookings, boarding passes, tickets, itineraries, maps, visas in use for a trip",
                    "legal": "contracts, leases, property papers, legal notices, agreements",
                    "spam": "marketing, promotions, ads, junk, memes or throwaway content with no lasting value",
                    "unknown": "cannot tell what this is",
                },
            },
            "core": {
                "instructions": "Is this core reference material worth coming back to often, rather than old notes to archive?",
            },
            # screenshots that aren't records: screenshots/<topic>
            "topic": {
                "instructions": "If this is a screenshot, what is it mainly about?",
                "criteria": {
                    "code": "code, terminal, error messages, logs, developer tools",
                    "chats": "messaging or chat conversations, emails, comments",
                    "reading": "articles, documentation, posts or text worth reading later",
                    "apps": "app or website UI, settings, dashboards, design inspiration",
                    "social": "social media posts and feeds, videos from feeds",
                    "shopping": "products, prices, wishlists, carts (not completed purchases)",
                    "maps": "maps, directions, places, addresses",
                    "media": "music, movies, TV, games, video stills",
                    "other": "none of the above",
                },
            },
            # camera photos: photos/<category>. Kept coarse on purpose; photo apps do faces and places better
            "photo": {
                "instructions": "If this is a camera photo, what is it mainly of?",
                "criteria": {
                    "people": "people, selfies, family, friends, group photos",
                    "places": "landscapes, cities, buildings, travel scenery",
                    "food": "meals, drinks, cooking, restaurants",
                    "pets": "pets and other animals",
                    "events": "celebrations, weddings, parties, concerts, festivals, ceremonies",
                    "other": "objects, everyday scenes or anything else",
                },
            },
        },
        # everything that isn't a PDF, image or text document: files/<folder> by extension, no model.
        # Unknown extensions are left where they are.
        "files": {
            "archives": [".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"],
            "installers": [".dmg", ".pkg", ".exe", ".msi", ".deb", ".rpm", ".appimage", ".apk", ".iso", ".img", ".flatpakref"],
            "audio": [".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".aiff"],
            "video": [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv"],
            "ebooks": [".epub", ".mobi", ".azw", ".azw3", ".djvu", ".cbz", ".cbr"],
            "fonts": [".ttf", ".otf", ".woff", ".woff2"],
            "code": [".py", ".js", ".ts", ".ipynb", ".json", ".yaml", ".yml", ".toml", ".sh", ".html", ".css",
                     ".c", ".cpp", ".h", ".rs", ".go", ".java", ".sql", ".xml"],
            "data": [".csv", ".tsv", ".parquet", ".sqlite", ".db", ".xls", ".numbers", ".ics", ".vcf", ".gpx", ".kml"],
            "documents": [".doc", ".ppt", ".pages", ".key", ".odg"],
            "3d": [".stl", ".obj", ".3mf", ".gcode", ".step", ".stp"],
            "torrents": [".torrent"],
            # saved web shortcuts (Windows .url, macOS .webloc). Not Linux .desktop: those are app
            # launchers, and moving one off the Desktop breaks it
            "links": [".url", ".webloc"],
        },
        # model answers outside the category options that mean one of them
        "category_aliases": {
            "personal_records": "personal",
            "identity": "personal",
            "health": "medical",
            "financial": "finance",
            "banking": "finance",
            "career": "work",
            "employment": "work",
            "education": "learning",
            "study": "learning",
            "property": "legal",
            "junk": "spam",
            "marketing": "spam",
            "unclear": "unknown",
            "misc": "unknown",
        },
    },
    # the lean profile for camera rolls: 3 questions per image, documents are not sub-sorted
    "photos": {
        "extends": "default",
        "description": "camera rolls and WhatsApp dumps: photos/<category>, memes/, screenshots/, docs/",
        # leaves archives, installers and other files where they are
        "files": None,
    },
}
# the questions each built-in layout asks; the rest of an inherited spec is unused
LAYOUT_QUESTIONS: Dict[str, Tuple[str, ...]] = {
    "default": ("kind", "junk", "record", "unusable", "category", "core", "topic", "photo"),
    "photos": ("kind", "junk", "unusable", "photo"),
}
# options the routing code depends on
REQUIRED_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "kind": ("screenshot", "photo", "meme", "document"),
    "category": ("unknown",),
    "topic": ("other",),
    "photo": ("other",),
}

PDF_EXTS: Tuple[str, ...] = (".pdf",)
# text-bearing documents: the text is extracted (standard library only) and asked about like a PDF
DOC_TEXT_EXTS: Tuple[str, ...] = (".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".rtf", ".txt", ".md", ".markdown")
# downloads still in progress are never touched
PARTIAL_EXTS: Tuple[str, ...] = (".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp")
ALL_KINDS: Tuple[str, ...] = ("pdf", "image", "doc", "file")
# png/jpeg/gif/bmp are decoded natively by llama.cpp; the rest are converted to PNG first
IMAGE_EXTS: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff")
NATIVE_IMAGE_MIME: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


# ---------------------------------------------------------------------------
# Terminal flair
# ---------------------------------------------------------------------------

BANNER_ART = r"""
                   _    _  _
 ___   ___   _ __ | |_ | |(_) _ __    __ _
/ __| / _ \ | '__|| __|| || || '_ \  / _` |
\__ \| (_) || |   | |_ | || || | | || (_| |
|___/ \___/ |_|    \__||_||_||_| |_| \__, |
                                     |___/"""


def unicode_ok(stream=None) -> bool:
    enc = (getattr(stream or sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


def icon(name: str) -> str:
    fancy = {"fast": "⚡", "thorough": "🐢", "hybrid": "⚡🐢", "hosted": "☁", "local": "⌂", "ok": "✓", "warn": "!"}
    plain = {"fast": ">>", "thorough": "~~", "hybrid": ">~", "hosted": "[cloud]", "local": "[local]", "ok": "ok", "warn": "!"}
    return (fancy if unicode_ok() else plain).get(name, "")


def color(text: str, code: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def print_banner() -> None:
    sparkle = " ✨" if unicode_ok() else ""
    tagline = f"a tiny wizard who sorts your screenshots, downloads and documents{sparkle}  v{__version__}"
    mode = face_mode(sys.stdout) if HERO_DATA else ""
    forced = os.environ.get("SORTLING_FACE", "").lower() == "force"
    art = BANNER_ART.strip("\n").split("\n")
    hero_w = load_hero()["w"] if mode else 0
    if not mode or (not forced and shutil.get_terminal_size((80, 24)).columns < hero_w + max(map(len, art)) + 6):
        print(color(BANNER_ART, "36"))
        print(color(f"  {tagline}", "2"))
        print()
        return
    # the hero beside the lettering, vertically centred, tagline underneath
    hero = render_face("hero", False, mode == "truecolor", load_hero())
    pad = max(0, (len(hero) - len(art)) // 2)
    side = [""] * pad + [color(line, "36") for line in art]
    for i, row in enumerate(hero):
        print(f"  {row}  {side[i] if i < len(side) else ''}")
    print(color(f"  {tagline}", "2"))
    print()


class ProgressBar:
    def __init__(self, total: int, label: str, enabled: bool = True, width: int = 30, stream=None) -> None:
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
            self.stream.write("\r\033[K" + msg)
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


# ---------------------------------------------------------------------------
# The face: a Doom-guy-style status head that reacts to each decision
# ---------------------------------------------------------------------------

# packed by tools/encode_faces.py from assets/faces/head-*.png; regenerate there, don't edit by hand
# --- FACES BEGIN
FACES_DATA = (
    "eNq1W9Fy4ri2/Zc8zwMhJHGm6j5ItowFuDuycYJz6z6AAUEg7e64ewTn1Pn3s7ZsEttAeu7MdFcxYYIiy/JeWnutvfn3hbn4"
    "vev8drHCj6vfLr5Ot4vv3xcXv//vxeW00+tcX/x2cXvTXV46eDO77GXdDG+us2738hZvrnqXl5cdvFnMbmbdJd5k2bVjx9w4"
    "3evLO7yZ926ur2iMs7y67dI8t71u1u3hTW95eXtJl1hMMTcNXt7NO9dz+s31HS5CfzW9ur2c0YSzbq97iTfdm85th+aZdq67"
    "XZp57tzOOjd09fnsdkpXX15ldzNa4c3d9d31jf3N/C6zl7hZ3M7tb2aL6XxK88zuZnd0rd70qndFb6ZXV127wuV8ebmgizrz"
    "2+7N1C7j+vqafrOcLhb2o/l03slowu6y2+3aW3YyZ0a/uZs617dTe63pzd2tva/p/I42qnd3teh17cKur7o0ZnFbXf2y17m5"
    "pD/vLe6mC7r3q8vbTlZu1PXiumPvfZbh6v+Hv3+dviyKi9//fVG8rLd4dBfs5D/hMhMK9sE/rmSf68zjRp8fpDd9V4Weq3PP"
    "PTsmHXCzwZgidHUYnhvDTOcz5hlxpYauNkdDPJYGzPQ+e9pRQpnYY/poLqFDj5n83lOde7YSkdAsPr4vzL/DZ7swEfRTta8l"
    "hGAp7p3WFA6ZNqHL8bv6NnCpuAlDrpKQ6SxwVYb7w0awpD6RYsbEQicP/iqd+EYr2k9Xax+fvM3kroTAPA99vpn2I/3Y3xn8"
    "3SYIlKqtXY0EjVn1FoF95Wl/54hA4drs7THGrlEaY576UTmu74qn/k76rL7vXPo+fe4yO0bqZBasNhrrlF5t8XKXL4J1mNnr"
    "rRUf4G/6Jok8VgsbN3warNOHAdeT/q5Y9Hfpi79K/MYYxvz+Pn2htftRQdd8xN8sa3tQrcs8HNZt177SM66OQ0XuGfYhn9rX"
    "OtFMnQ47uQ+fDy/2a/79D6C3mn79uj8NM84oIkwoz8+AR29smAFm7TB0PvmHUNObgELRNbnX2pJ7b6UOY7xyjJGAkd+AjulR"
    "XEoa46qOh5ALhdaj97mce5qLHhWFqKd6n4UpIs5lLFby/VrasWEmDN1Xx0LRZZof37kMOcslwHXvGWeMMZF3BFU5BsToiMGa"
    "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"
    "tuBZlP3nP/8FIMNFEg=="
)
# --- FACES END
# --- HERO BEGIN
HERO_DATA = (
    "eNqNlF1vozgUhv8L173gO07v/IVxE9rYaZrCai6IA3iUXbEaRgJpNP99j0mm7XabnSIhnOg857zn9bF/eKN3G6Ebz8InuvH+"
    "rv9svn9vvNs/vDD1jd94N97Cj4IQwWIZxSg8wiJdRlFgYJGEYRzUsKibJI1iWDSLpQnd4nhc1FEAC2PSOnJ4jAIzB4dtEAUR"
    "LFAc1WHrFs0iTl1MuzCHxJVIorAOFy5zmqah7xKmh2jpguFzXLp/WkgduYTHpk4iR0VpeK6eLJMgcnjrm/awnIuGSTCLT9M4"
    "duJb1CyPqdNsjrWZg00bNnMtg1AQzF2YwyGYe1+adDG7gY6JEx+huIkSV6tFQRJ4X4D/Vv/VDN7tD88233rv1sOfeZRkBHP8"
    "yYcQVeYUnwRV/ecAVUqqToJ05acYOhYu/x3EM4ZHYH6njXM8li4/MCPF3Qjf/42nv/Q4jmG5Yri4I9eBNR5dfAFy/AdgGBsH"
    "Cgzw/EoPTktZcMK3zBYUj+MKqx60FYyoj4jhjqpZC81I8cjHEzCD+z/D3e6dtB30C3mwcr2CKKn5VDySCRfvc7PZh95p5wQ8"
    "hRqUjYgSW+R8AmOBx/9mcngz0sk1VoAozkl3gj7dD6Sokq5m9lbMZYY2bNyRmdG7fdYVUKugtOuhhtwA85I/I+aRjM4jx/QQ"
    "391zbZ65PTHnAe56iHd1fnlrHgToPfvqmOGJ66HOSX/IRv+yl3JFQNtL/8R/5Lif+6cj2rAphvjRwFuLKa5w55gxo+Nu9Tqo"
    "Pog/9wPMfY79SkwzY4Q1WzrPV0FnzW+2+sU/3TfA6XMdqYG5x2oH81vC3CByOQyQH+bg/NwL6/zirkbDprIGpgIPYd46qCXf"
    "1JFrodEaY19zhfbc8kNuY2B7p+2yVwo8l6/CJpeXA2eeXf5Moa3oUJVNps7GAeKHDHoB/3YvDuQWeu38PdNz34dL/xr8bgUe"
    "VjA7sDdqTV/P117Y4ZltcSW0dB5ffO63mS0t7MmGqH71btj3sN8628pO6NNeaFzlHXitT51QA6Hjx2dQaL4V5NRJjB74hPdu"
    "QsSWW6mGqweXTagSZFdmFs4U2VmoUUkL71VmaKXebXLbazxhmMuyyyfUSipB3+6KrpOV+lSfZ6s3uUV7Sc5MPpUfXIhlK0E3"
    "mwZzmUnYw/g+s6ibmRH9B8m+YusYbs8zfJnJTaa7J7GVFfvAswziBeYE9rBhyt8KYLntXJ5S6CsX5PmubWEun+CerITtYR6H"
    "0uX5zSW8Pu8br9gHPnk/f/4Da8lAew=="
)
# --- HERO END

_SPRITES: Dict[str, dict] = {}


def _unpack(data: Sequence[str]) -> dict:
    """{"w", "h", "frames": {name: [[rgb or None] * w] * h}} from a packed sprite block."""
    import zlib

    blob = json.loads(zlib.decompress(base64.b64decode("".join(data))))
    palette = [None] + [tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) for c in blob["palette"]]
    w, h = blob["w"], blob["h"]
    frames = {}
    for name, packed in blob["frames"].items():
        px = base64.b64decode(packed)
        frames[name] = [[palette[px[y * w + x]] for x in range(w)] for y in range(h)]
    return {"w": w, "h": h, "frames": frames}


def load_faces() -> dict:
    if "faces" not in _SPRITES:
        _SPRITES["faces"] = _unpack(FACES_DATA)
    return _SPRITES["faces"]


def load_hero() -> dict:
    if "hero" not in _SPRITES:
        _SPRITES["hero"] = _unpack(HERO_DATA)
    return _SPRITES["hero"]


def _xterm256(rgb: Tuple[int, int, int]) -> int:
    r, g, b = (int(round(c / 255 * 5)) for c in rgb)
    return 16 + 36 * r + 6 * g + b


def render_face(name: str, mirror: bool, truecolor: bool, sprites: Optional[dict] = None) -> List[str]:
    """One string per terminal row: two pixel rows per row with half-block characters."""
    faces = sprites or load_faces()
    rows = faces["frames"][name]
    if mirror:
        rows = [list(reversed(r)) for r in rows]

    def fg(c):
        return f"\033[38;2;{c[0]};{c[1]};{c[2]}m" if truecolor else f"\033[38;5;{_xterm256(c)}m"

    def bg(c):
        return f"\033[48;2;{c[0]};{c[1]};{c[2]}m" if truecolor else f"\033[48;5;{_xterm256(c)}m"

    out = []
    for y in range(0, faces["h"], 2):
        top, bot = rows[y], rows[y + 1] if y + 1 < faces["h"] else [None] * faces["w"]
        line = []
        for t, b in zip(top, bot):
            if t is None and b is None:
                line.append("\033[0m ")
            elif b is None:
                line.append("\033[0m" + fg(t) + "▀")
            elif t is None:
                line.append("\033[0m" + fg(b) + "▄")
            else:
                line.append(fg(t) + bg(b) + "▀")
        out.append("".join(line) + "\033[0m")
    return out


def face_mode(stream=None) -> str:
    """'truecolor', '256' or '' (no face). SORTLING_FACE=0 turns it off, =force renders anywhere."""
    stream = stream or sys.stderr
    env = os.environ.get("SORTLING_FACE", "").lower()
    if env in ("0", "off", "no") or os.environ.get("NO_COLOR"):
        return ""
    forced = env == "force"
    if not forced:
        if not getattr(stream, "isatty", lambda: False)() or os.environ.get("TERM", "dumb") in ("", "dumb"):
            return ""
        if not unicode_ok(stream):
            return ""
        size = shutil.get_terminal_size((80, 24))
        if size.columns < 72 or size.lines < 18:
            return ""
    colorterm = os.environ.get("COLORTERM", "").lower()
    return "truecolor" if forced or colorterm in ("truecolor", "24bit") else "256"


# what the face does for each outcome, and the caption beside it
MOODS: Dict[str, Tuple[str, str]] = {
    "working": ("smile", "sorting..."),
    "confident": ("happy", "easy one"),
    "unsure": ("confused", "hmm, not sure -> _review"),
    "junk": ("surprised", "yikes, junk -> _delete"),
    "duplicate": ("nerd", "seen this one before"),
    "meme": ("cool", "a meme, keeping it in memes/"),
    "pet": ("love", "aww"),
    "record": ("searching", "a record, filing it with the docs"),
    "error": ("sad", "the model didn't answer, guessing from the filename"),
    "fallback": ("dizzy", "no model, guessing from the filename"),
    "loading": ("sleepy", "waking the model..."),
    "done": ("starstruck", "all sorted!"),
}


def mood_for(rec: "DocRecord") -> str:
    if "model_error" in rec.reason:
        return "error"
    if rec.classifier.startswith("fallback"):
        return "fallback"
    if rec.duplicate_of:
        return "duplicate"
    if rec.target_folder == REVIEW_FOLDER or rec.target_folder.endswith("/other"):
        return "unsure"
    if rec.target_folder == DELETE_FOLDER:
        return "junk"
    if rec.target_folder == MEMES_FOLDER:
        return "meme"
    if rec.target_folder == f"{PHOTOS_FOLDER}/pets":
        return "pet"
    if rec.kind == "image" and rec.target_folder.startswith("docs"):
        return "record"  # a screenshot or photo of a receipt, ticket or form
    return "confident" if rec.confidence >= 0.9 else "working"


class FaceBar(ProgressBar):
    """ProgressBar with the face beside it. A background thread keeps the face alive between
    updates: it glances left and right and blinks, like Doom guy waiting for something to happen."""

    REACT_SECONDS = 1.1
    TICK_SECONDS = 0.35

    def __init__(self, total: int, label: str, mode: str, stream=None, width: int = 30) -> None:
        import threading

        self.mode = mode
        self.lock = threading.Lock()
        self.mood = "working"
        self.mood_until = 0.0
        self.tick = 0
        self.done = 0
        self.detail = ""
        self.counts: Dict[str, int] = {}
        self.rows = (load_faces()["h"] + 1) // 2
        self.drawn = False
        self._stop = threading.Event()
        super().__init__(total, label, enabled=True, width=width, stream=stream)
        self.dynamic = True
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()

    def _frame(self) -> Tuple[str, bool]:
        mood = self.mood if time.time() < self.mood_until or self.mood in ("loading", "done") else "working"
        name = MOODS[mood][0]
        if mood != "working":
            return name, False
        # idle: look left, look right, blink, and now and then peer at the file through a magnifier
        beat = self.tick % 16
        if beat == 7:
            return "content", False
        if beat in (11, 12, 13):
            return "searching", beat == 12
        return "smile", beat in (3, 4, 5)

    def _draw(self) -> None:
        name, mirror = self._frame()
        face = render_face(name, mirror, self.mode == "truecolor")
        mood = self.mood if time.time() < self.mood_until or self.mood in ("loading", "done") else "working"
        side = [""] * self.rows
        side[2] = self._format(self.done, "")
        side[4] = self.detail[:60]
        side[6] = color(MOODS[mood][1], "2") if mood != "working" else ""
        if self.counts:
            tally = "  ".join(f"{k} {v}" for k, v in sorted(self.counts.items(), key=lambda kv: -kv[1])[:4])
            side[8] = color(tally[:60], "2")
        out = []
        if self.drawn:
            out.append(f"\033[{self.rows}F")
        for row, text in zip(face, side):
            out.append(f"\033[2K  {row}   {text}\n")
        self.stream.write("".join(out))
        self.stream.flush()
        self.drawn = True

    def _animate(self) -> None:
        while not self._stop.wait(self.TICK_SECONDS):
            with self.lock:
                self.tick += 1
                self._draw()

    def update(self, done: int, detail: str = "") -> None:
        with self.lock:
            self.done = min(max(int(done), 0), self.total)
            if detail:
                self.detail = detail
            self._draw()

    def react(self, mood: str, folder: str = "") -> None:
        with self.lock:
            if folder:
                top = folder.split("/", 1)[0]
                self.counts[top] = self.counts.get(top, 0) + 1
            if mood != "working" or time.time() >= self.mood_until:
                self.mood, self.mood_until = mood, time.time() + self.REACT_SECONDS
            self._draw()

    def finish(self, detail: str = "done") -> None:
        self._stop.set()
        self.thread.join(timeout=1)
        with self.lock:
            self.done, self.detail = self.total, detail
            self.mood, self.mood_until = "done", time.time() + 60
            self._draw()


def make_bar(total: int, label: str, enabled: bool, width: int, face: bool = True) -> ProgressBar:
    mode = face_mode() if enabled and face and FACES_DATA else ""
    return FaceBar(total, label, mode, width=width) if mode else ProgressBar(total, label, enabled=enabled, width=width)


class FaceWait:
    """The sleepy face while something slow happens (a router loading a model)."""

    def __init__(self, label: str, enabled: bool) -> None:
        mode = face_mode() if enabled and FACES_DATA else ""
        self.bar: Optional[FaceBar] = FaceBar(1, label, mode) if mode else None
        if self.bar:
            self.bar.react("loading")

    def close(self, label: str) -> None:
        if self.bar:
            self.bar._stop.set()
            self.bar.thread.join(timeout=1)
            with self.bar.lock:
                self.bar.done, self.bar.detail, self.bar.mood = 1, label, "confident"
                self.bar.mood_until = time.time() + 60
                self.bar._draw()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "default_backend": "local-d3",
    "backends": {
        "local-d3": {
            "type": "systemone",
            "label": "vLLM-SR d3-flash via llama-swap",
            "url": "http://localhost:8080/upstream/vllm-d3-flash/v1/systemone",
            "api_key_env": "LLAMA_SWAP_API_KEY",
        },
        "local-clef": {
            "type": "systemone",
            "label": "Cloudflare Clef-Flash via llama-swap (faster on images)",
            "url": "http://localhost:8080/upstream/cloudflare-clef-flash/v1/systemone",
            "api_key_env": "LLAMA_SWAP_API_KEY",
        },
        "jev": {
            "type": "systemone",
            "label": "TypeSafe Jev (hosted, text only)",
            "url": "https://api.typesafe.ai/v1/systemone",
            "model": "jev-latest",
            "api_key_env": "TYPESAFE_API_KEY",
            "vision": False,
        },
        "local-llm": {
            "type": "openai",
            "label": "any chat model on llama-swap / Ollama / LM Studio",
            "url": "http://localhost:8080/v1",
            "model": "set-me",
            "api_key_env": "LLAMA_SWAP_API_KEY",
        },
        "openai": {
            "type": "openai",
            "label": "OpenAI or any hosted OpenAI-compatible API",
            "url": "https://api.openai.com/v1",
            "model": "set-me",
            "api_key_env": "OPENAI_API_KEY",
        },
        "hybrid-local": {
            "type": "hybrid",
            "label": "d3 decides; a local chat LLM re-checks only the uncertain files",
            "first": "local-d3",
            "second": "local-llm",
        },
    },
    "profiles": {},
}


def config_path(override: str = "") -> Path:
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sortling" / "config.json"


def load_config(path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        cfg["default_backend"] = user.get("default_backend", cfg["default_backend"])
        # user entries replace built-in ones of the same name; hidden ones are dropped
        cfg["backends"].update(user.get("backends", {}))
        cfg["profiles"].update(user.get("profiles", {}))
        for name in user.get("hidden_backends", []):
            cfg["backends"].pop(name, None)
        if cfg["default_backend"] not in cfg["backends"] and cfg["backends"]:
            cfg["default_backend"] = next(iter(cfg["backends"]))
    return cfg


def save_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_user_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"backends": {}, "profiles": {}}


def is_hosted_url(url: str) -> bool:
    """True for public internet hosts. Loopback, private, link-local and Tailscale/CGNAT
    addresses (100.64.0.0/10), MagicDNS (*.ts.net) and bare LAN names count as local."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".lan", ".home", ".internal", ".ts.net")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


# ---------------------------------------------------------------------------
# Backends: both answer System One questions ({id: {type, instructions, criteria}})
# ---------------------------------------------------------------------------


class Backend:
    mode = "fast"

    def __init__(self, name: str, cfg: dict) -> None:
        self.name = name
        self.cfg = cfg
        self.url = str(cfg.get("url", ""))
        self.model = str(cfg.get("model", ""))
        self.label = str(cfg.get("label", ""))
        self.key_env = str(cfg.get("api_key_env", ""))
        self.api_key = os.environ.get(self.key_env, "") if self.key_env else ""
        self.hosted = bool(cfg["hosted"]) if "hosted" in cfg else is_hosted_url(self.url)
        self.vision = bool(cfg.get("vision", True))

    def headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def post(self, url: str, body: dict, timeout_s: int) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=self.headers())
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def decide(self, state: str, images: Sequence[str], questions: dict, timeout_s: int) -> Tuple[dict, str]:
        """Return (answers in System One shape, optional free-text reason)."""
        raise NotImplementedError

    def precheck(self, timeout_s: int) -> None:
        self.decide("precheck", [], {"ok": {"type": "noul", "instructions": "Is this a test?"}}, timeout_s)

    def describe(self) -> str:
        where = f"{icon('hosted')} hosted" if self.hosted else f"{icon('local')} local"
        eyes = "" if self.vision else ", text only"
        return f"{icon(self.mode)} {self.mode:<8} {self.name:<12} {where}{eyes}  {self.label}"


class SystemOneBackend(Backend):
    mode = "fast"

    def decide(self, state: str, images: Sequence[str], questions: dict, timeout_s: int) -> Tuple[dict, str]:
        body: dict = {"state": state, "questions": questions}
        if self.model:
            body["model"] = self.model
        if images and self.vision:
            body["images"] = list(images)
        data = self.post(self.url, body, timeout_s)
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise ValueError(f"decision response has no answers: {str(data)[:200]}")
        return answers, ""


class OpenAIBackend(Backend):
    """Emulates System One answers with a chat model and a JSON reply."""

    mode = "thorough"

    def _prompt(self, state: str, questions: dict) -> str:
        lines = [state, "", "Questions:"]
        for qid, q in questions.items():
            if q["type"] == "choice":
                lines.append(f'- "{qid}" (choose exactly one option): {q["instructions"]}')
                for opt, desc in q["criteria"].items():
                    lines.append(f"    {opt}: {desc}" if desc else f"    {opt}")
            else:
                lines.append(f'- "{qid}" (yes or no): {q["instructions"]}')
        return "\n".join(lines)

    def decide(self, state: str, images: Sequence[str], questions: dict, timeout_s: int) -> Tuple[dict, str]:
        system = (
            "You sort personal files into folders. You are shown one file as text and/or an image. "
            "Answer every question. Reply with ONLY a JSON object:\n"
            '{"answers": {"<question id>": {"choice": "<one option name>", "confidence": <0..1>} '
            'or {"yes": true|false, "confidence": <0..1>}}, "reason": "<one short sentence about the file>"}'
        )
        content: list = [{"type": "text", "text": self._prompt(state, questions)}]
        if images and self.vision:
            content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        body: dict = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 2048,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        url = self.url.rstrip("/") + "/chat/completions"
        try:
            data = self.post(url, body, timeout_s)
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            body.pop("response_format")  # some servers reject json mode
            data = self.post(url, body, timeout_s)
        text = str(data["choices"][0]["message"].get("content") or "")
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)  # reasoning models
        parsed = extract_json_object(text)
        raw = parsed.get("answers", parsed)
        answers: dict = {}
        for qid, q in questions.items():
            a = raw.get(qid) if isinstance(raw.get(qid), dict) else {}
            conf = to_float_0_1(a.get("confidence", 0.5))
            if q["type"] == "choice":
                opts = list(q["criteria"])
                pick = str(a.get("choice", "")).strip().lower()
                pick = next((o for o in opts if o.lower() == pick), "")
                if not pick:
                    conf, pick = 0.0, opts[-1]  # unparseable: lowest confidence, routes to review
                rest = (1.0 - conf) / max(1, len(opts) - 1)
                answers[qid] = {"type": "choice", "choice": pick, "probabilities": {o: conf if o == pick else rest for o in opts}}
            else:
                yes = a.get("yes")
                yes = yes if isinstance(yes, bool) else str(yes).lower() in {"true", "yes", "1"}
                answers[qid] = {"type": "noul", "noul": conf if yes else 1.0 - conf}
        return answers, str(parsed.get("reason", "")).strip()[:160]

    def precheck(self, timeout_s: int) -> None:
        if not self.model or self.model == "set-me":
            raise ValueError(f'set "model" for backend "{self.name}" in the config')
        super().precheck(timeout_s)


def make_backend(name: str, cfg: dict) -> Backend:
    kind = cfg.get("type", "systemone")
    if kind == "systemone":
        return SystemOneBackend(name, cfg)
    if kind == "openai":
        return OpenAIBackend(name, cfg)
    if kind == "hybrid":
        raise ValueError(f'backend "{name}" is a hybrid; resolve it with make_backends')
    raise ValueError(f'backend "{name}": unknown type "{kind}" (use systemone, openai or hybrid)')


def make_backends(name: str, cfg: dict, url: str = "", second: str = "") -> Tuple[Backend, Optional[Backend]]:
    """Resolve a backend name into (first pass, optional second pass)."""
    backends = cfg["backends"]
    if name not in backends:
        raise ValueError(f'unknown backend "{name}" (configured: {", ".join(backends)})')
    bcfg = dict(backends[name])
    if bcfg.get("type") == "hybrid":
        first_name, second_name = str(bcfg.get("first", "")), second or str(bcfg.get("second", ""))
        for part in (first_name, second_name):
            if part not in backends or backends[part].get("type") == "hybrid":
                raise ValueError(f'hybrid "{name}": "{part}" must be a configured non-hybrid backend')
        bcfg, name = dict(backends[first_name]), first_name
    elif second:
        second_name = second
        if second_name not in backends or backends[second_name].get("type") == "hybrid":
            raise ValueError(f'--second-pass "{second_name}" must be a configured non-hybrid backend')
    else:
        second_name = ""
    if url:
        bcfg["url"] = url
        bcfg.pop("hosted", None)
    first_backend = make_backend(name, bcfg)
    return first_backend, (make_backend(second_name, backends[second_name]) if second_name else None)


MODE_BLURBS: Dict[str, str] = {
    "fast": "decision model: one quick pass per file, real probabilities",
    "thorough": "chat model: explains each file; slower; confidence is self-reported",
    "hybrid": "fast on everything, thorough only on the files fast is unsure about",
}


def backend_row(name: str, cfg: dict, star: bool = False) -> Tuple[str, str, str]:
    """(label, value, description) for menus: name first, then mode and where it runs."""
    bcfg = cfg["backends"][name]
    mark = ("★ " if unicode_ok() else "* ") if star else "  "
    if bcfg.get("type") == "hybrid":
        label = f"{mark}{name:<22} {icon('hybrid')} hybrid"
        desc = f"{bcfg.get('first')} first, {bcfg.get('second')} re-checks unsure files · {MODE_BLURBS['hybrid']}"
        return label, name, desc
    b = make_backend(name, bcfg)
    where = f"{icon('hosted')} hosted" if b.hosted else f"{icon('local')} local"
    eyes = "" if b.vision else "  text only"
    label = f"{mark}{name:<22} {icon(b.mode)} {b.mode:<9} {where}{eyes}"
    return label, name, f"{b.label or b.url} · {MODE_BLURBS[b.mode]}"


def describe_entry(name: str, cfg: dict) -> str:
    bcfg = cfg["backends"][name]
    if bcfg.get("type") == "hybrid":
        return f"{icon('hybrid')} hybrid   {name:<12} {bcfg.get('first')} first, {bcfg.get('second')} re-checks uncertain files"
    return make_backend(name, bcfg).describe()


# ---------------------------------------------------------------------------
# Profiles: which questions to ask and how answers become folders
# ---------------------------------------------------------------------------


@dataclass
class Thresholds:
    min_confidence: float
    screenshot_min_confidence: float
    delete_min_prob: float
    record_min_prob: float
    unusable_min_prob: float


THRESHOLD_KEYS: Tuple[str, ...] = tuple(f.name for f in fields(Thresholds))
SPEC_KEYS: Tuple[str, ...] = ("description", "thresholds", "questions", "category_aliases", "files")


@dataclass
class Profile:
    name: str
    description: str = ""
    # "default" and "photos" are built in; "custom" uses folders (folder -> description)
    layout: str = "custom"
    folders: Dict[str, str] = field(default_factory=dict)
    # custom profiles: ".ext" -> folder, for files sorted by extension without a model
    extensions: Dict[str, str] = field(default_factory=dict)
    source: str = ""  # where it came from: built-in, config, file path
    # the merged BUILTIN_PROFILE_SPECS entry for built-in layouts; custom ones may set thresholds
    spec: dict = field(default_factory=dict)

    @property
    def builtin(self) -> bool:
        return self.layout != "custom"

    def roots(self) -> Tuple[str, ...]:
        """Top-level folders this profile writes, besides the system folders."""
        if self.layout == "default":
            return DEFAULT_PROFILE_ROOTS
        if self.layout == "photos":
            return (PHOTOS_FOLDER, MEMES_FOLDER, "screenshots", DOCS_FOLDER)
        return tuple(dict.fromkeys([*self.folders, *self.extensions.values()]))

    def question(self, qid: str) -> dict:
        return self.spec["questions"][qid]

    def options(self, qid: str) -> Dict[str, str]:
        return self.spec["questions"][qid]["criteria"]

    def thresholds(self, overrides: Optional[dict] = None) -> Thresholds:
        """The profile's thresholds over the built-in ones; overrides that aren't None (flags) win."""
        th = {**builtin_spec("default")["thresholds"], **self.spec.get("thresholds", {})}
        th.update({k: v for k, v in (overrides or {}).items() if v is not None})
        return Thresholds(**th)


LOCAL_PROFILE_FILE = ".sortling.json"
PROFILE_TUNING_NOTE = (
    "Tune a built-in profile by setting only the keys you change; the rest stays built-in and keeps "
    "improving with updates. See them all with: sortling.py --show-profile default. "
    'Example: {"thresholds": {"min_confidence": 0.55}, "questions": {"topic": {"criteria": {"shopping": null}}}}'
)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "other"


def merge_spec(base: dict, over: dict) -> dict:
    """Deep-merge JSON objects: nested objects merge, null removes a key, "_"-prefixed keys are notes."""
    out = dict(base)
    for k, v in over.items():
        if k.startswith("_"):
            continue
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_spec(out[k], v)
        else:
            out[k] = v
    return out


def builtin_spec(name: str) -> dict:
    """A built-in profile's spec with "extends" resolved, as a fresh copy."""
    raw = BUILTIN_PROFILE_SPECS[name]
    base = builtin_spec(raw["extends"]) if "extends" in raw else {}
    return json.loads(json.dumps(merge_spec(base, {k: v for k, v in raw.items() if k != "extends"})))


def check_thresholds(th: object, source: str) -> None:
    if not isinstance(th, dict):
        raise ValueError(f"{source}: thresholds must be a JSON object")
    for k, v in th.items():
        if k not in THRESHOLD_KEYS:
            raise ValueError(f"{source}: unknown threshold {k!r} (known: {', '.join(THRESHOLD_KEYS)})")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"{source}: thresholds.{k} must be a number from 0 to 1")


def check_spec(spec: dict, layout: str, source: str) -> None:
    """Reject overrides the routing code can't follow, with a message that says why."""
    unknown = sorted(k for k in spec if k not in SPEC_KEYS)
    if unknown:
        raise ValueError(f"{source}: unknown profile keys: {', '.join(unknown)} (known: {', '.join(SPEC_KEYS)})")
    check_thresholds(spec.get("thresholds", {}), source)
    questions = spec.get("questions")
    if not isinstance(questions, dict):
        raise ValueError(f"{source}: questions must be a JSON object")
    unknown = sorted(q for q in questions if q not in LAYOUT_QUESTIONS["default"])
    if unknown:
        raise ValueError(f"{source}: unknown questions: {', '.join(unknown)} (known: {', '.join(LAYOUT_QUESTIONS['default'])})")
    for qid in LAYOUT_QUESTIONS[layout]:
        q = questions.get(qid)
        where = f"{source}: questions.{qid}"
        if not isinstance(q, dict) or not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            raise ValueError(f"{where}.instructions must be non-empty text")
        if qid not in REQUIRED_OPTIONS:
            continue
        crit = q.get("criteria")
        if not isinstance(crit, dict) or not all(isinstance(v, str) for v in crit.values()):
            raise ValueError(f"{where}.criteria must map option names to descriptions")
        missing = [o for o in REQUIRED_OPTIONS[qid] if o not in crit]
        if missing:
            raise ValueError(f"{where}.criteria needs {', '.join(missing)}: sorting depends on it")
        if qid == "kind":
            extra = sorted(set(crit) - set(REQUIRED_OPTIONS["kind"]))
            if extra:
                raise ValueError(f"{where}.criteria: image kinds are fixed ({', '.join(REQUIRED_OPTIONS['kind'])}); reword them instead of adding {', '.join(extra)}")
        else:
            # options become folder names
            bad = [o for o in crit if slugify(o) != o or o == NONE_OPTION]
            if bad:
                raise ValueError(f"{where}.criteria: {', '.join(bad)} can't be a folder name (use lowercase letters, digits and -)")
    files = spec.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError(f"{source}: files must map folder names to lists of extensions")
    for folder, exts in files.items():
        if slugify(folder) != folder:
            raise ValueError(f"{source}: files.{folder} can't be a folder name (use lowercase letters, digits and -)")
        if not isinstance(exts, list) or not all(isinstance(e, str) and e.startswith(".") for e in exts):
            raise ValueError(f'{source}: files.{folder} must be a list of extensions like ".zip"')
    aliases = spec.get("category_aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(v, str) for v in aliases.values()):
        raise ValueError(f"{source}: category_aliases must map model answers to category names")


def custom_spec() -> dict:
    """Custom profiles ask the built-in junk question alongside their folder question."""
    return {"questions": {"junk": builtin_spec("default")["questions"]["junk"]}}


def tuned_profile(layout: str, base: dict, over: dict, name: str, source: str) -> Profile:
    """A built-in layout with its spec deep-merged with `over`."""
    spec = merge_spec(base, over)
    check_spec(spec, layout, source)
    # the layout decides which questions are asked; drop what it doesn't use
    spec["questions"] = {q: spec["questions"][q] for q in LAYOUT_QUESTIONS[layout]}
    return Profile(name=name, description=str(spec.get("description", "")), layout=layout, source=source, spec=spec)


def profile_from_dict(raw: dict, name: str, source: str, cfg: dict, seen: Tuple[str, ...] = ()) -> Profile:
    """A profile object: {"folders": {name: description}}, or {"profile": "<built-in or saved name>"}
    with optional overrides of a built-in's spec (questions, thresholds, ...)."""
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: profile must be a JSON object")
    if "profile" in raw and "folders" not in raw:
        parent = load_profile(str(raw["profile"]), cfg, seen)
        over = {k: v for k, v in raw.items() if k not in ("profile", "name")}
        if not over:
            return parent
        if not parent.builtin:
            raise ValueError(f"{source}: only built-in profiles can be tuned; {parent.name} is a list of folders")
        return tuned_profile(parent.layout, parent.spec, over, str(raw.get("name") or name), source)
    folders = {slugify(k): str(v or "") for k, v in (raw.get("folders") or {}).items()}
    if len(folders) < 2:
        raise ValueError(f"{source}: a custom profile needs at least 2 folders")
    clash = sorted(set(folders) & {*SYSTEM_FOLDERS, NONE_OPTION})
    if clash:
        raise ValueError(f"{source}: reserved folder names: {', '.join(clash)}")
    extensions: Dict[str, str] = {}
    for ext, folder in (raw.get("extensions") or {}).items():
        ext = ext.lower() if ext.startswith(".") else "." + ext.lower()
        extensions[ext] = slugify(str(folder))
    if set(extensions.values()) & {*SYSTEM_FOLDERS, NONE_OPTION}:
        raise ValueError(f"{source}: extensions can't map to a reserved folder")
    spec = custom_spec()
    if "thresholds" in raw:
        check_thresholds(raw["thresholds"], source)
        spec["thresholds"] = dict(raw["thresholds"])
    return Profile(
        name=str(raw.get("name") or name),
        description=str(raw.get("description", "")),
        layout="custom",
        folders=folders,
        extensions=extensions,
        source=source,
        spec=spec,
    )


def load_profile(name: str, cfg: dict, seen: Tuple[str, ...] = ()) -> Profile:
    name = name or "default"
    if name in seen:
        raise ValueError(f"profiles extend each other in a loop: {' -> '.join((*seen, name))}")
    raw = cfg.get("profiles", {}).get(name)
    if name in BUILTIN_PROFILE_SPECS:
        # a config entry with a built-in's name tunes that built-in
        over = raw or {}
        if not isinstance(over, dict) or "folders" in over or over.get("profile", name) != name:
            raise ValueError(f'config profile "{name}" can tune the built-in but not replace it; save yours under another name')
        over = {k: v for k, v in over.items() if k not in ("profile", "name") and not k.startswith("_")}
        return tuned_profile(name, builtin_spec(name), over, name, "built-in + config" if over else "built-in")
    if not raw:
        saved = ", ".join(n for n in cfg.get("profiles", {}) if n not in BUILTIN_PROFILE_SPECS) or "none"
        raise ValueError(f'unknown profile "{name}" (built-in: {", ".join(BUILTIN_PROFILE_SPECS)}; saved: {saved})')
    return profile_from_dict(raw, name, "config", cfg, (*seen, name))


def load_profile_file(path: str, cfg: dict) -> Profile:
    """--profile-file: a JSON profile from a file, or from stdin with '-'."""
    text = sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}")
    stem = "stdin" if path == "-" else slugify(Path(path).stem.lstrip("."))
    return profile_from_dict(raw, stem, "stdin" if path == "-" else str(Path(path).expanduser()), cfg)


def local_profile(root: Path, cfg: dict) -> Optional[Profile]:
    """A .sortling.json in the folder being sorted: that folder always sorts its own way."""
    path = root / LOCAL_PROFILE_FILE
    return load_profile_file(str(path), cfg) if path.is_file() else None


def build_questions(profile: Profile, kind: str, has_image: bool) -> dict:
    junk = {"type": "noul", "instructions": profile.question("junk")["instructions"]}
    if profile.layout == "custom":
        criteria = dict(profile.folders)
        criteria[NONE_OPTION] = "none of these folders fit"
        return {
            "folder": {"type": "choice", "instructions": "Which folder does this file belong in?", "criteria": criteria},
            "junk": junk,
        }

    def choice(qid: str) -> dict:
        return {"type": "choice", "instructions": profile.question(qid)["instructions"], "criteria": dict(profile.options(qid))}

    image_kind, photo = choice("kind"), choice("photo")
    unusable = {"type": "noul", "instructions": profile.question("unusable")["instructions"]}
    if profile.layout == "photos":
        # the lean profile: 3 questions per image, documents are not sub-sorted
        if kind == "image" and has_image:
            return {"kind": image_kind, "junk": junk, "unusable": unusable, "photo": photo}
        return {"junk": junk}
    category = choice("category")
    if kind == "image" and has_image:
        # images first split into documents, screenshots, photos and memes, each with its own tree
        return {
            "kind": image_kind,
            "junk": junk,
            "record": {"type": "noul", "instructions": profile.question("record")["instructions"]},
            "unusable": unusable,
            "category": category,
            "topic": choice("topic"),
            "photo": photo,
        }
    return {"category": category, "junk": junk, "core": {"type": "noul", "instructions": profile.question("core")["instructions"]}}


def top_choice(answers: dict, qid: str) -> Tuple[str, float]:
    ans = answers[qid]
    choice = str(ans.get("choice", ""))
    return choice, to_float_0_1(ans.get("probabilities", {}).get(choice, 0.0))


def normalize_category(profile: Profile, value: str) -> str:
    v = (value or "").strip().lower()
    v = profile.spec.get("category_aliases", {}).get(v, v)
    return v if v in profile.options("category") else "unknown"


@dataclass
class Routed:
    category: str
    decision: str
    folder: str
    confidence: float
    reason: str
    # a hybrid run sends these to the second, thorough pass
    uncertain: bool = False


def route(profile: Profile, answers: dict, th: Thresholds) -> Routed:
    junk_p = to_float_0_1(answers["junk"].get("noul"))
    # junk the model half-believes: not deleted, but worth a second look
    junkish = 0.5 <= junk_p < th.delete_min_prob

    if profile.layout == "custom":
        folder, p = top_choice(answers, "folder")
        reason = f"decision:{folder}={p:.2f},junk={junk_p:.2f}"
        if junk_p >= th.delete_min_prob:
            return Routed("junk", "delete_candidate", DELETE_FOLDER, junk_p, reason)
        if folder not in profile.folders or p < th.min_confidence:
            return Routed("unknown", "review", REVIEW_FOLDER, p, reason + " | low-confidence->review", True)
        return Routed(folder, "keep", folder, p, reason, junkish)

    if "kind" in answers:
        image_kind, kind_p = top_choice(answers, "kind")
        reason = f"decision:{image_kind}={kind_p:.2f},junk={junk_p:.2f}"
        if junk_p >= th.delete_min_prob:
            return Routed(f"{image_kind}:junk", "delete_candidate", DELETE_FOLDER, junk_p, reason)
        record_p = to_float_0_1(answers["record"].get("noul")) if "record" in answers else 0.0
        if "record" in answers:
            reason += f",record={record_p:.2f}"
        if image_kind != "document" and record_p >= th.record_min_prob:
            # a screenshot or photo of a receipt, ticket or confirmation is filed with the docs
            image_kind = "document"
            reason += " | record->docs"
        elif kind_p < th.min_confidence:
            # can't tell whether it is a record; a person should look
            return Routed("unknown", "review", REVIEW_FOLDER, kind_p, reason + " | kind-unsure->review", True)
        if image_kind == "meme":
            return Routed("meme", "keep", MEMES_FOLDER, kind_p, reason, junkish)
        if image_kind == "photo":
            unusable_p = to_float_0_1(answers["unusable"].get("noul")) if "unusable" in answers else 0.0
            if "unusable" in answers:
                reason += f",unusable={unusable_p:.2f}"
            if unusable_p >= th.unusable_min_prob:
                return Routed("photo:unusable", "delete_candidate", DELETE_FOLDER, unusable_p, reason)
            sub, sub_p = top_choice(answers, "photo")
            reason += f",photo:{sub}={sub_p:.2f}"
            if sub not in profile.options("photo") or sub_p < th.screenshot_min_confidence:
                return Routed("photo:other", "keep", f"{PHOTOS_FOLDER}/other", kind_p, reason + " | ->other", True)
            return Routed(f"photo:{sub}", "keep", f"{PHOTOS_FOLDER}/{sub}", min(kind_p, sub_p), reason, junkish)
        if image_kind == "screenshot":
            if profile.layout == "photos":
                return Routed("screenshot", "keep", "screenshots", kind_p, reason, junkish)
            topic, topic_p = top_choice(answers, "topic")
            reason += f",topic:{topic}={topic_p:.2f}"
            # misfiled screenshots cost little: unsure ones go to screenshots/other, not the review inbox
            if topic not in profile.options("topic") or topic_p < th.screenshot_min_confidence:
                reason += f" | topic<{th.screenshot_min_confidence:.2f}->other"
                return Routed("screenshot:other", "keep", "screenshots/other", kind_p, reason, True)
            return Routed(f"screenshot:{topic}", "keep", f"screenshots/{topic}", min(kind_p, topic_p), reason, junkish)
        # image_kind == "document": a scanned or screenshotted record, filed with the docs below
        if profile.layout == "photos":
            return Routed("document", "keep", DOCS_FOLDER, kind_p, reason, junkish)
    elif profile.layout == "photos":
        # PDFs in the photos profile are not sub-sorted
        reason = f"decision:junk={junk_p:.2f}"
        if junk_p >= th.delete_min_prob:
            return Routed("document:junk", "delete_candidate", DELETE_FOLDER, junk_p, reason)
        return Routed("document", "keep", DOCS_FOLDER, 1.0 - junk_p, reason, junkish)

    # documents: the folder comes from the category; yes/no answers pick delete or learning core vs archive
    cat_choice, cat_p = top_choice(answers, "category")
    category = normalize_category(profile, cat_choice)
    core_p = to_float_0_1(answers["core"].get("noul")) if "core" in answers else 1.0
    doc_reason = f"{category}={cat_p:.2f}" + (f",core={core_p:.2f}" if "core" in answers else "")
    reason = (reason + "," + doc_reason) if "kind" in answers else f"decision:{doc_reason},junk={junk_p:.2f}"
    if junk_p >= th.delete_min_prob:
        return Routed(category, "delete_candidate", DELETE_FOLDER, junk_p, reason)
    if category == "unknown" or cat_p < th.min_confidence:
        return Routed(category, "review", REVIEW_FOLDER, cat_p, reason + " | low-confidence->review", True)
    if category == "spam":
        return Routed(category, "review", REVIEW_FOLDER, min(cat_p, 1.0 - junk_p), reason + f" | junk<{th.delete_min_prob:.2f}->review", True)
    if category == "learning" and core_p < 0.5:
        return Routed(category, "archive", LEARNING_ARCHIVE_FOLDER, cat_p, reason, junkish)
    return Routed(category, "keep", f"{DOCS_FOLDER}/{category}", cat_p, reason, junkish)


def fallback_classify(profile: Profile, filename: str, text_excerpt: str) -> Routed:
    """Keyword rules for when no model is reachable. Custom profiles just go to review."""
    return Routed(*keyword_rules(profile, filename, text_excerpt))


def keyword_rules(profile: Profile, filename: str, text_excerpt: str) -> Tuple[str, str, str, float, str]:
    if not profile.builtin:
        return ("unknown", "review", REVIEW_FOLDER, 0.0, "fallback:no-model")
    hay = f"{filename} {text_excerpt}".lower()

    def has(*tokens: str) -> bool:
        return any(tok in hay for tok in tokens)

    if has("congrats on winning", "contest", "promo", "unsubscribe", "marketing"):
        return ("spam", "delete_candidate", DELETE_FOLDER, 0.65, "keyword:spam")
    if has("study permit", "passport", "oci", "visa", "pr card"):
        return ("personal", "keep", f"{DOCS_FOLDER}/personal", 0.78, "keyword:identity")
    if has("invoice", "statement", "receipt", "tangerine", "cibc", "tax", "w8", "payment"):
        return ("finance", "keep", f"{DOCS_FOLDER}/finance", 0.73, "keyword:finance")
    if has("resume", "employment", "offer letter", "reference check", "certificate"):
        return ("personal", "keep", f"{DOCS_FOLDER}/personal", 0.74, "keyword:personal")
    if has("blood report", "prescription", "health", "medical", "lab"):
        return ("medical", "keep", f"{DOCS_FOLDER}/medical", 0.75, "keyword:medical")
    if has("oracle", "supply chain", "ebs", "oaf", "doc id", "plsql"):
        return ("work", "archive", f"{DOCS_FOLDER}/work", 0.72, "keyword:work")
    if has("lecture", "cheat sheet", "numpy", "pandas", "machine learning", "deep learning"):
        return ("learning", "archive", LEARNING_ARCHIVE_FOLDER, 0.71, "keyword:learning")
    if has("screenshot", "screen shot", "screen_shot"):
        return ("screenshot:other", "keep", "screenshots/other", 0.5, "keyword:screenshot")
    if has("flight", "booking", "ticket", "map", "transit"):
        return ("travel", "review", REVIEW_FOLDER, 0.61, "keyword:travel")
    return ("unknown", "review", REVIEW_FOLDER, 0.35, "fallback:uncertain")


# ---------------------------------------------------------------------------
# Files: discovery, duplicates, text and image extraction
# ---------------------------------------------------------------------------


@dataclass
class DocRecord:
    file: str
    kind: str
    source_relpath: str
    sha1: str
    duplicate_of: str
    extractor: str
    text_chars: int
    used_vision: bool
    classifier: str
    standardized_category: str
    keep_decision: str
    target_folder: str
    confidence: float
    reason: str
    action: str
    result: str = ""
    error: str = ""
    second_pass: str = ""
    # seconds the model took for this file (0 when no model was asked)
    seconds: float = 0.0


def file_kind(path: Path) -> str:
    """pdf, image or doc (asked about), file (sorted by extension), or "" (left alone)."""
    name = path.name.lower()
    if name.startswith(".") or name.endswith(PARTIAL_EXTS):
        return ""  # hidden files (.DS_Store, .sortling.json) and unfinished downloads
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_TEXT_EXTS:
        return "doc"
    return "file" if ext else ""


def file_folder(profile: "Profile", path: Path) -> str:
    """The files/<folder> an extension-sorted file goes to under this profile, or "" to leave it."""
    ext = path.suffix.lower()
    if profile.layout == "custom":
        return profile.extensions.get(ext, "")
    for folder, exts in (profile.spec.get("files") or {}).items():
        if ext in exts:
            return f"{FILES_FOLDER}/{folder}"
    return ""


def wanted(profile: "Profile", path: Path, kinds: Sequence[str]) -> bool:
    kind = file_kind(path)
    if kind not in kinds:
        return False
    if profile.layout == "photos" and kind == "doc":
        return False  # the photos profile only sorts pictures and PDFs
    return kind != "file" or bool(file_folder(profile, path))


def is_in_sorted_folder(root: Path, file_path: Path, sorted_roots: Sequence[str]) -> bool:
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return False
    return len(rel.parts) > 1 and rel.parts[0] in sorted_roots


def list_source_files(root: Path, recursive: bool, kinds: Sequence[str], profile: "Profile") -> List[Path]:
    candidates = root.rglob("*") if recursive else root.iterdir()
    roots = sorted_roots(profile)
    return sorted(
        p
        for p in candidates
        if p.is_file() and not is_in_sorted_folder(root, p, roots) and wanted(profile, p, kinds)
    )


def count_supported(root: Path) -> Dict[str, int]:
    counts = {k: 0 for k in ALL_KINDS}
    try:
        for p in root.iterdir():
            k = file_kind(p) if p.is_file() else ""
            if k:
                counts[k] += 1
    except OSError:
        pass
    return counts


BIG_FILE_BYTES = 64 * 1024 * 1024


def sha1_file(path: Path) -> str:
    """Content hash. Files over 64 MB (videos, installers, archives) hash their size plus the
    first and last 8 MB, so a Downloads folder full of ISOs doesn't take minutes."""
    h = hashlib.sha1()
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > BIG_FILE_BYTES:
            h.update(str(size).encode())
            h.update(f.read(8 * 1024 * 1024))
            f.seek(size - 8 * 1024 * 1024)
            h.update(f.read())
            return "q" + h.hexdigest()[1:]
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def file_penalty(path: Path, source_set: set, root: Path) -> Tuple[int, int, int, int, str]:
    rel = str(path.relative_to(root))
    n = path.name.lower()
    in_source = 1 if path in source_set else 0
    deleteish = (DELETE_FOLDER, DUPLICATES_FOLDER, "98_Delete", "99_Duplicates")
    in_deleteish = 1 if rel.split("/", 1)[0] in deleteish else 0
    copyish = 1 if (" copy" in n or re.search(r"\(\d+\)\.[a-z0-9]+$", n) is not None) else 0
    numericish = 1 if re.match(r"^\d{7,}", n) else 0
    return (in_source, in_deleteish, copyish + numericish, len(n), n)


def build_duplicate_lookup(
    root: Path, source_files: Sequence[Path], kinds: Sequence[str], show_progress: bool, width: int
) -> Dict[Path, Path]:
    """Exact duplicates by content across the whole tree. The kept copy prefers already-sorted, non-'copy' names."""
    source_set = set(source_files)
    scan_files = sorted(p for p in root.rglob("*") if p.is_file() and file_kind(p) in kinds)
    hash_groups: Dict[str, List[Path]] = {}
    progress = ProgressBar(total=len(scan_files), label="Hash scan", enabled=show_progress, width=width)
    for idx, p in enumerate(scan_files, start=1):
        try:
            hash_groups.setdefault(sha1_file(p), []).append(p)
        except OSError:
            continue  # moved or deleted since the folder was listed (a sync, a download, the user)
        progress.update(idx, p.name)
    progress.finish("hash complete")
    duplicate_of: Dict[Path, Path] = {}
    for group in hash_groups.values():
        if len(group) < 2:
            continue
        primary = sorted(group, key=lambda p: file_penalty(p, source_set, root))[0]
        for p in group:
            if p != primary and p in source_set:
                duplicate_of[p] = primary
    return duplicate_of


def extract_ascii_pdf_strings(path: Path, max_chars: int) -> Tuple[str, str]:
    try:
        raw = path.read_bytes()[: 6 * 1024 * 1024]
    except Exception as exc:
        return "", f"ascii-scan:{exc}"
    out: List[str] = []
    total = 0
    for m in re.findall(rb"[ -~]{6,}", raw):
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


def extract_text_from_pdf(path: Path, max_pages: int, max_chars: int) -> Tuple[str, str]:
    errors: List[str] = []
    # testing: SORTLING_NO_PDF_TOOLS=1 behaves like a stock Mac (no pypdf, no pdftotext)
    no_tools = os.environ.get("SORTLING_NO_PDF_TOOLS") == "1"
    for module, reader_name in (() if no_tools else (("pypdf", "PdfReader"), ("PyPDF2", "PdfReader"))):
        try:
            reader_cls = getattr(__import__(module), reader_name)
            reader = reader_cls(str(path))
            chunks = []
            for i in range(min(len(reader.pages), max_pages)):
                text = reader.pages[i].extract_text() or ""
                if text.strip():
                    chunks.append(text.strip())
                if len("\n\n".join(chunks)) >= max_chars:
                    break
            joined = "\n\n".join(chunks)
            if joined.strip():
                return joined[:max_chars], module
            errors.append(f"{module}:no-text")
        except ImportError:
            errors.append(f"{module}:missing")
        except Exception as exc:
            errors.append(f"{module}:{exc}")

    if shutil.which("pdftotext") and not no_tools:
        try:
            cmd = ["pdftotext", "-f", "1", "-l", str(max_pages), "-layout", str(path), "-"]
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout[:max_chars], "pdftotext"
            errors.append(f"pdftotext:rc{proc.returncode}")
        except Exception as exc:
            errors.append(f"pdftotext:{exc}")
    else:
        errors.append("pdftotext:missing")

    # no PDF tools (a stock Mac, a minimal Linux): read the text streams ourselves
    try:
        text = builtin_pdf_text(path, max_chars)
        if readable(text):
            return text, "builtin-pdf"
        errors.append("builtin-pdf:no-text")
    except Exception as exc:
        errors.append(f"builtin-pdf:{type(exc).__name__}")

    text, source = extract_ascii_pdf_strings(path, max_chars=max_chars)
    if text.strip():
        return text[:max_chars], source
    errors.append(source)
    return "", ";".join(errors)


def _xml_text(xml: bytes, para_tags: Sequence[bytes]) -> str:
    """Text content of an Office/OpenDocument XML part, with a newline at each paragraph-like tag."""
    for tag in para_tags:
        xml = xml.replace(b"</" + tag + b">", b"\n")
    text = re.sub(rb"<[^>]+>", b"", xml).decode("utf-8", "replace")
    for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(ent, ch)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def extract_text_from_doc(path: Path, max_chars: int) -> Tuple[str, str]:
    """Text from docx/pptx/xlsx, OpenDocument, RTF and plain text, with the standard library only."""
    import zipfile

    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md", ".markdown"):
            with path.open("rb") as f:
                return f.read(max_chars * 4).decode("utf-8", "replace")[:max_chars], "text"
        if ext == ".rtf":
            raw = path.read_bytes()[: max_chars * 8].decode("latin-1", "replace")
            raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "replace"), raw)
            raw = re.sub(r"\\par[d]?\b|\\line\b", "\n", raw)
            raw = re.sub(r"\{\\\*[^{}]*\}|\\[a-zA-Z]+-?\d* ?|[{}]", "", raw)
            return re.sub(r"\n\s*\n+", "\n", raw).strip()[:max_chars], "rtf"
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if ext == ".docx":
                parts = ["word/document.xml"] + sorted(n for n in names if re.match(r"word/(header|footer)\d*\.xml", n))
                tags: Sequence[bytes] = (b"w:p",)
            elif ext == ".pptx":
                parts = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                               key=lambda n: int(re.findall(r"\d+", n)[-1]))
                tags = (b"a:p",)
            elif ext == ".xlsx":
                parts = [n for n in ("xl/sharedStrings.xml",) if n in names] + sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
                tags = (b"si", b"row")
            else:  # OpenDocument: odt, odp, ods
                parts, tags = ["content.xml"], (b"text:p", b"text:h", b"table:table-row")
            chunks, total = [], 0
            for part in parts:
                if part not in names:
                    continue
                text = _xml_text(z.read(part), tags)
                if text:
                    chunks.append(text)
                    total += len(text)
                if total >= max_chars:
                    break
            return "\n".join(chunks)[:max_chars], ext.lstrip(".")
    except Exception as exc:
        return "", f"{ext.lstrip('.')}:{type(exc).__name__}"


_PDF_STRING = re.compile(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<[0-9A-Fa-f\s]*>")
_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _pdf_literal(tok: bytes) -> bytes:
    """The bytes of a PDF string token: (literal with escapes) or <hex>."""
    if tok.startswith(b"<"):
        h = re.sub(rb"\s", b"", tok[1:-1])
        return bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
    body, out, i = tok[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i:i + 1]
        if c == b"\\" and i + 1 < len(body):
            n = body[i + 1:i + 2]
            if n in _PDF_ESCAPES:
                out += _PDF_ESCAPES[n]
                i += 2
            elif n.isdigit():
                m = re.match(rb"[0-7]{1,3}", body[i + 1:i + 4])
                out.append(int(m.group(0), 8) & 0xFF)
                i += 1 + len(m.group(0))
            else:
                i += 2  # line continuation or unknown escape
        else:
            out += c
            i += 1
    return bytes(out)


def _pdf_cmap(data: bytes) -> Dict[bytes, str]:
    """A ToUnicode CMap: glyph code bytes -> text."""
    cmap: Dict[bytes, str] = {}

    def uni(hexstr: bytes) -> str:
        b = bytes.fromhex(hexstr.decode())
        return b.decode("utf-16-be", "replace")

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            cmap[bytes.fromhex(src.decode())] = uni(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block):
            a, b, width = int(lo, 16), int(hi, 16), len(lo) // 2
            if b - a > 5000:
                continue
            if dst.startswith(b"["):
                for k, d in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", dst)):
                    cmap[(a + k).to_bytes(width, "big")] = uni(d)
            else:
                start = int(dst[1:-1], 16)
                for k in range(b - a + 1):
                    cmap[(a + k).to_bytes(width, "big")] = chr(start + k) if start + k < 0x110000 else ""
    return cmap


def _pdf_inflate(head: bytes, raw: bytes) -> Optional[bytes]:
    """A stream's decoded bytes: Flate or unfiltered only; None for anything else."""
    import zlib

    if b"/FlateDecode" in head:
        try:
            return zlib.decompressobj().decompress(raw)
        except zlib.error:
            return None
    return None if b"/Filter" in head else raw


def _pdf_objects(data: bytes) -> Dict[int, Tuple[bytes, Optional[bytes]]]:
    """Object number -> (dictionary text, decoded stream or None), including objects packed in
    object streams (PDF 1.5+)."""
    objs: Dict[int, Tuple[bytes, Optional[bytes]]] = {}
    for m in re.finditer(rb"(?<![\d])(\d+)\s+\d+\s+obj\b", data):
        start = m.end()
        end = data.find(b"endobj", start)
        if end < 0:
            continue
        body = data[start:end]
        sm = re.search(rb"stream\r?\n", body)
        if sm and b"endstream" in body[sm.end():]:
            head = body[:sm.start()]
            objs[int(m.group(1))] = (head, _pdf_inflate(head, body[sm.end():body.rfind(b"endstream")]))
        else:
            objs[int(m.group(1))] = (body, None)
    for head, raw in list(objs.values()):
        if raw is None or not re.search(rb"/Type\s*/ObjStm", head):
            continue
        first = re.search(rb"/First\s+(\d+)", head)
        if not first:
            continue
        off0 = int(first.group(1))
        nums = [int(x) for x in raw[:off0].split()]
        pairs = list(zip(nums[0::2], nums[1::2]))
        for i, (onum, off) in enumerate(pairs):
            end = pairs[i + 1][1] if i + 1 < len(pairs) else len(raw) - off0
            objs.setdefault(onum, (raw[off0 + off:off0 + end], None))
    return objs


def _pdf_ref(text: bytes, key: bytes) -> Optional[int]:
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", text)
    return int(m.group(1)) if m else None


def _pdf_pages(data: bytes, objs: Dict[int, Tuple[bytes, Optional[bytes]]]) -> List[Tuple[int, bytes]]:
    """(page object, inherited resources text) in reading order, from the catalog's page tree."""
    roots = re.findall(rb"/Root\s+(\d+)\s+\d+\s+R", data)
    if not roots or int(roots[-1]) not in objs:
        return []
    top = _pdf_ref(objs[int(roots[-1])][0], b"Pages")
    out: List[Tuple[int, bytes]] = []
    stack: List[Tuple[int, bytes]] = [(top, b"")] if top else []
    seen = set()
    while stack and len(out) < 2000:
        num, res = stack.pop()
        if num in seen or num not in objs:
            continue
        seen.add(num)
        node = objs[num][0]
        here = re.search(rb"/Resources\s*(\d+\s+\d+\s+R|<<.*)", node, re.S)
        res = node if here else res
        kids = re.search(rb"/Kids\s*\[(.*?)\]", node, re.S)
        if kids:
            refs = [int(k) for k in re.findall(rb"(\d+)\s+\d+\s+R", kids.group(1))]
            stack.extend((k, res) for k in reversed(refs))
        elif re.search(rb"/Type\s*/Page\b", node):
            out.append((num, res))
    return out


def _pdf_fonts(res: bytes, objs: Dict[int, Tuple[bytes, Optional[bytes]]], cache: Dict[int, Dict[bytes, str]]) -> Dict[bytes, Dict[bytes, str]]:
    """Font resource name -> its ToUnicode map, for one page's resources."""
    rref = re.search(rb"/Resources\s+(\d+)\s+\d+\s+R", res)
    if rref and int(rref.group(1)) in objs:
        res = objs[int(rref.group(1))][0]
    fref = _pdf_ref(res, b"Font")
    m = re.search(rb"/Font\s*<<(.*?)>>", res, re.S)
    fontdict = objs[fref][0] if fref and fref in objs else (m.group(1) if m else b"")
    fonts: Dict[bytes, Dict[bytes, str]] = {}
    for name, onum in re.findall(rb"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R", fontdict):
        onum_i = int(onum)
        if onum_i not in cache:
            tu = _pdf_ref(objs[onum_i][0], b"ToUnicode") if onum_i in objs else None
            stream = objs.get(tu, (b"", None))[1] if tu else None
            cache[onum_i] = _pdf_cmap(stream) if stream else {}
        fonts[name] = cache[onum_i]
    return fonts


_PDF_OPS = re.compile(
    rb"(\[(?:[^\[\]]|\((?:\\.|[^\\)])*\))*\])\s*TJ"  # 1: TJ array
    rb"|(" + _PDF_STRING.pattern + rb")\s*(?:Tj|'|\")"  # 2: one string
    rb"|/([^\s/\[\]()<>]+)\s+-?[\d.]+\s+Tf"  # 3: font switch
    rb"|\b(T\*|Tm|BT|ET)\b"  # 4: new line or new position
    rb"|(-?[\d.]+)\s+(-?[\d.]+)\s+T[dD]\b",  # 5, 6: a move by (tx, ty)
    re.S,
)


def _pdf_decode(b: bytes, cmap: Dict[bytes, str], widths: Sequence[int]) -> str:
    if not cmap:
        return b.decode("latin-1")
    out, i = [], 0
    while i < len(b):
        for w in widths:
            ch = cmap.get(b[i:i + w])
            if ch is not None:
                out.append(ch)
                i += w
                break
        else:
            out.append(chr(b[i]) if 32 <= b[i] < 127 else "")
            i += 1
    return "".join(out)


def _pdf_stream_text(stream: bytes, fonts: Dict[bytes, Dict[bytes, str]], max_chars: int) -> List[str]:
    lines: List[str] = []
    line: List[str] = []
    cmap: Dict[bytes, str] = {}
    widths: List[int] = []

    def flush() -> None:
        text = re.sub(r"\s+", " ", "".join(line)).strip()
        if text:
            lines.append(text)
        line.clear()

    for op in _PDF_OPS.finditer(stream):
        if op.group(3) is not None:
            cmap = fonts.get(op.group(3), {})
            widths = sorted({len(k) for k in cmap}, reverse=True)
        elif op.group(1):
            for tok in re.finditer(_PDF_STRING.pattern + rb"|(-?\d+\.?\d*)", op.group(1)):
                if tok.group(1):
                    if float(tok.group(1)) < -200:
                        line.append(" ")
                else:
                    line.append(_pdf_decode(_pdf_literal(tok.group(0)), cmap, widths))
        elif op.group(2):
            line.append(_pdf_decode(_pdf_literal(op.group(2)), cmap, widths))
        elif op.group(6) is not None:
            # a vertical move starts a new line; a horizontal one (glyph by glyph, as Chrome
            # writes PDFs) stays on it: real spaces are glyphs of their own
            if abs(float(op.group(6))) > 0.01:
                flush()
        elif op.group(4) in (b"BT", b"ET", b"T*"):
            flush()
        elif op.group(4) == b"Tm" and line and not "".join(line).endswith(" "):
            line.append(" ")
        if sum(len(x) for x in lines) >= max_chars:
            break
    flush()
    return lines


def builtin_pdf_text(path: Path, max_chars: int, max_bytes: int = 40 * 1024 * 1024) -> str:
    """Rough PDF text with the standard library: unpack object streams, walk the pages in order,
    read the text operators and decode each font's glyph codes through its own ToUnicode map.
    Good enough to classify a document, not to reproduce its layout."""
    data = path.read_bytes()[:max_bytes]
    objs = _pdf_objects(data)
    cache: Dict[int, Dict[bytes, str]] = {}
    lines: List[str] = []
    for num, res in _pdf_pages(data, objs):
        page = objs[num][0]
        contents = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", page)
        refs = [int(r) for r in re.findall(rb"(\d+)\s+\d+\s+R", contents.group(1))] if contents else []
        stream = b"\n".join(objs[r][1] or b"" for r in refs if r in objs)
        if stream:
            lines += _pdf_stream_text(stream, _pdf_fonts(res, objs, cache), max_chars)
        if sum(len(x) for x in lines) >= max_chars:
            break
    if not lines:
        # no usable page tree: every text stream in file order, all fonts' maps merged
        merged: Dict[bytes, str] = {}
        for head, raw in objs.values():
            if raw and b"begincmap" in raw:
                merged.update(_pdf_cmap(raw))
        for head, raw in objs.values():
            if raw and re.search(rb"\b(?:Tj|TJ)\b", raw) and b"begincmap" not in raw:
                lines += _pdf_stream_text(raw, {}, max_chars) if not merged else _pdf_stream_text(
                    b"/_ 1 Tf " + raw, {b"_": merged}, max_chars)
            if sum(len(x) for x in lines) >= max_chars:
                break
    return "\n".join(lines)[:max_chars]


def readable(text: str) -> bool:
    """Real text rather than glyph-code garbage: mostly letters, digits, spaces and everyday
    punctuation, with a fair share of letters. Invoices and statements are full of digits and
    symbols, so those count as good."""
    if len(text) < 40:
        return False
    good = sum(ch.isalnum() or ch.isspace() or ch in ".,:;!?$%#@&*+-=/\\()[]'\"_€£¥₹" for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    return good / len(text) >= 0.85 and letters / len(text) >= 0.25


def render_first_page_png_base64(path: Path, max_dim: int = 1100) -> Tuple[Optional[str], str]:
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(path))
        if doc.page_count < 1:
            doc.close()
            return None, "fitz:no-pages"
        page = doc.load_page(0)
        scale = min(max_dim / max(page.rect.width, 1.0), max_dim / max(page.rect.height, 1.0))
        scale = max(0.7, min(scale, 2.0))
        raw = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
        doc.close()
        return base64.b64encode(raw).decode("ascii"), "fitz"
    except ImportError:
        pass
    except Exception as exc:
        return None, f"fitz:{exc}"

    if shutil.which("pdftoppm"):
        try:
            # no output root: pdftoppm writes to stdout (a "-" root writes a file named "-.png")
            cmd = ["pdftoppm", "-png", "-f", "1", "-l", "1", "-singlefile", "-scale-to", str(max_dim), str(path)]
            proc = subprocess.run(cmd, check=False, capture_output=True)
            if proc.returncode == 0 and proc.stdout:
                return base64.b64encode(proc.stdout).decode("ascii"), "pdftoppm"
            return None, f"pdftoppm:rc{proc.returncode}"
        except Exception as exc:
            return None, f"pdftoppm:{exc}"

    if shutil.which("qlmanage"):
        try:
            with tempfile.TemporaryDirectory(prefix="sortling_preview_") as tmp:
                cmd = ["qlmanage", "-t", "-s", str(max_dim), "-o", tmp, str(path)]
                proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
                if proc.returncode != 0:
                    return None, f"qlmanage:rc{proc.returncode}"
                previews = sorted(Path(tmp).glob("*.png"))
                if not previews:
                    return None, "qlmanage:no-preview"
                return base64.b64encode(previews[0].read_bytes()).decode("ascii"), "qlmanage"
        except Exception as exc:
            return None, f"qlmanage:{exc}"

    return None, "fitz:missing;pdftoppm:missing;qlmanage:missing"


def load_image_base64(path: Path) -> Tuple[Optional[str], str, str]:
    """Return (base64, mime, source). Formats llama.cpp can't decode are converted to PNG."""
    mime = NATIVE_IMAGE_MIME.get(path.suffix.lower())
    if mime:
        try:
            return base64.b64encode(path.read_bytes()).decode("ascii"), mime, "file"
        except Exception as exc:
            return None, "", f"read:{exc}"
    # sips ships with macOS; ImageMagick elsewhere
    if shutil.which("sips") and platform.system() == "Darwin":
        with tempfile.TemporaryDirectory(prefix="sortling_img_") as tmp:
            out = Path(tmp) / "img.png"
            proc = subprocess.run(["sips", "-s", "format", "png", str(path), "--out", str(out)], check=False, capture_output=True)
            if proc.returncode == 0 and out.exists():
                return base64.b64encode(out.read_bytes()).decode("ascii"), "image/png", "sips"
    tool = shutil.which("magick") or shutil.which("convert")
    if not tool:
        return None, "", "convert:missing"
    try:
        # [0] takes the first frame of animated or multi-page files
        proc = subprocess.run([tool, f"{path}[0]", "png:-"], check=False, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            return base64.b64encode(proc.stdout).decode("ascii"), "image/png", "magick"
        return None, "", f"magick:rc{proc.returncode}"
    except Exception as exc:
        return None, "", f"magick:{exc}"


def build_state(filename: str, kind: str, text_excerpt: str, has_image: bool) -> str:
    if kind == "pdf":
        what = "PDF document" + (", first page shown above" if has_image else "")
    elif kind == "doc":
        what = f"{Path(filename).suffix.lstrip('.').upper() or 'text'} document"
    else:
        what = "image file (screenshot, photo or scan)" + (", shown above" if has_image else ", not shown")
    lines = [f"Filename: {filename}", f"File type: {what}"]
    if text_excerpt:
        lines += ["Extracted text (may be noisy):", text_excerpt]
    elif kind in ("pdf", "doc"):
        lines.append("Extracted text: none")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def to_float_0_1(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


def extract_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
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


def is_timeout_like_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or (reason and "timed out" in str(reason).lower()):
            return True
    txt = str(exc).lower()
    return "timed out" in txt or "timeout" in txt


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Planning, applying, undo
# ---------------------------------------------------------------------------


@dataclass
class RunOptions:
    root: Path
    kinds: Tuple[str, ...]
    recursive: bool
    profile: Profile
    backend: Optional[Backend]
    thresholds: Thresholds
    # hybrid: uncertain first-pass answers get a second look from this (usually thorough) backend
    second: Optional[Backend] = None
    vision_mode: str = "auto"
    vision_min_text_chars: int = 400
    max_pages: int = 3
    max_chars: int = 7000
    timeout_s: int = 120
    retry_timeouts: int = 1
    retry_backoff_sec: float = 1.2
    max_consecutive_timeouts: int = 3
    delete_exact_duplicates: bool = False
    hard_delete: bool = False
    progress: bool = True
    progress_width: int = 32
    face: bool = True
    status: str = ""
    second_status: str = ""


def sorted_roots(profile: Profile) -> Tuple[str, ...]:
    return (*profile.roots(), *SYSTEM_FOLDERS, *LEGACY_TARGET_FOLDERS)


def _check(backend: Backend, timeout_s: int) -> str:
    """Empty string when the backend answers, otherwise why it can't."""
    try:
        # first call may wait for a router to load the model
        backend.precheck(timeout_s=max(timeout_s, 60))
        return ""
    except Exception as exc:
        detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)[:80]
        return f"{type(exc).__name__}:{detail}"


def precheck_backend(opts: RunOptions) -> None:
    """Disable backends that can't answer, recording why. A missing first pass falls back to keywords."""
    if opts.backend is None:
        opts.status = "fallback:disabled"
    else:
        wait = FaceWait(f"waking {opts.backend.name}...", opts.progress and opts.face)
        why = _check(opts.backend, opts.timeout_s)
        wait.close(f"{opts.backend.name} {'is awake' if not why else 'did not answer'}")
        opts.status = f"fallback:{opts.backend.name}-unavailable:{why}" if why else f"{opts.backend.mode}:{opts.backend.name}"
        if why:
            opts.backend = None
    if opts.second is not None:
        why = _check(opts.second, opts.timeout_s) if opts.backend is not None else "first pass unavailable"
        opts.second_status = f"off:{opts.second.name}-unavailable:{why}" if why else f"{opts.second.mode}:{opts.second.name}"
        if why:
            opts.second = None


@dataclass
class FileInput:
    path: Path
    kind: str
    text: str
    extractor: str
    image_b64: Optional[str]
    image_mime: str
    vision_source: str


def prepare_input(path: Path, opts: RunOptions) -> FileInput:
    kind = file_kind(path)
    image_b64: Optional[str] = None
    image_mime = "image/png"
    vision_source = ""
    if kind == "pdf":
        text, extractor = extract_text_from_pdf(path, opts.max_pages, opts.max_chars)
        # ascii-scan output on scanned PDFs is mostly PDF syntax, not document text
        weak_text = extractor.startswith("ascii-scan")
        use_vision = opts.vision_mode == "always" or (
            opts.vision_mode == "auto" and (weak_text or len(text) < opts.vision_min_text_chars)
        )
        if use_vision:
            image_b64, vision_source = render_first_page_png_base64(path)
            if image_b64 is None:
                extractor = f"{extractor};{vision_source}"
        if weak_text and image_b64 is not None:
            text = ""
    elif kind == "doc":
        text, extractor = extract_text_from_doc(path, opts.max_chars)
    elif kind == "file":
        text, extractor = "", "extension"
    else:
        # images are classified from the pixels; there is no OCR step
        text, extractor = "", "image"
        if opts.vision_mode != "off":
            image_b64, image_mime, vision_source = load_image_base64(path)
            if image_b64 is None:
                extractor = f"image;{vision_source}"
    return FileInput(path, kind, text, extractor, image_b64, image_mime, vision_source)


def classify(backend: Backend, fi: FileInput, opts: RunOptions) -> Tuple[Routed, Optional[Exception]]:
    """Ask one backend about one file, with timeout retries. On failure, keyword rules decide."""
    sent_image = fi.image_b64 if backend.vision else None
    questions = build_questions(opts.profile, fi.kind, sent_image is not None)
    state = build_state(fi.path.name, fi.kind, fi.text, sent_image is not None)
    images = [f"data:{fi.image_mime};base64,{sent_image}"] if sent_image else []
    err: Optional[Exception] = None
    for attempt in range(opts.retry_timeouts + 1):
        try:
            answers, why = backend.decide(state, images, questions, opts.timeout_s)
            routed = route(opts.profile, answers, opts.thresholds)
            if why:
                routed.reason = f"{routed.reason} | {why}"
            return routed, None
        except Exception as exc:
            err = exc
            if is_timeout_like_error(exc) and attempt < opts.retry_timeouts:
                time.sleep(max(0.0, opts.retry_backoff_sec) * (attempt + 1))
                continue
            break
    routed = fallback_classify(opts.profile, fi.path.name, fi.text)
    routed.reason = f"{routed.reason} | model_error:{err}"
    return routed, err


def safe_sha1(path: Path) -> str:
    try:
        return sha1_file(path)
    except OSError:
        return ""


def pdf_renderer_available() -> bool:
    try:
        import fitz  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return bool(shutil.which("pdftoppm") or shutil.which("qlmanage"))


def latency_summary(records: Sequence["DocRecord"]) -> dict:
    """Per-file model time: median, p90, max, and how many files were far slower than typical."""
    t = sorted(r.seconds for r in records if r.seconds > 0)
    if not t:
        return {}
    med = t[len(t) // 2]
    return {"files_asked": len(t), "median_s": round(med, 2), "p90_s": round(t[min(len(t) - 1, 9 * len(t) // 10)], 2),
            "max_s": round(t[-1], 2), "slow_files": sum(1 for x in t if x > max(5.0, 8 * med))}


def plan(opts: RunOptions, on_file: Optional[Callable[[DocRecord], None]] = None) -> List[DocRecord]:
    root = opts.root
    source_files = list_source_files(root, opts.recursive, opts.kinds, opts.profile)
    if not source_files:
        return []

    stage_note("Stage 1/3: scanning duplicates", opts.progress)
    duplicate_of = build_duplicate_lookup(root, source_files, opts.kinds, opts.progress, opts.progress_width)

    if any(file_kind(p) == "pdf" for p in source_files) and not pdf_renderer_available():
        print(f"{icon('warn')} no PDF renderer (PyMuPDF, pdftoppm or qlmanage): scanned PDFs with no text are "
              "judged by filename only. Install poppler (pdftoppm) to fix.", file=sys.stderr)

    stage_note("Stage 2/3: extracting + classifying files", opts.progress)
    bar = make_bar(len(source_files), "Classify", opts.progress, opts.progress_width, opts.face)
    consecutive_timeouts = 0
    timings: List[float] = []
    warned_slow = False
    vanished = 0
    records: List[DocRecord] = []
    uncertain: List[Tuple[DocRecord, FileInput]] = []
    for idx, path in enumerate(source_files, start=1):
        if not path.is_file():
            vanished += 1  # moved or deleted since the folder was listed
            continue
        try:
            fi = prepare_input(path, opts)
        except OSError:
            vanished += 1
            continue
        backend = opts.backend
        if backend is not None and fi.kind == "image" and not backend.vision:
            backend = None  # a text-only model would only see the filename
            classifier = f"fallback:{opts.backend.name}-text-only" if opts.backend else "fallback"
        else:
            classifier = opts.status if backend is None else f"{backend.mode}:{backend.name}"

        is_uncertain = False
        asked = 0.0
        rule_folder = file_folder(opts.profile, path) if fi.kind == "file" else ""
        if fi.kind == "doc" and not readable(fi.text):
            # a document we couldn't read: sort it by its extension instead
            rule_folder = f"{FILES_FOLDER}/documents" if opts.profile.layout != "custom" else opts.profile.extensions.get(path.suffix.lower(), REVIEW_FOLDER)
        if rule_folder:
            routed = Routed(f"file:{rule_folder.rsplit('/', 1)[-1]}", "keep", rule_folder, 1.0, f"rule:extension {path.suffix.lower()}")
            classifier = "rule:extension"
        elif backend is not None:
            t_ask = time.time()
            routed, err = classify(backend, fi, opts)
            asked = time.time() - t_ask
            timings.append(asked)
            # a shared router reloading the model (another app asked for a different one) stalls
            # files without any error; say so instead of looking slow for no reason
            if len(timings) > 5 and not warned_slow:
                typical = sorted(timings)[len(timings) // 2]
                if asked > max(5.0, 8 * typical):
                    warned_slow = True
                    print(f"\n{icon('warn')} {path.name} took {asked:.0f}s against a typical {typical:.1f}s: the model "
                          "may be reloading (is another app using the same server?)", file=sys.stderr)
            is_uncertain = err is None and routed.uncertain
            if err is not None:
                classifier = f"fallback:{type(err).__name__}"
                if is_timeout_like_error(err):
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= max(1, opts.max_consecutive_timeouts):
                        opts.backend = None
                        opts.status = "fallback:timeouts"
            else:
                consecutive_timeouts = 0
        else:
            routed = fallback_classify(opts.profile, path.name, fi.text)

        rec = make_record(root, fi, routed, classifier, duplicate_of)
        rec.seconds = round(asked, 3)
        assign_action(rec, opts.delete_exact_duplicates, opts.hard_delete)
        records.append(rec)
        if is_uncertain and not rec.duplicate_of:
            uncertain.append((rec, fi))
        bar.update(idx, f"{path.name} -> {rec.target_folder}")
        if isinstance(bar, FaceBar):
            bar.react(mood_for(rec), rec.target_folder)
        if on_file:
            on_file(rec)
    bar.finish("classification complete")
    if vanished:
        print(f"{icon('warn')} {vanished} file(s) moved or disappeared during the run and were skipped", file=sys.stderr)

    if opts.second is not None and uncertain:
        second_pass(opts, uncertain)
    return records


def second_pass(opts: RunOptions, uncertain: Sequence[Tuple[DocRecord, FileInput]]) -> None:
    """Hybrid: re-ask the uncertain files with the second backend; its answer wins."""
    second = opts.second
    assert second is not None
    stage_note(f"Stage 2b/3: second look at {len(uncertain)} uncertain files ({second.mode}:{second.name})", opts.progress)
    bar = make_bar(len(uncertain), "Re-check", opts.progress, opts.progress_width, opts.face)
    for idx, (rec, fi) in enumerate(uncertain, start=1):
        if fi.kind == "image" and not second.vision:
            rec.second_pass = f"skipped:{second.name}-text-only"
        else:
            routed, err = classify(second, fi, opts)
            if err is not None:
                rec.second_pass = f"failed:{type(err).__name__}"
            else:
                first = rec.target_folder
                rec.second_pass = f"{second.mode}:{second.name}:{first}->{routed.folder}"
                rec.classifier = f"hybrid:{rec.classifier}>{second.mode}:{second.name}"
                rec.standardized_category = routed.category
                rec.keep_decision = routed.decision
                rec.target_folder = routed.folder
                rec.confidence = round(routed.confidence, 4)
                rec.reason = (f"{routed.reason} | first:{first}" + (f" | vision:{fi.vision_source}" if fi.image_b64 else ""))[:260]
                assign_action(rec, opts.delete_exact_duplicates, opts.hard_delete)
        bar.update(idx, f"{rec.file} -> {rec.target_folder}")
        if isinstance(bar, FaceBar):
            bar.react(mood_for(rec), rec.target_folder)
    bar.finish("second pass complete")


def make_record(root: Path, fi: FileInput, routed: Routed, classifier: str, duplicate_of: Dict[Path, Path]) -> DocRecord:
    reason = routed.reason
    if fi.image_b64 is not None and fi.vision_source:
        reason = f"{reason} | vision:{fi.vision_source}"
    return DocRecord(
        file=fi.path.name,
        kind=fi.kind,
        source_relpath=str(fi.path.relative_to(root)),
        sha1=safe_sha1(fi.path),
        duplicate_of=str(duplicate_of[fi.path].relative_to(root)) if fi.path in duplicate_of else "",
        extractor=fi.extractor,
        text_chars=len(fi.text),
        used_vision=fi.image_b64 is not None,
        classifier=classifier,
        standardized_category=routed.category,
        keep_decision=routed.decision,
        target_folder=routed.folder,
        confidence=round(routed.confidence, 4),
        reason=reason[:260],
        action="",
    )


def assign_action(record: DocRecord, delete_exact_duplicates: bool, hard_delete: bool) -> None:
    if record.duplicate_of:
        if delete_exact_duplicates:
            record.action = "DELETE_EXACT_DUPLICATE"
        else:
            record.target_folder = DUPLICATES_FOLDER
            record.action = "MOVE_TO_DUPLICATES"
        return
    if record.keep_decision == "delete_candidate" and hard_delete:
        record.action = "DELETE_CANDIDATE"
        return
    record.action = "MOVE"


def unique_destination(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


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


def apply_all(opts: RunOptions, records: Sequence[DocRecord]) -> None:
    stage_note("Stage 3/3: applying actions", opts.progress)
    bar = make_bar(len(records), "Apply", opts.progress, opts.progress_width, opts.face)
    for idx, rec in enumerate(records, start=1):
        try:
            apply_record(opts.root, rec)
        except Exception as exc:
            rec.error = str(exc)
            rec.result = "ERROR"
        bar.update(idx, f"{rec.action} {rec.file}")
    bar.finish("apply complete")


def manifest_payload(opts: RunOptions, records: Sequence[DocRecord], dry_run: bool) -> dict:
    return {
        "generated_at": datetime.now().isoformat(),
        "sortling_version": __version__,
        "mode": "dry_run" if dry_run else "apply",
        "root": str(opts.root),
        "profile": opts.profile.name,
        "profile_source": opts.profile.source,
        "profile_roots": list(opts.profile.roots()),
        "classifier": opts.status,
        "second_pass": opts.second_status,
        "summary": {
            "files": len(records),
            "by_folder": {f: sum(1 for r in records if r.target_folder == f) for f in sorted({r.target_folder for r in records})},
            "latency": latency_summary(records),
        },
        "records": [asdict(r) for r in records],
    }


def write_logs(output_dir: Path, opts: RunOptions, records: Sequence[DocRecord], dry_run: bool) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    csv_path = output_dir / f"sortling_manifest_{stamp}.csv"
    json_path = output_dir / f"sortling_manifest_{stamp}.json"
    fieldnames = [f.name for f in DocRecord.__dataclass_fields__.values()]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            writer.writerow(asdict(rec))
    payload = manifest_payload(opts, records, dry_run)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


def write_review_note(root: Path, records: Sequence[DocRecord], apply: bool) -> Path:
    review_dir = root / REVIEW_FOLDER
    review_dir.mkdir(parents=True, exist_ok=True)
    note = review_dir / "REVIEW_LIST.txt"
    if apply:
        files = sorted(p.name for p in review_dir.iterdir() if p.is_file() and file_kind(p))
        lines = ["Manual review list (current snapshot)", f"Total files in {REVIEW_FOLDER}: {len(files)}", ""]
        lines.extend(f"- {name}" for name in files)
    else:
        candidates = sorted(rec.file for rec in records if rec.target_folder == REVIEW_FOLDER)
        lines = ["Manual review list (dry-run projection)", f"Would route to {REVIEW_FOLDER}: {len(candidates)}", ""]
        lines.extend(f"- {name}" for name in candidates)
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note


def undo(manifest: Path) -> int:
    """Move files from an applied manifest back where they came from. Deleted files can't return."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("mode") != "apply" or "root" not in data:
        print(f"error: {manifest} is not an applied sortling manifest", file=sys.stderr)
        return 2
    root = Path(data["root"])
    restored = skipped = gone = 0
    for rec in data.get("records", []):
        result = str(rec.get("result", ""))
        if result == "DELETED":
            gone += 1
            continue
        if not result.startswith("MOVED:"):
            continue
        dest, src = root / result[len("MOVED:"):], root / rec["source_relpath"]
        if not dest.exists() or src.exists():
            print(f"skip: {rec['source_relpath']} ({'moved again or removed' if not dest.exists() else 'original path is taken'})")
            skipped += 1
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(src))
        restored += 1
    # remove folders the run created, if they are now empty
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            if d != root and not any(d.iterdir()) and d.relative_to(root).parts[0] in (*SYSTEM_FOLDERS, *data.get("profile_roots", DEFAULT_PROFILE_ROOTS)):
                d.rmdir()
        except OSError:
            pass
    print(f"UNDO: restored {restored}, skipped {skipped}, deleted-not-restorable {gone}")
    return 0


def summarize(records: Sequence[DocRecord]) -> str:
    by_action: Dict[str, int] = {}
    by_folder: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for rec in records:
        by_action[rec.action] = by_action.get(rec.action, 0) + 1
        by_folder[rec.target_folder] = by_folder.get(rec.target_folder, 0) + 1
        by_source[rec.classifier] = by_source.get(rec.classifier, 0) + 1
    parts = ["Action counts:", *(f"- {k}: {v}" for k, v in sorted(by_action.items()))]
    parts += ["Target folder counts:", *(f"- {k}: {v}" for k, v in sorted(by_folder.items()))]
    parts += ["Classifier source:", *(f"- {k}: {v}" for k, v in sorted(by_source.items()))]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


def ask(prompt: str, default: str = "", paths: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    restore = _enable_path_completion() if paths else None
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raise KeyboardInterrupt
    finally:
        if restore:
            restore()
    return value or default


def _enable_path_completion() -> Optional[Callable[[], None]]:
    """Tab-completes paths in input() where readline exists (macOS, Linux)."""
    try:
        import glob
        import readline
    except ImportError:
        return None
    old_completer, old_delims = readline.get_completer(), readline.get_completer_delims()

    def complete(text: str, state: int) -> Optional[str]:
        matches = [m + ("/" if os.path.isdir(m) else "") for m in glob.glob(os.path.expanduser(text) + "*")]
        return matches[state] if state < len(matches) else None

    readline.set_completer_delims("\t\n;")
    readline.set_completer(complete)
    readline.parse_and_bind("tab: complete")

    def restore() -> None:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)

    return restore


def ask_yes(prompt: str, default: bool = False) -> bool:
    raw = ask(f"{prompt} ({'Y/n' if default else 'y/N'})", "").lower()
    return default if not raw else raw.startswith("y")


def menus_supported() -> bool:
    if os.environ.get("SORTLING_MENUS", "").lower() in ("0", "off", "numbers"):
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()) or os.environ.get("TERM", "dumb") in ("", "dumb"):
        return False
    try:
        import termios  # noqa: F401
        return True
    except ImportError:
        try:
            import msvcrt  # noqa: F401
            return True
        except ImportError:
            return False


class _RawKeys:
    """Reads single keys: 'up', 'down', 'enter', 'esc', 'backspace', or the character."""

    def __enter__(self) -> "_RawKeys":
        self.win = False
        try:
            import termios
            import tty

            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        except ImportError:
            self.win = True
        return self

    def __exit__(self, *exc) -> None:
        if not self.win:
            import termios

            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def read(self) -> str:
        if self.win:
            import msvcrt

            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
        else:
            import select

            ch = os.read(self.fd, 1).decode("utf-8", "replace")
            if ch == "\x1b":
                seq = ""
                while select.select([self.fd], [], [], 0.03)[0]:
                    seq += os.read(self.fd, 1).decode("utf-8", "replace")
                if not seq:
                    return "esc"
                return {"[A": "up", "[B": "down", "OA": "up", "OB": "down", "[5~": "pgup", "[6~": "pgdn"}.get(seq, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x7f", "\x08"):
            return "backspace"
        return ch


def _visible(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _fit(text: str, width: int) -> str:
    plain = _visible(text)
    return text if len(plain) <= width else plain[: max(1, width - 1)] + "…"


def _filtered(options: Sequence[Tuple[str, object]], query: str) -> List[Tuple[str, object]]:
    """Options containing the query; labels that start with it first, then word starts."""
    if not query:
        return list(options)
    q = query.lower()

    def rank(label: str) -> int:
        text = _visible(label).lower().lstrip(" *★⚡🐢☁⌂+")
        if text.startswith(q):
            return 0
        return 1 if re.search(r"(^|[\s/_-])" + re.escape(q), text) else 2

    hits = [(rank(o[0]), i, o) for i, o in enumerate(options)
            if q in _visible(o[0]).lower() + " " + (str(o[2]).lower() if len(o) > 2 else "")]
    return [o for _, _, o in sorted(hits, key=lambda t: (t[0], t[1]))]


def select(title: str, options: Sequence[tuple], default: int = 1, hint: str = "") -> object:
    """Arrow-key menu: ↑/↓ moves, Enter picks, typing filters, Esc clears the filter.
    Options are (label, value) or (label, value, description); the description of the
    highlighted option shows under the list. Without a terminal it falls back to numbers."""
    descs = {id(o): (o[2] if len(o) > 2 else "") for o in options}
    if not menus_supported():
        if title:
            print(color(title, "1"))
        for i, opt in enumerate(options, start=1):
            print(f"  {i}) {opt[0]}" + (color(f"  {opt[2]}", "2") if len(opt) > 2 and opt[2] else ""))
        while True:
            raw = ask("  choose", str(default))
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                print()
                return options[int(raw) - 1][1]
            print("  enter a number from the list")

    has_desc = any(descs.values())
    query, cur, top, drawn = "", max(0, default - 1), 0, 0
    width = max(30, shutil.get_terminal_size((80, 24)).columns - 4)
    view = max(5, min(12, shutil.get_terminal_size((80, 24)).lines - 6))
    arrow = "❯" if unicode_ok() else ">"
    help_line = hint or ("↑/↓ move · enter select · type to filter" if unicode_ok() else "up/down move, enter select, type to filter")
    sys.stdout.write("\033[?25l")  # hide the cursor while the menu is up
    try:
        with _RawKeys() as keys:
            while True:
                shown = _filtered(options, query)
                cur = min(cur, max(0, len(shown) - 1))
                top = min(max(top, cur - view + 1), cur) if shown else 0
                lines = []
                if title:
                    # lines must not wrap, or the redraw miscounts them
                    room = width - (len(query) + 10 if query else 0)
                    lines.append(color(_fit(title, room), "1") + (color(f"  filter: {query}", "33") if query else ""))
                elif query:
                    lines.append(color(f"filter: {query}", "33"))
                if top > 0:
                    lines.append(color("   ↑ more", "2"))
                for i in range(top, min(len(shown), top + view)):
                    label = _fit(shown[i][0], width - 4)
                    lines.append(color(f" {arrow} ", "36") + color(label, "1;36") if i == cur else f"   {label}")
                if not shown:
                    lines.append(color("   (nothing matches)", "2"))
                if top + view < len(shown):
                    lines.append(color("   ↓ more", "2"))
                if has_desc:
                    lines.append("")
                    lines.append(color("   " + _fit(descs.get(id(shown[cur]), "") if shown else "", width - 3), "37"))
                lines.append(color(f"   {help_line}", "2"))
                if drawn:
                    sys.stdout.write(f"\033[{drawn}F\033[J")
                sys.stdout.write("".join(f"\033[2K{line}\n" for line in lines))
                sys.stdout.flush()
                drawn = len(lines)

                key = keys.read()
                if key == "up":
                    cur = (cur - 1) % len(shown) if shown else 0
                elif key == "down":
                    cur = (cur + 1) % len(shown) if shown else 0
                elif key == "pgup":
                    cur = max(0, cur - view)
                elif key == "pgdn":
                    cur = min(len(shown) - 1, cur + view) if shown else 0
                elif key == "enter" and shown:
                    label, value = shown[cur][0], shown[cur][1]
                    # collapse the menu to one line showing the choice
                    sys.stdout.write(f"\033[{drawn}F\033[J")
                    summary = (color(title, "1") + " " if title else "") + color(_fit(_visible(label), width - len(title) - 2), "36")
                    sys.stdout.write(summary + "\n\n")
                    return value
                elif key == "esc":
                    query = ""
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                    cur = 0
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


ask_choice = select  # the wizard's older name for it


def screenshot_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        try:
            proc = subprocess.run(
                ["defaults", "read", "com.apple.screencapture", "location"], capture_output=True, text=True, check=False
            )
            loc = proc.stdout.strip()
            if proc.returncode == 0 and loc:
                return Path(loc).expanduser()
        except OSError:
            pass
        return Path.home() / "Desktop"  # macOS default
    candidates: List[Path] = []
    try:
        proc = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, check=False)
        pictures = Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None
        # xdg-user-dir prints $HOME when no Pictures dir is configured
        if pictures and pictures != Path.home():
            candidates.append(pictures / "Screenshots")
    except OSError:
        pass
    candidates += [Path.home() / "Pictures" / "Screenshots", Path.home() / "Screenshots"]
    return next((c for c in candidates if c.is_dir()), candidates[0])


def describe_counts(path: Path) -> str:
    if not path.is_dir():
        return "not found"
    c = count_supported(path)
    parts = [f"{c['image']} images", f"{c['pdf']} PDFs"]
    if c["doc"] or c["file"]:
        parts += [f"{c['doc']} documents", f"{c['file']} other files"]
    return ", ".join(parts)


def create_profile_wizard(cfg_file: Path) -> Profile:
    print(color("New profile", "1"))
    print("  Name each folder and describe what goes in it in a few words.")
    print("  The description is what the model reads, so be specific. Blank name to finish.\n")
    name = slugify(ask("  profile name", "my-folders"))
    folders: Dict[str, str] = {}
    while True:
        folder = ask(f"  folder {len(folders) + 1} name (blank to finish)")
        if not folder:
            if len(folders) >= 2:
                break
            print("  add at least 2 folders")
            continue
        desc = ask(f"    what goes in '{slugify(folder)}'")
        folders[slugify(folder)] = desc
    profile = Profile(name=name, description="custom", folders=folders, spec=custom_spec())
    print(f"\n  uncertain files go to {REVIEW_FOLDER}/, junk to {DELETE_FOLDER}/")
    if ask_yes(f"  save '{name}' to {cfg_file}", True):
        user = read_user_config(cfg_file)
        user.setdefault("profiles", {})[name] = {"description": "custom", "folders": folders}
        save_config(cfg_file, user)
        print(f"  {icon('ok')} saved")
    print()
    return profile


# ---------------------------------------------------------------------------
# Model setup (sortling --setup, or from the wizard's model menu)
# ---------------------------------------------------------------------------

CHAT_SERVERS: Sequence[Tuple[str, str, str]] = (
    ("llama-swap / llama.cpp", "http://localhost:8080/v1", "LLAMA_SWAP_API_KEY"),
    ("Ollama", "http://localhost:11434/v1", ""),
    ("LM Studio", "http://localhost:1234/v1", ""),
    ("vLLM", "http://localhost:8000/v1", ""),
)
HOSTED_CHAT: Sequence[Tuple[str, str, str]] = (
    ("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ("OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
)
VISION_HINTS = ("vl", "vision", "llava", "gemma-3", "gemma3", "gemma-4", "gemma4", "pixtral", "qwen3.5", "qwen3-5", "gpt-4o", "gpt-5", "claude")


def fetch_models(base: str, key_env: str, timeout_s: int = 10) -> List[str]:
    """Model ids from an OpenAI-style /v1/models listing; empty on any failure."""
    base = base.rstrip("/")
    url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    headers = {"Accept": "application/json"}
    key = os.environ.get(key_env, "") if key_env else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        return sorted({str(m.get("id") or m.get("name")) for m in items if isinstance(m, dict) and (m.get("id") or m.get("name"))})
    except Exception:
        return []


def test_backend(name: str, cfg: dict, timeout_s: int = 300) -> str:
    """Empty string when it answers, otherwise why not. Hybrids test both halves."""
    try:
        first, second = make_backends(name, cfg)
    except ValueError as exc:
        return str(exc)
    for b in (first, second):
        if b is None:
            continue
        if b.key_env and not b.api_key:
            return f"${b.key_env} is not set"
        print(color(f"  testing {b.name}... (a cold model can take a while to load)", "2"))
        why = _check(b, timeout_s)
        if why:
            return f"{b.name}: {why}"
    return ""


def _pick_model(base: str, key_env: str, title: str) -> str:
    ids = fetch_models(base, key_env)
    if not ids:
        print(color(f"  couldn't list models at {base} (is it running? is the key set?)", "2"))
        return ask("  model id")
    picked = select(title, [(i, i) for i in ids] + [("type a model id instead...", None)])
    return str(picked) if picked else ask("  model id")


def add_backend_wizard(cfg: dict, cfg_file: Path) -> Optional[str]:
    kind = select("What kind of model?", [
        (f"{icon('fast')} local decision model", "systemone", "llama.cpp or llama-swap serving /v1/systemone"),
        (f"{icon('fast')} TypeSafe Jev", "jev", "hosted decision model, text only"),
        (f"{icon('thorough')} local chat model", "chat-local", "llama-swap, Ollama, LM Studio or vLLM"),
        (f"{icon('thorough')} hosted chat model", "chat-hosted", "OpenAI, OpenRouter or another OpenAI-compatible API"),
        (f"{icon('hybrid')} hybrid", "hybrid", "a fast model first, a chat model for the files it's unsure about"),
        ("  back", None, ""),
    ])
    if kind is None:
        return None
    bcfg: dict
    if kind == "systemone":
        base = ask("  server address", "http://localhost:8080").rstrip("/")
        key_env = ask("  environment variable with its API key (blank if none)", "LLAMA_SWAP_API_KEY")
        model = _pick_model(base, key_env, "Which decision model?")
        root = base[:-3] if base.endswith("/v1") else base
        candidates = [f"{root}/upstream/{model}/v1/systemone", f"{root}/v1/systemone"] if model else [f"{root}/v1/systemone"]
        url = candidates[0]
        for cand in candidates:
            probe = SystemOneBackend("probe", {"url": cand, "api_key_env": key_env})
            print(color(f"  trying {cand} ...", "2"))
            if not _check(probe, 300):
                url = cand
                break
        bcfg = {"type": "systemone", "url": url, "api_key_env": key_env,
                "vision": ask_yes("  can it read images (served with --mmproj)?", True),
                "label": f"{model or 'decision model'} on {urllib.parse.urlparse(root).netloc}"}
        default_name = slugify(model or "decision")
    elif kind == "jev":
        bcfg = {"type": "systemone", "url": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest",
                "api_key_env": ask("  environment variable with your TypeSafe key", "TYPESAFE_API_KEY"),
                "vision": False, "label": "TypeSafe Jev (hosted, text only)"}
        default_name = "jev"
    elif kind in ("chat-local", "chat-hosted"):
        servers = CHAT_SERVERS if kind == "chat-local" else HOSTED_CHAT
        server = select("Which server?", [(f"{n:<24} {u}", (n, u, k)) for n, u, k in servers] + [("another address...", None)])
        if server is None:
            name_, base, key_default = "server", ask("  base URL (ending in /v1)"), ""
        else:
            name_, base, key_default = server  # type: ignore[misc]
        key_env = ask("  environment variable with its API key (blank if none)", key_default)
        model = _pick_model(base, key_env, "Which model?")
        looks_visual = any(h in model.lower() for h in VISION_HINTS)
        bcfg = {"type": "openai", "url": base.rstrip("/"), "model": model, "api_key_env": key_env,
                "vision": ask_yes("  can it read images?", looks_visual), "label": f"{model} via {name_}"}
        default_name = slugify(model.split("/")[-1])
    else:
        fast = [n for n, c in cfg["backends"].items() if c.get("type", "systemone") == "systemone"]
        chat = [n for n, c in cfg["backends"].items() if c.get("type") == "openai"]
        if not fast or not chat:
            print("  a hybrid needs one decision model and one chat model; add those first.\n")
            return None
        first = str(select("First pass (fast)", [(describe_entry(n, cfg), n) for n in fast]))
        second = str(select("Second look at unsure files (thorough)", [(describe_entry(n, cfg), n) for n in chat]))
        bcfg = {"type": "hybrid", "first": first, "second": second, "label": f"{first} first, {second} re-checks unsure files"}
        default_name = f"hybrid-{first}"

    name = slugify(ask("  name for it", default_name))
    trial = json.loads(json.dumps(cfg))
    trial["backends"][name] = bcfg
    why = test_backend(name, trial)
    print(f"  {icon('ok')} {name} answered" if not why else f"  {icon('warn')} {name} didn't answer: {why}")
    if not ask_yes(f"  save '{name}'", not why):
        return None
    user = read_user_config(cfg_file)
    user.setdefault("backends", {})[name] = bcfg
    user["hidden_backends"] = [h for h in user.get("hidden_backends", []) if h != name]
    if ask_yes(f"  make '{name}' the default", not why):
        user["default_backend"] = name
    save_config(cfg_file, user)
    print(f"  {icon('ok')} saved to {cfg_file}\n")
    return name


def setup_wizard(cfg_file: Path) -> dict:
    """Add, test, hide and pick the default model. Returns the reloaded config."""
    while True:
        cfg = load_config(cfg_file)
        user = read_user_config(cfg_file)
        default = cfg.get("default_backend")
        star = "★" if unicode_ok() else "*"
        rows = []
        for n in cfg["backends"]:
            label, _, desc = backend_row(n, cfg, star=n == default)
            rows.append((label, ("edit", n), desc))
        rows += [("  + add a model...", ("add", ""), "a decision model, Jev, a chat model, or a hybrid of two"),
                 ("  done", ("done", ""), f"saved in {cfg_file}")]
        action, name = select(f"Models ({star} = default)", rows, default=len(rows))  # type: ignore[misc]
        if action == "done":
            return cfg
        if action == "add":
            add_backend_wizard(cfg, cfg_file)
            continue
        builtin = name in DEFAULT_CONFIG["backends"] and name not in user.get("backends", {})
        what = select(f"{name}", [
            ("test it", "test"),
            ("make it the default", "default"),
            ("hide it (built-in)" if builtin else "remove it", "remove"),
            ("back", "back"),
        ])
        if what == "test":
            why = test_backend(name, cfg)
            print(f"  {icon('ok')} {name} answered\n" if not why else f"  {icon('warn')} {why}\n")
        elif what == "default":
            user["default_backend"] = name
            save_config(cfg_file, user)
        elif what == "remove":
            if builtin:
                user["hidden_backends"] = sorted(set(user.get("hidden_backends", [])) | {name})
            else:
                user.get("backends", {}).pop(name, None)
            if user.get("default_backend") == name:
                user.pop("default_backend", None)
            save_config(cfg_file, user)


def interactive(args: argparse.Namespace, cfg: dict, cfg_file: Path) -> int:
    if not args.no_banner:
        print_banner()

    # 1. what to clean up
    shots = screenshot_dir()
    downloads = Path.home() / "Downloads"
    desktop = Path.home() / "Desktop"
    targets: List[tuple] = [
        (f"Screenshots   {describe_counts(shots)}", (shots, ("image",)), f"{shots} · images only"),
        (f"Downloads     {describe_counts(downloads)}", (downloads, ALL_KINDS), str(downloads)),
    ]
    if desktop != shots:
        targets.append((f"Desktop       {describe_counts(desktop)}", (desktop, ALL_KINDS), str(desktop)))
    targets.append(("Another folder...", None, "type a path; Tab completes"))
    picked = ask_choice("What do you want to tidy?", targets)
    if picked is None:
        root = Path(ask("  folder path", paths=True)).expanduser()
        kinds: Tuple[str, ...] = ALL_KINDS
        print()
    else:
        root, kinds = picked  # type: ignore[misc]
    root = root.resolve()
    if not root.is_dir():
        print(f"error: not a folder: {root}", file=sys.stderr)
        return 2

    # 2. how to sort
    profiles: List[Tuple[str, object]] = []
    try:
        here = local_profile(root, cfg)
    except (OSError, ValueError) as exc:
        print(f"  {icon('warn')} ignoring {root / LOCAL_PROFILE_FILE}: {exc}\n")
        here = None
    if here is not None:
        profiles.append((LOCAL_PROFILE_FILE, here, "this folder's own profile: " + ", ".join(here.roots())))
    for bname in BUILTIN_PROFILE_SPECS:
        try:
            builtin = load_profile(bname, cfg)
        except ValueError as exc:
            print(f"  {icon('warn')} ignoring your changes to {bname}: {exc}")
            builtin = load_profile(bname, {})
        tuned = " (tuned in your config)" if builtin.source != "built-in" else ""
        profiles.append((bname, builtin, builtin.description + tuned))
    for pname in cfg.get("profiles", {}):
        if pname in BUILTIN_PROFILE_SPECS:
            continue
        try:
            saved = load_profile(pname, cfg)
            what = f"tuned {saved.layout}: " if saved.builtin else "your folders: "
            profiles.append((pname, saved, what + ", ".join(saved.roots())))
        except ValueError as exc:
            print(f"  {icon('warn')} skipping saved profile {pname}: {exc}")
    profiles.append(("create your own...", None, "name your folders and say what goes in each"))
    picked_profile = ask_choice("How should files be sorted?", profiles)
    profile = create_profile_wizard(cfg_file) if picked_profile is None else picked_profile
    assert isinstance(profile, Profile)

    # 3. which model
    names = list(cfg["backends"])
    while True:
        default_idx = next((i for i, n in enumerate(names, start=1) if n == cfg.get("default_backend")), 1)
        gear = "⚙" if unicode_ok() else "*"
        rows = [backend_row(n, cfg, star=n == cfg.get("default_backend")) for n in names]
        rows.append((f"  {gear} set up models...", None, "add a model, test one, change the default"))
        picked = ask_choice("Which model?", rows, default_idx)
        if picked is not None:
            picked_name = str(picked)
            break
        cfg = setup_wizard(cfg_file)
        names = list(cfg["backends"])
    try:
        backend, second = make_backends(picked_name, cfg)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for b in (backend, second):
        if b is None:
            continue
        if b.key_env and not b.api_key:
            print(f"  {icon('warn')} ${b.key_env} is not set; {b.name} requests may be rejected.")
        if b.hosted:
            host = urllib.parse.urlparse(b.url).hostname
            what = "file text and images" if b.vision else "file text and names"
            scope = "uncertain files' " if b is second else ""
            print(f"  {icon('warn')} {b.name} is hosted: {scope}{what} will be uploaded to {host}.")
            if not ask_yes("  continue", False):
                return 1
    if not backend.vision and "image" in kinds:
        print(f"  {icon('warn')} {backend.name} is text-only: images will be sorted by filename keywords only.")
    print()

    opts = RunOptions(
        root=root,
        kinds=kinds,
        recursive=False,
        profile=profile,
        backend=backend,
        second=second,
        thresholds=profile.thresholds(threshold_flags(args)),
        timeout_s=args.timeout_s,
        progress=True,
        face=not args.no_face,
    )
    precheck_backend(opts)
    if opts.backend is None:
        print(f"  {icon('warn')} model unavailable ({opts.status}); using filename keywords instead.")
        if not ask_yes("  continue anyway", False):
            return 1
    if second is not None and opts.second is None:
        print(f"  {icon('warn')} second pass unavailable ({opts.second_status}); fast results only.")

    # 4. dry run, show, confirm
    records = plan(opts)
    if not records:
        print(f"Nothing to sort in {root}.")
        return 0
    print()
    by_folder: Dict[str, int] = {}
    for rec in records:
        by_folder[rec.target_folder] = by_folder.get(rec.target_folder, 0) + 1
    print(color(f"Plan for {len(records)} files in {root}:", "1"))
    for folder, n in sorted(by_folder.items(), key=lambda kv: (kv[0].startswith("_"), kv[0])):
        print(f"  {n:>4}  -> {folder}/")
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else root / "_sortling"
    _, json_path = write_logs(out_dir, opts, records, dry_run=True)
    print(f"\n  details: {json_path}")
    if not ask_yes("\nMove the files now", False):
        print("No files moved.")
        return 0
    apply_all(opts, records)
    _, json_path = write_logs(out_dir, opts, records, dry_run=False)
    errors = sum(1 for r in records if r.result == "ERROR")
    print(f"\n{icon('ok')} moved {len(records) - errors} files" + (f", {errors} errors" if errors else ""))
    print(f"  undo with: python3 {Path(sys.argv[0]).name} --undo '{json_path}'")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    show_all = "--help-all" in argv
    p = argparse.ArgumentParser(
        prog="sortling",
        description="Tidy screenshots, downloads and documents with decision models. Run with no arguments for the wizard.",
        epilog=None if show_all else "More options (thresholds, deletion, model and display tweaks): --help-all",
    )

    def advanced(text: str) -> str:
        return text if show_all else argparse.SUPPRESS

    run = p.add_argument_group("sorting")
    run.add_argument("--root", help="Folder to sort. Without it on a terminal, the wizard asks.")
    run.add_argument("--apply", action="store_true", help="Move the files (default: a dry run that only writes the plan)")
    run.add_argument("--undo", metavar="MANIFEST", help="Move an applied run's files back")
    run.add_argument("--profile", default="", help="default, photos, or one saved in your config")
    run.add_argument("--backend", default="", help="Model to use, from --list-backends (default: your config's default)")
    run.add_argument("--types", default=",".join(ALL_KINDS),
                     help="Which files: pdf, image, doc (Office/text, read), file (sorted by extension). Default: all")
    run.add_argument("--recursive-source", action="store_true", help="Also sort files in subfolders")

    scripts = p.add_argument_group("for scripts and agents (see SORTLING.md)")
    scripts.add_argument("--json", action="store_true", help="Print the plan as JSON on stdout (implies no progress output)")
    scripts.add_argument("--profile-file", default="", metavar="JSON",
                         help='Profile from a JSON file, or "-" for stdin: {"folders": {"name": "what goes in it", ...}}')
    scripts.add_argument("--output-dir", default="", help="Where the manifest goes (default: <root>/_sortling)")

    setup = p.add_argument_group("setup")
    setup.add_argument("--setup", action="store_true", help="Add, test and choose models")
    setup.add_argument("--list-backends", action="store_true", help="Show configured models")
    setup.add_argument("--show-profile", nargs="?", const="", metavar="NAME",
                       help="Print a profile's questions, options, thresholds and file rules as JSON")
    setup.add_argument("--init-config", action="store_true", help="Write a starter config")
    setup.add_argument("--version", action="version", version=f"sortling {__version__}")
    setup.add_argument("--help-all", action="store_true", help="Show every option")

    adv = p.add_argument_group("advanced") if show_all else p
    adv.add_argument("-i", "--interactive", action="store_true", help=advanced("Force the wizard even with --root"))
    adv.add_argument("--second-pass", default="", metavar="BACKEND",
                     help=advanced("Hybrid: re-check uncertain files with this backend (usually a thorough one)"))
    adv.add_argument("--url", default="", help=advanced("Override the backend URL"))
    adv.add_argument("--config", default="", help=advanced("Config file (default: $XDG_CONFIG_HOME/sortling/config.json)"))
    adv.add_argument("--disable-llm", action="store_true", help=advanced("Skip all models and use filename keywords"))
    # threshold flags default to None so the profile's own thresholds apply unless one is given
    adv.add_argument("--min-confidence", type=float, help=advanced("Below this, documents go to _review (profile default 0.6)"))
    adv.add_argument("--screenshot-min-confidence", type=float,
                     help=advanced("Below this topic probability, screenshots go to screenshots/other (profile default 0.3)"))
    adv.add_argument("--delete-min-prob", type=float, help=advanced("Minimum junk probability for _delete (profile default 0.85)"))
    adv.add_argument("--delete-exact-duplicates", action="store_true",
                     help=advanced("Really delete exact duplicates instead of moving them to _duplicates"))
    adv.add_argument("--hard-delete-delete-candidates", action="store_true",
                     help=advanced("Really delete junk instead of moving it to _delete"))
    adv.add_argument("--timeout-s", type=int, default=120, help=advanced("Model request timeout in seconds"))
    adv.add_argument("--max-pages", type=int, default=3, help=advanced("PDF pages to read text from"))
    adv.add_argument("--max-chars", type=int, default=7000, help=advanced("Text characters sent to the model"))
    adv.add_argument("--vision-mode", choices=("off", "auto", "always"), default="auto",
                     help=advanced("Send images: auto (PDF page snapshots when text is weak), always, or off"))
    adv.add_argument("--write-review-note", action="store_true", help=advanced("Write _review/REVIEW_LIST.txt"))
    adv.add_argument("--no-progress", action="store_true", help=advanced("No progress bars or stage notes"))
    adv.add_argument("--no-banner", action="store_true", help=advanced("Skip the wizard's banner"))
    adv.add_argument("--no-face", action="store_true", help=advanced("Plain progress bar instead of the face (also SORTLING_FACE=0)"))
    args = p.parse_args(argv)
    if args.help_all:
        p.print_help()
        raise SystemExit(0)
    if args.json:
        args.no_progress = True  # stdout is the JSON; keep stderr quiet too
    return args


def profile_payload(profile: Profile, flags: dict) -> dict:
    """What a run with this profile asks and how it decides, as JSON."""
    out: dict = {"name": profile.name, "layout": profile.layout, "source": profile.source, "description": profile.description}
    if profile.folders:
        out["folders"] = profile.folders
    out["thresholds"] = asdict(profile.thresholds(flags))
    out["questions"] = profile.spec.get("questions", {})
    if "category_aliases" in profile.spec:
        out["category_aliases"] = profile.spec["category_aliases"]
    if profile.spec.get("files"):
        out["files"] = profile.spec["files"]
    if profile.extensions:
        out["extensions"] = profile.extensions
    return out


def threshold_flags(args: argparse.Namespace) -> dict:
    # thresholds without a flag (record_min_prob, unusable_min_prob) are set in the profile only
    return {k: getattr(args, k, None) for k in THRESHOLD_KEYS}


def resolve_backends(args: argparse.Namespace, cfg: dict) -> Tuple[Backend, Optional[Backend]]:
    name = args.backend or cfg.get("default_backend", "")
    return make_backends(name, cfg, url=args.url, second=args.second_pass)


def resolve_profile(args: argparse.Namespace, cfg: dict, root: Path) -> Profile:
    """--profile-file, then --profile, then the folder's .sortling.json, then default."""
    if args.profile_file:
        return load_profile_file(args.profile_file, cfg)
    if args.profile:
        return load_profile(args.profile, cfg)
    return local_profile(root, cfg) or load_profile("default", cfg)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cfg_file = config_path(args.config)

    if args.init_config:
        if cfg_file.exists():
            print(f"config already exists: {cfg_file}")
            return 1
        starter = json.loads(json.dumps(DEFAULT_CONFIG))
        starter["profiles"] = {"default": {"_note": PROFILE_TUNING_NOTE}}
        save_config(cfg_file, starter)
        print(f"wrote {cfg_file}")
        return 0
    if args.undo:
        return undo(Path(args.undo).expanduser())

    try:
        cfg = load_config(cfg_file)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read config {cfg_file}: {exc}", file=sys.stderr)
        return 2

    if args.setup:
        try:
            if not args.no_banner:
                print_banner()
            setup_wizard(cfg_file)
            return 0
        except KeyboardInterrupt:
            print("\ncancelled.")
            return 130

    if args.show_profile is not None:
        try:
            if args.show_profile:
                profile = load_profile(args.show_profile, cfg)
            else:
                profile = resolve_profile(args, cfg, Path(args.root or ".").expanduser().resolve())
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(profile_payload(profile, threshold_flags(args)), ensure_ascii=False, indent=2))
        return 0

    if args.list_backends:
        for n in cfg["backends"]:
            mark = "*" if n == cfg.get("default_backend") else " "
            print(f"{mark} {describe_entry(n, cfg)}")
        return 0

    if args.interactive or (not args.root and not args.json and sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            return interactive(args, cfg, cfg_file)
        except KeyboardInterrupt:
            print("\ncancelled, no files moved.")
            return 130

    root = Path(args.root or ".").expanduser().resolve()
    if not root.is_dir():
        print(f"error: invalid root directory: {root}", file=sys.stderr)
        return 2
    kinds = tuple(k.strip() for k in args.types.split(",") if k.strip())
    if not kinds or any(k not in ALL_KINDS for k in kinds):
        print(f"error: --types must be a comma-separated subset of {','.join(ALL_KINDS)}: {args.types}", file=sys.stderr)
        return 2
    try:
        profile = resolve_profile(args, cfg, root)
        backend, second = (None, None) if args.disable_llm else resolve_backends(args, cfg)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    opts = RunOptions(
        root=root,
        kinds=kinds,
        recursive=args.recursive_source,
        profile=profile,
        backend=backend,
        second=second,
        thresholds=profile.thresholds(threshold_flags(args)),
        vision_mode=args.vision_mode,
        max_pages=args.max_pages,
        max_chars=args.max_chars,
        timeout_s=args.timeout_s,
        delete_exact_duplicates=args.delete_exact_duplicates,
        hard_delete=args.hard_delete_delete_candidates,
        progress=not args.no_progress,
        face=not args.no_face,
    )
    precheck_backend(opts)
    records = plan(opts)
    if not records:
        if args.json:
            print(json.dumps(manifest_payload(opts, [], dry_run=not args.apply)))
        else:
            print(f"No source files ({','.join(kinds)}) found to process.")
        return 0
    if args.apply:
        apply_all(opts, records)
    else:
        stage_note("Stage 3/3: dry-run (no file changes)", opts.progress)
        for rec in records:
            rec.result = "DRY_RUN"

    out_dir = Path(args.output_dir).expanduser() if args.output_dir else root / "_sortling"
    csv_path, json_path = write_logs(out_dir.resolve(), opts, records, dry_run=not args.apply)
    review_note = write_review_note(root, records, args.apply) if args.write_review_note else None

    if args.json:
        # machine-readable plan for agents and scripts: the manifest plus where it was written
        payload = manifest_payload(opts, records, dry_run=not args.apply)
        payload["manifest_json"], payload["manifest_csv"] = str(json_path), str(csv_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"MODE: {'APPLY' if args.apply else 'DRY_RUN'}")
    print(f"ROOT: {root}")
    print(f"SOURCE_FILES: {len(records)} ({','.join(kinds)})")
    print(f"PROFILE: {profile.name} ({profile.source})")
    print(f"BACKEND: {backend.describe() if backend else 'none'}")
    if second is not None or opts.second_status:
        print(f"SECOND_PASS: {opts.second_status}")
    print(f"PRECHECK: {opts.status}")
    print(summarize(records))
    print(f"MANIFEST_CSV: {csv_path}")
    print(f"MANIFEST_JSON: {json_path}")
    if review_note:
        print(f"REVIEW_NOTE: {review_note}")
    if args.apply:
        print(f"UNDO: python3 {Path(sys.argv[0]).name} --undo '{json_path}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
