import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'api' / '_lib'))
from simple_stl import processSimple
from binflatten.params import FlattenParams
from binflatten.export import to_svg, to_dxf, to_preview_svg

parser = argparse.ArgumentParser()
parser.add_argument('input', type=pathlib.Path)
parser.add_argument('--out', type=pathlib.Path, required=True)
parser.add_argument('--thickness', type=float, default=3.175)
parser.add_argument('--perf-dash', type=float, default=5)
parser.add_argument('--perf-gap', type=float, default=5)
args = parser.parse_args()
params = FlattenParams(material_thickness_mm=args.thickness, perf_dash_mm=args.perf_dash, perf_gap_mm=args.perf_gap, overlay_score=False, seam_tab_count=0, part_name=args.input.stem)
model, fp, geometry = processSimple(args.input, params)
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.with_suffix('.svg').write_text(to_svg(geometry, params))
args.out.with_name(args.out.name+'-preview.svg').write_text(to_preview_svg(fp, geometry, params))
args.out.with_suffix('.dxf').write_text(to_dxf(geometry, params))
receipt = {
    'source_sha256': model.source_sha256,
    'parameters': params.to_dict(),
    'source_panels': [{'id': face.fid, 'folded': face.outer.tolist(), 'normal': face.normal.tolist()} for face in model.faces],
    'source_hinges': [{'parent': parent, 'child': child, 'edge': [a.tolist(), b.tolist()]} for parent, child, a, b in model.simple_links],
    'panels': [{'id': panel.fid, 'flat': panel.poly.tolist()} for panel in fp.panels],
    'folds': [{'parent': fold.panel_a, 'child': fold.panel_b, 'flat': [fold.p0.tolist(), fold.p1.tolist()], 'dihedral_deg': fold.dihedral_deg} for fold in fp.folds],
    'width': geometry.width, 'height': geometry.height,
    'cut_loops': len(geometry.cut_loops), 'perforation_segments': len(geometry.score_segments),
    'planarity_union_grid_mm': 0.00001, 'numerical_closure_mm': 0.0001,
    'warnings': geometry.warnings,
}
args.out.with_suffix('.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps({key: receipt[key] for key in ['width', 'height', 'cut_loops', 'perforation_segments', 'warnings']}))
