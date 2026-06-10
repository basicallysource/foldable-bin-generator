/* Typed client for the stateless Python API (api/index.py).
   The browser keeps the uploaded STEP File and posts it with every request;
   SVG/DXF come back inline and are saved client-side as Blobs. */

export type FormValues = Record<string, string>;

export interface ProcessResult {
  error?: string;
  preview: string;
  svg: string;
  dxf: string;
  warnings: string[];
  width: number;
  height: number;
  n_panels: number;
  n_folds: number;
  root: number;
  shell_face_ids: number[];
}

export interface RefoldView {
  name: string;
  iou: number;
  svg: string;
  cad_size: number[];
  refold_size: number[];
  size_diff: number[];
}

export interface RefoldReport {
  error?: string;
  views: RefoldView[];
  extents: Record<string, { cad: number; refold: number; diff: number }>;
  warnings: string[];
  scene: RefoldScene;
}

export interface ScenePanel {
  poly: number[][];
  holes?: number[][][];
  matrix: number[];
  thickness: number;
}

export interface RefoldScene {
  panels: ScenePanel[];
  cad: { poly: number[][]; matrix: number[] }[];
  center: number[];
  size: number;
}

export interface CadScene {
  error?: string;
  cad: { poly: number[][]; matrix: number[] }[];
  center: number[];
  size: number;
}

export interface TesterResult {
  error?: string;
  preview: string;
  svg: string;
  dxf: string;
  n_cells: number;
}

export interface Defaults {
  flatten: Record<string, string | number | boolean>;
  tester: Record<string, string | number | boolean>;
}

async function postForm<T>(url: string, values: FormValues, file?: File): Promise<T> {
  const fd = new FormData();
  for (const [k, v] of Object.entries(values)) fd.append(k, v);
  if (file) fd.append("file", file, file.name);
  let r: Response;
  try {
    r = await fetch(url, { method: "POST", body: fd });
  } catch {
    throw new Error("network error — is the API running?");
  }
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (j.error) throw new Error(j.error);
  return j as T;
}

export const getDefaults = async (): Promise<Defaults> => {
  const r = await fetch("/api/defaults");
  if (!r.ok) throw new Error(`defaults: HTTP ${r.status}`);
  return r.json();
};

export const processStep = (file: File, v: FormValues) =>
  postForm<ProcessResult>("/api/process", v, file);

export const refoldStep = (file: File, v: FormValues) =>
  postForm<RefoldReport>("/api/refold", v, file);

export const testerCard = (v: FormValues) =>
  postForm<TesterResult>("/api/tester", v);

export const model3d = (file: File) =>
  postForm<CadScene>("/api/model3d", {}, file);

/** Built-in bins shipped with the app (public/bins/). */
export const BUILTIN_BINS = [
  "bin_half_left.step",
  "bin_half_right.step",
  "bin_third_left.step",
  "bin_third_center.step",
  "bin_third_right.step",
];

export async function loadBuiltinBin(name: string): Promise<File> {
  const r = await fetch(`/bins/${name}`);
  if (!r.ok) throw new Error(`could not load built-in ${name}`);
  return new File([await r.blob()], name, { type: "application/octet-stream" });
}

export function downloadText(text: string, filename: string) {
  const mime = filename.endsWith(".svg") ? "image/svg+xml" : "application/dxf";
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

/* dataclass defaults (numbers/bools) -> form-value strings, matching how the
   rev01 Jinja template rendered them into inputs */
export function toFormValues(d: Record<string, string | number | boolean>): FormValues {
  const out: FormValues = {};
  for (const [k, v] of Object.entries(d)) {
    if (typeof v === "boolean") out[k] = v ? "true" : "false";
    else out[k] = String(v);
  }
  return out;
}
