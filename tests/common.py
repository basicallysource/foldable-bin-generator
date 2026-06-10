"""
Shared definitions for the rev01 ↔ rev02 geometry-equivalence suite.

The contract: rev02 must reproduce rev01's laser output EXACTLY. Three layers
of proof (see EQUIVALENCE.md):

  1. check_source_identical.py — the vendored binflatten package is
     byte-for-byte the rev01 package (sha256 manifest).
  2. make_golden.py + check_equivalence.py — rev01's real HTTP surface
     (upload/process/refold/tester/download) is replayed against rev02's API
     in-process; SVG/DXF/preview must be BYTE-EQUAL (timestamps normalised),
     numeric JSON fields exactly equal.
  3. check_deployed.py — the same corpus against the live Vercel deployment;
     byte-equality is attempted first, with a fallback numeric comparison
     (identical text skeleton, numbers within FLOAT_TOL_MM) because the
     deployment runs a different CPU/Python/libs and may differ in the last
     printed decimal.

Cases cover every STEP file in the repo × parameter sets that exercise each
geometry-affecting code path (fold modes, compensation on/off, kerf, seam
tabs/dovetails, units/scale, labels/settings block, relief, overlay).
"""

from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REV02 = os.path.dirname(HERE)   # the app root (name kept from the rev02 port)
GOLDEN = os.path.join(HERE, "golden")

# The corpus inputs are the bins shipped with the app itself (public/bins/),
# so the suite stays self-contained after the legacy steps/ folder is gone.
STEP_FILES = [
    os.path.join(REV02, "public", "bins", n)
    for n in ("bin_half_left.step", "bin_half_right.step",
              "bin_third_left.step", "bin_third_center.step",
              "bin_third_right.step")
]

# Form-field overrides, exactly as the web UI would post them (strings).
# Defaults for everything else come from FlattenParams inside the pipeline.
PARAM_SETS = {
    "default": {},
    "perf": {
        "fold_mode": "perf", "perf_dash_mm": "2", "perf_gap_mm": "1",
    },
    "perf_overlay_relief": {
        "fold_mode": "perf", "perf_dash_mm": "3", "perf_gap_mm": "0.5",
        "overlay_score": "true", "fold_end_relief_mm": "2",
    },
    "comp_off": {
        "fold_comp_factor": "0", "fold_comp_angle_scaled": "no",
        "floor_clearance_factor": "0",
    },
    "inches_scaled_nokerf": {
        "output_units": "in", "scale": "1.27", "kerf_compensate": "false",
    },
    "tabs_off_settings_label": {
        "seam_tab_count": "0", "add_settings_label": "true",
        "add_labels": "false", "label_font_mm": "3",
    },
    "heavy_tabs_thick": {
        "seam_tab_count": "3", "seam_tab_width_mm": "8",
        "seam_tab_dovetail_mm": "1.2", "seam_slot_clearance_mm": "0.4",
        "material_thickness_mm": "6.35", "kerf_mm": "0.3",
    },
}

# refold is the expensive 3D check; run it on defaults + one comp variant
REFOLD_PARAM_SETS = ["default", "comp_off"]

TESTER_SETS = {
    "default": {},
    "custom_grid_overlay": {
        "dash_values_mm": "1, 2.5, 4", "gap_values_mm": "0.5, 1.25",
        "overlay_score": "true", "show_title": "false", "show_legends": "false",
        "score_inset_mm": "1.5", "coupon_w_mm": "28", "coupon_h_mm": "36",
    },
    "inches_no_continuous": {
        "output_units": "in", "include_continuous": "false",
        "gutter_mm": "6", "margin_mm": "8",
    },
}

# ---------------------------------------------------------------------------
# normalisation / comparison
# ---------------------------------------------------------------------------

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def normalize(text: str) -> str:
    """Make output text time-independent (the settings label engraves
    generation date/time)."""
    return _TIMESTAMP.sub("<TIMESTAMP>", text)


_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

# Numbers in SVG/DXF are printed with 4–5 decimals; cross-platform float
# differences can flip the last printed digit. 1e-3 mm = 1 µm, far below
# kerf/laser precision, so a pass at this tolerance is physically exact.
FLOAT_TOL = 1.5e-3


def numeric_diff(a: str, b: str, tol: float = FLOAT_TOL):
    """Compare two texts as (skeleton, numbers). Returns None if equivalent,
    else a human-readable reason."""
    a, b = normalize(a), normalize(b)
    if a == b:
        return None
    if _NUM.sub("#", a) != _NUM.sub("#", b):
        return "text skeletons differ (structure/ordering changed)"
    na = [float(x) for x in _NUM.findall(a)]
    nb = [float(x) for x in _NUM.findall(b)]
    if len(na) != len(nb):
        return f"number counts differ ({len(na)} vs {len(nb)})"
    worst = max((abs(x - y) for x, y in zip(na, nb)), default=0.0)
    if worst > tol:
        return f"max numeric deviation {worst:g} exceeds {tol:g}"
    return None


def json_diff(a, b, path="$", tol=FLOAT_TOL):
    """Recursive tolerant compare of JSON-ish structures (refold reports).
    Strings are compared via numeric_diff (the silhouette SVGs embed floats).
    Returns list of mismatch descriptions (empty = equivalent)."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        if sorted(a) != sorted(b):
            out.append(f"{path}: keys {sorted(a)} != {sorted(b)}")
            return out
        for k in a:
            out += json_diff(a[k], b[k], f"{path}.{k}", tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: list lengths {len(a)} != {len(b)}")
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            out += json_diff(x, y, f"{path}[{i}]", tol)
    elif isinstance(a, bool) or isinstance(b, bool):
        if a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(float(a) - float(b)) > tol:
            out.append(f"{path}: {a} != {b} (tol {tol:g})")
    elif isinstance(a, str) and isinstance(b, str):
        d = numeric_diff(a, b)
        if d:
            out.append(f"{path}: {d}")
    else:
        if a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    return out


def case_id(step_path: str, set_name: str) -> str:
    stem = os.path.splitext(os.path.basename(step_path))[0]
    return f"{stem.replace(' ', '_')}__{set_name}"


def write_case(case_dir: str, payload: dict):
    os.makedirs(case_dir, exist_ok=True)
    for key in ("svg", "dxf", "preview"):
        if key in payload:
            with open(os.path.join(case_dir, key + (".svg" if key != "dxf" else ".dxf")), "w") as f:
                f.write(normalize(payload.pop(key)))
    with open(os.path.join(case_dir, "meta.json"), "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)


def read_case(case_dir: str) -> dict:
    with open(os.path.join(case_dir, "meta.json")) as f:
        payload = json.load(f)
    for key, fn in (("svg", "svg.svg"), ("dxf", "dxf.dxf"), ("preview", "preview.svg")):
        p = os.path.join(case_dir, fn)
        if os.path.exists(p):
            with open(p) as f:
                payload[key] = f.read()
    return payload
