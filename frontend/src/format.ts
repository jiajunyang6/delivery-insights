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
export function signed(
  value: number | null | undefined,
  suffix = "%",
  scale = 100,
): string {
  return value == null
    ? "—"
    : (value > 0 ? "+" : "") + number(value * scale) + suffix;
}
export function timestamp(value: string): string {
  return (
    new Date(value).toLocaleString("en-US", {
      timeZone: "UTC",
      dateStyle: "medium",
      timeStyle: "short",
    }) + " UTC"
  );
}
export function safeGithubUrl(value: string): boolean {
  if (!value.startsWith("https://github.com/")) return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname === "github.com" &&
      !url.username &&
      !url.password &&
      !url.port
    );
  } catch {
    return false;
  }
}
export function dateRange(days: number) {
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.parse(to + "T00:00:00Z") - (days - 1) * 86_400_000)
    .toISOString()
    .slice(0, 10);
  return { from, to };
}
