// Shared client-side helpers. Every number on the site comes from /cecl/data/*.json.

export function base(): string {
  return document.body.dataset.base || "";
}

export function dataUrl(path: string): string {
  return `${base()}/data/${path}`;
}

const cache = new Map<string, Promise<any>>();
export function loadJson<T = any>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(dataUrl(path)).then((r) => {
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r.json();
      }),
    );
  }
  return cache.get(path)!;
}

export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export const fmtInt = (n: number | null | undefined) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
export const pct = (p: number | null | undefined, d = 2) => (p == null ? "–" : `${(p * 100).toFixed(d)}%`);
export const fixed = (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toFixed(d));

// One rounding rule for $ millions everywhere (as on the Bank Health site): $X.Xbn from $1bn,
// $X.Xm from $1m, whole thousands below. Rounds halves up.
const halfUp = (x: number, d: number) => (Math.round(x * 10 ** d + 1e-9) / 10 ** d).toFixed(d);
export function money(m: number | null | undefined): string {
  if (m == null) return "–";
  const sign = m < 0 ? "−" : "";
  const a = Math.abs(m);
  if (a >= 1000) return `${sign}$${halfUp(a / 1000, 1)}bn`;
  if (a >= 1) return `${sign}$${halfUp(a, 1)}m`;
  return `${sign}$${halfUp(a * 1000, 0)}k`;
}

export function signedMoney(m: number): string {
  return `${m > 0 ? "+" : ""}${money(m)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function asOfText(o: { dataAsOf: { reserveDate: string; updated: string } }): string {
  const r = new Date(`${o.dataAsOf.reserveDate}T12:00:00Z`);
  const u = new Date(`${o.dataAsOf.updated}T12:00:00Z`);
  return `Reserve date ${r.getUTCDate()} ${MONTHS[r.getUTCMonth()]} ${r.getUTCFullYear()} · updated ${MONTHS[u.getUTCMonth()]} ${u.getUTCDate()}, ${u.getUTCFullYear()}`;
}

export function quarterLabel(isoDate: string): string {
  const y = isoDate.slice(0, 4);
  const m = Number(isoDate.replace(/-/g, "").slice(4, 6));
  return `${y} Q${Math.ceil(m / 3)}`;
}

export const SCENARIO_NAMES: Record<string, string> = {
  baseline: "Baseline",
  adverse: "Adverse (halfway)",
  severely_adverse: "Severely adverse",
  weighted: "Weighted 60/30/10",
};

export function statusBox(s: { holdout: string; overlay: string; framing: string }): string {
  return `<p><strong>Model status.</strong> ${esc(s.holdout)}</p><p><strong>Overlay.</strong> ${esc(s.overlay)}</p><p>${esc(s.framing)}</p>`;
}

export function hbar(label: string, value: number, max: number, text: string, color = "var(--accent)"): string {
  const w = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return `<div class="hbar-row"><span>${esc(label)}</span><div class="hbar-track" aria-hidden="true"><div class="hbar-fill" style="width:${w.toFixed(1)}%;background:${color}"></div></div><span class="r num">${esc(text)}</span></div>`;
}
