import type { State } from "./types";

export const states: State[] = [
  "waiting_reviewer",
  "waiting_author",
  "waiting_ci",
  "waiting_merge",
];
export const stateLabels: Record<State, string> = {
  waiting_reviewer: "Reviewer",
  waiting_author: "Author",
  waiting_ci: "CI",
  waiting_merge: "Merge",
};
export const stateColors: Record<State, string> = {
  waiting_reviewer: "#208577",
  waiting_author: "#9db9bd",
  waiting_ci: "#dfae55",
  waiting_merge: "#657299",
};
export function number(value: number | null | undefined, digits = 1): string {
  return value == null
    ? "—"
    : value.toLocaleString("en-US", { maximumFractionDigits: digits });
}
export function percent(value: number | null | undefined): string {
  return value == null ? "—" : number(value * 100) + "%";
}
export function hours(value: number | null | undefined): string {
  return value == null ? "—" : number(value) + " h";
}
export function format(value: number | null | undefined, unit: string): string {
  if (unit === "share" || unit === "change") return percent(value);
  if (unit === "hours") return hours(value);
  if (unit === "rounds") return number(value, 2);
  return (
    number(value) +
    (value == null
      ? ""
      : unit === "ratio"
        ? "×"
        : unit === "minutes"
          ? " min"
          : "")
  );
}
/**
 * Inputs are fractions by default (0.12 → +12%). Pass scale 1 with a " pp" suffix for values
 * already in percentage points: a relative change in a share is not a point difference.
 */
export function signed(
  value: number | null | undefined,
  suffix = "%",
  scale = 100,
): string {
  return value == null
    ? "—"
    : (value > 0 ? "+" : "") + number(value * scale) + suffix;
}
// Inclusive range of UTC calendar days, matching the API's UTC date handling.
export function dateRange(days: number, to = new Date().toISOString().slice(0, 10)) {
  const from = new Date(Date.parse(to + "T00:00:00Z") - (days - 1) * 86_400_000)
    .toISOString()
    .slice(0, 10);
  return { from, to };
}

export function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export const chartColors = {
  grid: "#e4e9e7",
};
