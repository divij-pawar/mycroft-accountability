// Human-readable numbers. The raw value is always kept available (callers put it
// in a title/tooltip) — formatting is for reading, never a replacement for the record.

const SCALES: [number, string][] = [
  [1e12, "T"],
  [1e9, "B"],
  [1e6, "M"],
];

/** 94930000000 USD → "$94.93B"; 2.02 USD/shares → "$2.02/share"; 38.1 % → "38.1%". */
export function formatFigure(value: number | null | undefined, unit?: string | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const u = (unit ?? "").toLowerCase();
  if (u === "usd/shares" || u === "usd/share") return `$${value.toFixed(2)}/share`;
  if (u === "%" || u === "pure" || u === "percent") return `${value}%`;
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const prefix = u === "usd" ? "$" : "";
  for (const [scale, suffix] of SCALES) {
    if (abs >= scale) return `${sign}${prefix}${(abs / scale).toFixed(2)}${suffix}`;
  }
  const suffix = prefix || !unit ? "" : ` ${unit}`;
  return `${sign}${prefix}${abs.toLocaleString("en-US", { maximumFractionDigits: 2 })}${suffix}`;
}

/** 36859 → "36.9s"; 128 → "128ms"; null → "". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/** ISO timestamp → "HH:MM:SS.mmm" in local time, for terminal-style trace lines. */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "--:--:--.---";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--.---";
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

/** Filer fiscal label + form, e.g. "Q3 FY26 · 10-Q"; empty when the period is unknown. */
export function formatPeriodChip(fact: { fy?: number | null; fp?: string | null; form?: string | null }): string {
  const parts: string[] = [];
  if (fact.fy && fact.fp) {
    const fy = `FY${String(fact.fy).slice(-2)}`;
    parts.push(fact.fp === "FY" ? fy : `${fact.fp} ${fy}`);
  }
  if (fact.form) parts.push(fact.form);
  return parts.join(" · ");
}

export function shortId(id: string | null | undefined): string {
  return id ? `${id.slice(0, 8)}…` : "—";
}
