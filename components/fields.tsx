"use client";

/* Small form primitives. Every field writes a STRING into the shared values
   map, which is posted verbatim as form-data — the same wire format rev01's
   <form> produced, so the Python side coerces identically. */

import { FormValues } from "@/lib/api";

export interface FieldsApi {
  values: FormValues;
  set: (k: string, v: string) => void;
}

export function Fieldset({
  idx,
  title,
  children,
}: {
  idx?: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset>
      <legend>
        {idx && <span className="idx">{idx}</span>}
        {title}
      </legend>
      {children}
    </fieldset>
  );
}

export function Hint({ children }: { children: React.ReactNode }) {
  return <div className="hint">{children}</div>;
}

export function TextField({
  api,
  name,
  label,
  wide,
}: {
  api: FieldsApi;
  name: string;
  label?: string;
  wide?: boolean;
}) {
  const input = (
    <input
      type="text"
      className={wide ? "wide" : undefined}
      value={api.values[name] ?? ""}
      onChange={(e) => api.set(name, e.target.value)}
    />
  );
  if (!label) return input;
  return (
    <label className="row">
      <span>{label}</span>
      {input}
    </label>
  );
}

export function CheckField({
  api,
  name,
  label,
}: {
  api: FieldsApi;
  name: string;
  label: string;
}) {
  return (
    <label className="row">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={api.values[name] === "true"}
        onChange={(e) => api.set(name, e.target.checked ? "true" : "false")}
      />
    </label>
  );
}

export function SelectField({
  api,
  name,
  label,
  options,
}: {
  api: FieldsApi;
  name: string;
  label: string;
  options: [value: string, text: string][];
}) {
  return (
    <label className="row">
      <span>{label}</span>
      <select value={api.values[name] ?? options[0][0]} onChange={(e) => api.set(name, e.target.value)}>
        {options.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
}
