# Roadmap

Phases are implemented in order. The full acceptance criteria for each phase are in
[CLAUDE.md](../CLAUDE.md), under "Phase N Acceptance Criteria", except Phase 12's, which
CLAUDE.md does not list; they were set when it was implemented and are recorded in
[progress.md](../progress.md), where current status is tracked.

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
| 8 | MCP interface | Complete |
| 9 | Deterministic dataset generator (`erp dataset generate`) | Complete |
| 10 | Benchmark / evaluation framework (`erp benchmark run`) | Complete |
| 11 | Example accounting agents (`opensumma.agents`) | Complete |
| 12 | PostgreSQL compatibility | Complete |

A phase is complete only when its implementation, unit, integration, and acceptance tests
pass, Ruff passes, the documentation and `progress.md` are updated, and the work is
committed.
