"""
All tunable parameters for a laser job live here, in one dataclass.

The philosophy (per the project brief): *everything that can affect a
successful laser cut is a parameter*, because we only have the CAD of the
desirable finished bin and must reverse it into a flat sheet job. Defaults are
chosen for 1/8" cardboard on a typical diode/CO2 laser in LightBurn.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, fields, field


MM_PER_INCH = 25.4


@dataclass
class FlattenParams:
    # ----- material ---------------------------------------------------------
    # Actual sheet you will cut. The CAD models a ~1.8 mm wall, but the real
    # material is independent; default 1/8" cardboard.
    material_thickness_mm: float = 0.125 * MM_PER_INCH  # 3.175 mm

    # ----- units / scale ----------------------------------------------------
    # The CAD/STEP is parsed and normalised to millimetres internally. Output
    # units only affect the SVG/DXF numbers + header.
    output_units: str = "mm"            # "mm" or "in"
    scale: float = 1.0                  # extra uniform scale on the flat pattern

    # ----- kerf -------------------------------------------------------------
    # Laser beam removes material of width = kerf. To hold finished dimensions,
    # outer cut contours are offset OUTWARD by kerf/2 and interior cutouts
    # (e.g. bracket notches) inward by kerf/2.
    kerf_mm: float = 0.15
    kerf_compensate: bool = True
    # Interior holes smaller than this are dropped as seam slivers (artifacts of
    # interlocking notch edges between panels). Real cutouts are larger.
    min_hole_area_mm2: float = 2.0

    # ----- fold / score lines ----------------------------------------------
    # How fold lines are emitted so the single sheet can be folded into a bin.
    #   "score"   : a single engrave/score line down the fold (LightBurn "line"
    #               layer at low power) — default for cardboard.
    #   "perf"    : dashed/perforated cut so it folds crisply.
    #   "none"    : emit fold lines on their own layer but leave treatment to
    #               the user in LightBurn.
    fold_mode: str = "score"
    # For "perf": dash + gap length along the fold (mm).
    perf_dash_mm: float = 2.0
    perf_gap_mm: float = 1.0
    # Optional: shave a relief gap at each end of a fold so thick stock folds
    # without binding at the corners (mm, 0 = off).
    fold_end_relief_mm: float = 0.0

    # ----- stock-thickness compensation -------------------------------------
    # The CAD walls (~1.8 mm) are thinner than the real stock, and a
    # perforated fold pivots about the intact skin on the inside of the bend,
    # so an uncompensated bin folds up one stock thickness too tall and two
    # too wide. Fix: at every fold, remove a strip of width
    #     fold_comp_factor * material_thickness [* tan(fold_angle/2)]
    # from EACH side of the fold line; the folded panel (and everything
    # hinged on it) slides toward its parent to stay attached. 1.0 = full
    # compensation, 0 = off.
    fold_comp_factor: float = 1.0
    # Scale the strip by tan(fold_angle/2) so shallower folds (the ~113°
    # front corner) remove proportionally less than the 90° floor folds.
    fold_comp_angle_scaled: bool = True
    # Panels that do NOT hinge on the floor (the front wall) get their bottom
    # edge cut floor_clearance_factor * material_thickness above the floor
    # plane, so they clear the real-thickness floor and its bracket toes
    # instead of carrying the CAD-thickness tabs that land on the toes.
    floor_clearance_factor: float = 1.0

    # ----- corner tabs / slots (experimental) --------------------------------
    # Lock the open corner seam: the wall placed deeper in the fold tree (the
    # front wall, which swings in last) gets `seam_tab_count` tabs sticking
    # out of its free edge; the wall it meets gets matching through-slots.
    # Tabs protrude seam_tab_depth_factor * material_thickness so they end
    # flush with the other wall's outside face. Slot centres sit
    # seam_slot_inset_factor * material_thickness behind the corner line,
    # which pulls the front wall slightly inward of its CAD plane — intended.
    # 0 tabs = feature off.
    seam_tab_count: int = 2
    seam_tab_width_mm: float = 12.0
    seam_tab_depth_factor: float = 1.0
    # Dovetail lock: each tab flares this much WIDER per side at its tip, so
    # the tip wedges into the hollow corrugation exposed at the slot's end
    # walls as it seats (the slot itself stays tab-width + clearance). Keep
    # small — it has to crush into the flutes. 0 = straight tabs.
    seam_tab_dovetail_mm: float = 0.6
    seam_slot_clearance_mm: float = 0.2   # added to slot length and width
    seam_slot_inset_factor: float = 1.5
    # The CAD's exterior front face runs to the exterior corner, i.e. THROUGH
    # the side wall's slab; trim the free edge back by
    # seam_edge_trim_factor * thickness / sin(corner angle) so it stops at the
    # side wall's inner face instead of overshooting it. Applied even when
    # seam_tab_count is 0... see _add_seam_tabs.
    seam_edge_trim_factor: float = 1.0

    # ----- shell selection / geometry --------------------------------------
    # Which surface of the bin's wall to flatten. "outer" (default) develops
    # the bin's exterior faces — the right choice for dimensional control
    # (machine fit, bracket toes) and for folding away from the scored side.
    # "inner" develops the cavity faces. Note: the thickness-compensation
    # defaults above assume the outer shell.
    shell: str = "outer"
    # Pick the flat-pattern root (the panel kept un-rotated, others fold off it).
    #   "largest" : biggest-area panel (usually the floor) — default.
    #   a face id  : force a specific STEP face id as the root.
    root: str = "largest"
    # Two faces are treated as a slab (wall) if parallel and their planes are
    # within this distance. Slightly above the CAD wall (1.8 mm) with margin.
    slab_max_thickness_mm: float = 4.0

    # ----- layout / export --------------------------------------------------
    margin_mm: float = 5.0              # margin around the flat pattern
    cut_color: str = "#ff0000"          # SVG stroke for cut layer
    score_color: str = "#0000ff"        # SVG stroke for score/fold layer
    add_labels: bool = True             # annotate panels (engrave text)

    # ----------------------------------------------------------------------- #
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FlattenParams":
        known = {f.name for f in fields(cls)}
        clean = {}
        for k, v in d.items():
            if k not in known:
                continue
            # coerce types from web form strings
            cur = cls.__dataclass_fields__[k].type
            if cur in ("float", float):
                clean[k] = float(v)
            elif cur in ("int", int):
                clean[k] = int(float(v))
            elif cur in ("bool", bool):
                clean[k] = str(v).lower() in ("1", "true", "on", "yes")
            else:
                clean[k] = v
        return cls(**clean)


def _parse_floats(s):
    """'1, 2, 3.5' -> [1.0, 2.0, 3.5]; passes through real lists."""
    if isinstance(s, (list, tuple)):
        return [float(x) for x in s]
    return [float(x) for x in str(s).replace(";", ",").split(",") if x.strip()]


@dataclass
class TesterParams:
    """Parameters for the fold/score test card (a laser-material-test analog).

    The card is a grid of small foldable coupons. Each coupon has a fold line
    rendered with a particular perforation pattern: rows sweep dash length,
    columns sweep gap length. A final 'score' column is a continuous (un-perfed)
    score line for reference. Cut and fold one chip per setting, fold it, and
    keep whichever creases cleanly in your 1/8" cardboard.
    """
    dash_values_mm: list = field(default_factory=lambda: [1.0, 2.0, 3.0, 5.0])
    gap_values_mm: list = field(default_factory=lambda: [0.5, 1.0, 1.5, 2.0])
    include_continuous: bool = True   # add a continuous-score reference column

    coupon_w_mm: float = 32.0
    coupon_h_mm: float = 42.0         # tall enough to fold by hand
    gutter_mm: float = 8.0
    margin_mm: float = 10.0
    # How far the fold line stops short of the coupon's side edges. 0 = the
    # crease runs fully edge-to-edge so the coupon folds across its whole width
    # (what you want for a fold test). Raise it only if you want an uncreased
    # margin at the ends.
    score_inset_mm: float = 0.0

    material_thickness_mm: float = 0.125 * MM_PER_INCH  # for the title note
    output_units: str = "mm"
    cut_color: str = "#ff0000"
    score_color: str = "#0000ff"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TesterParams":
        known = {f.name for f in fields(cls)}
        clean = {}
        for k, v in d.items():
            if k not in known:
                continue
            if k in ("dash_values_mm", "gap_values_mm"):
                clean[k] = _parse_floats(v)
            elif cls.__dataclass_fields__[k].type in ("float", float):
                clean[k] = float(v)
            elif cls.__dataclass_fields__[k].type in ("bool", bool):
                clean[k] = str(v).lower() in ("1", "true", "on", "yes")
            else:
                clean[k] = v
        return cls(**clean)
