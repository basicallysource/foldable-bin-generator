"""
binflatten — local web UI for rev01.

Upload a bin STEP file, tweak every laser parameter, see the flattened net
(cut = red, fold/score = blue), and download SVG / DXF for LightBurn.

Run:  python app.py   then open http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import uuid

from flask import Flask, request, jsonify, render_template, send_file, abort

from binflatten.params import FlattenParams
from binflatten.pipeline import process
from binflatten.export import to_svg, to_dxf, to_preview_svg

HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(HERE, "outputs", "uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB


def _params_from_form(form) -> FlattenParams:
    return FlattenParams.from_dict({k: form.get(k) for k in form
                                    if k in FlattenParams.__dataclass_fields__})


@app.route("/")
def index():
    return render_template("index.html",
                           defaults=FlattenParams().to_dict())


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="no file"), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".step", ".stp"):
        return jsonify(error=f"unsupported '{ext}'; upload a .step/.stp"), 400
    token = uuid.uuid4().hex
    path = os.path.join(UPLOADS, token + ext)
    f.save(path)
    return jsonify(token=token, filename=f.filename)


def _resolve(token):
    for ext in (".step", ".stp"):
        p = os.path.join(UPLOADS, token + ext)
        if os.path.exists(p):
            return p
    return None


@app.route("/process", methods=["POST"])
def process_route():
    token = request.form.get("token")
    path = _resolve(token) if token else None
    if not path:
        return jsonify(error="unknown or missing upload token"), 400
    try:
        params = _params_from_form(request.form)
        fp, geom = process(path, params)
        preview = to_preview_svg(fp, geom, params)
    except Exception as e:  # surface geometry errors to the UI
        return jsonify(error=f"{type(e).__name__}: {e}"), 500

    # write downloadable files
    base = os.path.join(UPLOADS, token)
    with open(base + ".svg", "w") as f:
        f.write(to_svg(geom, params))
    with open(base + ".dxf", "w") as f:
        f.write(to_dxf(geom, params))

    return jsonify(
        preview=preview,
        warnings=geom.warnings,
        width=round(geom.width, 2),
        height=round(geom.height, 2),
        n_panels=len(fp.panels),
        n_folds=len(fp.folds),
        root=fp.root_fid,
        shell_face_ids=fp.shell_face_ids,
        svg_url=f"/download/{token}/svg",
        dxf_url=f"/download/{token}/dxf",
    )


@app.route("/download/<token>/<kind>")
def download(token, kind):
    ext = {"svg": ".svg", "dxf": ".dxf"}.get(kind)
    if not ext:
        abort(404)
    path = os.path.join(UPLOADS, token + ext)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True,
                     download_name=f"bin_flat{ext}")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
