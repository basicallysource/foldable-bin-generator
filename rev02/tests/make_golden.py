"""
Generate the golden corpus from rev01's REAL HTTP surface.

Drives rev01's Flask app (repo-root app.py) through its actual routes —
upload → process → download svg/dxf, refold, tester — via Flask's test
client, and freezes every response under tests/golden/. This is the
reference rev02 must match.

Run from rev02/:  python tests/make_golden.py
(requires the rev01 code still present at the repo root)
"""

from __future__ import annotations

import io
import os
import shutil
import sys

from common import (GOLDEN, PARAM_SETS, REFOLD_PARAM_SETS, REPO, REV02, STEP_FILES,
                    TESTER_SETS, case_id, write_case)

sys.path.insert(0, REPO)
from app import app as rev01_app  # noqa: E402  (rev01's Flask app)


def main() -> int:
    if os.path.isdir(GOLDEN):
        shutil.rmtree(GOLDEN)
    os.makedirs(GOLDEN)
    client = rev01_app.test_client()
    n = 0

    for step in STEP_FILES:
        with open(step, "rb") as f:
            data = f.read()
        up = client.post("/upload", data={
            "file": (io.BytesIO(data), os.path.basename(step))})
        token = up.get_json()["token"]

        for set_name, overrides in PARAM_SETS.items():
            form = dict(overrides, token=token)
            r = client.post("/process", data=form)
            j = r.get_json()
            assert r.status_code == 200, f"{step} {set_name}: {j}"
            svg = client.get(j["svg_url"]).get_data(as_text=True)
            dxf = client.get(j["dxf_url"]).get_data(as_text=True)
            payload = dict(
                kind="process", step=os.path.relpath(step, REV02),
                params=overrides,
                preview=j["preview"], svg=svg, dxf=dxf,
                warnings=j["warnings"], width=j["width"], height=j["height"],
                n_panels=j["n_panels"], n_folds=j["n_folds"], root=j["root"],
                shell_face_ids=j["shell_face_ids"],
            )
            write_case(os.path.join(GOLDEN, case_id(step, set_name)), payload)
            n += 1
            print(f"golden: {case_id(step, set_name)}")

        for set_name in REFOLD_PARAM_SETS:
            form = dict(PARAM_SETS[set_name], token=token)
            r = client.post("/refold", data=form)
            j = r.get_json()
            assert r.status_code == 200, f"{step} refold {set_name}: {j}"
            payload = dict(kind="refold", step=os.path.relpath(step, REV02),
                           params=PARAM_SETS[set_name], report=j)
            write_case(os.path.join(GOLDEN, case_id(step, f"refold_{set_name}")),
                       payload)
            n += 1
            print(f"golden: {case_id(step, f'refold_{set_name}')}")

    for set_name, overrides in TESTER_SETS.items():
        r = client.post("/tester", data=dict(overrides))
        j = r.get_json()
        assert r.status_code == 200, f"tester {set_name}: {j}"
        svg = client.get(j["svg_url"]).get_data(as_text=True)
        dxf = client.get(j["dxf_url"]).get_data(as_text=True)
        payload = dict(kind="tester", params=overrides, preview=j["preview"],
                       svg=svg, dxf=dxf, n_cells=j["n_cells"])
        write_case(os.path.join(GOLDEN, f"tester__{set_name}"), payload)
        n += 1
        print(f"golden: tester__{set_name}")

    print(f"\nwrote {n} golden cases -> {GOLDEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
