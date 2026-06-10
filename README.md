# foldable-bin-generator

Turn a bin's CAD (STEP B-rep) into a single, foldable, laser-cuttable flat
pattern (SVG + DXF) for LightBurn — laser one piece of 1/8" cardboard and
fold it up into a sorting-machine bin.

Live: **[https://foldable-bin-generator.vercel.app](https://bin-gen.basically.website/)** — pushes to `main` auto-deploy.

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
