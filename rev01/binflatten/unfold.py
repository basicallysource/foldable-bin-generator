"""
Unfold a bin's B-rep into a flat sheet pattern.

Pipeline
--------
1. From the parsed STEP model, select the structural shell (inner cavity faces
   or outer faces). Each wall of the bin is a thin slab = a pair of parallel
   faces; we keep one face per slab (the chosen shell side).
2. The kept faces form an open box: one floor panel edge-adjacent to several
   wall panels. Shared edges between panels are the fold lines.
3. Build a spanning tree rooted at the floor and rotate every wall panel about
   its fold edge until it is coplanar with the floor (flattened outward).
4. Project everything to 2D in the floor's plane. The result is a set of 2D
   panels plus the fold segments (score lines).

The cut contour / kerf offset / file writing are handled in export.py. This
module is pure geometry and returns a `FlatPattern`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon

from .step_io import Model, Face, _polygon_area_3d
from .params import FlattenParams


# --------------------------------------------------------------------------- #
# small vector helpers                                                          #
# --------------------------------------------------------------------------- #

def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n else v


def _rot_matrix(axis_dir, angle):
    """3x3 rotation matrix about a unit axis by angle (Rodrigues)."""
    k = _unit(axis_dir)
    kx, ky, kz = k
    K = np.array([[0, -kz, ky], [kz, 0, -kx], [-ky, kx, 0]])
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)


def _apply(T, X):
    """Apply rigid transform T=(R,t) to Nx3 row-vectors X: X @ R.T + t."""
    R, t = T
    return X @ R.T + t


def _rot_about_axis(points, axis_pt, axis_dir, angle):
    """Rotate Nx3 points about a line (axis_pt, axis_dir) by angle (radians)."""
    R = _rot_matrix(axis_dir, angle)
    return (points - axis_pt) @ R.T + axis_pt


def _shared_edge_points(poly_a, poly_b, tol=1e-6):
    """Vertices common to both polygons (the fold edge endpoints)."""
    common = []
    for pa in poly_a:
        for pb in poly_b:
            if np.linalg.norm(pa - pb) < tol:
                common.append(pa)
                break
    # de-duplicate
    uniq = []
    for c in common:
        if not any(np.linalg.norm(c - u) < tol for u in uniq):
            uniq.append(c)
    return uniq


# --------------------------------------------------------------------------- #
# 2D polygon clipping helpers (shapely-backed)                                  #
# --------------------------------------------------------------------------- #

def _largest_piece(geom):
    """Largest Polygon in a shapely geometry, or None."""
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    pieces = [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    return max(pieces, key=lambda g: g.area) if pieces else None


def _clip_halfplane(pts, nrm, dmax):
    """Keep the part of polygon `pts` with nrm·x <= dmax. Returns Nx2 or None."""
    nrm = np.asarray(nrm, float)
    L = np.linalg.norm(nrm)
    nrm, dmax = nrm / L, dmax / L
    axis = np.array([-nrm[1], nrm[0]])
    big = 1e6
    p = nrm * dmax
    quad = np.array([p + axis * big, p - axis * big,
                     p - axis * big - nrm * big, p + axis * big - nrm * big])
    res = _largest_piece(Polygon(pts).buffer(0).intersection(Polygon(quad)))
    return np.array(res.exterior.coords[:-1]) if res is not None else None


def _subtract_strip(pts, p0, p1, nrm, c, ext):
    """Remove from polygon `pts` the strip of width c on the -nrm side of the
    segment p0-p1 (extended by `ext` along the segment). Returns Nx2 or None."""
    axis = _unit(np.asarray(p1, float) - np.asarray(p0, float))
    a0, a1 = sorted([float(axis @ p0), float(axis @ p1)])
    a0, a1 = a0 - ext, a1 + ext
    d = float(nrm @ p0)
    corners = np.array([axis * a0 + nrm * (d - c), axis * a1 + nrm * (d - c),
                        axis * a1 + nrm * (d + 1e-3), axis * a0 + nrm * (d + 1e-3)])
    res = _largest_piece(Polygon(pts).buffer(0).difference(Polygon(corners)))
    return np.array(res.exterior.coords[:-1]) if res is not None else None


def _thickness_compensate(fp, byid, s, params):
    """Account for real stock thickness at the folds.

    The pattern develops one shell of a CAD whose walls (~1.8 mm) are thinner
    than the stock, and a perforated fold pivots about the intact skin on the
    inside of the bend — so each folded panel lands one stock thickness
    outside its fold line. Measured on the outer shell: the bin comes out two
    thicknesses too wide and one too tall.

    Fix (a): at every fold remove a strip of width
        c = fold_comp_factor * thickness [* tan(fold_angle/2)]
    from EACH side of the fold line; the child panel and everything hinged on
    it slide 2c toward the parent so the panels stay edge-to-edge, and the
    fold line moves to the new shared edge.

    Fix (b): panels not hinged on the root (the front wall) get everything
    below `floor_clearance_factor * thickness` above the root plane cut off,
    so their bottom edge clears the real-thickness floor and its bracket toes
    (instead of carrying CAD-thickness tabs that land on the toes).
    """
    t = params.material_thickness_mm
    panels = {p.fid: p for p in fp.panels}
    orig = {p.fid: p.poly.copy() for p in fp.panels}
    trans = {fid: np.zeros(2) for fid in panels}
    parent_of = {f.panel_b: f.panel_a for f in fp.folds}

    def _update(panel, pts, what):
        if pts is None or len(pts) < 3:
            fp.warnings.append(
                f"thickness compensation removed panel #{panel.fid} entirely "
                f"({what}) — left it unmodified")
            return
        panel.poly = pts
        panel.centroid = pts.mean(0)

    if params.fold_comp_factor > 0 and t > 0:
        # fp.folds is in placement order, so a panel's hinge fold is processed
        # before any fold where it is the parent and `trans` is ready.
        for f in fp.folds:
            rot = np.radians(max(0.0, 180.0 - f.dihedral_deg))
            c = params.fold_comp_factor * t
            if params.fold_comp_angle_scaled:
                c *= np.tan(rot / 2.0)
            if c <= 0:
                continue
            par, ch = f.panel_a, f.panel_b
            p0, p1 = np.asarray(f.p0, float), np.asarray(f.p1, float)
            axis = _unit(p1 - p0)
            nrm = np.array([-axis[1], axis[0]])
            if np.dot(panels[ch].centroid - (p0 + p1) / 2, nrm) < 0:
                nrm = -nrm                      # nrm points toward the child
            p0, p1 = p0 + trans[par], p1 + trans[par]
            d = float(nrm @ p0)
            # parent: a finite strip, not a half-plane — the infinite line
            # would also shave features far from the hinge (the floor's toes),
            # and extending past the fold ends bites adjacent corners.
            _update(panels[par],
                    _subtract_strip(panels[par].poly, p0, p1, nrm, c, ext=0.05),
                    f"fold strip toward #{ch}")
            # child subtree slides 2c toward the parent; child loses its strip
            trans[ch] = trans[par] - 2.0 * c * nrm
            _update(panels[ch],
                    _clip_halfplane(orig[ch] + trans[ch], -nrm, -(d - c)),
                    f"hinge strip on #{par}")
            f.p0, f.p1 = p0 - c * nrm, p1 - c * nrm

    if params.floor_clearance_factor > 0 and t > 0:
        clearance = params.floor_clearance_factor * t
        root = fp.root_fid
        rf = byid[root]
        n_r = _unit(rf.normal)
        r0 = rf.outer[0] * s
        heights = [((byid[p.fid].outer * s) - r0) @ n_r
                   for p in fp.panels if p.fid != root]
        if heights and np.concatenate(heights).mean() < 0:
            n_r = -n_r                          # orient toward the bin interior
        for p in fp.panels:
            if p.fid == root or parent_of.get(p.fid) == root:
                continue   # floor-hinged walls: the fold strip IS their trim
            h = ((byid[p.fid].outer * s) - r0) @ n_r
            if h.min() > clearance - 1e-6:
                continue
            # height above the floor plane is affine in the flat coordinates
            # (the panel is rigid), so the cut is a straight 2D line.
            A = np.column_stack([orig[p.fid], np.ones(len(h))])
            (a, b, c0), *_ = np.linalg.lstsq(A, h, rcond=None)
            tx, ty = trans[p.fid]
            dmax = -(clearance - c0 + a * tx + b * ty)
            _update(p, _clip_halfplane(p.poly, np.array([-a, -b]), dmax),
                    "floor clearance")

    return trans


def _face_wedge_deg(fa, fb, e0, e1):
    """True dihedral between faces fa/fb at their shared edge e0-e1: the angle
    between the two surface wings measured at the edge (180 = coplanar/flat).
    Unlike normal-based angles this is unambiguous — STEP normals here are not
    consistently oriented."""
    axis = _unit(np.asarray(e1, float) - np.asarray(e0, float))

    def wing(f):
        w = f.outer.mean(0) - e0
        return _unit(w - np.dot(w, axis) * axis)

    return float(np.degrees(np.arccos(np.clip(np.dot(wing(fa), wing(fb)), -1, 1))))


def _merged_shared_edge(model, fa, fb):
    """Full extent of all edges shared by faces fa/fb (model units), or None."""
    pts = []
    for ei in fa.edge_ids & fb.edge_ids:
        e = model.edges.get(ei)
        if e is not None:
            pts.extend([e.v0, e.v1])
    if len(pts) < 2:
        pts = _shared_edge_points(fa.outer, fb.outer)
    if len(pts) < 2:
        return None
    pts = np.array(pts)
    axis = _unit(pts[np.argmax(((pts - pts[0]) ** 2).sum(1))] - pts[0])
    t = pts @ axis
    return pts[t.argmin()], pts[t.argmax()]


def _add_seam_tabs(fp, model, byid, s, params, transforms, to2d, off2d):
    """Lock the open corner seam with tabs + slots (experimental).

    The net leaves the seam between the front wall's free edge and the
    opposite side wall open. For every pair of wall panels that share a 3D
    edge but are not joined by a fold, the panel placed deeper in the fold
    tree (the front wall — it swings into place last) gets tabs sticking out
    of that edge, and the other panel gets matching through-slots. Tabs are
    one stock thickness deep so they end flush with the slotted wall's
    outside face; slots sit inset behind the corner line, which pulls the
    front wall slightly inside its CAD plane as it engages.
    """
    n = int(params.seam_tab_count)
    t = params.material_thickness_mm
    if t <= 0 or (n <= 0 and params.seam_edge_trim_factor <= 0):
        return
    panels = {p.fid: p for p in fp.panels}
    joined = {(f.panel_a, f.panel_b) for f in fp.folds}
    joined |= {(b, a) for (a, b) in joined}
    depth = {fp.root_fid: 0}
    for f in fp.folds:                      # placement order: parent first
        depth[f.panel_b] = depth.get(f.panel_a, 0) + 1

    def map2(fid, P3):
        X = _apply(transforms[fid], np.atleast_2d(P3))
        return to2d(X)[0] + off2d[fid]

    fids = sorted(panels)
    for i, a in enumerate(fids):
        for b in fids[i + 1:]:
            if (a, b) in joined or fp.root_fid in (a, b):
                continue                    # folded pairs and the floor (toes)
            seam = _merged_shared_edge(model, byid[a], byid[b])
            if seam is None:
                continue
            E0, E1 = seam[0] * s, seam[1] * s
            wedge = _face_wedge_deg(byid[a], byid[b], seam[0], seam[1])
            tab_fid, slot_fid = ((a, b) if depth.get(a, 99) >= depth.get(b, 99)
                                 else (b, a))

            # --- tabs on the free wall ---------------------------------- #
            # The CAD's exterior face runs all the way to the exterior corner,
            # i.e. THROUGH the other wall's slab — trim the free edge back so
            # it stops at that wall's INNER face, then let the tabs bridge the
            # slab thickness and end flush with its outside face.
            pt = panels[tab_fid]
            A0, A1 = map2(tab_fid, E0), map2(tab_fid, E1)
            u = _unit(A1 - A0)
            nrm = np.array([-u[1], u[0]])
            if np.dot(pt.centroid - (A0 + A1) / 2, nrm) > 0:
                nrm = -nrm                  # outward, away from the panel
            trim = (params.seam_edge_trim_factor * t /
                    max(np.sin(np.radians(wedge)), 0.2))
            if trim > 0:
                trimmed = _subtract_strip(pt.poly, A0, A1, nrm, trim, ext=0.05)
                if trimmed is not None and len(trimmed) >= 3:
                    pt.poly = trimmed
                    pt.centroid = trimmed.mean(0)
            tab_d = params.seam_tab_depth_factor * t
            w = params.seam_tab_width_mm
            shape = Polygon(pt.poly, pt.holes).buffer(0)
            for k in range(n):
                c = A0 + (k + 1) / (n + 1) * (A1 - A0) - nrm * trim
                tab = Polygon([c - u * w / 2 - nrm * 0.2,
                               c + u * w / 2 - nrm * 0.2,
                               c + u * w / 2 + nrm * tab_d,
                               c - u * w / 2 + nrm * tab_d])
                shape = shape.union(tab)
            shape = _largest_piece(shape)
            pt.poly = np.array(shape.exterior.coords[:-1])
            pt.holes = [np.array(r.coords[:-1]) for r in shape.interiors]
            pt.centroid = pt.poly.mean(0)

            # --- slots in the wall it lands on --------------------------- #
            ps = panels[slot_fid]
            B0, B1 = map2(slot_fid, E0), map2(slot_fid, E1)
            u = _unit(B1 - B0)
            nrm = np.array([-u[1], u[0]])
            if np.dot(ps.centroid - (B0 + B1) / 2, nrm) < 0:
                nrm = -nrm                  # inward, into the panel
            sw = params.seam_tab_width_mm + params.seam_slot_clearance_mm
            sd = t + params.seam_slot_clearance_mm
            inset = params.seam_slot_inset_factor * t
            shape = Polygon(ps.poly, ps.holes).buffer(0)
            for k in range(n):
                c = B0 + (k + 1) / (n + 1) * (B1 - B0) + nrm * inset
                slot = Polygon([c - u * sw / 2 - nrm * sd / 2,
                                c + u * sw / 2 - nrm * sd / 2,
                                c + u * sw / 2 + nrm * sd / 2,
                                c - u * sw / 2 + nrm * sd / 2])
                shape = shape.difference(slot)
            shape = _largest_piece(shape)
            ps.poly = np.array(shape.exterior.coords[:-1])
            ps.holes = [np.array(r.coords[:-1]) for r in shape.interiors]
            ps.centroid = ps.poly.mean(0)


# --------------------------------------------------------------------------- #
# data structures                                                               #
# --------------------------------------------------------------------------- #

@dataclass
class Panel2D:
    fid: int
    role: str               # "floor" | "wall"
    poly: np.ndarray        # Nx2 flattened polygon (mm)
    centroid: np.ndarray    # 2D
    holes: list = field(default_factory=list)  # list[Nx2] interior cutouts (slots)


@dataclass
class FoldLine:
    p0: np.ndarray          # 2D
    p1: np.ndarray          # 2D
    angle_deg: float        # display angle (180 - normals angle, sign-ambiguous)
    panel_a: int
    panel_b: int
    dihedral_deg: float = 90.0  # true CAD wedge angle at the edge (180 = flat)


@dataclass
class FlatPattern:
    panels: list = field(default_factory=list)      # list[Panel2D]
    folds: list = field(default_factory=list)       # list[FoldLine]
    warnings: list = field(default_factory=list)
    # bookkeeping / diagnostics
    shell_face_ids: list = field(default_factory=list)
    root_fid: int | None = None
    # sheet frame: maps final 2D pattern coords back onto the root plane in
    # CAD space (origin/u/v/n in mm, n = inward normal; shift = final 2D
    # margin shift). Filled by unfold(); consumed by refold.py.
    frame: dict = field(default_factory=dict)

    def bounds(self):
        allp = np.vstack([p.poly for p in self.panels])
        return allp.min(0), allp.max(0)


# --------------------------------------------------------------------------- #
# shell selection                                                               #
# --------------------------------------------------------------------------- #

def _select_shell(model: Model, params: FlattenParams):
    """Return the list of structural Face objects for the chosen shell side.

    Strategy: take the large planar faces, pair them into slabs (parallel,
    plane separation ~ wall thickness), then keep one connected component of
    edge-adjacent faces. The inner shell and outer shell are the two
    components; we pick by which side the user asked for.
    """
    s = model.unit_scale_to_mm
    planar = [f for f in model.faces if f.is_planar and f.outer is not None]
    if not planar:
        raise ValueError("no planar faces found in model")

    areas = {f.fid: _polygon_area_3d(f.outer, f.normal) * s * s for f in planar}
    # keep faces that participate in slabs (have a parallel partner within
    # thickness) — these are the real wall/floor surfaces, not little rim faces.
    big = sorted(planar, key=lambda f: -areas[f.fid])
    # Heuristic: structural faces are the large ones. Use those whose area is at
    # least 5% of the largest face area.
    amax = areas[big[0].fid]
    struct = [f for f in big if areas[f.fid] >= 0.05 * amax]

    byid = {f.fid: f for f in struct}
    sids = set(byid)

    # connected components via shared edges (only among structural faces)
    seen = set()
    comps = []
    for f in struct:
        if f.fid in seen:
            continue
        stack = [f.fid]
        comp = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            for ei in byid[cur].edge_ids:
                for g in model.edge_faces.get(ei, []):
                    if g in sids and g not in seen:
                        stack.append(g)
        comps.append(comp)

    comps.sort(key=lambda c: -sum(areas[i] for i in c))
    if len(comps) < 2:
        # only one shell visible (e.g. open/zero-thickness model); use it
        chosen = comps[0]
    else:
        # The exterior shell wraps around the outside of the slabs, so every
        # exterior face is larger than its cavity-side partner: total area
        # tells the shells apart. (Face normals in these STEP exports are not
        # consistently oriented, so a normal-based test is unreliable.)
        chosen = comps[1] if params.shell == "inner" else comps[0]

    return [byid[i] for i in chosen]


# --------------------------------------------------------------------------- #
# unfolding                                                                     #
# --------------------------------------------------------------------------- #

def _plane_basis(normal, p0, p1):
    """Orthonormal in-plane basis (u, v) for a plane, u along p1-p0."""
    n = _unit(normal)
    u = _unit(p1 - p0)
    u = _unit(u - np.dot(u, n) * n)
    v = np.cross(n, u)
    return u, v


def unfold(model: Model, params: FlattenParams) -> FlatPattern:
    fp = FlatPattern()
    shell = _select_shell(model, params)
    byid = {f.fid: f for f in shell}
    sids = set(byid)
    fp.shell_face_ids = sorted(sids)
    s = model.unit_scale_to_mm

    areas = {f.fid: _polygon_area_3d(f.outer, f.normal) for f in shell}

    # adjacency among shell faces. The fold segment between two faces is the
    # FULL extent of all edges they share (the notched floor/front junction is
    # several collinear edge pieces, so we merge them by projecting their
    # endpoints onto the common direction and taking the extremes).
    adj = {fid: [] for fid in sids}
    for fid in sids:
        for g in sids:
            if g == fid:
                continue
            shared_ids = byid[fid].edge_ids & byid[g].edge_ids
            pts = []
            for ei in shared_ids:
                e = model.edges.get(ei)
                if e is not None:
                    pts.extend([e.v0, e.v1])
            if len(pts) < 2:
                pts = _shared_edge_points(byid[fid].outer, byid[g].outer)
            if len(pts) < 2:
                continue
            pts = np.array(pts)
            axis = _unit(pts[np.argmax(((pts - pts[0]) ** 2).sum(1))] - pts[0])
            t = pts @ axis
            seg0, seg1 = pts[t.argmin()], pts[t.argmax()]
            adj[fid].append((g, seg0, seg1))

    # root selection: the floor is the hub — adjacent to the most other panels.
    # Tie-break by area (the floor is usually the biggest hub).
    degree = {fid: len({g for (g, _, _) in adj[fid]}) for fid in sids}
    if params.root != "largest" and params.root.isdigit() and int(params.root) in sids:
        root = int(params.root)
    else:
        root = max(sids, key=lambda i: (degree[i], areas[i]))
    fp.root_fid = root
    root_face = byid[root]
    n_root = _unit(root_face.normal)

    # 2D projection basis from the (unmoved) root plane.
    root3d = root_face.outer.copy() * s
    origin = root3d[0]
    u, v = _plane_basis(root_face.normal, root3d[0], root3d[1])

    def to2d(P3):
        d = np.atleast_2d(P3) - origin
        return np.column_stack([d @ u, d @ v]) * params.scale

    # --- overlap-aware greedy placement -----------------------------------
    # Each face gets a rigid 3D transform T=(R,t) into the flattened frame and a
    # 2D polygon. A wall is placed by hinging on an already-placed neighbour; we
    # try EVERY placed neighbour and keep the hinge whose flap does not collide
    # with anything already on the sheet. This is what lets the front wall avoid
    # the floor's protruding toes by folding off a side wall instead.
    I = (np.eye(3), np.zeros(3))
    transforms = {root: I}
    flat3d = {root: root3d}
    placed_poly = {root: Polygon(to2d(root3d))}
    fp.panels.append(Panel2D(fid=root, role="floor", poly=to2d(root3d),
                             centroid=to2d(root3d).mean(0)))

    overlap_tol = 1.0  # mm^2 — ignore numerical edge touching

    def _flatten_on(parent, child_fid, e0, e1):
        """Flatten child_fid by hinging on `parent`; return (flat3d, R, E0, E1)."""
        Tpar = transforms[parent]
        child = byid[child_fid]
        child_moved = _apply(Tpar, child.outer * s)
        E0 = _apply(Tpar, (e0 * s).reshape(1, 3))[0]
        E1 = _apply(Tpar, (e1 * s).reshape(1, 3))[0]
        axis_dir = _unit(E1 - E0)
        par_cen = flat3d[parent].mean(0)
        nc = _unit(np.cross(child_moved[1] - child_moved[0],
                            child_moved[2] - child_moved[0]))

        def perp(x):
            return _unit(x - np.dot(x, axis_dir) * axis_dir)
        base = np.arccos(np.clip(np.dot(perp(nc), perp(n_root)), -1, 1))
        best = None
        for ang in (base, -base, np.pi - base, -(np.pi - base)):
            R = _rot_matrix(axis_dir, ang)
            test = (child_moved - E0) @ R.T + E0
            tn = _unit(np.cross(test[1] - test[0], test[2] - test[0]))
            if abs(abs(np.dot(tn, n_root)) - 1) > 1e-3:
                continue
            d = np.linalg.norm(test.mean(0) - par_cen)  # unfold = swing away
            if best is None or d > best[0]:
                best = (d, test, R, E0, E1)
        return best  # (d, flat3d, R, E0, E1) or None

    remaining = sids - {root}
    island_off = {}        # fid -> ad-hoc 2D shift applied to detached islands
    while remaining:
        # gather best placement for every remaining face with a placed neighbour
        options = []  # (overlap, fid, parent, flat3d, R, E0, E1)
        for fid in remaining:
            for (nb, e0, e1) in adj[fid]:
                if nb not in transforms:
                    continue
                res = _flatten_on(nb, fid, e0, e1)
                if res is None:
                    continue
                _, f3d, R, E0, E1 = res
                poly2 = Polygon(to2d(f3d)).buffer(0)
                # Overlap with everything already placed — INCLUDING the hinge
                # parent. A clean fold only shares the (zero-area) hinge line
                # with its parent; a real area overlap here is a collision (this
                # is exactly how the floor's protruding toes get caught).
                ov = sum(poly2.intersection(op).area for op in placed_poly.values())
                options.append((ov, fid, nb, f3d, R, E0, E1, poly2))
        if not options:
            fp.warnings.append(
                f"faces {sorted(remaining)} are not connected to the net")
            break
        options.sort(key=lambda o: o[0])
        ov, fid, parent, f3d, R, E0, E1, poly2 = options[0]

        Rp, tp = transforms[parent]
        transforms[fid] = (R @ Rp, R @ tp + E0 - R @ E0)
        flat3d[fid] = f3d
        is_fold = ov <= overlap_tol
        if not is_fold:
            # cannot fold here without collision -> detach as a separate island
            mn_all = np.vstack([p.poly for p in fp.panels]).max(0)
            poly2d = to2d(f3d)
            shift = np.array([mn_all[0] + params.margin_mm * 2 - poly2d[:, 0].min(),
                              params.margin_mm - poly2d[:, 1].min()])
            poly2d = poly2d + shift
            island_off[fid] = shift
            poly2 = Polygon(poly2d)
            fp.warnings.append(
                f"face #{fid} cannot fold off any neighbour without overlap "
                f"(the toes/feature block it) — emitted as a SEPARATE piece; "
                f"join it to #{parent} by hand")
        else:
            poly2d = to2d(f3d)

        placed_poly[fid] = poly2
        fp.panels.append(Panel2D(fid=fid, role="wall", poly=poly2d,
                                  centroid=poly2d.mean(0)))
        if is_fold:
            dih = np.degrees(np.arccos(np.clip(
                abs(np.dot(_unit(byid[fid].normal), _unit(byid[parent].normal))), -1, 1)))
            seg = next(((s0, s1) for (g, s0, s1) in adj[fid] if g == parent), None)
            wedge = (_face_wedge_deg(byid[parent], byid[fid], seg[0], seg[1])
                     if seg is not None else 180.0 - dih)
            e2 = to2d(np.vstack([E0, E1]))
            fp.folds.append(FoldLine(p0=e2[0], p1=e2[1],
                                     angle_deg=180 - dih,
                                     panel_a=parent, panel_b=fid,
                                     dihedral_deg=wedge))
        remaining.discard(fid)

    # real-stock thickness compensation (fold strips + floor clearance)
    trans = _thickness_compensate(fp, byid, s, params)

    # tabs + slots across the open corner seam (front wall <-> far side wall)
    off2d = {p.fid: island_off.get(p.fid, np.zeros(2)) + trans.get(p.fid, np.zeros(2))
             for p in fp.panels}
    _add_seam_tabs(fp, model, byid, s, params, transforms, to2d, off2d)

    # shift so min corner sits at margin
    mn, _ = fp.bounds()
    shift = np.array([params.margin_mm, params.margin_mm]) - mn
    for p in fp.panels:
        p.poly = p.poly + shift
        p.centroid = p.centroid + shift
        p.holes = [h + shift for h in p.holes]
    for f in fp.folds:
        f.p0 = f.p0 + shift
        f.p1 = f.p1 + shift

    # sheet frame for refold verification (refold.py): final 2D coords x map
    # onto the root plane in CAD space via origin + ((x - shift)/scale)·(u,v).
    wall_h = [(f.outer * s - origin) @ n_root for f in shell
              if f.fid != root]
    n_in = n_root if (wall_h and np.concatenate(wall_h).mean() >= 0) else -n_root
    fp.frame = dict(origin=origin, u=u, v=v, n=n_in, scale=params.scale,
                    shift=shift,
                    cad_centroid={f.fid: f.outer.mean(0) * s for f in shell})

    return fp
