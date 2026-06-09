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
# data structures                                                               #
# --------------------------------------------------------------------------- #

@dataclass
class Panel2D:
    fid: int
    role: str               # "floor" | "wall"
    poly: np.ndarray        # Nx2 flattened polygon (mm)
    centroid: np.ndarray    # 2D


@dataclass
class FoldLine:
    p0: np.ndarray          # 2D
    p1: np.ndarray          # 2D
    angle_deg: float        # dihedral fold angle (180 - interior)
    panel_a: int
    panel_b: int


@dataclass
class FlatPattern:
    panels: list = field(default_factory=list)      # list[Panel2D]
    folds: list = field(default_factory=list)       # list[FoldLine]
    warnings: list = field(default_factory=list)
    # bookkeeping / diagnostics
    shell_face_ids: list = field(default_factory=list)
    root_fid: int | None = None

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
        c0, c1 = comps[0], comps[1]
        # Decide which component is the inner (cavity) shell: the inner faces'
        # outward normals point toward the other shell's centroid region.
        def centroid(comp):
            pts = np.vstack([byid[i].outer for i in comp])
            return pts.mean(0)
        cen0, cen1 = centroid(c0), centroid(c1)
        # For component c0, average dot of (face normal) with (vector to c1).
        def inwardness(comp, other_cen):
            acc = 0.0
            for i in comp:
                f = byid[i]
                fc = f.outer.mean(0)
                acc += np.dot(_unit(f.normal), _unit(other_cen - fc))
            return acc / len(comp)
        in0 = inwardness(c0, cen1)
        in1 = inwardness(c1, cen0)
        # inner shell = the one whose normals point AWAY from the other shell
        # (cavity side faces away from the surrounding material shell).
        inner = c0 if in0 < in1 else c1
        outer = c1 if inner is c0 else c0
        chosen = inner if params.shell == "inner" else outer

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
            e2 = to2d(np.vstack([E0, E1]))
            fp.folds.append(FoldLine(p0=e2[0], p1=e2[1],
                                     angle_deg=180 - dih,
                                     panel_a=parent, panel_b=fid))
        remaining.discard(fid)

    # shift so min corner sits at margin
    mn, _ = fp.bounds()
    shift = np.array([params.margin_mm, params.margin_mm]) - mn
    for p in fp.panels:
        p.poly = p.poly + shift
        p.centroid = p.centroid + shift
    for f in fp.folds:
        f.p0 = f.p0 + shift
        f.p1 = f.p1 + shift

    return fp
