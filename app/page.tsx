"use client";

/* binflatten rev02 — Next.js front end over the unchanged Python engine.

   The page is one shared store (the chosen STEP File + parameters + results
   live here and persist across tabs). Flattening is automatic: picking a
   model or touching any parameter re-runs the pipeline (debounced); there is
   no flatten button. The refold check is its own tab, enabled once a model
   is loaded. */

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  BUILTIN_BINS,
  CadScene,
  Defaults,
  FormValues,
  ProcessResult,
  RefoldReport,
  TesterResult,
  downloadText,
  getDefaults,
  loadBuiltinBin,
  model3d,
  processStep,
  refoldStep,
  testerCard,
  toFormValues,
} from "@/lib/api";
import { CheckField, Fieldset, Hint, SelectField, TextField } from "@/components/fields";
import Modal from "@/components/Modal";

const RefoldViewer = dynamic(() => import("@/components/RefoldViewer"), { ssr: false });
const CadViewer = dynamic(() => import("@/components/CadViewer"), { ssr: false });

type Status = { msg: string; cls: string };
type Tab = "flatten" | "refold" | "tester";

function useFormValues() {
  const [values, setValues] = useState<FormValues>({});
  const set = useCallback(
    (k: string, v: string) => setValues((p) => ({ ...p, [k]: v })),
    []
  );
  return { values, set, setValues };
}
type FormApi = ReturnType<typeof useFormValues>;

export default function Page() {
  const [tab, setTab] = useState<Tab>("flatten");
  const [ready, setReady] = useState(false);
  const flatten = useFormValues();
  const tester = useFormValues();

  // ---- shared model + results store --------------------------------------
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ProcessResult | null>(null);
  const [status, setStatus] = useState<Status>({ msg: "", cls: "" });
  const [refold, setRefold] = useState<RefoldReport | null>(null);
  const refoldKey = useRef<string>("");
  const seq = useRef(0);

  useEffect(() => {
    getDefaults()
      .then((d: Defaults) => {
        flatten.setValues(toFormValues(d.flatten));
        tester.setValues(toFormValues(d.tester));
        setReady(true);
      })
      .catch((e) => console.error("defaults:", e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pick = useCallback((f: File | undefined | null) => {
    if (!f) return;
    if (!/\.(step|stp)$/i.test(f.name)) {
      setStatus({ msg: `unsupported '${f.name}' — needs a .step/.stp`, cls: "status-err" });
      return;
    }
    setFile(f);
    setStatus({ msg: `loaded ${f.name}`, cls: "" });
  }, []);

  // ---- automatic flatten: model picked or any parameter touched ----------
  useEffect(() => {
    if (!ready || !file) return;
    const my = ++seq.current;
    const t = setTimeout(async () => {
      setStatus({ msg: "flattening", cls: "status-busy" });
      try {
        const r = await processStep(file, flatten.values);
        if (my !== seq.current) return; // a newer run superseded this one
        setResult(r);
        setStatus(
          r.warnings?.length
            ? { msg: "⚠ " + r.warnings.join(" · "), cls: "status-warn" }
            : { msg: "up to date ✓", cls: "status-ok" }
        );
      } catch (e) {
        if (my !== seq.current) return;
        setResult(null);
        setStatus({ msg: String((e as Error).message), cls: "status-err" });
      }
    }, 350);
    return () => clearTimeout(t);
  }, [ready, file, flatten.values]);

  // ---- refold: runs when its tab is open and inputs changed ---------------
  const [refoldBusy, setRefoldBusy] = useState(false);
  useEffect(() => {
    if (tab !== "refold" || !file || !ready) return;
    const key =
      `${file.name}/${file.size}/${file.lastModified}/` + JSON.stringify(flatten.values);
    if (key === refoldKey.current) return;
    let stale = false;
    const t = setTimeout(async () => {
      setRefoldBusy(true);
      try {
        const r = await refoldStep(file, flatten.values);
        if (stale) return;
        refoldKey.current = key;
        setRefold(r);
      } catch (e) {
        if (!stale) setStatus({ msg: String((e as Error).message), cls: "status-err" });
      } finally {
        if (!stale) setRefoldBusy(false);
      }
    }, 250);
    return () => {
      stale = true;
      clearTimeout(t);
    };
  }, [tab, file, ready, flatten.values]);

  return (
    <div className="frame">
      <header className="bar">
        <div className="brand">
          <h1>
            bin<span className="dim">flatten</span>
          </h1>
        </div>
        <span className="subtitle">STEP → foldable flat pattern → LightBurn</span>
        <nav className="tabs">
          <button
            className={`tab${tab === "flatten" ? " active" : ""}`}
            onClick={() => setTab("flatten")}
          >
            flatten bin
          </button>
          <button
            className={`tab${tab === "refold" ? " active" : ""}`}
            disabled={!file}
            title={file ? undefined : "load a model first"}
            onClick={() => setTab("refold")}
          >
            refold check
          </button>
          <button
            className={`tab${tab === "tester" ? " active" : ""}`}
            onClick={() => setTab("tester")}
          >
            fold tester
          </button>
        </nav>
      </header>
      {!ready ? (
        <div className="view" style={{ flex: 1 }}>
          <div className="placeholder">loading parameter defaults…</div>
        </div>
      ) : tab === "flatten" ? (
        <FlattenPane api={flatten} file={file} pick={pick} result={result} status={status} />
      ) : tab === "refold" ? (
        <RefoldPane report={refold} busy={refoldBusy} file={file} />
      ) : (
        <TesterPane api={tester} />
      )}
    </div>
  );
}

/* ============================== FLATTEN ================================= */

function FlattenPane({
  api,
  file,
  pick,
  result,
  status,
}: {
  api: FormApi;
  file: File | null;
  pick: (f: File | undefined | null) => void;
  result: ProcessResult | null;
  status: Status;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [stageDrag, setStageDrag] = useState(false);
  const [preview3d, setPreview3d] = useState<CadScene | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);

  const openPreview = async () => {
    if (!file) return;
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreview3d(null);
    try {
      setPreview3d(await model3d(file));
    } catch (e) {
      console.error(e);
    } finally {
      setPreviewLoading(false);
    }
  };

  const dropProps = (set: (b: boolean) => void) => ({
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      set(true);
    },
    onDragLeave: () => set(false),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      set(false);
      pick(e.dataTransfer.files?.[0]);
    },
  });

  return (
    <div className="wrap">
      <form className="controls" onSubmit={(e) => e.preventDefault()}>
        <Fieldset idx="1 ·" title="model">
          <select
            className="builtin"
            value=""
            onChange={async (e) => {
              const name = e.target.value;
              if (!name) return;
              try {
                pick(await loadBuiltinBin(name));
              } catch (err) {
                console.error(err);
              }
            }}
          >
            <option value="">built-in bins…</option>
            {BUILTIN_BINS.map((b) => (
              <option key={b} value={b}>
                {b.replace(/\.step$/, "").replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <div
            className={`filedrop${dragOver ? " over" : ""}`}
            onClick={() => fileInput.current?.click()}
            {...dropProps(setDragOver)}
          >
            {file ? (
              <>
                <div className="name">{file.name}</div>
                <div className="sub">{(file.size / 1024).toFixed(0)} kB — click to replace</div>
              </>
            ) : (
              <>
                or drop your own <b>.step</b> here
                <div className="sub">click to browse</div>
              </>
            )}
            <input
              ref={fileInput}
              type="file"
              accept=".step,.stp"
              hidden
              onChange={(e) => pick(e.target.files?.[0])}
            />
          </div>
          <button type="button" className="ghost" disabled={!file} onClick={openPreview}>
            preview 3D model
          </button>
        </Fieldset>

        <Fieldset idx="2 ·" title="material">
          <TextField api={api} name="material_thickness_mm" label="thickness (mm)" />
          <Hint>1/8&quot; cardboard = 3.175 mm</Hint>
          <SelectField api={api} name="output_units" label="output units"
            options={[["mm", "mm"], ["in", "in"]]} />
          <TextField api={api} name="scale" label="extra scale" />
        </Fieldset>

        <Fieldset idx="3 ·" title="kerf">
          <CheckField api={api} name="kerf_compensate" label="compensate" />
          <TextField api={api} name="kerf_mm" label="kerf (mm)" />
          <TextField api={api} name="min_hole_area_mm2" label="min hole (mm²)" />
        </Fieldset>

        <Fieldset idx="4 ·" title="fold / score">
          <SelectField api={api} name="fold_mode" label="mode"
            options={[["perf", "perforate"], ["score", "score"], ["none", "line only"]]} />
          <TextField api={api} name="perf_dash_mm" label="perf dash (mm)" />
          <TextField api={api} name="perf_gap_mm" label="perf gap (mm)" />
          <TextField api={api} name="fold_end_relief_mm" label="end relief (mm)" />
          <CheckField api={api} name="overlay_score" label="green solid overlay" />
          <Hint>
            continuous green line (own layer) on every fold — score pass + perf on the same crease
          </Hint>
        </Fieldset>

        <Fieldset idx="5 ·" title="thickness comp">
          <TextField api={api} name="fold_comp_factor" label="fold comp ×t" />
          <Hint>strip removed each side of every fold; 1 = one stock thickness, 0 = off</Hint>
          <SelectField api={api} name="fold_comp_angle_scaled" label="angle scaled"
            options={[["true", "yes (×tan(fold/2))"], ["false", "no"]]} />
          <TextField api={api} name="floor_clearance_factor" label="floor clear ×t" />
          <Hint>front wall bottom cut this far above the floor plane (clears the toes)</Hint>
        </Fieldset>

        <Fieldset idx="6 ·" title="corner tabs">
          <TextField api={api} name="seam_tab_count" label="tab count" />
          <Hint>tabs on the front wall&apos;s free edge + slots in the far side wall; 0 = off</Hint>
          <TextField api={api} name="seam_tab_width_mm" label="tab width (mm)" />
          <TextField api={api} name="seam_tab_depth_factor" label="tab depth ×t" />
          <TextField api={api} name="seam_tab_dovetail_mm" label="dovetail (mm)" />
          <Hint>
            tab tip flares this much per side — wedges into the hollow flutes at the slot ends; 0 =
            straight
          </Hint>
          <TextField api={api} name="seam_slot_clearance_mm" label="slot clearance (mm)" />
          <TextField api={api} name="seam_slot_inset_factor" label="slot inset ×t" />
          <Hint>inset pulls the front wall inward so the tabs engage</Hint>
        </Fieldset>

        <Fieldset idx="7 ·" title="geometry">
          <SelectField api={api} name="shell" label="shell"
            options={[["outer", "outer"], ["inner", "inner"]]} />
          <TextField api={api} name="root" label="root face" />
          <Hint>&quot;largest&quot; = auto floor, or a STEP face id</Hint>
          <TextField api={api} name="slab_max_thickness_mm" label="slab max (mm)" />
        </Fieldset>

        <Fieldset idx="8 ·" title="engrave">
          <CheckField api={api} name="add_settings_label" label="settings label" />
          <Hint>
            engraves name, date/time, perf dash/gap, thickness, kerf, comp… line by line on the
            floor panel (SVG text)
          </Hint>
          <TextField api={api} name="label_color" label="label color" />
          <TextField api={api} name="label_font_mm" label="label font (mm)" />
        </Fieldset>
      </form>

      <div className="stage">
        <div className={`view droppable${stageDrag ? " over" : ""}`} {...dropProps(setStageDrag)}>
          {result ? (
            <div className="svgfill" dangerouslySetInnerHTML={{ __html: result.preview }} />
          ) : (
            <div className="placeholder">
              drop a bin <b>.step</b> anywhere here
              <br />
              <span style={{ fontSize: 11 }}>or pick a built-in bin on the left</span>
            </div>
          )}
        </div>
        <div className="statusbar">
          <span className={`grow ${status.cls}`}>{status.msg}</span>
          {result && (
            <span>
              {result.width} × {result.height} mm · {result.n_panels} panels · {result.n_folds}{" "}
              folds
            </span>
          )}
          <span>
            <span className="swatch" style={{ background: "var(--cut)" }} />
            cut
          </span>
          <span>
            <span className="swatch" style={{ background: "var(--score)" }} />
            fold/score
          </span>
          {result && (
            <>
              <a className="dl-big" onClick={() => downloadText(result.svg, "bin_flat.svg")}>
                ⬇ SVG
              </a>
              <a className="dl-big" onClick={() => downloadText(result.dxf, "bin_flat.dxf")}>
                ⬇ DXF
              </a>
            </>
          )}
        </div>
      </div>

      {previewOpen && file && (
        <Modal title={file.name} onClose={() => setPreviewOpen(false)}>
          {previewLoading ? (
            <div className="modal-loading">parsing STEP…</div>
          ) : preview3d ? (
            <CadViewer scene3={preview3d} />
          ) : (
            <div className="modal-loading status-err">could not parse the model</div>
          )}
        </Modal>
      )}
    </div>
  );
}

/* ============================== REFOLD ================================== */

function RefoldPane({
  report,
  busy,
  file,
}: {
  report: RefoldReport | null;
  busy: boolean;
  file: File | null;
}) {
  return (
    <div className="stage" style={{ overflowY: "auto" }}>
      {busy && (
        <div className="refold">
          <span className="status-busy">refolding {file?.name} in 3D</span>
        </div>
      )}
      {report && !busy ? (
        <div className="refold">
          <h2>refold verification — folded sheet vs CAD · {file?.name}</h2>
          <table>
            <thead>
              <tr>
                <th>outer dimension</th>
                <th>CAD</th>
                <th>refold</th>
                <th>diff</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(report.extents).map(([k, d]) => {
                const bad = Math.abs(d.diff) > 1.5;
                return (
                  <tr key={k}>
                    <td>{k}</td>
                    <td>{d.cad}</td>
                    <td>{d.refold}</td>
                    <td style={{ color: bad ? "var(--cut)" : "var(--ok)" }}>
                      {d.diff > 0 ? "+" : ""}
                      {d.diff} mm
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="iou">
            silhouette IoU:{" "}
            {report.views.map((v) => `${v.name.split(" ")[0]} ${v.iou.toFixed(3)}`).join(" · ")}
          </div>
          {report.warnings?.length > 0 && (
            <div className="warnline">⚠ {report.warnings.join(" · ")}</div>
          )}
          <div className="sils">
            {report.views.map((v) => (
              <div key={v.name} dangerouslySetInnerHTML={{ __html: v.svg }} />
            ))}
          </div>
          <RefoldViewer scene3={report.scene} />
        </div>
      ) : null}
    </div>
  );
}

/* ============================== TESTER ================================== */

function TesterPane({ api }: { api: FormApi }) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>({ msg: "", cls: "" });
  const [result, setResult] = useState<TesterResult | null>(null);

  const generate = async () => {
    setBusy(true);
    setStatus({ msg: "generating", cls: "status-busy" });
    try {
      const r = await testerCard(api.values);
      setResult(r);
      setStatus({ msg: "done ✓", cls: "status-ok" });
    } catch (e) {
      setResult(null);
      setStatus({ msg: String((e as Error).message), cls: "status-err" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wrap">
      <form className="controls" onSubmit={(e) => e.preventDefault()}>
        <Fieldset title="fold / score sweep">
          <label className="row">
            <span>dash lengths (mm)</span>
          </label>
          <TextField api={api} name="dash_values_mm" wide />
          <label className="row">
            <span>gap lengths (mm)</span>
          </label>
          <TextField api={api} name="gap_values_mm" wide />
          <Hint>rows = dash · cols = gap · one coupon per combo</Hint>
          <CheckField api={api} name="include_continuous" label="continuous-score column" />
          <CheckField api={api} name="overlay_score" label="green solid overlay" />
          <Hint>
            adds a continuous green line (own layer) on every perf crease — run score + perf on the
            same fold
          </Hint>
          <CheckField api={api} name="show_title" label="title line" />
          <CheckField api={api} name="show_legends" label="axis legends" />
          <Hint>per-coupon tags always stay; turning these off compacts the card</Hint>
        </Fieldset>

        <Fieldset title="coupon">
          <TextField api={api} name="coupon_w_mm" label="width (mm)" />
          <TextField api={api} name="coupon_h_mm" label="height (mm)" />
          <TextField api={api} name="gutter_mm" label="gutter (mm)" />
          <TextField api={api} name="margin_mm" label="margin (mm)" />
          <TextField api={api} name="score_inset_mm" label="score inset (mm)" />
          <Hint>0 = crease runs edge-to-edge (folds full width)</Hint>
        </Fieldset>

        <Fieldset title="context">
          <TextField api={api} name="material_thickness_mm" label="stock thickness (mm)" />
          <SelectField api={api} name="output_units" label="output units"
            options={[["mm", "mm"], ["in", "in"]]} />
          <TextField api={api} name="label_color" label="text color" />
          <Hint>power/speed are set per layer in LightBurn — this sweeps geometry (dash×gap)</Hint>
        </Fieldset>

        <button type="button" className="primary" disabled={busy} onClick={generate}>
          generate card ▸
        </button>
      </form>

      <div className="stage">
        <div className="view">
          {result ? (
            <div className="svgfill" dangerouslySetInnerHTML={{ __html: result.preview }} />
          ) : (
            <div className="placeholder">
              press <b>generate card</b> for a fold-test grid
            </div>
          )}
        </div>
        <div className="statusbar">
          <span className={`grow ${status.cls}`}>{status.msg}</span>
          {result && <span>{result.n_cells} coupons</span>}
          <span>
            <span className="swatch" style={{ background: "var(--cut)" }} />
            cut
          </span>
          <span>
            <span className="swatch" style={{ background: "var(--score)" }} />
            fold/score
          </span>
          {result && (
            <>
              <a className="dl-big" onClick={() => downloadText(result.svg, "fold_test_card.svg")}>
                ⬇ SVG
              </a>
              <a className="dl-big" onClick={() => downloadText(result.dxf, "fold_test_card.dxf")}>
                ⬇ DXF
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
