# binflatten · rev01

Turn a bin's CAD (STEP B-rep) into a single, foldable, laser-cuttable flat
pattern (SVG + DXF) for LightBurn.

rev01 goal: **flatten the whole bin into one part and score the fold lines**, so
you laser one piece of 1/8" cardboard and fold it up into the bin.

## Run

```bash
/opt/homebrew/opt/python@3.11/libexec/bin/python -m pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

Upload a bin `.step`, tweak parameters, watch the net update (red = cut, blue =
fold/score), download SVG/DXF.

## How it works

```
STEP B-rep ──▶ parse faces+edges ──▶ pick shell ──▶ overlap-aware unfold ──▶ kerf+score ──▶ SVG/DXF
 step_io.py        step_io.py        unfold.py          unfold.py            export.py     export.py
```

1. **Parse** (`step_io.py`) — a small pure-Python STEP reader. The bin is mostly
   planar faces with exact plane equations plus full topology (which faces share
   an edge). No CAD kernel needed. Units auto-detected (Onshape exports metres).
2. **Shell** (`unfold.py`) — each wall is a thin slab (a parallel inner/outer
   face pair ~1.8 mm apart in the CAD). We keep one connected shell = floor +
   walls. Default is the **outer** (exterior) shell — the exterior faces are
   the larger component (face normals in these exports are not reliably
   oriented, so total area tells the shells apart). The exterior is what the
   machine fit and the bracket toes care about.
3. **Unfold** (`unfold.py`) — pick the floor (the hub adjacent to the most
   walls), then place each wall by hinging it on an already-placed neighbour.
   It is **overlap-aware**: it tries every candidate hinge and rejects any that
   makes the flap collide. This is what makes the **front wall fold off a side
   wall** instead of the floor's protruding bracket "toes". A panel with no
   collision-free hinge is emitted as a separate island with a warning.
   Then **stock-thickness compensation** runs: a perforated fold pivots about
   the intact skin on the inside of the bend, so an uncompensated bin folds up
   one stock thickness too tall and two too wide. A strip of
   `fold_comp_factor * thickness * tan(fold/2)` is removed from *each* side of
   every fold (panels slide toward the floor to stay attached), and panels not
   hinged on the floor (the front wall) get their bottom edge cut
   `floor_clearance_factor * thickness` above the floor plane so they clear the
   real-thickness floor and its toes (this also removes the CAD-thickness tabs
   between the toes that used to land on them).
4. **Export** (`export.py`) — union the panels, kerf-compensate (offset the
   solid outward by kerf/2 so the part holds nominal size), emit CUT (red) and
   SCORE/FOLD (blue) on separate layers/colours for LightBurn.

## Refold verification

The fold-up is simulated and checked against the CAD, so you don't have to
cut cardboard to find a geometry bug:

* **Web UI**: press **refold check** after flattening — folded 3D view
  (three.js, internet needed for the CDN), silhouette overlays (green = CAD,
  red = refold) and the outermost-dimension table work per uploaded STEP.
* **CLI** (what an agent / CI can run):

  ```bash
  python verify.py "../steps/0_bins - bin_third_left.step" --out outputs/verify \
      --set fold_comp_factor=1.0
  ```

  writes `view_*.svg` overlays + `metrics.json`, prints IoU / dimension
  diffs, exit code 1 if any outer dimension deviates more than `--tol`
  (default 1.5 mm).

The simulator uses the same crease model the compensation assumes (pivot
`fold_comp_factor·t` above the plotted face, panels = slabs one stock
thickness deep, folded to the true CAD dihedral, crease wedges filling the
corners). Known, physical deviation: a leaning front wall whose bottom edge
sits one stock thickness up (on the real floor/toes) pulls the ground-level
front extent back ~1 mm vs CAD.

## Parameters

Everything tunable lives in `binflatten/params.py` (`FlattenParams`). Highlights:
material thickness (default 1/8" = 3.175 mm), kerf + compensation, fold mode
(score / perforate / line-only), perforation dash/gap, fold end relief,
fold-comp + floor-clearance factors (stock-thickness compensation, in units of
material thickness), corner tab/slot count + sizing (experimental seam lock),
shell side, root face, layout margin, labels, units.

## Known limitations (candidates for rev02+)

- Corner seams: the front wall's free edge is trimmed back one wall thickness
  (the CAD exterior face runs THROUGH the far side wall's slab — uncut it
  overshoots the side wall), then gets experimental tabs that engage
  through-slots in the far side wall (`seam_tab_count` etc.); the slot inset
  deliberately pulls the front wall a little inside its CAD plane. The
  floor-front seam is still open (the toes live there). No glue tabs yet.
- Thickness compensation assumes the fold pivots about the intact skin on the
  inside of the bend (matches measured behaviour for perforated 1/8"
  cardboard); the factors are parameters — calibrate per material.
- STL input not yet supported (STEP only — it carries exact topology).
- Curved edges are chorded (fine here; only tiny fillet faces are curved).
