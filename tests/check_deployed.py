"""
Guarantee #3: the LIVE deployment reproduces the golden corpus.

Replays every golden case over real HTTP against a deployed (or local)
instance. The deployment runs a different CPU / Python / numpy / shapely-GEOS
build than the machine that froze the goldens, so outputs can differ in two
environment-noise ways that are NOT logic changes:

  * last printed decimal of a coordinate (float rounding), and
  * a boolean-op (union/buffer) vertex that one GEOS build emits and the
    other dissolves — nanometres from collinear, zero area.

The bar, per output, strictest first (each case reports its tier):

  ok=   byte-equal after timestamp normalisation
  ok~   identical text skeleton, every number within FLOAT_TOL (1.5 µm)
  ok@   geometric equality: every cut/panel ring matches its golden ring with
        symmetric-difference area within hairline tolerance, every score/fold
        segment within FLOAT_TOL, all engraved text identical and in place,
        all scalar metrics (dims, panel/fold counts, warnings) exactly equal

Anything beyond that fails hard.

Run:  python tests/check_deployed.py https://<deployment>.vercel.app
      python tests/check_deployed.py http://127.0.0.1:3000   (local dev)
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

from common import (FLOAT_TOL, GOLDEN, REV02, json_diff, normalize,
                    numeric_diff, read_case)

PREVIEW_TOL = 1.1e-2   # the preview SVG prints 2 decimals; a rounding flip is 0.01


# ---------------------------------------------------------------------------
# geometric comparison helpers
# ---------------------------------------------------------------------------

_PATH_D = re.compile(r'<path[^>]*? d="([^"]*)"')
_POLY_PTS = re.compile(r'<polygon[^>]*? points="([^"]*)"')
_LINE = re.compile(r'<line x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"')
_TEXT = re.compile(r'<text x="(-?[\d.]+)" y="(-?[\d.]+)"[^>]*>([^<]*)</text>')
_COORD = re.compile(r"(-?\d+(?:\.\d+)?)[,\s]+(-?\d+(?:\.\d+)?)")


def _d_rings(d: str):
    out = []
    for seg in d.split("Z"):
        pts = [(float(x), float(y)) for x, y in _COORD.findall(seg)]
        if len(pts) >= 3:
            out.append(pts)
    return out


def _ring_diff(ra, rb, where: str):
    """Compare two rings as polygons: symmetric-difference area within a
    hairline (sub-µm sliver) tolerance. Returns reason or None."""
    from shapely.geometry import Polygon
    pa, pb = Polygon(ra).buffer(0), Polygon(rb).buffer(0)
    sym = pa.symmetric_difference(pb).area
    lim = 1e-6 * pa.union(pb).area + 1e-4
    if sym > lim:
        return f"{where}: ring differs by {sym:g} area units (> {lim:g})"
    return None


def _seg_diff(sa, sb, where: str, tol: float):
    if len(sa) != len(sb):
        return f"{where}: segment counts differ ({len(sa)} vs {len(sb)})"
    for i, (a, b) in enumerate(zip(sa, sb)):
        if max(abs(x - y) for x, y in zip(a, b)) > tol:
            return f"{where}: segment {i} deviates more than {tol:g}"
    return None


def _strip_geometry(svg: str) -> str:
    """The document with all compared-elsewhere geometry payloads removed —
    what's left (header, viewBox, styling, structure) is compared as text."""
    svg = _PATH_D.sub("<path*", svg)
    svg = _POLY_PTS.sub("<polygon*", svg)
    svg = _LINE.sub("<line*", svg)
    svg = _TEXT.sub("<text*", svg)
    return svg


def svg_geo_diff(a: str, b: str, num_tol: float):
    """Geometric-equality check of two SVGs from the same generator: rings as
    polygons, segments/texts in order within tolerance, chrome as text."""
    ra = [r for d in _PATH_D.findall(a) for r in _d_rings(d)]
    ra += [r for p in _POLY_PTS.findall(a) for r in _d_rings(p)]
    rb = [r for d in _PATH_D.findall(b) for r in _d_rings(d)]
    rb += [r for p in _POLY_PTS.findall(b) for r in _d_rings(p)]
    if len(ra) != len(rb):
        return f"ring counts differ ({len(ra)} vs {len(rb)})"
    for i, (x, y) in enumerate(zip(ra, rb)):
        d = _ring_diff(x, y, f"ring {i}")
        if d:
            return d
    sa = [tuple(map(float, m)) for m in _LINE.findall(a)]
    sb = [tuple(map(float, m)) for m in _LINE.findall(b)]
    d = _seg_diff(sa, sb, "lines", num_tol)
    if d:
        return d
    ta, tb = _TEXT.findall(a), _TEXT.findall(b)
    if len(ta) != len(tb):
        return f"text counts differ ({len(ta)} vs {len(tb)})"
    for i, (xa, xb) in enumerate(zip(ta, tb)):
        if xa[2] != xb[2]:
            return f"text {i}: {xa[2]!r} != {xb[2]!r}"
        if max(abs(float(xa[j]) - float(xb[j])) for j in (0, 1)) > num_tol:
            return f"text {i} position deviates more than {num_tol:g}"
    return numeric_diff(_strip_geometry(a), _strip_geometry(b), num_tol)


def _parse_dxf(text: str):
    """Minimal reader for the R12 subset export.py writes: closed POLYLINEs
    and LINEs. Returns (rings, segments) in document order."""
    vals = text.split("\n")
    pairs = list(zip(vals[0::2], vals[1::2]))
    rings, segs = [], []
    i = 0
    while i < len(pairs):
        c, v = pairs[i]
        if c == "0" and v == "POLYLINE":
            ring = []
            while i < len(pairs) and pairs[i] != ("0", "SEQEND"):
                if pairs[i][0] == "10":
                    ring.append((float(pairs[i][1]), float(pairs[i + 1][1])))
                i += 1
            rings.append(ring)
        elif c == "0" and v == "LINE":
            xy = {}
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                if pairs[i][0] in ("10", "20", "11", "21"):
                    xy[pairs[i][0]] = float(pairs[i][1])
                i += 1
            segs.append((xy["10"], xy["20"], xy["11"], xy["21"]))
            continue
        i += 1
    return rings, segs


def dxf_geo_diff(a: str, b: str, num_tol: float):
    ra, sa = _parse_dxf(a)
    rb, sb = _parse_dxf(b)
    if len(ra) != len(rb):
        return f"polyline counts differ ({len(ra)} vs {len(rb)})"
    for i, (x, y) in enumerate(zip(ra, rb)):
        d = _ring_diff(x, y, f"polyline {i}")
        if d:
            return d
    return _seg_diff(sa, sb, "lines", num_tol)


def silhouette_svg_diff(a: str, b: str):
    """The refold report's overlay drawings come from GEOS buffer() — vertex
    layout is build-dependent — so they are compared as evenodd shapes."""
    from shapely.geometry import Polygon

    def shape(d):
        polys = sorted((Polygon(r).buffer(0) for r in _d_rings(d)),
                       key=lambda p: -p.area)
        out = None
        for p in polys:
            out = p if out is None else out.symmetric_difference(p)
        return out

    da, db = _PATH_D.findall(a), _PATH_D.findall(b)
    if len(da) != len(db):
        return f"path counts differ ({len(da)} vs {len(db)})"
    for i, (xa, xb) in enumerate(zip(da, db)):
        pa, pb = shape(xa), shape(xb)
        if pa is None or pb is None:
            if (pa is None) != (pb is None):
                return f"path {i}: one side empty"
            continue
        sym = pa.symmetric_difference(pb).area
        lim = max(1.0, 0.002 * pa.union(pb).area)  # mm² in view coords
        if sym > lim:
            return f"path {i}: silhouettes differ by {sym:.2f} mm² (> {lim:.2f})"
    return None


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

def multipart(fields: dict, file_field=None) -> tuple:
    bound = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f"--{bound}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    if file_field:
        name, filename, data = file_field
        out.append(f"--{bound}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                   f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
        out.append(data)
        out.append(b"\r\n")
    out.append(f"--{bound}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={bound}"


def post(base: str, route: str, fields: dict, file_field=None) -> dict:
    body, ctype = multipart(fields, file_field)
    req = urllib.request.Request(base.rstrip("/") + route, data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"HTTP {e.code}"}


def run_case(base: str, case: dict) -> dict:
    if case["kind"] == "tester":
        return post(base, "/api/tester", dict(case["params"]))
    step = os.path.join(REV02, case["step"])
    with open(step, "rb") as f:
        ff = ("file", os.path.basename(step), f.read())
    route = "/api/refold" if case["kind"] == "refold" else "/api/process"
    return post(base, route, dict(case["params"]), ff)


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------

def compare(case: dict, got: dict):
    """Returns (errors, tier) — tier: 0 byte-equal, 1 numeric, 2 geometric."""
    errs, tier = [], 0
    if not isinstance(got, dict) or got.get("error"):
        return [f"rev02 errored: {got and got.get('error')}"], 2

    if case["kind"] == "refold":
        if json.dumps(case["report"], sort_keys=True) == json.dumps(got, sort_keys=True):
            return errs, 0
        tier = 1
        ref, dep = copy.deepcopy(case["report"]), copy.deepcopy(got)
        for i, (va, vb) in enumerate(zip(ref.get("views", []), dep.get("views", []))):
            d = silhouette_svg_diff(va.pop("svg", ""), vb.pop("svg", ""))
            if d:
                errs.append(f"views[{i}].svg: {d}")
        errs += json_diff(ref, dep)[:8]
        return errs, tier

    for k in ("preview", "svg", "dxf"):
        dep = normalize(got[k])
        if dep == case[k]:
            continue
        tier = max(tier, 1)
        num_tol = PREVIEW_TOL if k == "preview" else FLOAT_TOL
        if numeric_diff(case[k], dep, num_tol) is None:
            continue
        # texts differ structurally — fall back to geometric equality
        tier = 2
        d = (dxf_geo_diff(case[k], dep, num_tol) if k == "dxf"
             else svg_geo_diff(case[k], dep, num_tol))
        if d:
            errs.append(f"{k}: {d}")
    for k in (("warnings", "width", "height", "n_panels", "n_folds", "root",
               "shell_face_ids") if case["kind"] == "process" else ("n_cells",)):
        if got.get(k) != case[k]:
            errs.append(f"{k}: golden {case[k]!r} != deployed {got.get(k)!r}")
    return errs, tier


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    base = sys.argv[1]
    cases = sorted(os.listdir(GOLDEN))
    if not cases:
        print("no golden cases — run tests/make_golden.py first")
        return 2
    failures = []
    tiers = [0, 0, 0]
    marks = {0: "ok=  ", 1: "ok~  ", 2: "ok@  "}
    for name in cases:
        case = read_case(os.path.join(GOLDEN, name))
        got = run_case(base, case)
        errs, tier = compare(case, got)
        failures += [f"{name}: {e}" for e in errs]
        if not errs:
            tiers[tier] += 1
        print(("FAIL " if errs else marks[tier]) + name)
        for e in errs:
            print("       " + e)
    print(f"\n{len(cases)} cases: {tiers[0]} byte-equal, {tiers[1]} within "
          f"{FLOAT_TOL:g} mm, {tiers[2]} geometrically equal, "
          f"{len(set(f.split(':')[0] for f in failures))} failed")
    if failures:
        print("FAIL — deployment does not match the golden corpus")
        return 1
    print("PASS — deployment reproduces rev01's laser output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
