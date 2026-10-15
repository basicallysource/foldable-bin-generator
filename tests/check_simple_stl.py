import io
import pathlib
import sys
import tempfile
import unittest

import numpy as np
from shapely.geometry import Polygon, LineString

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'api' / '_lib'))
from simple_stl import processSimple, readSimpleStl
from binflatten.params import FlattenParams
from binflatten.export import to_svg


def fixture(path, roof=False):
    axes = [[0, 2, 98, 100], [0, 2, 78, 80], [0, 2, 60]]
    filled = {(i,j,k) for i in range(3) for j in range(3) for k in range(2) if k == 0 or i == 2 or j in (0,2)}
    if roof:
        filled |= {(i,j,1) for i in range(3) for j in range(3)}
    triangles = []
    for cell in filled:
        for axis in range(3):
            for sign in [-1,1]:
                neighbor = list(cell)
                neighbor[axis] += sign
                if tuple(neighbor) in filled:
                    continue
                other = [a for a in range(3) if a != axis]
                ring = []
                for a,b in [(0,0),(1,0),(1,1),(0,1)]:
                    point = [0.,0.,0.]
                    point[axis] = axes[axis][cell[axis]+(sign==1)]
                    point[other[0]] = axes[other[0]][cell[other[0]]+a]
                    point[other[1]] = axes[other[1]][cell[other[1]]+b]
                    ring.append(np.array(point))
                if np.cross(ring[1]-ring[0],ring[2]-ring[0])[axis]*sign < 0:
                    ring.reverse()
                triangles.extend([[ring[0],ring[1],ring[2]],[ring[0],ring[2],ring[3]]])
    text = 'solid fixture\n'
    for tri in triangles:
        text += 'facet normal 0 0 0\nouter loop\n'+''.join('vertex '+' '.join(map(str,p))+'\n' for p in tri)+'endloop\nendfacet\n'
    path.write_text(text+'endsolid fixture\n')


class SimpleStlTests(unittest.TestCase):
    def testApi(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'api'))
        from index import app
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)/'open.stl'
            fixture(path)
            client = app.test_client()
            for route in ['process', 'model3d', 'refold']:
                response = client.post('/api/'+route, data={'file': (io.BytesIO(path.read_bytes()), 'open.stl')})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertNotIn('error', response.get_json())

    def testPlainNet(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)/'open.stl'
            fixture(path)
            _, pattern, geometry = processSimple(path, FlattenParams())
            self.assertEqual(len(pattern.panels),4)
            self.assertEqual(len(pattern.folds),3)
            self.assertEqual(sum(f.panel_a==1 for f in pattern.folds),2)
            self.assertEqual(len(geometry.cut_loops),1)
            self.assertFalse(any(p.holes for p in pattern.panels))
            by_id = {p.fid: p for p in pattern.panels}
            for fold in pattern.folds:
                line = LineString([fold.p0, fold.p1])
                for fid in [fold.panel_a, fold.panel_b]:
                    self.assertLess(line.difference(Polygon(by_id[fid].poly).buffer(.00002)).length, .0001)
            points = np.vstack([loop for piece in geometry.cut_loops for loop in piece])
            self.assertTrue((points.min(0) >= 0).all())
            self.assertTrue((points.max(0) <= [geometry.width, geometry.height]).all())
            svg = to_svg(geometry, FlattenParams(overlay_score=False,seam_tab_count=0))
            self.assertIn('#ff0000',svg)
            self.assertIn('#0000ff',svg)
            self.assertNotIn('#00a650',svg)
            for i,p in enumerate(pattern.panels):
                for q in pattern.panels[:i]:
                    self.assertLess(Polygon(p.poly).intersection(Polygon(q.poly)).area,.01)
            _, thicker, _ = processSimple(path, FlattenParams(material_thickness_mm=4))
            self.assertNotEqual(pattern.bounds()[1].tolist(),thicker.bounds()[1].tolist())

    def testRoofRejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)/'roof.stl'
            fixture(path,True)
            with self.assertRaises(ValueError):
                readSimpleStl(path)


if __name__ == '__main__':
    unittest.main()
