# Project: Laser-cut LEGO sorter bins

Tooling to convert bin CAD into laser-cuttable flat patterns for a LEGO sorting
machine. Each revision of the converter lives in its own folder (`rev01/`, …)
because the approach is expected to iterate heavily.

## The machine & bins (domain context)

- It's a **LEGO sorting machine**. Sorted pieces drop from a funnel into bins.
- The machine has **modular layers**. Each layer has **6 sections**.
- A section holds a configurable number of bins: **1 big bin, 2, 3, or 5**.
  So a bin can be a full section, a left/right half, a left/middle/right third,
  etc. Bins use a **trapezoid geometry** to pack efficiently on the machine
  (walls are slanted, not orthogonal) — this is why the flat pattern panels are
  not simple rectangles.
- A bin has **4 sides total but no top and no back**: it's an open **trough** =
  a floor + two slanted side walls + one front wall. The **back is open** —
  that's where the funnel deposits pieces.
- The **bottom-most bin has notches ("toes") at the front** that slot into a
  special holder **bracket** on the machine. The toes **stick out past the
  floor's front edge** — like "a box with toes".

### Stock-thickness compensation (learned 2026-06-09)

The CAD wall (~1.8 mm) is thinner than the real 3.175 mm cardboard, and a
perforated fold pivots about the intact skin on the **inside** of the bend.
Measured result without compensation: bins fold up **two stock thicknesses too
wide and one too tall**, and the front wall's between-toe tabs land on the
toes. rev01 now removes a strip of `thickness · tan(fold/2)` from **each side
of every fold** and cuts the front wall's bottom edge one thickness above the
floor plane (`fold_comp_factor` / `floor_clearance_factor` in params, in units
of material thickness). Also: STEP face normals are not reliably oriented —
the exterior vs cavity shell is identified by total area (exterior is larger),
and the **outer shell is the default** (exterior dims are what the machine
cares about).

### Critical geometry consequence (learned 2026-06-08)

Because the toes protrude from the floor's front edge, the **front wall cannot
be hinged/folded off that edge** — folding it flat would land it on top of the
toes (a real ~118 mm² collision in the sample part). The front wall must instead
**fold off one of the side walls**. The unfolder discovers this automatically by
being overlap-aware (see `rev01/`). This generalises: any panel is hinged on the
neighbour that produces no collision.

## Source CAD

- We only have the CAD of the **desired finished bin** (a solid), not a flat
  pattern. The converter must reverse it into a flat sheet.
- Input lives in `stls/` (STL) and `steps/` (STEP). **Prefer STEP**: it's an
  exact B-rep (Onshape AP242 export) with planar faces + topology, so fold lines
  come straight from shared edges. STL is a triangle soup and only approximate.
- Sample part: `bin_third_left` (left-third bin). In CAD it's modelled with a
  **~1.8 mm wall thickness** and is exported in **metres**.

## Material & laser

- Real material is **1/8" cardboard = 3.175 mm** (independent of the CAD's 1.8 mm
  wall — that's just the model). Treated as a parameter.
- Output goes into **LightBurn**. We emit **SVG + DXF** with separate
  **CUT (red)** and **SCORE/FOLD (blue)** layers/colours.
- "Everything that can affect a successful laser job is a parameter" — kerf,
  kerf compensation, fold mode (score/perf/line), perforation dash/gap, fold end
  relief, material thickness, units, margins, etc. See
  `rev01/binflatten/params.py`.

## rev01 — what it does

A local Python web app: upload a bin STEP, tune parameters, preview the flat net
(cut/fold colour-coded), download SVG/DXF. Pipeline:
`STEP → parse → pick shell → overlap-aware unfold → kerf + score → SVG/DXF`.
See `rev01/README.md` for the module map and limitations.

**Verify before cutting**: `rev01/verify.py` re-folds the generated pattern in
3D (same crease model as the compensation) and compares silhouettes +
outermost dimensions against the STEP — run it after any geometry change
(`python verify.py <step> --out outputs/verify`; exit 1 = dimension off by
more than tol). The web UI has the same check ("refold check" button: 3D
view + overlay images + dimension table). One known physical deviation: the
leaning front wall's raised bottom edge costs ~1 mm of ground-level front
extent.

Run: `cd rev01 && python app.py` → http://127.0.0.1:5000

## Conventions

- Python interpreter: `/opt/homebrew/opt/python@3.11/libexec/bin/python`
  (per global CLAUDE.md; no venvs).
- Internal units are **millimetres**; conversions happen at parse (metres→mm)
  and export (mm→output units).
- New approaches go in a new `revNN/` folder; don't break older revs.
