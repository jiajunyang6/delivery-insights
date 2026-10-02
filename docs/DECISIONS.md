# Decisions

Deviations and explicit trade-offs relative to `docs/plan/`.

| Date | Topic | Decision | Reason |
|---|---|---|---|
| 2026-10-02 | Host binding | Bind the demo API and UI to loopback by default | The plan deliberately excludes authentication; localhost is sufficient for the intended demo. |
| 2026-10-02 | Windows verification | Run the exact uv subcommands from Makefile when GNU make is unavailable | Native PowerShell development uses the same ruff, mypy and pytest gates; Makefile remains the Linux/CI entrypoint. |
