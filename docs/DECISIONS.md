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
