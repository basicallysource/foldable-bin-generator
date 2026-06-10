"""
binflatten rev02 — stateless HTTP API, deployable as a Vercel Python function.

The geometry engine is the rev01 `binflatten` package, vendored BYTE-FOR-BYTE
in api/_lib/binflatten (the underscore keeps Vercel from treating its modules
as functions; `tests/check_source_identical.py` enforces the byte equality).
This file only adapts transport: where rev01's app.py stored the upload on
disk under a token and served downloads from disk, Vercel functions have no
shared disk, so every request carries the STEP file and the SVG/DXF come back
inline in the JSON for the browser to save client-side.

Request fields, parameter coercion, part-name fallback and error formatting
are kept identical to rev01's app.py so the two are interchangeable
end-to-end (see tests/check_equivalence.py).

Local dev:  python api/index.py   (Flask on :5328; `npm run dev` proxies /api)
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))

from flask import Flask, request, jsonify  # noqa: E402

from binflatten.params import FlattenParams, TesterParams  # noqa: E402
from binflatten.pipeline import process, load_model  # noqa: E402
from binflatten.unfold import unfold  # noqa: E402
from binflatten.refold import verify_refold  # noqa: E402
from binflatten.export import to_svg, to_dxf, to_preview_svg  # noqa: E402
from binflatten.tester import tester_svg, tester_dxf, tester_preview_svg  # noqa: E402

app = Flask(__name__)
# Vercel caps request bodies at 4.5 MB anyway; bin STEP exports are ~50 KB.
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024


def _params_from_form(form) -> FlattenParams:
    # same filtering rev01's app.py used
    return FlattenParams.from_dict({k: form.get(k) for k in form
                                    if k in FlattenParams.__dataclass_fields__})


def _save_step_upload():
    """Validate the uploaded STEP and park it in a temp file (read_step wants a
    path). Returns (path, original_stem, error_response)."""
    f = request.files.get("file")
    if not f or not f.filename:
        return None, None, (jsonify(error="no file"), 400)
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".step", ".stp"):
        return None, None, (jsonify(error=f"unsupported '{ext}'; upload a .step/.stp"), 400)
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as out:
        out.write(f.read())
    return path, os.path.splitext(os.path.basename(f.filename))[0], None


@app.route("/api/defaults")
def defaults_route():
    """Parameter defaults straight from the dataclasses — the UI builds its
    form from these so Python stays the single source of truth."""
    tdef = TesterParams().to_dict()
    tdef["dash_values_mm"] = ", ".join(f"{x:g}" for x in tdef["dash_values_mm"])
    tdef["gap_values_mm"] = ", ".join(f"{x:g}" for x in tdef["gap_values_mm"])
    return jsonify(flatten=FlattenParams().to_dict(), tester=tdef)


@app.route("/api/process", methods=["POST"])
def process_route():
    path, stem, err = _save_step_upload()
    if err:
        return err
    try:
        params = _params_from_form(request.form)
        if not params.part_name:
            params.part_name = stem
        fp, geom = process(path, params)
        preview = to_preview_svg(fp, geom, params)
        svg = to_svg(geom, params)
        dxf = to_dxf(geom, params)
    except Exception as e:  # surface geometry errors to the UI
        return jsonify(error=f"{type(e).__name__}: {e}"), 500
    finally:
        os.unlink(path)

    return jsonify(
        preview=preview,
        warnings=geom.warnings,
        width=round(geom.width, 2),
        height=round(geom.height, 2),
        n_panels=len(fp.panels),
        n_folds=len(fp.folds),
        root=fp.root_fid,
        shell_face_ids=fp.shell_face_ids,
        svg=svg,
        dxf=dxf,
    )


@app.route("/api/refold", methods=["POST"])
def refold_route():
    """Fold the generated pattern back up in 3D and compare it to the CAD:
    silhouette overlays, outermost dimensions and a three.js scene."""
    path, _stem, err = _save_step_upload()
    if err:
        return err
    try:
        params = _params_from_form(request.form)
        model = load_model(path)
        fp = unfold(model, params)
        rep = verify_refold(model, fp, params)
        rep["warnings"] = (rep.get("warnings") or []) + (fp.warnings or [])
    except Exception as e:
        return jsonify(error=f"{type(e).__name__}: {e}"), 500
    finally:
        os.unlink(path)
    return jsonify(rep)


@app.route("/api/model3d", methods=["POST"])
def model3d_route():
    """Planar CAD faces of the uploaded STEP as three.js shapes — for the
    model-preview modal. Same face→(poly, matrix) mapping the refold scene
    uses for its CAD reference layer, minus the (expensive) unfold/refold."""
    import numpy as np
    from binflatten.unfold import _plane_basis

    path, _stem, err = _save_step_upload()
    if err:
        return err
    try:
        model = load_model(path)
        s = model.unit_scale_to_mm
        cad = []
        for f in model.faces:
            if not (f.is_planar and f.outer is not None and len(f.outer) >= 3):
                continue
            ring = f.outer * s
            u, v = _plane_basis(f.normal, ring[0], ring[1])
            nrm = np.cross(u, v)
            d = ring - ring[0]
            poly = np.column_stack([d @ u, d @ v])
            M = np.eye(4)
            M[:3, :3] = np.column_stack([u, v, nrm])
            M[:3, 3] = ring[0]
            cad.append(dict(poly=np.round(poly, 3).tolist(),
                            matrix=M.flatten(order="F").tolist()))
        allp = np.vstack([f.outer * s for f in model.faces
                          if f.outer is not None and len(f.outer)])
        center = allp.mean(0)
        size = float(np.linalg.norm(allp.max(0) - allp.min(0)))
    except Exception as e:
        return jsonify(error=f"{type(e).__name__}: {e}"), 500
    finally:
        os.unlink(path)
    return jsonify(cad=cad, center=np.round(center, 2).tolist(),
                   size=round(size, 1))


@app.route("/api/tester", methods=["POST"])
def tester_route():
    try:
        tp = TesterParams.from_dict(request.form.to_dict())
        preview = tester_preview_svg(tp)
        svg = tester_svg(tp)
        dxf = tester_dxf(tp)
    except Exception as e:
        return jsonify(error=f"{type(e).__name__}: {e}"), 500
    n_cells = len(tp.dash_values_mm) * (len(tp.gap_values_mm) + (1 if tp.include_continuous else 0))
    return jsonify(preview=preview, n_cells=n_cells, svg=svg, dxf=dxf)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5328")))
