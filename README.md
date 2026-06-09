# foldable-bin-generator

Turn a bin's CAD (STEP B-rep) into a single, foldable, laser-cuttable flat
pattern (SVG + DXF) for LightBurn.

Goal: **flatten the whole bin into one part and score the fold lines**, so
you laser one piece of 1/8" cardboard and fold it up into the bin.

## Run

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

Upload a bin `.step`, tweak parameters, watch the net update (red = cut, blue =
fold/score), download SVG/DXF.

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

