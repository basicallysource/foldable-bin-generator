# foldable-bin-generator

Turn a bin's CAD (STEP B-rep) into a single, foldable, laser-cuttable flat
pattern (SVG + DXF) for LightBurn — laser one piece of 1/8" cardboard and
fold it up into a sorting-machine bin.

Live: **[https://bin-gen.basically.website](https://bin-gen.basically.website/)** — pushes to `main` auto-deploy.

```
app/, components/, lib/   Next.js UI (flatten, refold check, fold tester)
api/index.py              stateless Flask API  →  one Vercel Python function
api/_lib/binflatten/      geometry engine (DO NOT EDIT casually — see below)
public/bins/              the 5 built-in bin STEPs, selectable in the UI
tests/                    equivalence suite + frozen golden corpus
verify.py                 refold-verification CLI (run before cutting)
```

The browser keeps the chosen STEP and posts it with every request (bin STEPs
are ~50 kB; Vercel's request cap is 4.5 MB); SVG/DXF come back inline and
download client-side. Flattening re-runs automatically on every model pick
and parameter change.

## Run locally

```bash
npm install
pip install -r requirements.txt   # flask/numpy/shapely, pinned
npm run dev                       # Next on :3000 + Flask API on :5328 (proxied)
```

## Changing the geometry engine

The engine (`api/_lib/binflatten/`) is the carefully-calibrated part: fold
compensation, overlap-aware unfolding, seam tabs, kerf. Any change must
follow `EQUIVALENCE.md`: refold-verify every bin (`python verify.py
public/bins/<bin>.step`), regenerate the golden corpus
(`python tests/make_golden.py`), update the sha256 manifest in
`tests/check_source_identical.py`, and re-verify the deployment:

```bash
python tests/check_source_identical.py   # engine untouched? (sha256)
python tests/check_equivalence.py        # 48-case corpus, byte-equal locally
python tests/check_deployed.py https://foldable-bin-generator.vercel.app
```

Known physical deviation: the leaning front wall's raised bottom edge costs
~1 mm of ground-level front extent (it sits on the real floor/toes).

## Simple STL bins

Upload an STL in millimetres with its floor down, open roof and open back.
This mode supports two planar side walls and either one flat front panel or
three connected front facets. It retains the floor outline, hinges both sides
on the floor, and attaches the front chain to one side only. The other seam is
left unattached: no tabs, slots, flaps or dovetails are generated.

The default stock is 3.175 mm cardboard. Fold compensation reuses the STEP
engine's thickness correction; folds use blue 5 mm cuts / 5 mm gaps and the
outline is red. The optional green overlay is disabled for STL files.
Material compression and the unattached seam still need a physical sample.

For batch exports, using Python 3.11 with `requirements.txt` installed:

```sh
python scripts/simple_stl.py open-bin.stl --out outputs/open-bin --thickness 3.175
```

This writes SVG, DXF, a preview SVG and a JSON receipt with input hash, source
panels, hinge tree, compensated flat panels and output bounds. Non-horizontal
floors, roofs, curved fronts and structural holes are rejected. STL coordinates
have no unit metadata; this mode explicitly interprets them as millimetres.
Coplanar facet merging uses a 0.00001 mm grid and 0.0001 mm numerical closure.
For stock thinner than a native wall offset, the simple mode extends the
parallel child hinge edge back to the nominal joint before trimming. This
preserves a continuous hinge without changing the source mesh or STEP engine.
The simple mode clips each fold to its two compensated panels and pads the
viewBox around the actual kerf contour.

The original STEP package remains byte-identical. Run
`python tests/check_simple_stl.py` for the synthetic STL fixtures and
`python tests/check_step_unchanged.py --baseline BASE_COMMIT` to compare all
48 existing STEP cases on one runtime. The historical golden checks remain
unchanged; their strict byte comparison can expose cross-platform GEOS/libm
rounding differences.

Simple STL exports include open red release cuts wherever compensated panels
meet outside the hinge tree. These are through-cut paths in SVG and DXF, not
fold marks: they release the front from the floor at the small corner bridge.
The JSON receipt lists their panel IDs and endpoints as `cut_segments`.
Adjacency recognition includes the existing exporter's 0.001 mm numerical
weld; it does not enlarge a notch or trim away a panel. The nominal panel
outlines remain unchanged except for the physical laser kerf at each slit.
