"""
Minimal pure-Python STEP (ISO-10303 / AP242) B-rep reader.

We only need the slice of STEP that an Onshape solid export uses to describe a
bin: planar faces, their boundary loops made of straight (and a few curved)
edges, and the topological adjacency between faces (which faces share an edge).
That shared-edge adjacency is exactly the fold-line information we want, so a
heavy CAD kernel (OpenCASCADE/cadquery) is unnecessary here.

What we deliberately ignore for rev01:
  * exact curve geometry of CIRCLE / B_SPLINE / ELLIPSE edges -> we use the
    edge's two end vertices (a chord). The only curved faces in the sample are
    tiny fillet/chamfer faces, so chords are fine for flattening.

Units: the sample is exported in METERS. We expose the file unit and a helper
to scale to millimetres.

The public entry point is `read_step(path) -> Model`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------- #
# Low level: tokenize the DATA section into {id: (type, raw_args)}             #
# --------------------------------------------------------------------------- #

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_INSTANCE = re.compile(r"#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*$", re.DOTALL)


def _strip_comments(text: str) -> str:
    return _COMMENT.sub("", text)


def _split_statements(data_section: str):
    """Yield raw statements (without trailing ';'), respecting strings."""
    buf = []
    in_str = False
    for ch in data_section:
        if ch == "'":
            in_str = not in_str
            buf.append(ch)
        elif ch == ";" and not in_str:
            yield "".join(buf).strip()
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        yield tail


def _split_args(s: str):
    """Split a STEP argument list on top-level commas (parens/strings aware)."""
    args = []
    depth = 0
    in_str = False
    buf = []
    for ch in s:
        if ch == "'":
            in_str = not in_str
            buf.append(ch)
        elif in_str:
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    last = "".join(buf).strip()
    if last or args:
        args.append(last)
    return args


@dataclass
class RawEntity:
    etype: str
    args: list  # parsed argument tokens (strings; #refs kept as ints)


class StepFile:
    """Holds the raw instance table and resolves entities lazily."""

    def __init__(self, path: str):
        raw = open(path, "r", errors="replace").read()
        raw = _strip_comments(raw)
        # keep only the DATA; ... ENDSEC; body
        m = re.search(r"DATA;(.*?)ENDSEC;", raw, re.DOTALL)
        body = m.group(1) if m else raw
        self.table: dict[int, RawEntity] = {}
        for stmt in _split_statements(body):
            im = _INSTANCE.match(stmt)
            if not im:
                continue
            eid = int(im.group(1))
            etype = im.group(2)
            args = self._tokenize_args(im.group(3))
            self.table[eid] = RawEntity(etype, args)

    @staticmethod
    def _tokenize_args(s: str):
        out = []
        for tok in _split_args(s):
            tok = tok.strip()
            if tok.startswith("#"):
                out.append(int(tok[1:]))
            elif tok.startswith("(") and tok.endswith(")"):
                # nested list (e.g. a list of #refs or numbers)
                inner = StepFile._tokenize_args(tok[1:-1])
                out.append(inner)
            else:
                out.append(tok)
        return out

    def __getitem__(self, eid: int) -> RawEntity:
        return self.table[eid]


# --------------------------------------------------------------------------- #
# Mid level: typed geometry/topology resolvers                                 #
# --------------------------------------------------------------------------- #

def _num(tok) -> float:
    return float(tok)


@dataclass
class Edge:
    eid: int
    v0: np.ndarray  # start vertex (geometric)
    v1: np.ndarray  # end vertex
    curve_type: str  # 'LINE', 'CIRCLE', 'B_SPLINE_CURVE_WITH_KNOTS', ...


@dataclass
class Face:
    fid: int
    surface_type: str  # 'PLANE' | 'CYLINDRICAL_SURFACE' | 'CONICAL_SURFACE' | ...
    normal: np.ndarray | None  # plane normal (None for non-planar)
    origin: np.ndarray | None  # a point on the surface
    loops: list = field(default_factory=list)   # list of ordered vertex arrays
    edge_ids: set = field(default_factory=set)   # underlying EDGE_CURVE ids used
    outer: np.ndarray | None = None              # ordered Nx3 outer-loop polygon

    @property
    def is_planar(self) -> bool:
        return self.surface_type == "PLANE"


@dataclass
class Model:
    faces: list  # list[Face]
    unit_scale_to_mm: float
    edge_faces: dict  # edge_id -> list[fid]
    edges: dict = field(default_factory=dict)  # edge_id -> Edge (geometry)


class _Resolver:
    def __init__(self, sf: StepFile):
        self.sf = sf
        self._cache: dict[int, object] = {}

    def point(self, eid: int) -> np.ndarray:
        e = self.sf[eid]
        if e.etype == "CARTESIAN_POINT":
            coords = e.args[1]
            return np.array([_num(c) for c in coords], dtype=float)
        if e.etype == "VERTEX_POINT":
            return self.point(e.args[1])
        raise ValueError(f"not a point: #{eid} {e.etype}")

    def direction(self, eid: int) -> np.ndarray:
        e = self.sf[eid]
        v = np.array([_num(c) for c in e.args[1]], dtype=float)
        n = np.linalg.norm(v)
        return v / n if n else v

    def edge_curve(self, eid: int) -> Edge:
        if eid in self._cache:
            return self._cache[eid]
        e = self.sf[eid]
        # EDGE_CURVE('',#v0,#v1,#curve,.T.)
        v0 = self.point(e.args[1])
        v1 = self.point(e.args[2])
        curve = self.sf[e.args[3]]
        edge = Edge(eid, v0, v1, curve.etype)
        self._cache[eid] = edge
        return edge

    def oriented_edge(self, eid: int):
        """Return (edge, forward_bool). The geometric vertices already match the
        underlying edge; orientation tells us traversal direction."""
        e = self.sf[eid]
        # ORIENTED_EDGE('',*,*,#edge,.T./.F.)
        edge_ref = e.args[3]
        orient = e.args[4] == ".T."
        return self.edge_curve(edge_ref), orient

    def edge_loop(self, eid: int):
        """Return (ordered_points Nx3, set_of_edge_ids)."""
        e = self.sf[eid]
        oe_list = e.args[1]
        pts = []
        edge_ids = set()
        for oe in oe_list:
            edge, fwd = self.oriented_edge(oe)
            edge_ids.add(edge.eid)
            a, b = (edge.v0, edge.v1) if fwd else (edge.v1, edge.v0)
            if not pts or not np.allclose(pts[-1], a, atol=1e-9):
                pts.append(a)
            pts.append(b)
        # drop closing duplicate
        if len(pts) > 1 and np.allclose(pts[0], pts[-1], atol=1e-9):
            pts = pts[:-1]
        return np.array(pts), edge_ids

    def face_bound(self, eid: int):
        e = self.sf[eid]  # FACE_BOUND / FACE_OUTER_BOUND ('',#loop,.T.)
        return self.edge_loop(e.args[1])

    def surface(self, eid: int):
        e = self.sf[eid]
        if e.etype == "PLANE":
            ax = self.sf[e.args[1]]  # AXIS2_PLACEMENT_3D
            origin = self.point(ax.args[1])
            normal = self.direction(ax.args[2])
            return "PLANE", normal, origin
        if e.etype in ("CYLINDRICAL_SURFACE", "CONICAL_SURFACE"):
            ax = self.sf[e.args[1]]
            origin = self.point(ax.args[1])
            axis = self.direction(ax.args[2])
            return e.etype, axis, origin
        return e.etype, None, None

    def advanced_face(self, eid: int) -> Face:
        e = self.sf[eid]  # ADVANCED_FACE('',(#bounds...),#surface,.T./.F.)
        bounds = e.args[1]
        surf_id = e.args[2]
        stype, nrm, org = self.surface(surf_id)
        face = Face(fid=eid, surface_type=stype, normal=nrm, origin=org)
        outer_area = -1.0
        for b in bounds:
            is_outer = self.sf[b].etype == "FACE_OUTER_BOUND"
            pts, eids = self.face_bound(b)
            face.loops.append(pts)
            face.edge_ids |= eids
            if face.is_planar and nrm is not None and len(pts) >= 3:
                area = _polygon_area_3d(pts, nrm)
                if is_outer or area > outer_area:
                    outer_area = area
                    face.outer = pts
        if face.outer is None and face.loops:
            face.outer = max(face.loops, key=len)
        return face


def _polygon_area_3d(pts: np.ndarray, normal: np.ndarray) -> float:
    """Signed-magnitude area of a planar polygon in 3D."""
    if len(pts) < 3:
        return 0.0
    acc = np.zeros(3)
    for i in range(len(pts)):
        acc += np.cross(pts[i], pts[(i + 1) % len(pts)])
    return abs(np.dot(acc, normal)) / 2.0


def _detect_unit_scale_to_mm(sf: StepFile) -> float:
    """Find the global length unit. Onshape exports in metres -> 1000 mm/m."""
    for e in sf.table.values():
        if e.etype in ("SI_UNIT",):
            flat = " ".join(str(a) for a in e.args)
            if "MILLI" in flat:
                return 1.0
            if "METRE" in flat or "METER" in flat:
                return 1000.0
    # Fallback: infer from model size. Onshape metre exports look tiny (<10).
    return None  # decided by caller after reading geometry


def read_step(path: str) -> Model:
    sf = StepFile(path)
    res = _Resolver(sf)
    faces = []
    for eid, e in sf.table.items():
        if e.etype == "ADVANCED_FACE":
            faces.append(res.advanced_face(eid))

    # build edge -> faces adjacency
    edge_faces: dict[int, list] = {}
    for f in faces:
        for ei in f.edge_ids:
            edge_faces.setdefault(ei, []).append(f.fid)

    # determine units
    scale = _detect_unit_scale_to_mm(sf)
    if scale is None:
        allpts = np.vstack([f.outer for f in faces if f.outer is not None])
        extent = (allpts.max(0) - allpts.min(0)).max()
        scale = 1000.0 if extent < 10.0 else 1.0  # <10 units => metres

    # expose resolved edge geometry (cached during face resolution)
    edges = {e.eid: e for e in res._cache.values() if isinstance(e, Edge)}

    return Model(faces=faces, unit_scale_to_mm=scale,
                 edge_faces=edge_faces, edges=edges)


if __name__ == "__main__":
    import sys
    m = read_step(sys.argv[1])
    planar = [f for f in m.faces if f.is_planar]
    print(f"faces: {len(m.faces)}  planar: {len(planar)}  unit->mm: {m.unit_scale_to_mm}")
    s = m.unit_scale_to_mm
    areas = sorted(
        ((_polygon_area_3d(f.outer, f.normal) * s * s, f) for f in planar),
        key=lambda x: -x[0],
    )
    print("\nTop planar faces by area (mm^2):")
    for a, f in areas[:12]:
        nrm = np.round(f.normal, 3)
        nv = len(f.outer)
        nbr = sum(1 for ei in f.edge_ids if len(m.edge_faces[ei]) > 1)
        print(f"  #{f.fid:<5} area={a:9.1f}  n={nrm}  verts={nv:2d}  shared_edges={nbr}")
    curved = [f for f in m.faces if not f.is_planar]
    print(f"\ncurved faces: {[(f.fid, f.surface_type) for f in curved]}")
