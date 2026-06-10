# binflatten · rev02

rev01 (Flask + Jinja, local only) rebuilt as a **Next.js app deployable on
Vercel**. The geometry engine is **unchanged rev01 Python**, vendored
byte-for-byte at `api/_lib/binflatten/` and run as a Vercel Python serverless
function — see `EQUIVALENCE.md` for the procedure that proves (and keeps
proving) the laser output is identical.

```
app/, components/, lib/   Next.js UI (flatten tab, fold-tester tab, refold 3D)
api/index.py              stateless Flask API  →  one Vercel Python function
api/_lib/binflatten/      rev01 geometry engine, byte-identical (DO NOT EDIT)
tests/                    equivalence suite + frozen golden corpus
```

Because Vercel functions share no disk, the API is stateless: the browser
keeps the chosen STEP file and posts it with every flatten/refold request
(bin STEPs are ~50 kB; Vercel's request limit is 4.5 MB); SVG/DXF come back
inline and download client-side.

## Run locally

```bash
npm install
pip install -r requirements.txt   # flask/numpy/shapely, pinned
npm run dev                       # Next on :3000 + Flask API on :5328 (proxied)
```

## Deploy

Live: **https://rev02-lyart.vercel.app** (Vercel project `rev02`, verified
against the golden corpus 2026-06-09 — see EQUIVALENCE.md).

```bash
./scripts/deploy_and_verify.sh          # preview + golden-corpus check
./scripts/deploy_and_verify.sh --prod   # production + check
```

Note: preview deployments sit behind Vercel's deployment protection (401
without a Vercel login); production is public.

Then verify the deployment reproduces rev01's output:

```bash
python tests/check_deployed.py https://<deployment>.vercel.app
```

## Tests

```bash
python tests/check_source_identical.py   # engine untouched (sha256)
python tests/check_equivalence.py        # 57-case golden corpus, byte-equal
node /tmp/rev02_uitest/uitest.mjs <step> # headless UI e2e (see EQUIVALENCE.md)
```

The refold check (button in the UI, or rev01's `verify.py` against the same
engine) still applies before cutting anything — one known physical deviation:
the leaning front wall's raised bottom edge costs ~1 mm of ground-level front
extent.
