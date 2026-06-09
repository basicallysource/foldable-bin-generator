"""High-level glue: file path + params -> flat pattern + laser geometry."""

from __future__ import annotations

import os

from .params import FlattenParams
from .step_io import read_step
from .unfold import unfold
from .export import build_geometry, to_svg, to_dxf, to_preview_svg


def load_model(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".step", ".stp"):
        return read_step(path)
    raise ValueError(
        f"unsupported file type '{ext}'. rev01 reads STEP (.step/.stp); "
        "STL support is planned for a later rev.")


def process(path: str, params: FlattenParams):
    """Return (flat_pattern, laser_geometry)."""
    model = load_model(path)
    fp = unfold(model, params)
    geom = build_geometry(fp, params)
    return fp, geom


def process_to_files(path: str, params: FlattenParams, out_base: str):
    """Run the pipeline and write <out_base>.svg / .dxf. Returns dict of paths."""
    fp, geom = process(path, params)
    svg_path = out_base + ".svg"
    dxf_path = out_base + ".dxf"
    with open(svg_path, "w") as f:
        f.write(to_svg(geom, params))
    with open(dxf_path, "w") as f:
        f.write(to_dxf(geom, params))
    return {"svg": svg_path, "dxf": dxf_path,
            "preview": to_preview_svg(fp, geom, params),
            "warnings": geom.warnings,
            "width": geom.width, "height": geom.height,
            "shell_face_ids": fp.shell_face_ids, "root": fp.root_fid}
