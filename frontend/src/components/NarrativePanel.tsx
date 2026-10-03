import { useEffect, useState } from "react";
import { fetchJson, message } from "../api";
import { format, signed, safeGithubUrl } from "../format";
import type { Audience, Narrative } from "../types";
import { viewLabels } from "../views";

const reasons: Record<string, string> = {
  insufficient_sample: "insufficient sample",
  no_data: "no data",
  below_threshold: "below threshold",
  not_selected: "not in the top 3",
};
export function NarrativePanel({
  snapshotId,
  audience,
}: {
  snapshotId: string;
  audience: Audience;
}) {
  const [data, setData] = useState<Narrative | null>(null);
  const [error, setError] = useState("");
  const [highlight, setHighlight] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    setHighlight("");
    fetchJson<Narrative>(
      "/v1/snapshots/" +
        snapshotId +
        "/narrative?audience=" +
        audience,
      controller.signal,
    )
      .then((r) => {
        if (!controller.signal.aborted) setData(r.data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          const p = message(e);
          setError(
            p.title +
              ": " +
              p.detail +
              (p.request_id ? " · Request " + p.request_id : ""),
          );
        }
      });
    return () => controller.abort();
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
  const text = (value: string) =>
    value
      .split(/(\[E\d+\])/)
      .map((part, i) =>
        /^\[E\d+\]$/.test(part) ? (
          tag(part.slice(1, -1), String(i))
        ) : (
          <span key={i}>{part}</span>
        ),
      );
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
        <span className="badge neutral">
          {viewLabels[audience]}
        </span>
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
          {data.abstained && (
            <p className="notice">Signals are insufficient for a root cause.</p>
          )}
          <p className="narrative-body">{text(data.narrative)}</p>
          <div className="hypotheses">
            {data.hypotheses.map((h) => (
              <article className="hypothesis" key={h.id}>
                <div className="hypothesis-header">
                  <h3>
                    {h.title}
                    {h.location && <span> · {h.location}</span>}
                  </h3>
                  <b>
                    {format(h.confidence, "share")}{" "}
                    <span className="badge">{h.confidence_level}</span>
                  </b>
                </div>
                <progress
                  value={h.confidence}
                  max={1}
                  aria-label={"Evidence strength " + h.confidence}
                />
                <p className="footnote">
                  Evidence-strength score, not a calibrated probability
                </p>
                {h.source === "llm" && (
                  <p className="notice">Outside the hypothesis library</p>
                )}
                <p>{text(h.statement)}</p>
                <div className="evidence-chain">
                  {h.evidence_chain.map((step, i) => (
                    <div key={step.step}>
                      {i > 0 && <span className="chain-arrow">→</span>}
                      <span className="chain-step">{step.step}</span>
                      {step.evidence.map((id) => tag(id))}
                    </div>
                  ))}
                </div>
                {h.counter_evidence.length > 0 && (
                  <p>
                    <strong>Counter-evidence </strong>
                    {h.counter_evidence.map((id) => tag(id))}
                  </p>
                )}
                {h.alternatives_ruled_out.map((a) => (
                  <p className="alternative" key={a.hypothesis}>
                    {a.hypothesis} ruled out by{" "}
                    {a.evidence.map((id) => tag(id))}
                  </p>
                ))}
                {h.alternatives_open.map((a) => (
                  <p className="alternative" key={a.hypothesis}>
                    {a.hypothesis} not assessed: {reasons[a.reason] ?? a.reason}
                  </p>
                ))}
                {h.confidence_basis.llm_downgrade && (
                  <p className="notice">
                    Reduced from {h.confidence_basis.llm_downgrade.from}:{" "}
                    {text(h.confidence_basis.llm_downgrade.reason)}
                  </p>
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
            ))}
          </div>
          <div className="evidence-list">
            <h3>Trace every claim</h3>
            <p className="muted">
              Select a citation to find its value in the snapshot.
            </p>
            {data.evidence.map((e) => (
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
                    {e.examples.map((url) =>
                      safeGithubUrl(url) ? (
                        <a
                          key={url}
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          PR #{url.split("/").pop()} ↗
                        </a>
                      ) : (
                        <span key={url}>{url}</span>
                      ),
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
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
