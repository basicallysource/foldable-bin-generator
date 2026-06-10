# Geometry-equivalence procedure (rev01 → rev02)

The whole point of rev02 is a Vercel-deployable web shell **without touching
the geometry**. The fold compensation, overlap-aware unfolding, seam tabs,
kerf offsets etc. were hard to get right; this document is the procedure that
proves they survived the port — and the procedure to re-run whenever either
side changes.

## Why no rewrite was needed

rev01's engine (`binflatten/`) is pure Python on numpy + shapely with a
custom STEP parser — no CAD kernel, no native deps beyond numpy/shapely
wheels. Vercel runs Python serverless functions, so the engine is **vendored
byte-for-byte** at `api/_lib/binflatten/` and executed unchanged. Only
transport changed: rev01 stored uploads on disk under a token; Vercel
functions have no shared disk, so the browser holds the STEP file and posts
it with each request, and SVG/DXF return inline in the JSON.

## The three guarantees

### 1. Source identity — `tests/check_source_identical.py`

sha256 of every vendored `api/_lib/binflatten/*.py` must equal the rev01
package (live copy at the repo root while it exists, else the frozen manifest
embedded in the script). **Run after ANY change under `api/`.** If you
intentionally change the engine, you are no longer porting — regenerate
goldens from a verified state and update the manifest consciously.

### 2. Behavioural identity on this machine — golden corpus

* `tests/make_golden.py` drives **rev01's real Flask routes**
  (upload → process → download, refold, tester) over a corpus of
  **all 6 STEP files × 7 parameter sets** (fold modes, perforation, overlay,
  end relief, compensation on/off, kerf on/off, inches+scale, seam
  tabs/dovetails on/off, thick stock, settings label) plus 12 refold-report
  cases and 3 tester cards — 57 cases. Responses are frozen under
  `tests/golden/` (timestamps in the engraved settings block are normalised).
* `tests/check_equivalence.py` replays every case against **rev02's Flask app
  in-process** and requires **byte equality** of SVG / DXF / preview and
  exact equality of dimensions, panel/fold counts, root face, shell face ids,
  warnings, and the full refold JSON (silhouettes, IoU, extents, 3D scene).

Same machine, same interpreter, same libs ⇒ any difference is a transport
bug, not float noise. Result on 2026-06-09: **57/57 byte-equal.**

### 3. Deployment identity — `tests/check_deployed.py <url>`

Replays the same corpus over real HTTP against the deployed app (or
`http://127.0.0.1:3000` in dev, which also exercises the Next.js proxy).
The deployment runs a different CPU/Python/numpy/shapely-GEOS build, so each
output is accepted at the strictest tier it reaches:

* `ok=` byte-equal (timestamps normalised);
* `ok~` identical text skeleton, every number within **1.5 µm** — the same
  laser job (kerf is 150 µm);
* `ok@` geometric equality: every ring matches its golden ring within a
  hairline symmetric-difference area, every score segment within 1.5 µm,
  all text identical, all scalar metrics exactly equal. This tier exists
  because GEOS builds legitimately disagree about emitting a vertex that is
  nanometres from collinear (observed: 13 nm) — same cut, different text.

Structural differences, missing entities, or larger deviations fail hard.
`requirements.txt` pins numpy/shapely to the golden-corpus versions.

## Portability fixes the first deployment surfaced (2026-06-09)

Deploying exposed two ways rev01's output depended on the machine, not the
logic. Both were fixed in the ENGINE (rev01 + vendored copy in lockstep, per
the intentional-change procedure: `verify.py` sweep over all 6 bins, goldens
regenerated, manifest updated):

1. **Hinge tie-break** (`unfold.py`): candidate placements were ranked by
   raw overlap area; all collision-free candidates sit within float noise of
   zero, so the chosen hinge depended on the CPU/library build (the half
   bins' front wall folded off a different side wall on Vercel than locally).
   Now the overlap is quantised to 1e-3 mm² (real collisions are ~118 mm²)
   with ties broken by face id — same net on every machine.
2. **Canonical serialisation** (`export.py`): GEOS builds order union
   pieces/holes differently and start rings at different vertices, and a
   union can leave a near-collinear debris vertex. Cut loops are now emitted
   in sorted order, CCW, from the lexicographically smallest vertex, after a
   0.1 µm `simplify`. (A 13 nm debris vertex can still survive on one build —
   that's what the `ok@` tier is for.)

Result on 2026-06-09 against https://rev02-lyart.vercel.app:
**57/57 pass — 14 `ok=`, 42 `ok~`, 1 `ok@`** — plus the headless-browser
end-to-end test against the live site (flatten → preview → downloads →
refold table/3D → tester card) with numbers identical to local.

## When to run what

| change | run |
|---|---|
| anything under `rev02/api/` | 1 then 2 |
| frontend only (`app/`, `components/`, `lib/`) | 2 (cheap, catches form-field drift) + the UI test |
| new deployment | 3 against the deployment URL |
| intentional engine change | make the change in ONE place, re-verify with `verify.py` (refold check), regenerate goldens, update the manifest in `check_source_identical.py` |

```bash
# from rev02/
python tests/check_source_identical.py
python tests/make_golden.py        # only to (re)freeze the reference (needs rev01 at repo root)
python tests/check_equivalence.py
python tests/check_deployed.py https://<deployment>.vercel.app
```

(`python` = `/opt/homebrew/opt/python@3.11/libexec/bin/python` per project
convention.)

## What the corpus covers / does not cover

Covered: every parameter that shapes geometry (`fold_mode`, perf dash/gap,
`overlay_score`, `fold_end_relief_mm`, `fold_comp_factor`,
`fold_comp_angle_scaled`, `floor_clearance_factor`, `kerf_compensate`,
`kerf_mm`, `material_thickness_mm`, seam tab count/width/depth/dovetail/
clearance/inset, `output_units`, `scale`, labels and the engraved settings
block) across every bin STEP in the repo, plus the refold 3-D verification
output and the fold-tester card generator.

Not covered: `shell=inner` and explicit `root=<face id>` (exercise manually
if you start using them), STEP files outside this repo, and concurrent-
request behaviour. The UI itself is covered by the Playwright script
described in README (form → preview → downloads → refold → tester).
