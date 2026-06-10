"""
Guarantee #2: rev02's API reproduces rev01's outputs byte-for-byte.

Replays every golden case (made by make_golden.py from rev01's real HTTP
surface) against rev02's Flask app IN-PROCESS, on this machine — same Python,
same numpy/shapely — so the bar is byte equality:

  * SVG / DXF / preview: byte-equal after timestamp normalisation
  * width/height/panel counts/root/face ids/warnings: exactly equal
  * refold report (3D verification JSON incl. silhouettes + scene): exactly
    equal as canonical JSON

Run from rev02/:  python tests/check_equivalence.py     (exit 0 = pass)
"""

from __future__ import annotations

import io
import json
import os
import sys

from common import GOLDEN, REV02, normalize, numeric_diff, read_case

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "api"))
from index import app as rev02_app  # noqa: E402


def post_case(client, case: dict):
    """Replay one golden case against rev02's stateless API; return the JSON."""
    if case["kind"] == "tester":
        return client.post("/api/tester", data=dict(case["params"])).get_json()
    step = os.path.join(REV02, case["step"])
    with open(step, "rb") as f:
        data = {"file": (io.BytesIO(f.read()), os.path.basename(step))}
    data.update(case["params"])
    route = "/api/refold" if case["kind"] == "refold" else "/api/process"
    return client.post(route, data=data).get_json()


def compare(name: str, case: dict, got: dict) -> list:
    errs = []
    if got is None or got.get("error"):
        return [f"{name}: rev02 errored: {got and got.get('error')}"]

    if case["kind"] == "refold":
        a = json.dumps(case["report"], sort_keys=True)
        b = json.dumps(got, sort_keys=True)
        if a != b:
            from common import json_diff
            details = json_diff(case["report"], got, tol=0.0)[:8]
            errs.append(f"{name}: refold report differs: " + "; ".join(details))
        return errs

    text_keys = ("preview", "svg", "dxf") if case["kind"] == "process" else ("preview", "svg", "dxf")
    for k in text_keys:
        if normalize(got[k]) != case[k]:
            why = numeric_diff(case[k], got[k]) or "byte difference at equal numbers"
            errs.append(f"{name}: {k} not byte-equal ({why})")
    scalar_keys = (("warnings", "width", "height", "n_panels", "n_folds",
                    "root", "shell_face_ids") if case["kind"] == "process"
                   else ("n_cells",))
    for k in scalar_keys:
        if got[k] != case[k]:
            errs.append(f"{name}: {k}: golden {case[k]!r} != rev02 {got[k]!r}")
    return errs


def main() -> int:
    cases = sorted(os.listdir(GOLDEN))
    if not cases:
        print("no golden cases — run tests/make_golden.py first")
        return 2
    client = rev02_app.test_client()
    failures = []
    for name in cases:
        case = read_case(os.path.join(GOLDEN, name))
        got = post_case(client, case)
        errs = compare(name, case, got)
        failures += errs
        print(("FAIL " if errs else "ok   ") + name)
        for e in errs:
            print("       " + e)
    print(f"\n{len(cases) - len(set(e.split(':')[0] for e in failures))}/{len(cases)} cases byte-equal")
    if failures:
        print("FAIL — rev02 does not reproduce rev01 exactly")
        return 1
    print("PASS — rev02 output is byte-identical to rev01 on the full corpus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
