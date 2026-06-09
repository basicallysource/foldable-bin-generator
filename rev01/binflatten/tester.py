"""
Fold/score test card generator — a laser-material-test analog for fold lines.

Produces a grid of small foldable coupons. Each coupon carries one fold line
rendered with a specific perforation pattern (rows = dash length, columns = gap
length), plus an optional continuous-score reference column. Cut the card, fold
each chip, and keep whichever pattern creases cleanly in your stock.

We can't set laser power/speed from a vector file (that's a LightBurn layer
setting), so the sweep is over the *geometry* we do control: dash and gap. Set
the cut layer (red) and score layer (blue) power/speed in LightBurn as usual.

Output reuses the same LaserGeometry + SVG/DXF writers as the flattener, so the
red/blue layer convention is identical.
"""

from __future__ import annotations

import numpy as np

from .params import TesterParams, FlattenParams
from .export import LaserGeometry, _perf_dashes, to_svg, to_dxf


def _rect(x, y, w, h):
    return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])


def build_tester(tp: TesterParams) -> LaserGeometry:
    cut_loops = []        # each coupon is its own cut rectangle
    score_segments = []   # pre-expanded dash segments (drawn literally)
    labels = []           # (text, pos)

    pad = 5.0             # inset of the fold line from coupon side edges
    left_label_w = 16.0
    top_label_h = 9.0
    title_h = 11.0

    columns = list(tp.gap_values_mm)
    if tp.include_continuous:
        columns = columns + [None]            # None = continuous score line
    rows = list(tp.dash_values_mm)

    x0 = tp.margin_mm + left_label_w
    y0 = tp.margin_mm + title_h + top_label_h
    cw, ch, g = tp.coupon_w_mm, tp.coupon_h_mm, tp.gutter_mm

    # title
    labels.append((
        f"FOLD / SCORE TEST  -  {tp.material_thickness_mm:.2f} mm stock  -  "
        f"rows=dash(mm)  cols=gap(mm)",
        np.array([x0, tp.margin_mm + title_h * 0.5])))

    # column headers
    for c, col in enumerate(columns):
        cx = x0 + c * (cw + g) + cw / 2
        txt = "score" if col is None else f"gap {col:g}"
        labels.append((txt, np.array([cx, y0 - 2.5])))

    for r, dash in enumerate(rows):
        ry = y0 + r * (ch + g)
        # row header
        labels.append((f"dash {dash:g}",
                       np.array([tp.margin_mm + left_label_w / 2, ry + ch / 2])))
        for c, col in enumerate(columns):
            cx = x0 + c * (cw + g)
            cut_loops.append([_rect(cx, ry, cw, ch)])
            ymid = ry + ch / 2
            p0 = np.array([cx + pad, ymid])
            p1 = np.array([cx + cw - pad, ymid])
            if col is None:
                score_segments.append((p0, p1))          # continuous score
                tag = "score"
            else:
                for a, b in _perf_dashes(p0, p1, dash, col):
                    score_segments.append((a, b))
                tag = f"{dash:g}/{col:g}"
            labels.append((tag, np.array([cx + cw / 2, ymid + ch * 0.30])))

    width = x0 + len(columns) * cw + (len(columns) - 1) * g + tp.margin_mm
    height = y0 + len(rows) * ch + (len(rows) - 1) * g + tp.margin_mm
    return LaserGeometry(cut_loops, score_segments, labels, width, height, [])


def _flatten_params_for(tp: TesterParams) -> FlattenParams:
    """A FlattenParams shim so we can reuse to_svg/to_dxf. fold_mode='score' so
    the already-expanded dash segments are drawn literally (not re-dashed)."""
    return FlattenParams(
        output_units=tp.output_units, cut_color=tp.cut_color,
        score_color=tp.score_color, fold_mode="score",
        add_labels=True, margin_mm=tp.margin_mm)


def tester_svg(tp: TesterParams) -> str:
    return to_svg(build_tester(tp), _flatten_params_for(tp), title="fold test card")


def tester_dxf(tp: TesterParams) -> str:
    return to_dxf(build_tester(tp), _flatten_params_for(tp))


def tester_preview_svg(tp: TesterParams) -> str:
    """Dark-themed inline preview for the web UI."""
    geom = build_tester(tp)
    W, H = geom.width, geom.height
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="100%" preserveAspectRatio="xMidYMid meet">']
    out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#0d1117"/>')
    for loops in geom.cut_loops:
        for loop in loops:
            d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in loop) + " Z"
            out.append(f'<path d="{d}" fill="#161b22" stroke="{tp.cut_color}" '
                       f'stroke-width="0.5"/>')
    for p0, p1 in geom.score_segments:
        out.append(f'<line x1="{p0[0]:.2f}" y1="{p0[1]:.2f}" x2="{p1[0]:.2f}" '
                   f'y2="{p1[1]:.2f}" stroke="{tp.score_color}" stroke-width="0.7"/>')
    for text, pos in geom.labels:
        out.append(f'<text x="{pos[0]:.1f}" y="{pos[1]:.1f}" fill="#9ad" '
                   f'font-size="3.6" text-anchor="middle" '
                   f'font-family="monospace">{text}</text>')
    out.append('</svg>')
    return "\n".join(out)
