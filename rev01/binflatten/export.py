"""
Turn a FlatPattern into laser-ready geometry and write SVG / DXF for LightBurn.

Geometry rules
--------------
* CUT: the outer boundary of the union of all panels (plus any interior holes).
  With kerf compensation on, the whole solid is offset OUTWARD by kerf/2, which
  simultaneously grows the outer profile and shrinks interior cutouts by the
  right amount so the finished part holds nominal size.
* SCORE / FOLD: the hinge segments between panels, on their own layer/colour so
  LightBurn can run them at low power (score) or as a perforation.

Everything is in millimetres. LightBurn imports SVG at 1 user-unit = 1 mm when
the viewBox and width/height (in mm) agree, which we ensure here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from shapely.geometry import Polygon, LineString, MultiPolygon
from shapely.ops import unary_union

from .params import FlattenParams, MM_PER_INCH
from .unfold import FlatPattern


@dataclass
class LaserGeometry:
    cut_loops: list      # list[np.ndarray Nx2] : [outer, hole, hole, ...] per piece, flattened
    score_segments: list # list[(np.ndarray2, np.ndarray2)]
    labels: list         # list[(text, np.ndarray2)]
    width: float         # mm
    height: float        # mm
    warnings: list       # list[str]
    # optional second score layer (e.g. the tester's continuous overlay on
    # perforated creases), drawn as solid lines in its own colour/layer
    aux_segments: list | None = None
    aux_color: str = "#00a650"


def _trim_segment(p0, p1, relief):
    if relief <= 0:
        return p0, p1
    d = p1 - p0
    L = np.linalg.norm(d)
    if L <= 2 * relief:
        return p0, p1
    u = d / L
    return p0 + u * relief, p1 - u * relief


def settings_label_lines(params: FlattenParams) -> list:
    """The settings that shaped this cut, one short line each, for engraving:
    part name, generation date/time, then the geometry-shaping parameters."""
    lines = []
    if params.part_name:
        lines.append(params.part_name)
    lines.append(datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append(f"t={params.material_thickness_mm:g}mm")
    if params.fold_mode == "perf":
        lines.append(f"perf {params.perf_dash_mm:g}/{params.perf_gap_mm:g}")
    else:
        lines.append(f"fold={params.fold_mode}")
    if params.overlay_score:
        lines.append("+score overlay")
    lines.append(f"kerf {params.kerf_mm:g}")
    lines.append(f"comp {params.fold_comp_factor:g}")
    lines.append(f"clear {params.floor_clearance_factor:g}")
    if params.fold_end_relief_mm:
        lines.append(f"relief {params.fold_end_relief_mm:g}")
    if params.seam_tab_count:
        lines.append(f"tabs {params.seam_tab_count}x{params.seam_tab_width_mm:g}"
                     f"+{params.seam_tab_dovetail_mm:g}dt")
    return lines


def _settings_entries(fp, params: FlattenParams) -> list:
    """Settings block as (text, pos) label entries, stacked line by line and
    centred on the floor panel (so the finished box carries its own recipe).
    Uses the area centroid — the vertex mean is biased toward the densely
    vertexed toe edge."""
    floor = next((p for p in fp.panels if p.role == "floor"), None)
    if floor is None:
        return []
    cen = Polygon(floor.poly).centroid
    lines = settings_label_lines(params)
    line_h = params.label_font_mm * 1.6
    top = cen.y - (len(lines) - 1) * line_h / 2.0
    return [(txt, np.array([cen.x, top + i * line_h]))
            for i, txt in enumerate(lines)]


def build_geometry(fp: FlatPattern, params: FlattenParams) -> LaserGeometry:
    polys = []
    for pan in fp.panels:
        poly = Polygon(pan.poly, pan.holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        polys.append(poly)
    union = unary_union(polys)

    # Weld hairline numerical seams: panels meet along (zero-width) fold edges,
    # and float error can leave sub-micron gaps that stop shapely from merging
    # them into one piece. A tiny out-then-in buffer closes those without any
    # visible distortion (1 µm << laser precision).
    weld = 1e-3
    union = union.buffer(weld, join_style=2).buffer(-weld, join_style=2)

    if params.kerf_compensate and params.kerf_mm > 0:
        union = union.buffer(params.kerf_mm / 2.0, join_style=2)  # 2 = mitre

    pieces = list(union.geoms) if isinstance(union, MultiPolygon) else [union]
    warnings = []
    dropped = 0
    cut_loops = []
    for piece in pieces:
        loops = [np.array(piece.exterior.coords)]
        for ring in piece.interiors:
            if Polygon(ring).area < params.min_hole_area_mm2:
                dropped += 1
                continue
            loops.append(np.array(ring.coords))
        cut_loops.append(loops)
    if dropped:
        warnings.append(
            f"dropped {dropped} interior hole(s) smaller than "
            f"{params.min_hole_area_mm2} mm² (notch-seam slivers)")
    if len(pieces) > 1:
        warnings.append(
            f"flat pattern split into {len(pieces)} disconnected pieces — "
            "panels may not all be joined by folds")

    # score / fold segments: clipped to the material (compensation can leave a
    # fold line slightly longer than the new shared edge), then end relief.
    score_segments = []
    for f in fp.folds:
        p0, p1 = np.asarray(f.p0), np.asarray(f.p1)
        seg = LineString([p0, p1]).intersection(union)
        if seg.geom_type == "MultiLineString" and len(seg.geoms):
            seg = max(seg.geoms, key=lambda g: g.length)
        if seg.geom_type == "LineString" and seg.length > 1e-6:
            coords = np.asarray(seg.coords)
            p0, p1 = coords[0], coords[-1]
        p0, p1 = _trim_segment(p0, p1, params.fold_end_relief_mm)
        score_segments.append((p0, p1))

    labels = []
    if params.add_labels:
        for pan in fp.panels:
            labels.append((f"{pan.role} #{pan.fid}", pan.centroid))

    mn, mx = fp.bounds()
    width = mx[0] - mn[0] + 2 * params.margin_mm
    height = mx[1] - mn[1] + 2 * params.margin_mm
    if params.add_settings_label:
        labels.extend(_settings_entries(fp, params))
    warnings = (fp.warnings or []) + warnings
    # optional continuous overlay on the same creases (own colour/layer)
    aux = list(score_segments) if params.overlay_score else None
    return LaserGeometry(cut_loops, score_segments, labels, width, height,
                         warnings, aux_segments=aux,
                         aux_color=params.overlay_color)


# --------------------------------------------------------------------------- #
# SVG                                                                           #
# --------------------------------------------------------------------------- #

def _unit_scale(params: FlattenParams):
    """SVG unit + numeric scale for the chosen output unit."""
    if params.output_units == "in":
        return "in", 1.0 / MM_PER_INCH
    return "mm", 1.0


def _perf_dashes(p0, p1, dash, gap):
    """Yield (a,b) sub-segments approximating a perforation."""
    d = p1 - p0
    L = np.linalg.norm(d)
    if L == 0:
        return
    u = d / L
    t = 0.0
    while t < L:
        a = p0 + u * t
        b = p0 + u * min(t + dash, L)
        yield a, b
        t += dash + gap


def to_svg(geom: LaserGeometry, params: FlattenParams, title="bin flat pattern") -> str:
    unit, sc = _unit_scale(params)
    W, H = geom.width * sc, geom.height * sc
    out = []
    out.append(f'<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{W:.4f}{unit}" height="{H:.4f}{unit}" '
        f'viewBox="0 0 {W:.4f} {H:.4f}">')
    out.append(f'<title>{title}</title>')
    # stroke width: thin, in user units
    sw = 0.1 * sc

    # CUT layer
    out.append(f'<g id="cut" stroke="{params.cut_color}" fill="none" '
               f'stroke-width="{sw:.4f}">')
    for loops in geom.cut_loops:
        for loop in loops:
            pts = loop * sc
            d = "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in pts) + " Z"
            out.append(f'  <path d="{d}"/>')
    out.append('</g>')

    # SCORE / FOLD layer
    out.append(f'<g id="score" stroke="{params.score_color}" fill="none" '
               f'stroke-width="{sw:.4f}">')
    for p0, p1 in geom.score_segments:
        if params.fold_mode == "perf":
            for a, b in _perf_dashes(p0, p1, params.perf_dash_mm, params.perf_gap_mm):
                a, b = a * sc, b * sc
                out.append(f'  <line x1="{a[0]:.4f}" y1="{a[1]:.4f}" '
                           f'x2="{b[0]:.4f}" y2="{b[1]:.4f}"/>')
        else:
            a, b = p0 * sc, p1 * sc
            out.append(f'  <line x1="{a[0]:.4f}" y1="{a[1]:.4f}" '
                       f'x2="{b[0]:.4f}" y2="{b[1]:.4f}"/>')
    out.append('</g>')

    # second score layer (solid overlay lines, own colour => own layer)
    if geom.aux_segments:
        out.append(f'<g id="score2" stroke="{geom.aux_color}" fill="none" '
                   f'stroke-width="{sw:.4f}">')
        for p0, p1 in geom.aux_segments:
            a, b = p0 * sc, p1 * sc
            out.append(f'  <line x1="{a[0]:.4f}" y1="{a[1]:.4f}" '
                       f'x2="{b[0]:.4f}" y2="{b[1]:.4f}"/>')
        out.append('</g>')

    # labels (engrave) — own layer; geom.labels is already gated per option
    # (panel tags by add_labels, settings line by add_settings_label)
    if geom.labels:
        out.append(f'<g id="labels" fill="{params.label_color}" '
                   f'font-size="{params.label_font_mm*sc:.3f}" '
                   f'font-family="sans-serif">')
        for text, pos in geom.labels:
            x, y = pos * sc
            out.append(f'  <text x="{x:.3f}" y="{y:.3f}" '
                       f'text-anchor="middle">{text}</text>')
        out.append('</g>')

    out.append('</svg>')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# DXF (minimal R12 ASCII — universally importable, incl. LightBurn)            #
# --------------------------------------------------------------------------- #

def _dxf_polyline(loop, layer):
    s = ["0", "POLYLINE", "8", layer, "66", "1", "70", "1"]  # 70=1 closed
    for x, y in loop:
        s += ["0", "VERTEX", "8", layer, "10", f"{x:.5f}", "20", f"{y:.5f}", "30", "0.0"]
    s += ["0", "SEQEND"]
    return s


def _dxf_line(a, b, layer):
    return ["0", "LINE", "8", layer,
            "10", f"{a[0]:.5f}", "20", f"{a[1]:.5f}", "30", "0.0",
            "11", f"{b[0]:.5f}", "21", f"{b[1]:.5f}", "31", "0.0"]


def to_dxf(geom: LaserGeometry, params: FlattenParams) -> str:
    _, sc = _unit_scale(params)
    codes = []
    codes += ["0", "SECTION", "2", "ENTITIES"]
    for loops in geom.cut_loops:
        for loop in loops:
            codes += _dxf_polyline(loop * sc, "CUT")
    for p0, p1 in geom.score_segments:
        if params.fold_mode == "perf":
            for a, b in _perf_dashes(p0, p1, params.perf_dash_mm, params.perf_gap_mm):
                codes += _dxf_line(a * sc, b * sc, "SCORE")
        else:
            codes += _dxf_line(p0 * sc, p1 * sc, "SCORE")
    for p0, p1 in (geom.aux_segments or []):
        codes += _dxf_line(p0 * sc, p1 * sc, "SCORE2")
    codes += ["0", "ENDSEC", "0", "EOF"]
    # DXF is code/value pairs, one per line
    return "\n".join(codes) + "\n"


# --------------------------------------------------------------------------- #
# Preview SVG (for the web UI) — like to_svg but with faint panel fills and a   #
# light background so the operator can read the net at a glance.                #
# --------------------------------------------------------------------------- #

def to_preview_svg(fp: FlatPattern, geom: LaserGeometry, params: FlattenParams) -> str:
    W, H = geom.width, geom.height
    pad = 0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad} {-pad} '
           f'{W+2*pad} {H+2*pad}" width="100%" preserveAspectRatio="xMidYMid meet">']
    out.append(f'<rect x="{-pad}" y="{-pad}" width="{W+2*pad}" height="{H+2*pad}" '
               f'fill="#0d1117"/>')
    # panel fills
    fills = {"floor": "#1f3a5f", "wall": "#3a2f1f"}
    for pan in fp.panels:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in pan.poly)
        out.append(f'<polygon points="{pts}" fill="{fills.get(pan.role,"#333")}" '
                   f'fill-opacity="0.55" stroke="none"/>')
    # cut contour
    for loops in geom.cut_loops:
        for loop in loops:
            d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in loop) + " Z"
            out.append(f'<path d="{d}" fill="none" stroke="{params.cut_color}" '
                       f'stroke-width="0.6"/>')
    # continuous overlay (under the fold lines so dashes stay readable)
    for p0, p1 in (geom.aux_segments or []):
        out.append(f'<line x1="{p0[0]:.2f}" y1="{p0[1]:.2f}" x2="{p1[0]:.2f}" '
                   f'y2="{p1[1]:.2f}" stroke="{geom.aux_color}" '
                   f'stroke-width="1.6" stroke-opacity="0.8"/>')
    # score / fold lines
    for p0, p1 in geom.score_segments:
        dash = ' stroke-dasharray="2 1.5"' if params.fold_mode == "perf" else ""
        out.append(f'<line x1="{p0[0]:.2f}" y1="{p0[1]:.2f}" x2="{p1[0]:.2f}" '
                   f'y2="{p1[1]:.2f}" stroke="{params.score_color}" '
                   f'stroke-width="0.8"{dash}/>')
    # fold angle labels
    for f in fp.folds:
        mid = (np.asarray(f.p0) + np.asarray(f.p1)) / 2
        out.append(f'<text x="{mid[0]:.1f}" y="{mid[1]:.1f}" fill="#9ad" '
                   f'font-size="5" text-anchor="middle">{f.angle_deg:.0f}°</text>')
    if params.add_labels:
        for pan in fp.panels:
            out.append(f'<text x="{pan.centroid[0]:.1f}" y="{pan.centroid[1]:.1f}" '
                       f'fill="#cdd" font-size="6" text-anchor="middle">'
                       f'{pan.role} #{pan.fid}</text>')
    if params.add_settings_label:
        for txt, pos in _settings_entries(fp, params):
            out.append(f'<text x="{pos[0]:.1f}" y="{pos[1]:.1f}" '
                       f'fill="{params.label_color}" '
                       f'font-size="{params.label_font_mm:g}" '
                       f'text-anchor="middle">{txt}</text>')
    out.append('</svg>')
    return "\n".join(out)
