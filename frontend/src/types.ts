/** Dashboard response contracts and report parameters. */
export type State =
  "waiting_reviewer" | "waiting_author" | "waiting_merge";
export type Params = { repo: string; from: string; to: string };
export interface DateLimits {
  earliest_from: string;
  latest_to: string;
  max_days: number;
}
export interface SetupStatus {
  github: {
    token_configured: boolean;
    problems: {
      repo: string;
      status: "missing_token" | "auth_error" | "not_found";
      syncing: boolean;
    }[];
  };
  llm: {
    key_configured: boolean;
    region: string;
    model_id: string;
    last_error: string | null;
  };
}
export interface RepoList {
  items: RepoStatus[];
  date_limits: DateLimits;
  setup: SetupStatus;
}
export interface RepoStatus {
  repo: string;
  last_sync_status: string;
  last_sync_error: string | null;
  last_synced_at: string | null;
  syncing: boolean;
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
/** The deterministic headline of an insight: a factual statement and its numbers. */
export interface InsightSummary {
  statement: string;
  largest_wait: {
    state: State;
    share: number;
    previous_share: number | null;
  } | null;
  largest_change: { state: State; change_pp: number } | null;
  cycle_time_p50_hours: {
    value: number | null;
    previous: number | null;
    change_rel: number | null;
    significant: boolean | null;
    n: number;
  };
  merged_prs: { value: number; previous: number | null };
}
export interface Insight {
  snapshot_id: string;
  repo: string;
  period: {
    from: string;
    to: string;
    days: number;
    complete: boolean;
    compared_to: { from: string; to: string };
  };
  as_of: string;
  comparison_available: boolean;
  insight: InsightSummary;
  time_ledger: {
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
}
export interface Evidence {
  id: string;
  label: string;
  value: number;
  previous: number | null;
  unit: string;
  change_rel: number | null;
  change_pp: number | null;
  extra?: Record<string, number>;
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
export type AbstainReason = "no_comparison" | "no_slowdown" | "insufficient_signal";
export interface Narrative {
  narrative: string;
  abstained: boolean;
  abstain_reason: AbstainReason | null;
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
