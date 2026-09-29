# Roadmap

Phases are implemented in order. The full acceptance criteria for each phase are in
[CLAUDE.md](../CLAUDE.md), under "Phase N Acceptance Criteria". Current status is tracked
in [progress.md](../progress.md).

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Repository and development infrastructure | Complete |
| 1 | Chart of accounts, dimensions, accounting periods | Complete |
| 2 | Journal entries and deterministic validation | Complete |
| 3 | Immutable ledger and financial reports | Complete |
| 4 | Accounting Object model and event model | Complete |
| 5 | Workflow / state machine | Complete |
| 6 | Audit / event log | Complete |
| 7 | FastAPI REST interface | Complete |
| 8 | MCP interface | Not started |
| 9 | Deterministic dataset generator (`erp dataset generate`) | Not started |
| 10 | Benchmark / evaluation framework (`erp benchmark run`) | Not started |
| 11 | Example accounting agents | Not started |
| 12 | PostgreSQL compatibility | Not started |

A phase is complete only when its implementation, unit, integration, and acceptance tests
pass, Ruff passes, the documentation and `progress.md` are updated, and the work is
committed.
