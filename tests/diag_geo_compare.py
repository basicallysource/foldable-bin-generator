"""Diagnostic: is a 'failing' deployed SVG geometrically identical to golden?

Parses both SVGs' cut paths into shapely polygons and score lines into
segment sets, then compares as GEOMETRY (invariant to ring start point, ring
order, orientation): symmetric-difference area and Hausdorff distance.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import GOLDEN, read_case
from check_deployed import run_case

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union


def svg_loops(svg: str):
    loops = []
    for d in re.findall(r'<path d="([^"]+)"', svg):
        pts = [tuple(map(float, p.split(","))) for p in
               re.findall(r"(-?[\d.]+,-?[\d.]+)", d)]
        loops.append(pts)
    return loops


def svg_lines(svg: str):
    segs = set()
    for m in re.finditer(r'<line x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"', svg):
        a = (float(m.group(1)), float(m.group(2)))
        b = (float(m.group(3)), float(m.group(4)))
        segs.add(tuple(sorted((a, b))))
    return segs


def to_shape(loops):
    # build polygons treating loops as evenodd rings
    polys = [Polygon(l) for l in loops if len(l) >= 3]
    out = None
    for p in sorted(polys, key=lambda q: -abs(q.area)):
        p = p.buffer(0)
        out = p if out is None else out.symmetric_difference(p)
    return out or Polygon()


def main():
    name, base = sys.argv[1], sys.argv[2]
    case = read_case(os.path.join(GOLDEN, name))
    got = run_case(base, case)

    A, B = case["svg"], got["svg"]
    la, lb = svg_loops(A), svg_loops(B)
    print(f"cut loops: golden {len(la)} (pts {[len(x) for x in la]}), "
          f"deployed {len(lb)} (pts {[len(x) for x in lb]})")
    sa, sb = to_shape(la), to_shape(lb)
    sym = sa.symmetric_difference(sb).area
    print(f"cut area: golden {sa.area:.6f}  deployed {sb.area:.6f}  "
          f"symmetric difference {sym:.9f} mm^2")
    print(f"hausdorff: {sa.hausdorff_distance(sb):.9f} mm")

    ga, gb = svg_lines(A), svg_lines(B)
    only_a, only_b = ga - gb, gb - ga
    worst = 0.0
    if only_a and len(only_a) == len(only_b):
        for s in only_a:
            worst = max(worst, min(
                max(abs(s[i][j] - t[i][j]) for i in (0, 1) for j in (0, 1))
                for t in only_b))
    print(f"score segs: golden {len(ga)} deployed {len(gb)}; "
          f"unmatched {len(only_a)}/{len(only_b)}; worst nearest-coord diff {worst:g}")


if __name__ == "__main__":
    main()
