"""
Refold verification CLI: fold the generated flat pattern back up in 3D and
compare it to the CAD STEP — silhouette overlays + outermost dimensions.

Usage:
    python verify.py "../steps/0_bins - bin_third_left.step" [--out DIR]
                     [--set fold_comp_factor=1.0] [--set seam_tab_count=0] ...

Writes <out>/view_*.svg overlays (green = CAD, red = refold) and
<out>/metrics.json, prints a summary table to stdout. Exit code 1 if any
outermost dimension deviates more than --tol (default 1.0 mm).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from binflatten.params import FlattenParams
from binflatten.pipeline import load_model
from binflatten.unfold import unfold
from binflatten.refold import verify_refold


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("step", help="path to the bin .step file")
    ap.add_argument("--out", default="outputs/verify", help="output dir")
    ap.add_argument("--set", action="append", default=[], metavar="K=V",
                    help="override a FlattenParams field")
    ap.add_argument("--tol", type=float, default=1.5,
                    help="pass/fail tolerance on outer dimensions (mm). Note: "
                         "a leaning front wall whose bottom edge sits one stock "
                         "thickness up (on the real floor/toes) legitimately "
                         "pulls the ground-level front extent back ~1 mm.")
    args = ap.parse_args(argv)

    overrides = dict(kv.split("=", 1) for kv in args.set)
    params = FlattenParams.from_dict({**FlattenParams().to_dict(), **overrides})

    model = load_model(args.step)
    fp = unfold(model, params)
    rep = verify_refold(model, fp, params)

    os.makedirs(args.out, exist_ok=True)
    for i, vw in enumerate(rep["views"]):
        path = os.path.join(args.out, f"view_{i}_{vw['name'].split()[0]}.svg")
        with open(path, "w") as f:
            f.write(vw["svg"])
        print(f"wrote {path}")

    print(f"\n{'view':38s} {'IoU':>6s}  {'CAD WxH':>16s}  {'refold WxH':>16s}  diff")
    for vw in rep["views"]:
        print(f"{vw['name']:38s} {vw['iou']:6.3f}  "
              f"{vw['cad_size'][0]:7.2f}x{vw['cad_size'][1]:<8.2f} "
              f"{vw['refold_size'][0]:7.2f}x{vw['refold_size'][1]:<8.2f} "
              f"{vw['size_diff'][0]:+.2f},{vw['size_diff'][1]:+.2f}")
    print(f"\n{'outer dimension':14s} {'CAD':>8s} {'refold':>8s} {'diff':>7s}")
    fail = False
    for k, d in rep["extents"].items():
        flag = "" if abs(d["diff"]) <= args.tol else "  <-- FAIL"
        fail |= bool(flag)
        print(f"{k:14s} {d['cad']:8.2f} {d['refold']:8.2f} {d['diff']:+7.2f}{flag}")
    for w in (rep.get("warnings") or []) + (fp.warnings or []):
        print("warning:", w)

    rep_out = {k: v for k, v in rep.items() if k != "scene"}
    for vw in rep_out["views"]:
        vw.pop("svg", None)
    with open(os.path.join(args.out, "metrics.json"), "w") as f:
        json.dump(rep_out, f, indent=2)
    print(f"\nmetrics -> {os.path.join(args.out, 'metrics.json')}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
