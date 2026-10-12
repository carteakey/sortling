#!/usr/bin/env bash
# Build sortling's labelled test fixtures into tests/fixtures/ (gitignored).
#
#   tests/make_fixtures.sh            # build all sets
#   tests/make_fixtures.sh --force    # rebuild from scratch
#
# Needs ImageMagick (magick or convert), fontconfig (fc-match), python3, and curl for the
# 10 stock photos from picsum.photos (Unsplash license). Labels are in tests/expected.json.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/fixtures"
[ "${1:-}" = "--force" ] && rm -rf "$OUT"

IM="$(command -v magick || command -v convert || true)"
[ -n "$IM" ] || { echo "error: ImageMagick (magick or convert) is required" >&2; exit 1; }
font() { fc-match -f '%{file}' "$1" 2>/dev/null || true; }
SANS="$(font 'DejaVu Sans')"; MONO="$(font 'DejaVu Sans Mono')"
BOLD="$(font 'DejaVu Sans:bold')"; SERIF_I="$(font 'DejaVu Serif:italic')"
for f in "$SANS" "$MONO" "$BOLD" "$SERIF_I"; do
  [ -f "$f" ] || { echo "error: DejaVu fonts not found (install fonts-dejavu / ttf-dejavu)" >&2; exit 1; }
done

# --- docs: records, a screenshot, a meme, a text PDF, a scanned PDF, one duplicate ----------
D="$OUT/docs"
if [ ! -d "$D" ]; then
  mkdir -p "$D"
  "$IM" -size 600x800 xc:white -font "$SANS" -fill black -pointsize 28 -annotate +40+60 'FRESHCO GROCERY #2231' \
    -pointsize 22 -annotate +40+120 $'Bananas 1.2kg        2.38\nMilk 2L              5.49\nBread                3.99\nEggs dozen           4.79\n\nSUBTOTAL            16.65\nHST 13%              0.52\nTOTAL               17.17\n\nVISA ****4421  APPROVED\n2026-10-03 18:42' \
    "$D/IMG_20261003_receipt.jpg"
  "$IM" -size 1280x720 xc:'#1e1e1e' -font "$MONO" -fill '#d4d4d4' -pointsize 20 -annotate +30+50 \
    $'$ cargo build --release\n   Compiling l3ms v0.7.0 (/home/k/repos/l3ms)\nerror[E0308]: mismatched types\n  --> src/app.rs:412:17\n    |\n412 |     let n: u32 = items.len();\n    |            ---   ^^^^^^^^^^^ expected `u32`, found `usize`\n\nerror: could not compile `l3ms` due to previous error' \
    "$D/Screenshot from 2026-10-08 14-22-07.png"
  "$IM" -size 1000x420 xc:'#f4f7fb' -font "$SANS" -fill '#0b3d91' -pointsize 34 -annotate +40+60 'AIR CANADA  BOARDING PASS' \
    -fill black -pointsize 24 -annotate +40+130 $'PASSENGER: CHAUHAN/KARTIKEY\nFLIGHT AC 856   TORONTO YYZ -> LONDON LHR\nDATE 21 NOV 2026   BOARDING 18:45   GATE E77\nSEAT 32K   ZONE 4' \
    "$D/Screenshot_20261012-091500.png"
  "$IM" -size 800x600 xc:'#ffcc00' -font "$SANS" -fill black -pointsize 44 -gravity north -annotate +0+40 'WHEN THE BUILD PASSES' \
    -gravity south -annotate +0+40 'ON THE FIRST TRY' -gravity center -fill '#ff3366' -draw 'circle 400,300 400,200' "$D/funny.webp"
  cp "$D/IMG_20261003_receipt.jpg" "$D/IMG_20261003_receipt (2).jpg"
  # an image-only PDF, like a scan: no text layer, so sortling must render the page
  "$IM" -size 1240x1754 xc:white -font "$SANS" -fill black -pointsize 36 -annotate +80+120 'LIFELABS - LABORATORY REPORT' \
    -pointsize 26 -annotate +80+220 $'Patient: Kartikey Chauhan    DOB: 1994-03-11\nCollected: 2026-09-14\n\nTest                 Result     Reference\nHemoglobin           148 g/L    135-175\nFerritin             42 ug/L    30-400\nVitamin D            61 nmol/L  75-250   LOW\nTSH                  1.9 mIU/L  0.3-4.2\n\nOrdering physician: Dr. A. Patel' \
    -density 150 "$D/scanned_lab_report.pdf"
  # a PDF with a real text layer, written by hand so no PDF library is needed
  python3 -I - "$D/cnn_lecture4_notes.pdf" <<'PY'
import sys
lines = ["ONLINE COURSE: Deep Learning Specialization", "Lecture 4 notes - Convolutional Neural Networks",
         "Topics: padding, strides, pooling layers, ResNets,", "1x1 convolutions, transfer learning and data augmentation.",
         "Assignment: implement a residual block in PyTorch."]
content = "BT /F1 14 Tf 60 760 Td 18 TL " + " ".join("(%s) '" % l.replace("(", "\\(").replace(")", "\\)") for l in lines) + " ET"
objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content), "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
out, offs = b"%PDF-1.4\n", []
for i, o in enumerate(objs, 1):
    offs.append(len(out)); out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode()
x = len(out)
out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode() + b"".join(("%010d 00000 n \n" % o).encode() for o in offs)
out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)).encode()
open(sys.argv[1], "wb").write(out)
PY
  echo "built $D"
fi

# --- docs-neutral: the same files with names that give nothing away -----------------------
N="$OUT/docs-neutral"
if [ ! -d "$N" ]; then
  mkdir -p "$N"
  cp "$D/cnn_lecture4_notes.pdf" "$N/file01.pdf"; cp "$D/funny.webp" "$N/file02.webp"
  cp "$D/IMG_20261003_receipt.jpg" "$N/file03.jpg"; cp "$D/scanned_lab_report.pdf" "$N/file04.pdf"
  cp "$D/Screenshot_20261012-091500.png" "$N/file05.png"; cp "$D/Screenshot from 2026-10-08 14-22-07.png" "$N/file06.png"
  echo "built $N"
fi

# --- photos: 10 stock photos, memes, wishes and quote cards, blurry and dark junk -----------
P="$OUT/photos"
if [ ! -d "$P" ]; then
  mkdir -p "$P"
  for id in 237 1025 1011 1039 1047 1080 292 1062 1059 429; do
    curl -sfL -m 60 -o "$P/p$id.jpg" "https://picsum.photos/id/$id/640/480" || { echo "error: download failed (picsum id $id)" >&2; rm -rf "$P"; exit 1; }
  done
  "$IM" "$P/p237.jpg" -font "$BOLD" -pointsize 46 -fill white -stroke black -strokewidth 3 -gravity north -annotate +0+18 'ME AT 3AM' \
    -gravity south -annotate +0+18 'ONE MORE EPISODE' "$P/IMG-20261003-WA0012.jpg"
  "$IM" -size 640x640 gradient:'#ff9a9e-#fad0c4' -font "$SERIF_I" -fill '#b5005b' -pointsize 72 -gravity north -annotate +0+70 'Good Morning' \
    -fill '#7a0040' -pointsize 30 -gravity center -annotate +0+40 $'May your day be filled\nwith blessings and joy' \
    -fill '#ffd700' -draw 'circle 120,520 140,520' -draw 'circle 520,500 545,500' -draw 'circle 320,560 340,560' "$P/IMG-20261005-WA0003.jpg"
  "$IM" -size 800x500 xc:'#1d3557' -font "$SERIF_I" -fill white -pointsize 34 -gravity center \
    -annotate +0-20 $'"Success is not final, failure is not fatal:\nit is the courage to continue that counts."' \
    -pointsize 24 -fill '#a8dadc' -annotate +0+110 '- share with 10 friends -' "$P/IMG-20261006-WA0021.jpg"
  "$IM" "$P/p1047.jpg" -blur 0x18 "$P/IMG_20261007_101512.jpg"
  "$IM" "$P/p1059.jpg" -brightness-contrast -92x-40 "$P/IMG_20261008_224401.jpg"
  cp "$D/IMG_20261003_receipt.jpg" "$P/IMG_20261009_183000.jpg"
  cp "$D/Screenshot from 2026-10-08 14-22-07.png" "$P/Screenshot_20261010.png"
  echo "built $P"
fi
echo "fixtures ready in $OUT"
