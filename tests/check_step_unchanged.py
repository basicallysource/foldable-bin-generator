import argparse
import importlib.util
import json
import pathlib
import subprocess
import tempfile

from check_equivalence import post_case, rev02_app
from common import GOLDEN, read_case, normalize

BASELINE_COMMIT = '6701d0047930df5e0449ed8ef740dfbee5490d6c'

parser = argparse.ArgumentParser()
parser.add_argument('--baseline', default=BASELINE_COMMIT)
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
source = subprocess.check_output(['git','show',args.baseline+':api/index.py'],cwd=root)
with tempfile.TemporaryDirectory() as folder:
    path = pathlib.Path(folder)/'baseline_api.py'
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location('baseline_api',path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    old, new = baseline.app.test_client(), rev02_app.test_client()
    for directory in sorted(pathlib.Path(GOLDEN).iterdir()):
        case = read_case(str(directory))
        a, b = post_case(old,case), post_case(new,case)
        for value in [a,b]:
            for key in ['svg','dxf','preview']:
                if key in value:
                    value[key] = normalize(value[key])
        if a != b:
            raise AssertionError('STEP transport changed: '+directory.name)
    print('PASS48/48 STEP cases exactly match baseline on the same runtime')
