import { useState, type ReactNode } from "react";
import { useAbortable } from "../hooks/useAbortable";
import { GithubLink } from "./GithubLink";
import { fetchJson, message } from "../api";
import { format, percent, signed, capitalize, states, viewLabels } from "../format";
import type {
  AbstainReason,
  Audience,
  Evidence,
  Hypothesis,
  Narrative,
  Snapshot,
  State,
} from "../types";

const hypothesisTitles: Record<string, string> = {
  H_review_capacity: "Limited review capacity",
  H_ci_bottleneck: "Slow or congested CI",
  H_pr_size_growth: "Pull requests getting larger",
  H_quality_tradeoff: "Speed gained by lighter review",
};
const openReasons: Record<string, string> = {
  insufficient_sample: "too few PRs to judge",
  no_data: "no data for this check",
  below_threshold: "evidence too weak",
  not_selected: "weaker than the top three",
};
const stepLabels: Record<string, string> = {
  symptom: "What changed",
  stage: "Where the time went",
  location: "Which area",
  mechanism: "Why it may happen",
  cited: "Evidence",
};
const abstainText: Record<AbstainReason, { title: string; detail: string }> = {
  no_slowdown: {
    title: "No slowdown to explain",
    detail:
      "Delivery did not get significantly slower than the previous period, so there is no root cause to look for.",
  },
  insufficient_signal: {
    title: "No clear cause yet",
    detail:
      "The evidence does not point to a single cause this period. Start with where the time goes now.",
  },
  no_comparison: {
    title: "No previous period to compare",
    detail:
      "Root causes need a previous period with data. Choose a later period or sync more history.",
  },
};
const waitingOn: Record<State, string> = {
  waiting_reviewer: "waiting on reviewers",
  waiting_author: "waiting on authors",
  waiting_ci: "waiting on CI",
  waiting_merge: "waiting to merge after approval",
};

/** "dir:crates/bevy_pbr" → "crates/bevy_pbr (directory)". Labels stay as they are. */
export function place(location: string): string {
  if (location.startsWith("dir:")) return location.slice(4) + " (directory)";
  if (location.startsWith("codeowners:"))
    return location.slice(11) + " (CODEOWNERS)";
  return location;
}

function change(e: Evidence): string {
  if (e.change_pp != null) return signed(e.change_pp, " pp", 1);
  if (e.change_rel != null) return signed(e.change_rel);
  return "";
}

function LookFirst({ snapshot }: { snapshot: Snapshot }) {
  const top = snapshot.bottlenecks[0];
  if (top) {
    return (
      <aside className="look-first" aria-label="Where to look first">
        <span className="eyebrow">WHERE TO LOOK FIRST</span>
        <h3>{top.title}</h3>
        <p>
          <b>{percent(top.impact_share)} of PR time</b> · {top.recommendation}
        </p>
        <a href="#bottlenecks">See all bottlenecks ↓</a>
      </aside>
    );
  }
  const ledger = snapshot.time_ledger.states;
  const largest = states
    .filter((s) => ledger[s])
    .sort((a, b) => ledger[b].share - ledger[a].share)[0];
  if (!largest) return null;
  return (
    <aside className="look-first" aria-label="Where to look first">
      <span className="eyebrow">WHERE TO LOOK FIRST</span>
      <h3>No single bottleneck stands out</h3>
      <p>
        The largest share of PR time,{" "}
        <b>{percent(ledger[largest].share)}</b>, is spent {waitingOn[largest]}.
      </p>
    </aside>
  );
}

function HypothesisCard({ h, text, chip }: {
  h: Hypothesis;
  text: (value: string, bare?: boolean) => ReactNode;
  chip: (id: string) => ReactNode;
}) {
  const steps = h.evidence_chain.filter((step) => step.evidence.length);
  const downgrade = h.confidence_basis.llm_downgrade;
  const checked = h.alternatives_ruled_out.length + h.alternatives_open.length;
  return (
    <article className="hypothesis" key={h.id}>
      <div className="hypothesis-header">
        <h3>
          {h.title}
          {h.location && <span> · {place(h.location)}</span>}
        </h3>
        <span className="strength">
          Evidence strength{" "}
          <span className="badge">
            {capitalize(h.confidence_level)}
          </span>{" "}
          <span className="muted">{h.confidence.toFixed(2)}</span>
        </span>
      </div>
      <progress
        value={h.confidence}
        max={1}
        aria-label={"Evidence strength " + h.confidence.toFixed(2)}
      />
      <p className="footnote">
        Scored from 0 to 1 by fixed rules: Low 0.35–0.50, Medium above 0.50,
        High 0.75 and above. Not a probability.
      </p>
      {h.source === "llm" && (
        <p className="notice">Outside the hypothesis library</p>
      )}
      <p className="statement">{text(h.statement)}</p>
      {(steps.length > 0 || h.counter_evidence.length > 0) && (
        <dl className="evidence-chain">
          {steps.map((step) => (
            <div key={step.step}>
              <dt>{stepLabels[step.step] ?? step.step}</dt>
              <dd>{step.evidence.map(chip)}</dd>
            </div>
          ))}
          {h.counter_evidence.length > 0 && (
            <div className="against">
              <dt>Evidence against</dt>
              <dd>{h.counter_evidence.map(chip)}</dd>
            </div>
          )}
        </dl>
      )}
      {downgrade && (
        <p className="notice">
          <strong>
            Lowered from {capitalize(downgrade.from)} to{" "}
            {capitalize(downgrade.to)}:
          </strong>{" "}
          {text(downgrade.reason, true)}
        </p>
      )}
      {checked > 0 && (
        <div className="alternatives">
          <strong>Other explanations checked</strong>
          <ul>
            {h.alternatives_ruled_out.map((a) => (
              <li key={a.hypothesis}>
                {hypothesisTitles[a.hypothesis] ?? a.hypothesis}:{" "}
                <span className="verdict">ruled out</span> by{" "}
                {a.evidence.map(chip)}
              </li>
            ))}
            {h.alternatives_open.map((a) => (
              <li key={a.hypothesis}>
                {hypothesisTitles[a.hypothesis] ?? a.hypothesis}:{" "}
                <span className="verdict open">not assessed</span>,{" "}
                {openReasons[a.reason] ?? a.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {h.action && (
        <div className="action">
          <strong>Try next</strong>
          <p>{h.action}</p>
          <strong>How to verify</strong>
          <p>{h.verify_next}</p>
        </div>
      )}
    </article>
  );
}

function EvidenceList({ evidence, highlight }: { evidence: Evidence[]; highlight: string }) {
  return (
    <div className="evidence-list">
      <h3>Trace every claim</h3>
      <p className="muted">
        Select a citation to find its value in the snapshot.
      </p>
      {evidence.map((e) => (
        <article
          tabIndex={-1}
          id={"evidence-" + e.id}
          key={e.id}
          className={
            "evidence-item " + (highlight === e.id ? "highlighted" : "")
          }
        >
          <span className="evidence-id">{e.id}</span>
          <div>
            <strong>{e.label}</strong>
            <p>
              <b>{format(e.value, e.unit)}</b>
              {e.previous != null && (
                <> · Previous {format(e.previous, e.unit)}</>
              )}
              {e.change_rel != null && <> · {signed(e.change_rel)}</>}
              {e.change_pp != null && (
                <> · {signed(e.change_pp, " pp", 1)}</>
              )}
            </p>
            <code>{e.ref}</code>
            <div className="example-links">
              {e.examples.map((url) => (
                <GithubLink key={url} url={url} fallback={url}>
                  PR #{url.split("/").pop()}
                </GithubLink>
              ))}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

export function NarrativePanel({
  snapshot,
  audience,
}: {
  snapshot: Snapshot;
  audience: Audience;
}) {
  const snapshotId = snapshot.snapshot_id;
  const [data, setData] = useState<Narrative | null>(null);
  const [error, setError] = useState("");
  const [highlight, setHighlight] = useState("");
  useAbortable((signal) => {
    setData(null);
    setError("");
    setHighlight("");
    fetchJson<Narrative>(
      "/v1/snapshots/" + snapshotId + "/narrative?audience=" + audience,
      signal,
    )
      .then((r) => {
        if (!signal.aborted) setData(r.data);
      })
      .catch((e) => {
        if (!signal.aborted) {
          const p = message(e);
          setError(
            p.title +
              ": " +
              p.detail +
              (p.request_id ? " · Request " + p.request_id : ""),
          );
        }
      });
  }, [snapshotId, audience]);
  function focus(id: string) {
    setHighlight(id);
    document
      .getElementById("evidence-" + id)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  const tag = (id: string, key?: string) => (
    <button
      key={key ?? id}
      className="citation"
      onClick={() => focus(id)}
      aria-label={"Show evidence " + id}
    >
      {id}
    </button>
  );
  // Bracketed citations in narrative text; bare IDs too in free-text reasons.
  const text = (value: string, bare = false) =>
    value
      .split(bare ? /(\[?\bE\d+\b\]?)/ : /(\[E\d+\])/)
      .map((part, i) =>
        /^\[?E\d+\]?$/.test(part) && (bare || part.startsWith("[")) ? (
          tag(part.replace(/[[\]]/g, ""), String(i))
        ) : (
          <span key={i}>{part}</span>
        ),
      );
  const byId = new Map((data?.evidence ?? []).map((e) => [e.id, e]));
  const chip = (id: string) => {
    const e = byId.get(id);
    return (
      <button
        key={id}
        className="evidence-chip"
        onClick={() => focus(id)}
        aria-label={"Show evidence " + id}
      >
        {e ? (
          <>
            <span>{e.label}</span>
            <b>{format(e.value, e.unit)}</b>
            {change(e) && <em>{change(e)}</em>}
          </>
        ) : (
          <span>Evidence</span>
        )}
        <small>{id}</small>
      </button>
    );
  };
  const reason = data?.abstained ? data.abstain_reason : null;
  return (
    <section
      className="panel narrative-panel"
      aria-labelledby="narrative-heading"
      lang="en"
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">FROM METRICS TO A WORKING EXPLANATION</span>
          <h2 id="narrative-heading">The evidence, in words</h2>
        </div>
        <span className="badge neutral">{viewLabels[audience]}</span>
      </div>
      {error ? (
        <p role="alert" className="error-text">
          {error}
        </p>
      ) : !data ? (
        <p className="loading">
          <span className="spinner" />
          Generating narrative…
        </p>
      ) : (
        <>
          <p className="narrative-body">{text(data.narrative)}</p>
          {reason && (
            <p className="abstain-note" role="status">
              <strong>{abstainText[reason].title}.</strong>{" "}
              {abstainText[reason].detail}
            </p>
          )}
          {data.abstained && <LookFirst snapshot={snapshot} />}
          <div className="hypotheses">
            {data.hypotheses.map((h) => (
              <HypothesisCard key={h.id} h={h} text={text} chip={chip} />
            ))}
          </div>
          <EvidenceList evidence={data.evidence} highlight={highlight} />
          <footer className="narrative-meta">
            {data.meta.generated_by === "llm"
              ? "Generated by LLM (" +
                data.meta.model +
                ") · prompt " +
                data.meta.prompt_version +
                " · validation " +
                data.meta.validation
              : "Template narrative (" + data.meta.fallback_reason + ")"}
          </footer>
        </>
      )}
    </section>
  );
}
