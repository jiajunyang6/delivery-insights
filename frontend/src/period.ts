import type { DateLimits, Params } from "./types";

const DAY = 86_400_000;
// Round-trips through UTC so impossible dates such as 2024-02-31 are rejected.
function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + "T00:00:00Z");
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function isPeriodSelected(
  params: Pick<Params, "from" | "to">,
  days: number,
): boolean {
  if (!validDate(params.from) || !validDate(params.to)) return false;
  return (Date.parse(params.to) - Date.parse(params.from)) / DAY + 1 === days;
}

/**
 * Mirrors the API's period checks against the UTC date limits from /v1/repos, so invalid
 * ranges are caught before a request. ISO dates compare correctly as strings.
 */
export function periodError(
  params: Pick<Params, "from" | "to">,
  limits: DateLimits | null,
): string | null {
  if (!validDate(params.from) || !validDate(params.to) || params.from > params.to)
    return "Choose a valid date range with From no later than To.";
  if (!limits) return null;
  if (params.from < limits.earliest_from)
    return `History is configured from ${limits.earliest_from} (UTC). Choose a later From date.`;
  if (params.to > limits.latest_to)
    return `To must not be later than ${limits.latest_to} (UTC).`;
  const days = (Date.parse(params.to) - Date.parse(params.from)) / DAY + 1;
  if (days > limits.max_days)
    return `Choose a period of at most ${limits.max_days} days.`;
  return null;
}
