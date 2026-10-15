import hashlib
import math
import pathlib
import re
import struct
import sys

import numpy as np
from shapely.geometry import Polygon, LineString
from shapely import union_all

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from binflatten.step_io import Face, Model, Edge
from binflatten.unfold import FlatPattern, Panel2D, FoldLine, _apply, _unit, _rot_matrix, _plane_basis, _face_wedge_deg, _thickness_compensate
from binflatten.export import build_geometry

PLANE_TOLERANCE = 0.02
MIN_PANEL_AREA = 100


def readTriangles(path):
    raw = pathlib.Path(path).read_bytes()
    if len(raw) >= 84 and 84 + 50 * struct.unpack_from('<I', raw, 80)[0] == len(raw):
        count = struct.unpack_from('<I', raw, 80)[0]
        triangles = np.array([struct.unpack_from('<9f', raw, 84 + i * 50 + 12) for i in range(count)]).reshape(-1, 3, 3)
    else:
        values = re.findall(rb'\bvertex\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)', raw)
        if not values or len(values) % 3:
            raise ValueError('Invalid ASCII or binary STL')
        triangles = np.array(values, dtype=float).reshape(-1, 3, 3)
    if len(triangles) > 150000 or not np.isfinite(triangles).all():
        raise ValueError('STL exceeds supported finite triangle count')
    return triangles


def planarFaces(triangles):
    groups = {}
    for tri in triangles:
        normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        area = np.linalg.norm(normal)
        if area < 1e-8:
            continue
        normal /= area
        if abs(normal[2]) > .99999:
            normal = np.array([0., 0., np.sign(normal[2])])
        distance = float(normal @ tri[0])
        key = (*np.round(normal, 4), round(distance, 3))
        groups.setdefault(key, []).append(tri)
    faces = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        normal = _unit(sum((np.cross(t[1]-t[0], t[2]-t[0]) for t in members), np.zeros(3)))
        if abs(normal[2]) > .99999:
            normal = np.array([0., 0., np.sign(normal[2])])
        origin = members[0][0]
        u, v = _plane_basis(normal, origin, members[0][1])
        merged = union_all([Polygon(np.column_stack([(t - origin) @ u, (t - origin) @ v])) for t in members], grid_size=0.00001)
        merged = merged.buffer(0.0001, join_style=2).buffer(-0.0001, join_style=2)
        if merged.geom_type != 'Polygon' or merged.area < MIN_PANEL_AREA:
            continue
        points = np.array(merged.exterior.coords[:-1])
        ring = origin + points[:, :1] * u + points[:, 1:] * v
        face = Face(len(faces) + 1, 'PLANE', normal, origin, outer=ring)
        face.hole_areas = [Polygon(hole).area for hole in merged.interiors]
        faces.append((merged.area, face))
    return faces


def readSimpleStl(path):
    triangles = readTriangles(path)
    faces = planarFaces(triangles)
    z_min = float(triangles[:, :, 2].min())
    floors = [(area, face) for area, face in faces if abs(face.normal[2]) > .9999 and abs(face.outer[:, 2].mean() - z_min) < PLANE_TOLERANCE]
    if not floors:
        raise ValueError('Place the STL floor horizontal with its underside at the lowest Z')
    floor_area, floor = max(floors, key=lambda item: item[0])
    if any(area > floor_area * .12 and abs(face.normal[2]) > .4 and face.outer[:, 2].mean() > z_min + 5 for area, face in faces):
        raise ValueError('Simple STL mode requires a roofless bin')
    floor.fid = 1
    floor.normal = np.array([0., 0., 1.])
    center = floor.outer.mean(0)
    walls = []
    for area, face in faces:
        horizontal = np.linalg.norm(face.normal[:2])
        if horizontal < .8 or np.ptp(face.outer[:, 2]) < 10:
            continue
        if (face.outer.mean(0) - center)[:2] @ face.normal[:2] <= 0:
            continue
        if area < max(MIN_PANEL_AREA, floor_area * .05):
            continue
        walls.append(face)
    if len(walls) not in (3, 5):
        raise ValueError(f'Expected two side walls and one or three planar front faces; found {len(walls)}. Roofs and curved fronts are unsupported.')
    if any(area > .00001 for face in [floor] + walls for area in face.hole_areas):
        raise ValueError('Simple STL mode does not support holes in selected structural panels')
    angles = sorted((math.atan2(f.normal[1], f.normal[0]) % (2 * math.pi), i) for i, f in enumerate(walls))
    gaps = [(angles[(i + 1) % len(angles)][0] + (2 * math.pi if i == len(angles) - 1 else 0) - a, i) for i, (a, _) in enumerate(angles)]
    split = max(gaps)[1] + 1
    order = angles[split:] + angles[:split]
    walls = [walls[i] for _, i in order]
    for i, wall in enumerate(walls):
        wall.fid = i + 2
        heights = wall.outer[:, 2]
        bottom = heights < heights.min() + PLANE_TOLERANCE
        points = wall.outer.copy()
        delta = points[bottom, 2] - z_min
        points[bottom, :2] += delta[:, None] * wall.normal[2] * wall.normal[:2] / np.dot(wall.normal[:2], wall.normal[:2])
        points[bottom, 2] = z_min
        wall.outer = points
    links = []
    for side in (walls[0], walls[-1]):
        points = side.outer[np.abs(side.outer[:, 2] - z_min) < PLANE_TOLERANCE]
        if len(points) < 2:
            raise ValueError('Side wall has no floor hinge')
        a = points[0]
        b = max(points, key=lambda p: np.linalg.norm(p - a))
        links.append((1, side.fid, a, b))
    for parent, child in zip(walls, walls[1:-1]):
        normal = child.normal
        distance = normal @ child.outer[0]
        points = parent.outer[np.abs(parent.outer @ normal - distance) < PLANE_TOLERANCE]
        if len(points) < 2:
            raise ValueError('Front chain does not have continuous side-to-front hinges')
        a = points[0]
        b = max(points, key=lambda p: np.linalg.norm(p - a))
        links.append((parent.fid, child.fid, a, b))
    model = Model([floor] + walls, 1., {})
    by_id = {face.fid: face for face in model.faces}
    for edge_id, (parent, child, a, b) in enumerate(links, 1):
        model.edge_faces[edge_id] = [parent, child]
        model.edges[edge_id] = Edge(edge_id, np.array([a, b]), 'LINE')
        by_id[parent].edge_ids.add(edge_id)
        by_id[child].edge_ids.add(edge_id)
    model.simple_links = links
    model.source_sha256 = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    return model


def unfoldSimple(model, params):
    params.seam_tab_count = 0
    params.overlay_score = False
    by_id = {f.fid: f for f in model.faces}
    root = by_id[1]
    origin = root.outer[0]
    u, v = _plane_basis(root.normal, root.outer[0], root.outer[1])
    def to2d(points):
        delta = np.atleast_2d(points) - origin
        return np.column_stack([delta @ u, delta @ v]) * params.scale
    fp = FlatPattern(root_fid=1, shell_face_ids=sorted(by_id))
    flat = {1: root.outer}
    transforms = {1: (np.eye(3), np.zeros(3))}
    poly = to2d(root.outer)
    fp.panels.append(Panel2D(1, 'floor', poly, poly.mean(0)))
    for parent, child, a, b in model.simple_links:
        moved = _apply(transforms[parent], by_id[child].outer)
        e0, e1 = _apply(transforms[parent], np.array([a, b]))
        axis = _unit(e1 - e0)
        normal = _apply((transforms[parent][0], np.zeros(3)), by_id[child].normal[None])[0]
        base = math.acos(np.clip(normal @ root.normal, -1, 1))
        options = []
        for angle in [base, -base, math.pi - base, base - math.pi]:
            rotation = _rot_matrix(axis, angle)
            trial = (moved - e0) @ rotation.T + e0
            if np.ptp(trial[:, 2]) > .02:
                continue
            polygon = Polygon(to2d(trial))
            overlap = sum(polygon.intersection(Polygon(p.poly)).area for p in fp.panels)
            options.append((overlap, -np.linalg.norm(trial.mean(0) - flat[parent].mean(0)), trial, rotation))
        if not options:
            raise ValueError('Cannot flatten the requested hinge tree')
        _, _, trial, rotation = min(options, key=lambda row: (round(row[0], 4), row[1]))
        rp, tp = transforms[parent]
        transforms[child] = rotation @ rp, rotation @ tp + e0 - rotation @ e0
        flat[child] = trial
        polygon = to2d(trial)
        fp.panels.append(Panel2D(child, 'wall', polygon, polygon.mean(0)))
        edge = to2d(np.array([e0, e1]))
        wedge = _face_wedge_deg(by_id[parent], by_id[child], a, b)
        fp.folds.append(FoldLine(edge[0], edge[1], 180 - wedge, parent, child, wedge))
    _thickness_compensate(fp, by_id, 1., params)
    panels = {panel.fid: panel for panel in fp.panels}
    for fold in fp.folds:
        line = LineString([fold.p0, fold.p1])
        for fid in [fold.panel_a, fold.panel_b]:
            line = line.intersection(Polygon(panels[fid].poly).buffer(.00001))
        if line.geom_type != 'LineString' or line.length < .1:
            raise ValueError('Compensated fold has no continuous shared material edge')
        fold.p0, fold.p1 = np.array(line.coords)[[0,-1]]
    for i, panel in enumerate(fp.panels):
        if not Polygon(panel.poly).is_valid:
            raise ValueError('Invalid compensated panel')
        for other in fp.panels[:i]:
            if Polygon(panel.poly).intersection(Polygon(other.poly)).area > .01:
                raise ValueError('Unfolded panels overlap; this STL cannot use the simple hinge layout')
    minimum, _ = fp.bounds()
    shift = np.array([params.margin_mm, params.margin_mm]) - minimum
    for panel in fp.panels:
        panel.poly += shift
        panel.centroid += shift
    for fold in fp.folds:
        fold.p0 += shift
        fold.p1 += shift
    fp.frame = dict(origin=origin, u=u, v=v, n=root.normal, scale=params.scale, shift=shift, cad_centroid={f.fid: f.outer.mean(0) for f in model.faces})
    return fp


def processSimple(path, params):
    if not math.isfinite(params.material_thickness_mm) or not .1 <= params.material_thickness_mm <= 10:
        raise ValueError('Material thickness must be between0.1 and10mm')
    if params.perf_dash_mm <= 0 or params.perf_gap_mm <= 0:
        raise ValueError('Perforation dash and gap must be positive')
    model = readSimpleStl(path)
    fp = unfoldSimple(model, params)
    geometry = build_geometry(fp, params)
    cut_points = np.vstack([loop for piece in geometry.cut_loops for loop in piece])
    minimum, maximum = cut_points.min(0), cut_points.max(0)
    shift = .1 - minimum
    geometry.cut_loops = [[loop + shift for loop in piece] for piece in geometry.cut_loops]
    geometry.score_segments = [(a + shift, b + shift) for a, b in geometry.score_segments]
    geometry.labels = [(text, position + shift) for text, position in geometry.labels]
    geometry.width, geometry.height = maximum - minimum + .2
    for panel in fp.panels:
        panel.poly += shift
        panel.centroid += shift
    for fold in fp.folds:
        fold.p0 += shift
        fold.p1 += shift
    fp.frame['shift'] += shift
    return model, fp, geometry
