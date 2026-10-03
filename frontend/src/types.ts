export type Audience = "director" | "manager";
export type Lang = "en" | "zh";
export type State =
  "waiting_reviewer" | "waiting_author" | "waiting_ci" | "waiting_merge";
export type Params = { repo: string; from: string; to: string };
export interface Metric {
  value: number | null;
  previous: number | null;
  change_rel: number | null;
  change_abs: number | null;
  significant: boolean | null;
  status: "ok" | "insufficient_sample";
  unit: string;
  n: number;
  extra: Record<string, number | string>;
}
export interface RepoStatus {
  repo: string;
  last_sync_status: string;
  last_synced_at: string | null;
}
export interface PendingRepo {
  repo: string;
  reason: "never_synced" | "backfill" | "open_sweep" | "rederive" | "stale";
  covered_since: string | null;
  required_since: string;
  job: { phase: string | null; status: string } | null;
}
export interface Pending {
  status: "pending";
  repos: PendingRepo[];
}
export interface WhatIf {
  stage: string;
  target_hours: number;
  change_rel: number;
  affected_prs: number;
}
export interface Finding {
  id: string;
  rank: number;
  severity: string;
  title: string;
  impact_pr_hours: number;
  impact_share: number;
  recommendation: string;
  what_if: WhatIf | null;
  evidence: {
    label: string;
    value: number | null;
    unit: string;
    ref: string;
  }[];
}
export interface Location {
  location: string;
  merged_prs: number;
  pickup_p50_hours: number | null;
  pickup_ratio_vs_rest: number | null;
  waiting_reviewer_share: number;
  inflow: number;
  outflow: number;
  at_risk_prs: number;
  owners_count: number | null;
}
export interface RiskPr {
  repo: string;
  number: number;
  title: string;
  url: string;
  author: string | null;
  state: State;
  age_hours: number;
  threshold_hours: number;
  severity: string;
}
export interface PrRow {
  repo: string;
  number: number;
  title: string;
  url: string;
  author: string | null;
  current_state: State | null;
  current_state_age_hours: number | null;
  at_risk: { threshold_hours: number; severity: string } | null;
}
export interface PrPage {
  snapshot_id: string;
  total: number;
  items: PrRow[];
  next_cursor: string | null;
}
export interface Week {
  week_start: string;
  inflow: number;
  outflow: number;
  open_at_week_end: number;
}
export interface Snapshot {
  snapshot_id: string;
  headline: string;
  as_of: string;
  repos: string[];
  period: { from: string; to: string; days: number; complete: boolean };
  efficiency: {
    cycle_time_p50_hours: Metric;
    cycle_time_p90_hours: Metric;
    effective_throughput: Metric;
    merged_within_n_days: Metric;
    waiting_share: Metric;
    waste_share: Metric;
    avg_review_rounds: Metric;
    review_concentration_top_k: Metric;
    revert_rate: Metric;
  };
  time_ledger: {
    ci_data_available: boolean;
    total_pr_hours: number;
    states: Record<
      State,
      {
        share: number;
        previous_share: number | null;
        pr_hours: number;
        previous_pr_hours: number | null;
      }
    >;
  };
  bottlenecks: Finding[];
  bottleneck_analysis: {
    locations: Location[];
    review_queue: {
      weeks: Week[];
      weeks_total: number;
      weeks_inflow_exceeds_outflow: number;
      open_growth_rel: number | null;
      net_inflow_share: number | null;
    };
  };
  at_risk_prs: RiskPr[];
  at_risk_summary: { total: number; critical: number };
  guardrail: {
    verdict: "ok" | "watch" | "tradeoff_suspected";
    cycle_time_p50_change_rel: number | null;
    revert_rate_change_pp: number | null;
    revert_rate: number | null;
  };
  meta: {
    comparison_available: boolean;
    sample: { merged_prs: number; open_prs_at_as_of: number };
    data_freshness: {
      repo: string;
      last_synced_at: string;
      last_sync_status: string;
    }[];
  };
}
export interface Evidence {
  id: string;
  label: string;
  value: number;
  previous: number | null;
  unit: string;
  change_abs: number | null;
  change_rel: number | null;
  change_pp: number | null;
  ref: string;
  examples: string[];
}
export interface Hypothesis {
  id: string;
  title: string;
  location: string | null;
  statement: string;
  source: "library" | "llm";
  confidence: number;
  confidence_level: string;
  confidence_basis: {
    llm_downgrade: { from: string; to: string; reason: string } | null;
  };
  evidence_chain: { step: string; evidence: string[] }[];
  counter_evidence: string[];
  alternatives_ruled_out: { hypothesis: string; evidence: string[] }[];
  alternatives_open: { hypothesis: string; reason: string }[];
  action: string | null;
  verify_next: string | null;
}
export interface Narrative {
  snapshot_id: string;
  narrative: string;
  abstained: boolean;
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  meta: {
    generated_by: "llm" | "template";
    model: string;
    prompt_version: string;
    validation: string;
    fallback_reason: string | null;
  };
}
