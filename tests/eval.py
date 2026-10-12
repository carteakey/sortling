#!/usr/bin/env python3
"""Score sortling backends on the labelled fixtures (build them with tests/make_fixtures.sh).

    python3 tests/eval.py --backend local-d3 --backend local-clef
    python3 tests/eval.py --backend local-d3 --sets photos --profiles photos --misses

Runs sortling.py as a dry run (nothing is moved) and compares each file's target folder
with tests/expected.json. Uses your normal sortling config for backends unless --config
is given.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SORTLING = HERE.parent / "sortling.py"


def run(root: Path, backend: str, profile: str, config: str, timeout_s: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="sortling-eval-") as out:
        cmd = [sys.executable, "-B", str(SORTLING), "--root", str(root), "--profile", profile, "--backend", backend,
               "--json", "--no-progress", "--output-dir", out, "--timeout-s", str(timeout_s)]
        if config:
            cmd += ["--config", config]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"sortling failed ({proc.returncode}): {proc.stderr.strip()[-400:]}")
        return json.loads(proc.stdout)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", action="append", required=True, help="backend name from the sortling config (repeatable)")
    p.add_argument("--sets", default="", help="comma-separated fixture sets (default: all in expected.json)")
    p.add_argument("--profiles", default="", help="comma-separated profiles to score (default: all listed per set)")
    p.add_argument("--config", default="", help="sortling config file")
    p.add_argument("--timeout-s", type=int, default=300)
    p.add_argument("--misses", action="store_true", help="print each wrong answer with the model's probabilities")
    args = p.parse_args()

    expected = {k: v for k, v in json.loads((HERE / "expected.json").read_text()).items() if not k.startswith("_")}
    # optional local-only sets from the user's own files: tests/expected.local.json + tests/fixtures-local/
    local = HERE / "expected.local.json"
    local_sets = {k: v for k, v in json.loads(local.read_text()).items() if not k.startswith("_")} if local.exists() else {}
    expected.update(local_sets)
    sets = [s for s in args.sets.split(",") if s] or list(expected)
    want_profiles = [s for s in args.profiles.split(",") if s]

    jobs = []
    for name in sets:
        root = HERE / ("fixtures-local" if name in local_sets else "fixtures") / name
        if not root.is_dir():
            print(f"error: {root} missing; run tests/make_fixtures.sh first", file=sys.stderr)
            return 2
        for profile, truth in expected[name].items():
            if not want_profiles or profile in want_profiles:
                jobs.append((name, profile, truth, root))

    totals = {b: [0, 0, 0.0] for b in args.backend}
    rows = []
    # one backend at a time: on a single-GPU router, alternating backends reloads a model per job
    for backend in args.backend:
        for name, profile, truth, root in jobs:
            t0 = time.time()
            try:
                data = run(root, backend, profile, args.config, args.timeout_s)
            except RuntimeError as exc:
                print(f"{name}/{profile}/{backend}: {exc}", file=sys.stderr)
                return 1
            got = {r["file"]: r for r in data["records"]}
            misses = []
            for fname, want in truth.items():
                rec = got.get(fname)
                if want is None:  # must be left alone
                    if rec:
                        misses.append((fname, "(left alone)", rec["target_folder"], rec["reason"]))
                    continue
                ok_set = want if isinstance(want, list) else [want]
                folder = rec["target_folder"] if rec else "(missing)"
                if folder not in ok_set:
                    misses.append((fname, " or ".join(ok_set), folder, rec["reason"] if rec else ""))
            right, secs = len(truth) - len(misses), time.time() - t0
            totals[backend][0] += right
            totals[backend][1] += len(truth)
            totals[backend][2] += secs
            fallback = data.get("classifier", "").startswith("fallback")
            rows.append((name, profile, backend, right, len(truth), secs, fallback, misses))
    # print grouped by set so backends sit side by side
    order = {(n, p): i for i, (n, p, _, _) in enumerate(jobs)}
    rows.sort(key=lambda r: (order[(r[0], r[1])], args.backend.index(r[2])))

    print(f"{'set':<14} {'profile':<9} {'backend':<14} {'score':>7}  {'time':>6}")
    for name, profile, backend, right, n, secs, fallback, misses in rows:
        note = "  (model unavailable: keyword fallback)" if fallback else ""
        print(f"{name:<14} {profile:<9} {backend:<14} {right:>3}/{n:<3}  {secs:5.0f}s{note}")
        if args.misses:
            for fname, want, folder, reason in misses:
                print(f"    x {fname}: want {want}, got {folder}  [{reason[:110]}]")
    print()
    for backend, (right, n, secs) in totals.items():
        print(f"total {backend:<14} {right}/{n} ({100 * right / max(n, 1):.0f}%)  in {secs:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
