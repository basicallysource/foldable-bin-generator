"""
(Re)freeze the golden corpus from the CURRENT engine.

The corpus in tests/golden/ is the reference that check_equivalence.py and
check_deployed.py compare against. It was originally generated from rev01's
real Flask HTTP surface (see git history) and carried over byte-identically;
running this script REPLACES it with a snapshot of the current engine via the
current API (api/index.py, the same app the deployment runs).

Only regenerate after an INTENTIONAL engine change, and only after the
refold verification sweep passes (see EQUIVALENCE.md):

    for f in public/bins/*.step; do python verify.py "$f" || break; done
    python tests/make_golden.py
    python tests/check_source_identical.py   # update its manifest first

Run from the repo root:  python tests/make_golden.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys

from common import (GOLDEN, PARAM_SETS, REFOLD_PARAM_SETS, REV02, STEP_FILES,
                    TESTER_SETS, case_id, write_case)

sys.path.insert(0, os.path.join(REV02, "api"))
from index import app as ref_app  # noqa: E402


def main() -> int:
    if os.path.isdir(GOLDEN):
        shutil.rmtree(GOLDEN)
    os.makedirs(GOLDEN)
    client = ref_app.test_client()
    n = 0

    for step in STEP_FILES:
        with open(step, "rb") as f:
            data = f.read()

        for set_name, overrides in PARAM_SETS.items():
            form = dict(overrides)
            form["file"] = (io.BytesIO(data), os.path.basename(step))
            r = client.post("/api/process", data=form)
            j = r.get_json()
            assert r.status_code == 200, f"{step} {set_name}: {j}"
            payload = dict(
                kind="process", step=os.path.relpath(step, REV02),
                params=overrides,
                preview=j["preview"], svg=j["svg"], dxf=j["dxf"],
                warnings=j["warnings"], width=j["width"], height=j["height"],
                n_panels=j["n_panels"], n_folds=j["n_folds"], root=j["root"],
                shell_face_ids=j["shell_face_ids"],
            )
            write_case(os.path.join(GOLDEN, case_id(step, set_name)), payload)
            n += 1
            print(f"golden: {case_id(step, set_name)}")

        for set_name in REFOLD_PARAM_SETS:
            form = dict(PARAM_SETS[set_name])
            form["file"] = (io.BytesIO(data), os.path.basename(step))
            r = client.post("/api/refold", data=form)
            j = r.get_json()
            assert r.status_code == 200, f"{step} refold {set_name}: {j}"
            payload = dict(kind="refold", step=os.path.relpath(step, REV02),
                           params=PARAM_SETS[set_name], report=j)
            write_case(os.path.join(GOLDEN, case_id(step, f"refold_{set_name}")),
                       payload)
            n += 1
            print(f"golden: {case_id(step, f'refold_{set_name}')}")

    for set_name, overrides in TESTER_SETS.items():
        r = client.post("/api/tester", data=dict(overrides))
        j = r.get_json()
        assert r.status_code == 200, f"tester {set_name}: {j}"
        payload = dict(kind="tester", params=overrides, preview=j["preview"],
                       svg=j["svg"], dxf=j["dxf"], n_cells=j["n_cells"])
        write_case(os.path.join(GOLDEN, f"tester__{set_name}"), payload)
        n += 1
        print(f"golden: tester__{set_name}")

    print(f"\nwrote {n} golden cases -> {GOLDEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
