# Decisions

Deviations and explicit trade-offs relative to `docs/plan/`.

| Date | Topic | Decision | Reason |
|---|---|---|---|
| 2026-10-02 | Host binding | Bind the demo API and UI to loopback by default | The plan deliberately excludes authentication; localhost is sufficient for the intended demo. |
| 2026-10-02 | Reopened PR duration | Exclude temporary closed intervals from waiting ledgers, but retain them in elapsed milestone durations | This follows the specified distinction between active waiting and calendar time. |
| 2026-10-02 | Linked delivery chains | Persist revert, reland and supersession links; defer chain-level elapsed delivery time | Explicit plan deferral; cycle time remains per PR. |
| 2026-10-02 | Windows verification | Run the exact uv subcommands from Makefile when GNU make is unavailable | Native PowerShell development uses the same ruff, mypy and pytest gates; Makefile remains the Linux/CI entrypoint. |
| 2026-10-02 | Dataset I/O boundary | Put load_dataset in db/dataset.py; keep immutable types in analytics/dataset.py | Preserves the explicit requirement that analytics computation has no I/O; API and worker share the same loader. |
| 2026-10-02 | Synthetic arrival interpretation | Apply area arrival multipliers to the weighted total Poisson arrival rate, then sample areas with adjusted weights | The plan specifies a daily rate and an area mix; this preserves the baseline total daily rate of 15 on weekdays and 5 on weekends. |
| 2026-10-02 | Concurrent snapshot computation | Allow deterministic duplicate computation and deduplicate in Postgres | Avoids a distributed lock on the read path; same identity has identical canonical content. |
| 2026-10-02 | API title | Use Delivery Insights API | The authoritative API contract overrides the shorter infrastructure example. |
| 2026-10-02 | Abstention wording | Treat 'not strong enough' as cautious abstention for V7b; retain the explicit V12 phrases when there are no candidates | Testing section 10 explicitly requires this V7b example although section 07 lists only 'insufficient' and 'not enough'. |
| 2026-10-02 | Unicode lint | Allow the Chinese punctuation and multiplication/range characters required by the bilingual contract | Preserve the exact prompt, templates and numeric unit grammar while keeping all other ruff rules. |
| 2026-10-02 | Human golden review | Keep B6 explicitly pending through subsequent local milestones | Automated independent arithmetic is complete; the agent cannot claim a human review. AGENTS permits stopping for human review only after delivery preparation. |
| 2026-10-02 | Shared container image build | Declare backend build only on api; migrate and worker reuse its tagged image | Clean-clone Compose acceptance exposed parallel BuildKit exports to the same tag failing with a missing parent snapshot. One build avoids the collision and redundant work. |
| 2026-10-02 | CLI logging | Share one JSON log configuration across uvicorn, arq bootstrap and application logging | Container acceptance exposed arq's default handler duplicating records as plain text; configuring before startup also makes bootstrap messages JSON. |

| 2026-10-02 | Post-approval CI sample | Compute the post-approval overlap median only for CI-covered approved-and-merged PRs (minimum 10), leaving it null without coverage. | Treating missing CI telemetry as zero would understate the observed delay; the overall coverage remains explicit. |

| 2026-10-02 | Sparse author-WIP correlation | Return null for fewer than 10 paired observations or a constant variable; tied ranks use their average. | The plan defines the correlation but no minimum; this uses the same minimum as driver groups and avoids misleading tiny-sample coefficients. |
