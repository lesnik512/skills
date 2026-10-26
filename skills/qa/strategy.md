# Risk and strategy

Read this to score risk, choose a test strategy, set CI gates or plan a sprint's testing.

## Risk

Score every feature or module before deciding how much to test it.

Risk = Impact (1-5) x Likelihood (1-5). Score the two independently, then multiply.

| Impact | Meaning |
|---|---|
| 5 | Revenue loss, data breach, legal action, safety |
| 4 | SLA violation, major feature broken, support flood |
| 3 | Broken, but a workaround exists |
| 2 | Cosmetic |
| 1 | Internal only or deprecated |

| Likelihood | Signals |
|---|---|
| 5 | High churn, no tests, complex logic |
| 4 | Recent changes, partial coverage, tech debt |
| 3 | Has failed before |
| 2 | Stable, well covered, simple |
| 1 | Battle-tested |

Bands: Critical 15-25, High 10-14, Medium 5-9, Low 1-4. (One source uses 20/12/6; this guide uses the stricter bands so that a moderate-impact module under heavy churn, 3 x 5 = 15, lands in Critical.)

| Band | Automation | Unit branch cov. | Integration | E2E | Manual |
|---|---|---|---|---|---|
| Critical | Full, every commit | 90%+ | All service boundaries | Full journey + error paths | Exploratory every release; synthetic monitoring in prod |
| High | Full, every PR | 80%+ | Key interactions | Happy path + top 3 errors | Spot checks |
| Medium | Happy path + key errors | 70%+ | Happy path | Happy path | On major change |
| Low | Basic happy path | basic | none | none | On initial build |

Where risks come from: product owner (critical flows), engineers (fragile code), support (recurring complaints), postmortems from the last 6-12 months, churn ranking (`git log --since="3 months ago" --name-only`, count by file), modules whose authors left, dependencies that hard-fail, shared databases and synchronous call chains.

For every item scoring 10+, write five lines: Trigger, Blast radius, Detection method, Current mitigation, Gap. An empty Gap line means the analysis was not done.

Re-score within 48h of a production incident, on a new feature area, a critical dependency change, a team change, and at least quarterly. If a top-10 churn module has Likelihood below 4, the model has drifted.

Anti-patterns: testing everything equally; a one-off assessment; ignoring near-misses (a critical bug caught only manually is an automation gap); risk theater (heatmap exists, test allocation never changed); confusing severity with priority; "80% coverage everywhere" instead of per-module targets.

## Strategy and planning

### Pyramid

Target roughly 70 / 20 / 10 unit / integration / E2E by count (healthy range 60-80 / 15-25 / 5-15). Diagnose the current shape before prescribing:

- Ice cream cone (E2E outnumbers unit; CI 45+ min, breaks on every UI change, nobody trusts it). Freeze E2E growth except new critical paths; push logic assertions down a level (tax math in a checkout E2E becomes a unit test); require unit tests for business-logic PRs; separate real flakes from tests exposing real races before quarantining.
- Hourglass (missing integration). Invest in DB fixtures and stubs; test happy and error paths at every service boundary; add contract tests between services.
- Trophy (integration-heavy) is valid for some apps. Decide on purpose.

### Calibrate to maturity

- Startup: unit tests plus a few critical E2E paths. No contract tests or formal metrics until CI is reliable.
- Growing: full pyramid, coverage targets, flakiness thresholds, CI gates, risk-based prioritization.
- Established: SLA-backed gates, multiple environments, contract, chaos and observability testing, formal review cadence.

Keep the strategy proportional: a five-person team needs about five pages. It lives in PR templates, CI config and the Definition of Done, not only in a wiki.

### Gates

| Stage | Gate |
|---|---|
| PR | Unit + integration green; coverage not below baseline; no new lint or high/critical SAST findings; one approval |
| Merge | E2E smoke on preview deploy; branch up to date |
| Deploy | Full E2E on staging; perf benchmarks; security scan; flags configured; rollback tested |
| Nightly | Full E2E, visual, load, a11y, dependency scan; someone reviews it next morning |

Gate on delta from baseline, not an absolute coverage number. A gate someone can click past does not block anything. If gates are routinely overridden, they are wrong or untrusted: fix them. Prove a gate fires by opening a throwaway PR that should be blocked.

Before deleting tests to speed up CI, try sharding, caching, test impact analysis (full suite still runs nightly), and smoke-on-PR / full-on-merge.

### Sprint test plan

A plan answers what, how deep, who, by when. Keep it to one page and update it daily.

1. List what is new, changed, unchanged, and any infra or dependency changes.
2. Map every requirement to at least one test. Each GAP row needs a decision: write a test, accept the risk, or defer.
3. Decompose each feature into: happy path; validation (empty, format, boundary); errors (5xx, timeout); edge (unicode, oversized, unsupported formats); concurrency (simultaneous edit, double submit, retry after timeout: the most often missed); integration points.
4. Order work by risk x effort (effort: low <1h, medium 1-4h, high >4h):

| Risk \ Effort | Low | Medium | High |
|---|---|---|---|
| High | 1st | 2nd | 3rd |
| Medium | 2nd | 3rd | Defer |
| Low | If time | Defer | Skip |

5. Plan 70-80% of capacity. Name the remaining 20-30% as buffer for bug verification and retests. Protect the buffer when the schedule slips.
6. Budget test data and environment setup as its own line *(starting point: 0.5-2h per feature)*. Set it up on day 1.
7. At least two people can test the critical path. The last day is for confirmation, not new testing.

Default exit criteria: all High-risk areas covered, no open P0/P1, zero unexplained GAP rows.

### Shift left

- Review stories in planning. Acceptance criteria must be testable ("user can sort the table", not "user-friendly"). Add edge cases: empty data, max length, concurrency, network loss mid-operation.
- Three Amigos (product, dev, QA), 30 min max, for Medium+ risk, payments/auth/data integrity, ambiguous or cross-team stories. Skip for clear bug fixes, copy changes, behavior-free bumps. A session that changed no acceptance criterion and added no edge case was ceremony.
- Every bug fix starts with a failing test. If you cannot write one, you do not understand the bug yet.
- TDD fits pure logic, bugs with a repro, clear API contracts, and agent-written implementations (the failing test is the spec); run it with the `tdd` skill. Test-after fits UI prototyping, third-party integrations, data migrations (fixtures first), and performance work (benchmark first).
- Commit-stage tests under 5 min; over 10 min kills the feedback loop.
- Shift left does not replace testing in production, and it changes when testing happens, not who does it.
