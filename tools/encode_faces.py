#!/usr/bin/env python3
"""Pack assets/faces/head-*.png (the progress face) and assets/poses/sortling_idle.png (the
banner hero) into sortling.py's embedded sprites.

    python3 tools/encode_faces.py            # rewrite the FACES block in sortling.py
    python3 tools/encode_faces.py --width 28 --check

Needs ImageMagick to read the PNGs. Each head is area-averaged to --width pixels, alpha is cut at 50%, colors go into one shared palette, and everything
is zlib-compressed and base64-encoded between the "FACES BEGIN/END" markers.
"""

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("smile", "happy", "surprised", "confused", "nerd", "content", "love", "sleepy",
         "dizzy", "sad", "angry", "cool", "searching", "crying", "starstruck", "wink")


IM = shutil.which("magick") or shutil.which("convert")


def shared_palette(paths, width: int, colors: int, out: Path) -> None:
    """One palette for all heads, so every frame indexes the same colors."""
    subprocess.run([IM, *map(str, paths), "-filter", "box", "-resize", f"{width}x", "-background", "none", "+append",
                    "-alpha", "off", "+dither", "-colors", str(colors), "-unique-colors", str(out)], check=True)


def rgba(path: Path, width: int, palette_png: Path):
    # the art isn't on a true pixel grid, so area-average: point sampling drops eye and mouth pixels
    sampled = [IM, str(path), "-filter", "box", "-resize", f"{width}x"]
    size = subprocess.run([*sampled, "-format", "%w %h", "info:"], capture_output=True, text=True, check=True).stdout.split()
    w, h = int(size[0]), int(size[1])
    # -remap drops alpha, so colors and the transparency mask are read separately
    rgb = subprocess.run([*sampled, "-alpha", "off", "+dither", "-remap", str(palette_png), "-depth", "8", "rgb:-"],
                         capture_output=True, check=True).stdout
    mask = subprocess.run([*sampled, "-alpha", "extract", "-threshold", "45%", "-depth", "8", "gray:-"],
                          capture_output=True, check=True).stdout
    return w, h, [(*rgb[i * 3:i * 3 + 3], mask[i]) for i in range(w * h)]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--width", type=int, default=28)
    p.add_argument("--colors", type=int, default=48)
    p.add_argument("--hero-width", type=int, default=38)
    p.add_argument("--check", action="store_true", help="print sizes, don't write")
    args = p.parse_args()

    if not IM:
        sys.exit("error: ImageMagick is required")
    paths = [ROOT / "assets" / "faces" / f"head-{name}.png" for name in NAMES]
    tmp = Path(tempfile.mkdtemp(prefix="sortling-faces-")) / "palette.png"
    shared_palette(paths, args.width, args.colors, tmp)
    palette, index, frames, dims = [], {}, {}, None
    for name, path in zip(NAMES, paths):
        w, h, px = rgba(path, args.width, tmp)
        if dims and dims != (w, h):
            sys.exit(f"error: head-{name}.png is {w}x{h}, others are {dims[0]}x{dims[1]}")
        dims = (w, h)
        out = bytearray()
        for r, g, b, a in px:
            if a < 128:
                out.append(0)  # 0 = transparent
                continue
            key = "%02x%02x%02x" % (r, g, b)
            if key not in index:
                palette.append(key)
                index[key] = len(palette)
                if len(palette) > 255:
                    sys.exit("error: more than 255 colors; quantize the art first")
            out.append(index[key])
        frames[name] = base64.b64encode(bytes(out)).decode()
    blob = json.dumps({"w": dims[0], "h": dims[1], "palette": palette, "frames": frames}, separators=(",", ":"))
    packed = base64.b64encode(zlib.compress(blob.encode(), 9)).decode()
    lines = [packed[i:i + 96] for i in range(0, len(packed), 96)]
    print(f"{len(NAMES)} faces, {dims[0]}x{dims[1]} px, {len(palette)} colors, {len(packed)} bytes embedded")

    hero = pack_hero(ROOT / "assets" / "poses" / "sortling_idle.png", args.hero_width)
    print(f"hero: {args.hero_width} px wide, {len(hero)} bytes embedded")
    if args.check:
        return 0

    target = ROOT / "sortling.py"
    src = target.read_text(encoding="utf-8")
    for marker, var, packed_text in (("FACES", "FACES_DATA", packed), ("HERO", "HERO_DATA", hero)):
        chunks = [packed_text[i:i + 96] for i in range(0, len(packed_text), 96)]
        block = f"{var} = (\n" + "".join(f'    "{c}"\n' for c in chunks) + ")\n"
        src, n = re.subn(rf"(# --- {marker} BEGIN.*?\n).*?(# --- {marker} END)", lambda m: m.group(1) + block + m.group(2), src, flags=re.S)
        if n != 1:
            sys.exit(f"error: {marker} BEGIN/END markers not found in sortling.py")
    target.write_text(src, encoding="utf-8")
    print(f"updated {target}")
    return 0


def pack_hero(path: Path, width: int) -> str:
    """The banner pose. It isn't drawn on a pixel grid, so area-average it, then cut the palette."""
    shrunk = [IM, str(path), "-filter", "box", "-resize", f"{width}x"]
    w, h = map(int, subprocess.run([*shrunk, "-format", "%w %h", "info:"], capture_output=True, text=True, check=True).stdout.split())
    rgb = subprocess.run([*shrunk, "-alpha", "off", "+dither", "-colors", "40", "-depth", "8", "rgb:-"],
                         capture_output=True, check=True).stdout
    mask = subprocess.run([*shrunk, "-alpha", "extract", "-threshold", "45%", "-depth", "8", "gray:-"],
                          capture_output=True, check=True).stdout
    palette, index, out = [], {}, bytearray()
    for i in range(w * h):
        if mask[i] < 128:
            out.append(0)
            continue
        key = rgb[i * 3:i * 3 + 3].hex()
        if key not in index:
            palette.append(key)
            index[key] = len(palette)
        out.append(index[key])
    blob = json.dumps({"w": w, "h": h, "palette": palette, "frames": {"hero": base64.b64encode(bytes(out)).decode()}},
                      separators=(",", ":"))
    return base64.b64encode(zlib.compress(blob.encode(), 9)).decode()


if __name__ == "__main__":
    raise SystemExit(main())
