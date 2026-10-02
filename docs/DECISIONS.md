# Decisions

Deviations and explicit trade-offs relative to `docs/plan/`.

| Date | Topic | Decision | Reason |
|---|---|---|---|
| 2026-10-02 | Host binding | Bind the demo API and UI to loopback by default | The plan deliberately excludes authentication; localhost is sufficient for the intended demo. |
| 2026-10-02 | Reopened PR duration | Exclude temporary closed intervals from waiting ledgers, but retain them in elapsed milestone durations | This follows the specified distinction between active waiting and calendar time. |
| 2026-10-02 | Linked delivery chains | Persist revert, reland and supersession links; defer chain-level elapsed delivery time | Explicit plan deferral; cycle time remains per PR. |
| 2026-10-02 | Windows verification | Run the exact uv subcommands from Makefile when GNU make is unavailable | Native PowerShell development uses the same ruff, mypy and pytest gates; Makefile remains the Linux/CI entrypoint. |
