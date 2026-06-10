"""
Re-fold the flat pattern back into 3D and verify it against the CAD solid.

The simulator applies the SAME crease model the thickness compensation
assumes: each fold pivots about an axis `fold_comp_factor * thickness` above
the sheet's plotted (exterior) face — i.e. on the inside of the bend — and
every panel is a slab extruded one stock thickness inward from the plotted
face. Panels are folded to the true CAD dihedral of their shared edge.

Verification compares, in CAD coordinates:
  * silhouettes ("shadows") along three bin-natural axes (floor normal +
    the two principal in-plane directions) — IoU and per-view bounding boxes
  * the overall outermost 3D extents along those axes

If compensation and crease model agree, the refolded exterior reproduces the
CAD exterior and all bbox deltas are ~0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union

from .params import FlattenParams
from .unfold import FlatPattern, _rot_matrix, _unit, _plane_basis


# --------------------------------------------------------------------------- #
# refold                                                                        #
# --------------------------------------------------------------------------- #

@dataclass
class RefoldPanel:
    fid: int
    role: str
    poly2d: np.ndarray      # final pattern coords, exterior ring (Nx2)
    holes2d: list           # list[Nx2]
    matrix: np.ndarray      # 4x4 affine: (x, y, z_local) pattern -> CAD mm
    thickness: float        # slab thickness (extruded along local +z)

    def ring3d(self, ring=None, z=0.0):
        r = self.poly2d if ring is None else ring
        P = np.column_stack([r, np.full(len(r), z), np.ones(len(r))])
        return (self.matrix @ P.T).T[:, :3]


@dataclass
class RefoldResult:
    panels: list = field(default_factory=list)   # list[RefoldPanel]
    creases: list = field(default_factory=list)  # list[list[Nx3]] hull rings per fold
    warnings: list = field(default_factory=list)


def refold(fp: FlatPattern, params: FlattenParams) -> RefoldResult:
    """Fold the final 2D pattern back up in CAD coordinates."""
    fr = fp.frame
    origin = np.asarray(fr["origin"], float)
    u, v, n = (np.asarray(fr[k], float) for k in ("u", "v", "n"))
    sc = float(fr["scale"]) or 1.0
    shift = np.asarray(fr["shift"], float)
    t = params.material_thickness_mm
    pivot = params.fold_comp_factor * t      # crease pivot depth into the sheet

    def P3(x2):
        x = (np.atleast_2d(x2) - shift) / sc
        return origin + x[:, :1] * u + x[:, 1:2] * v

    res = RefoldResult()
    panels = {p.fid: p for p in fp.panels}
    T = {fp.root_fid: (np.eye(3), np.zeros(3))}
    for f in fp.folds:
        Rp, tp = T[f.panel_a]
        H = P3(np.vstack([f.p0, f.p1])) @ Rp.T + tp
        axis = _unit(H[1] - H[0])
        A = H[0] + pivot * (Rp @ n)          # pivot on the inside of the bend
        rot = np.radians(max(0.0, 180.0 - f.dihedral_deg))
        c_flat = (P3(panels[f.panel_b].centroid.reshape(1, 2)) @ Rp.T + tp)[0]
        cad_c = fp.frame["cad_centroid"].get(f.panel_b)
        best = None
        for sgn in (1.0, -1.0):              # fold direction: toward CAD pose
            R = _rot_matrix(axis, sgn * rot)
            d = np.linalg.norm(R @ (c_flat - A) + A - cad_c)
            if best is None or d < best[0]:
                best = (d, R)
        R = best[1]
        T[f.panel_b] = (R @ Rp, R @ (tp - A) + A)

        # crease wedge: the material that wraps around the pivot between the
        # parent's edge and the child's edge — it fills the outer corner, so
        # the silhouette / extents see what the real (crushed) fold provides.
        # Clip to where the parent actually has material, so the wedge does
        # not overhang past panel corners.
        seg = LineString([f.p0, f.p1]).intersection(
            Polygon(panels[f.panel_a].poly).buffer(0.02).union(
                Polygon(panels[f.panel_b].poly).buffer(0.02)))
        if seg.geom_type == "MultiLineString" and len(seg.geoms):
            seg = max(seg.geoms, key=lambda g: g.length)
        ends = (np.asarray(seg.coords)[[0, -1]]
                if seg.geom_type == "LineString" and seg.length > 1e-6
                else np.vstack([f.p0, f.p1]))
        Rc, tc = T[f.panel_b]
        Hp = P3(ends) @ Rp.T + tp
        Hc = P3(ends) @ Rc.T + tc
        Np_t, Nc_t = (Rp @ n) * t, (Rc @ n) * t
        quad_p = np.array([Hp[0], Hp[1], Hp[1] + Np_t, Hp[0] + Np_t])
        quad_c = np.array([Hc[0], Hc[1], Hc[1] + Nc_t, Hc[0] + Nc_t])
        rings = [quad_p, quad_c]
        for i, j in ((0, 1), (1, 2), (2, 3), (3, 0)):
            rings.append(np.array([quad_p[i], quad_p[j], quad_c[j], quad_c[i]]))
        res.creases.append(rings)

    for p in fp.panels:
        if p.fid not in T:
            res.warnings.append(
                f"panel #{p.fid} is a detached island — left flat, excluded "
                f"from comparison")
            continue
        R, tv = T[p.fid]
        M = np.eye(4)
        M[:3, :3] = np.column_stack([R @ (u / sc), R @ (v / sc), R @ n])
        M[:3, 3] = R @ (origin - (shift[0] * u + shift[1] * v) / sc) + tv
        res.panels.append(RefoldPanel(fid=p.fid, role=p.role, poly2d=p.poly,
                                      holes2d=p.holes, matrix=M, thickness=t))
    return res


# --------------------------------------------------------------------------- #
# comparison vs the CAD solid                                                   #
# --------------------------------------------------------------------------- #

def _cad_rings(model):
    s = model.unit_scale_to_mm
    return [f.outer * s for f in model.faces
            if f.is_planar and f.outer is not None and len(f.outer) >= 3]


def _bin_axes(model, fp):
    """(e1, e2, n): two principal in-plane directions + inward floor normal."""
    n = _unit(np.asarray(fp.frame["n"], float))
    pts = np.vstack(_cad_rings(model))
    X = pts - np.outer(pts @ n, n)
    X = X - X.mean(0)
    _, vecs = np.linalg.eigh(X.T @ X)
    e1 = _unit(vecs[:, -1] - n * (vecs[:, -1] @ n))
    e2 = _unit(np.cross(n, e1))
    return e1, e2, n


def _silhouette(rings, a, b):
    polys = []
    for r in rings:
        q = np.column_stack([r @ a, r @ b])
        if len(q) < 3:
            continue
        pg = Polygon(q).buffer(0)
        if not pg.is_empty:
            polys.append(pg)
    sil = unary_union(polys)
    # close hairline gaps between adjacent faces/slabs
    return sil.buffer(0.05, join_style=2).buffer(-0.05, join_style=2)


def _panel_rings(rp: RefoldPanel):
    """3D rings whose projections cover the slab's silhouette."""
    bot = rp.ring3d(z=0.0)
    top = rp.ring3d(z=rp.thickness)
    rings = [bot, top]
    m = len(bot)
    for i in range(m):
        j = (i + 1) % m
        rings.append(np.array([bot[i], bot[j], top[j], top[i]]))
    return rings


def _svg_path(geom):
    if geom.is_empty:
        return ""
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    d = []
    for pg in polys:
        for ring in [pg.exterior, *pg.interiors]:
            pts = np.asarray(ring.coords)
            d.append("M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z")
    return " ".join(d)


def _overlay_svg(sil_cad, sil_ref, title, iou):
    both = unary_union([sil_cad, sil_ref])
    minx, miny, maxx, maxy = both.bounds
    pad = 8
    w, h = maxx - minx + 2 * pad, maxy - miny + 2 * pad
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx-pad:.1f} '
        f'{miny-pad:.1f} {w:.1f} {h:.1f}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet">'
        f'<rect x="{minx-pad:.1f}" y="{miny-pad:.1f}" width="{w:.1f}" '
        f'height="{h:.1f}" fill="#0d1117"/>'
        # mirror y so the view reads right-side up
        f'<g transform="translate(0 {miny+maxy:.2f}) scale(1 -1)">'
        f'<path d="{_svg_path(sil_cad)}" fill="#26a641" fill-opacity="0.45" '
        f'fill-rule="evenodd" stroke="#26a641" stroke-width="0.4"/>'
        f'<path d="{_svg_path(sil_ref)}" fill="#f85149" fill-opacity="0.45" '
        f'fill-rule="evenodd" stroke="#f85149" stroke-width="0.4"/>'
        f'</g>'
        f'<text x="{minx-pad+3:.1f}" y="{miny-pad+9:.1f}" fill="#ccc" '
        f'font-size="7" font-family="sans-serif">{title} — green=CAD '
        f'red=refold, IoU {iou:.3f}</text>'
        f'</svg>')


def verify_refold(model, fp: FlatPattern, params: FlattenParams) -> dict:
    """Refold + compare. Returns dict with per-view metrics, overlay SVGs,
    overall extents and the three.js scene payload."""
    rf = refold(fp, params)
    e1, e2, n = _bin_axes(model, fp)
    cad_rings = _cad_rings(model)
    ref_rings = [r for rp in rf.panels for r in _panel_rings(rp)]
    ref_rings += [r for rings in rf.creases for r in rings]

    views = []
    for name, a, b in (("plan (along floor normal)", e1, e2),
                       ("elevation A (along long axis)", e2, n),
                       ("elevation B (along short axis)", e1, n)):
        sc_ = _silhouette(cad_rings, a, b)
        sr_ = _silhouette(ref_rings, a, b)
        inter = sc_.intersection(sr_).area
        union = sc_.union(sr_).area
        iou = inter / union if union > 0 else 0.0
        cb, rb = sc_.bounds, sr_.bounds
        views.append(dict(
            name=name,
            iou=round(iou, 4),
            cad_size=[round(cb[2] - cb[0], 2), round(cb[3] - cb[1], 2)],
            refold_size=[round(rb[2] - rb[0], 2), round(rb[3] - rb[1], 2)],
            size_diff=[round((rb[2] - rb[0]) - (cb[2] - cb[0]), 2),
                       round((rb[3] - rb[1]) - (cb[3] - cb[1]), 2)],
            svg=_overlay_svg(sc_, sr_, name, iou)))

    cad_pts = np.vstack(cad_rings)
    ref_pts = np.vstack(ref_rings) if ref_rings else cad_pts
    extents = {}
    for label, ax in (("width(e1)", e1), ("depth(e2)", e2), ("height(n)", n)):
        c = cad_pts @ ax
        r = ref_pts @ ax
        extents[label] = dict(cad=round(float(c.max() - c.min()), 2),
                              refold=round(float(r.max() - r.min()), 2),
                              diff=round(float((r.max() - r.min()) -
                                               (c.max() - c.min())), 2))

    return dict(views=views, extents=extents, warnings=rf.warnings,
                scene=threejs_scene(model, rf))


# --------------------------------------------------------------------------- #
# three.js scene payload (web viewer)                                           #
# --------------------------------------------------------------------------- #

def threejs_scene(model, rf: RefoldResult) -> dict:
    """Panels as 2D shapes + 4x4 matrices (column-major) for ExtrudeGeometry,
    plus the CAD faces as reference shapes."""
    s = model.unit_scale_to_mm
    panels = [dict(fid=p.fid, role=p.role,
                   poly=np.round(p.poly2d, 3).tolist(),
                   holes=[np.round(h, 3).tolist() for h in p.holes2d],
                   matrix=p.matrix.flatten(order="F").tolist(),
                   thickness=p.thickness)
              for p in rf.panels]
    cad = []
    for f in model.faces:
        if not (f.is_planar and f.outer is not None and len(f.outer) >= 3):
            continue
        ring = f.outer * s
        u, v = _plane_basis(f.normal, ring[0], ring[1])
        nrm = np.cross(u, v)
        d = ring - ring[0]
        poly = np.column_stack([d @ u, d @ v])
        M = np.eye(4)
        M[:3, :3] = np.column_stack([u, v, nrm])
        M[:3, 3] = ring[0]
        cad.append(dict(poly=np.round(poly, 3).tolist(),
                        matrix=M.flatten(order="F").tolist()))
    allp = np.vstack([f.outer * s for f in model.faces
                      if f.outer is not None and len(f.outer)])
    center = allp.mean(0)
    size = float(np.linalg.norm(allp.max(0) - allp.min(0)))
    return dict(panels=panels, cad=cad,
                center=np.round(center, 2).tolist(), size=round(size, 1))
