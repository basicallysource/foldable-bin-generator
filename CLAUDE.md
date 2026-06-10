# Project: Laser-cut LEGO sorter bins

Web app that converts bin CAD (STEP) into laser-cuttable foldable flat
patterns for a LEGO sorting machine. Next.js UI + Python geometry engine,
deployed on Vercel (pushes to `main` auto-deploy to
https://foldable-bin-generator.vercel.app).

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
toes. The engine removes a strip of `thickness · tan(fold/2)` from **each side
of every fold** and cuts the front wall's bottom edge one thickness above the
floor plane (`fold_comp_factor` / `floor_clearance_factor` in params, in units
of material thickness). Also: STEP face normals are not reliably oriented —
the exterior vs cavity shell is identified by total area (exterior is larger),
and the **outer shell is the default** (exterior dims are what the machine
cares about).

### Critical geometry consequence (learned 2026-06-08)

Because the toes protrude from the floor's front edge, the **front wall cannot
be hinged/folded off that edge** — folding it flat would land it on top of the
toes (a real ~118 mm² collision). The front wall must instead **fold off one
of the side walls**. The unfolder discovers this automatically by being
overlap-aware (candidate hinges ranked by collision area, quantised +
fid-tie-broken so the choice is deterministic across machines).

## Architecture

```
app/, components/, lib/   Next.js UI (flatten / refold check / fold tester)
api/index.py              stateless Flask API → ONE Vercel Python function
api/_lib/binflatten/      the geometry ENGINE (underscore = not a function)
public/bins/              5 built-in bin STEPs (also the test corpus inputs)
tests/                    equivalence suite + frozen golden corpus
verify.py                 refold-verification CLI
```

- The API is stateless (Vercel functions share no disk): the browser holds
  the STEP file, posts it per request; SVG/DXF return inline in JSON.
- `/api/*` paths are rewritten to the Flask app (next.config.mjs); in dev the
  rewrite proxies to a local Flask on :5328.

## THE RULE: changing api/_lib/binflatten

The engine encodes hard-won fold/kerf/compensation behaviour. Follow
`EQUIVALENCE.md` for ANY change to it:

1. refold-verify every bin: `python verify.py public/bins/<bin>.step`
   (exit 1 = an outer dimension off by > tol)
2. regenerate goldens: `python tests/make_golden.py`
3. update the sha256 manifest in `tests/check_source_identical.py`
4. `python tests/check_equivalence.py` must be byte-equal locally
5. after deploy: `python tests/check_deployed.py <prod url>` (three-tier
   bar: byte / 1.5 µm numeric / geometric — cross-platform GEOS noise is
   expected, anything structural is a bug)

## Material & laser

- Real material is **1/8" cardboard = 3.175 mm** (independent of the CAD's
  ~1.8 mm wall — that's just the model). Treated as a parameter.
- Output goes into **LightBurn**: SVG + DXF with **CUT (red)**,
  **SCORE/FOLD (blue)** and optional **overlay (green)** layers.
- Calibrated defaults (2026-06-09): perforate dash 5 / gap 5 + continuous
  green overlay scored on the same crease.
- The UI applies per-file default overrides on top of the global defaults
  when a model is picked (`FILE_DEFAULT_OVERRIDES` in `lib/api.ts`, keyed by
  lower-case file name) — e.g. the half bins default to 3 corner tabs.
- "Everything that can affect a successful laser job is a parameter" — see
  `api/_lib/binflatten/params.py`.

## Run / develop

- `npm run dev` → UI on http://127.0.0.1:3000, Flask API on :5328.
- Python interpreter: `/opt/homebrew/opt/python@3.11/libexec/bin/python`
  (per global CLAUDE.md; no venvs). `requirements.txt` pins flask/numpy/shapely.
- Internal units are **millimetres**; conversions happen at parse (metres→mm)
  and export (mm→output units).
- Deploy manually with `scripts/deploy_and_verify.sh --prod` (also replays
  the golden corpus against the deployment); normally just push to `main`.
