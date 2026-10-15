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
    -pointsize 26 -annotate +80+220 $'Patient: Alex Example    DOB: 1990-01-01\nCollected: 2026-09-14\n\nTest                 Result     Reference\nHemoglobin           148 g/L    135-175\nFerritin             42 ug/L    30-400\nVitamin D            61 nmol/L  75-250   LOW\nTSH                  1.9 mIU/L  0.3-4.2\n\nOrdering physician: Dr. A. Patel' \
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
# --- charts: a bar chart, a paper-style line figure and a cheat sheet; none of them memes --------
C="$OUT/charts"
if [ ! -d "$C" ]; then
  mkdir -p "$C"
  "$IM" -size 900x600 xc:white -font "$BOLD" -fill black -pointsize 30 -annotate +40+55 'GPU memory bandwidth by generation (TB/s)' \
    -font "$SANS" -pointsize 20 -fill '#555555' -draw 'line 80,520 860,520' -draw 'line 80,100 80,520' \
    -fill '#4c78a8' -draw 'rectangle 130,420 230,520' -draw 'rectangle 300,340 400,520' \
    -draw 'rectangle 470,250 570,520' -draw 'rectangle 640,140 740,520' \
    -fill black -annotate +150+550 'A100' -annotate +320+550 'H100' -annotate +490+550 'H200' -annotate +660+550 'B200' \
    -annotate +165+410 '2.0' -annotate +335+330 '3.4' -annotate +505+240 '4.8' -annotate +675+130 '8.0' \
    -fill '#777777' -pointsize 16 -annotate +40+590 'Source: vendor datasheets' "$C/bandwidth_chart.png"
  "$IM" -size 800x560 xc:white -font "$SANS" -fill black -pointsize 22 -annotate +300+40 'Figure 3. Validation loss' \
    -stroke black -draw 'line 90,480 760,480' -draw 'line 90,80 90,480' -stroke none \
    -pointsize 16 -annotate +380+520 'training steps (k)' -annotate +100+505 '0' -annotate +410+505 '50' -annotate +730+505 '100' \
    -fill none -stroke '#d62728' -strokewidth 3 -draw 'path "M 95,110 C 200,330 350,400 750,430"' \
    -stroke '#1f77b4' -draw 'stroke-dasharray 10 6 path "M 95,130 C 220,380 400,440 750,455"' -stroke none \
    -fill black -pointsize 16 -annotate +560+150 '— baseline' -annotate +560+175 '- - ours' "$C/fig3.png"
  "$IM" -size 900x700 xc:'#fdfdf6' -font "$BOLD" -fill '#222222' -pointsize 34 -annotate +40+60 'GIT CHEAT SHEET' \
    -font "$MONO" -pointsize 19 -annotate +40+120 $'git status            show changed files\ngit add -p            stage hunks interactively\ngit commit --amend    rewrite the last commit\ngit rebase -i HEAD~3  edit the last 3 commits\ngit stash push -m x   shelve work in progress\ngit log --oneline     compact history\ngit bisect start      find the bad commit\ngit reflog            recover lost commits' \
    "$C/cheatsheet.jpg"
  echo "built $C"
fi
# --- files: Office/text documents (read and asked about), extension-sorted files, files left alone ---
F="$OUT/files"
if [ ! -d "$F" ]; then
  mkdir -p "$F"
  python3 -I - "$F" <<'PY'
import sys, zipfile, zlib, os
out = sys.argv[1]
def office(name, parts):
    with zipfile.ZipFile(os.path.join(out, name), "w", zipfile.ZIP_DEFLATED) as z:
        for part, body in parts.items():
            z.writestr(part, body)
def wp(lines):  # WordprocessingML paragraphs
    return '<?xml version="1.0"?><w:document xmlns:w="w"><w:body>' + "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in lines) + "</w:body></w:document>"
office("invoice-oct.docx", {"word/document.xml": wp(["INVOICE #4471", "Northwind Hosting Ltd.", "Bill to: A. Example", "Dedicated server, October 2026: $89.00", "HST 13%: $11.57", "Total due: $100.57 by 2026-11-01"])})
office("lecture-07.pptx", {f"ppt/slides/slide{i}.xml": f'<p:sld xmlns:a="a" xmlns:p="p"><a:p><a:t>{t}</a:t></a:p></p:sld>' for i, t in enumerate(
    ["CS 480 Lecture 7: Attention and Transformers", "Scaled dot-product attention: softmax(QK^T / sqrt(d)) V", "Multi-head attention and positional encodings", "Reading: Vaswani et al. 2017"], start=1)})
office("statement-sept.xlsx", {"xl/sharedStrings.xml": '<sst><si><t>Chequing account statement</t></si><si><t>September 2026</t></si><si><t>Opening balance 2,410.22</t></si><si><t>Payroll deposit 3,850.00</t></si><si><t>Rent payment -1,950.00</t></si><si><t>Closing balance 4,310.22</t></si></sst>'})
open(os.path.join(out, "lease-agreement.rtf"), "w").write(r"{\rtf1\ansi {\b RESIDENTIAL LEASE AGREEMENT}\par This lease is made between the Landlord and the Tenant for the premises at 12 Elm St, Unit 4.\par Term: 12 months from 2026-11-01. Rent: $1,950 per month.\par Signed by both parties.\par}")
open(os.path.join(out, "rx-notes.txt"), "w").write("Prescription\nPatient: A. Example\nRx: Amoxicillin 500 mg, three times daily for 7 days\nPrescribed by Dr. A. Patel, family medicine\nRefills: 0\n")
open(os.path.join(out, "lisbon-trip.md"), "w").write("# Lisbon trip itinerary\n\n- Flight TP 258 YYZ -> LIS, 14 Nov 2026, 21:40\n- Hotel: Casa do Bairro, check-in 15 Nov\n- Day 2: Belem tower, LX Factory\n- Return flight 21 Nov\n")
# a PDF whose text is Flate-compressed, like nearly every real PDF: exercises the built-in reader
text = ["EMPLOYMENT OFFER LETTER", "Dear Alex,", "We are pleased to offer you the position of Senior Data Engineer", "starting 2026-12-01 at an annual salary of $142,000.", "Please sign and return by 2026-11-15."]
content = zlib.compress(("BT /F1 13 Tf 60 760 Td 18 TL " + " ".join("(%s) '" % l for l in text) + " ET").encode())
objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
pdf, offs = b"%PDF-1.4\n", []
for i, o in enumerate(objs, 1):
    offs.append(len(pdf)); pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
x = len(pdf)
pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
open(os.path.join(out, "offer-letter.pdf"), "wb").write(pdf)
# sorted by extension, no model
with zipfile.ZipFile(os.path.join(out, "project-src.zip"), "w") as z:
    z.writestr("main.py", "print('hi')\n")
for name, data in {"Installer-4.2.dmg": b"koly" + os.urandom(2000), "song.mp3": b"ID3" + os.urandom(3000),
                   "clip.mp4": b"\x00\x00\x00\x18ftypmp42" + os.urandom(3000), "novel.epub": b"PK\x03\x04epub" + os.urandom(500),
                   "Inter.ttf": b"\x00\x01\x00\x00" + os.urandom(800), "cleanup.py": b"import os\nprint(os.listdir('.'))\n",
                   "sales.csv": b"month,revenue\n2026-08,1200\n2026-09,1350\n", "ubuntu.torrent": b"d8:announce35:udp://tracker.example:80e",
                   "raspios.img": b"\0" * 512, "docs-link.url": b"[InternetShortcut]\r\nURL=https://example.com/docs\r\n",
                   "recipe.webloc": b'<?xml version="1.0"?><plist version="1.0"><dict><key>URL</key><string>https://example.com/recipe</string></dict></plist>',
                   "bracket.stl": b"solid bracket\nendsolid bracket\n",
                   # left alone: an unfinished download, a hidden file, an unknown extension
                   "big.iso.crdownload": os.urandom(1000), ".DS_Store": os.urandom(100), "notes.xyz": b"whatever"}.items():
    open(os.path.join(out, name), "wb").write(data)
PY
  echo "built $F"
fi
echo "fixtures ready in $OUT"
