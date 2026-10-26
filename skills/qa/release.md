# Release, postmortems and metrics

Read this to decide go/no-go on a release, run a postmortem or choose QA metrics.

## Release go/no-go

Automated, must pass: all pipelines green; smoke on staging; no open P0/P1; performance budgets met; no high/critical vulnerabilities; contract tests pass; no new visual or a11y violations.

Manual: flag states documented; alerts exist for new features; rollback documented and rehearsed on staging; migrations tested forward (and backward if reversible); dependency changes reviewed; release notes; named on-call; no conflicting releases; not at peak or before a weekend.

Risk: scope S/M/L; blast radius in % of users; can it be reverted in under 5 minutes; does revert need a data migration.

Smoke suite: 5-8 tests under 5 minutes: health, auth, core workflow, data read, payment with test credentials, API schema, navigation, 404. No edge cases or benchmarks; setup through the API; no sleeps. In production: 3-5 tests with synthetic accounts within a minute of deploy; any failure triggers rollback evaluation.

Staged rollout *(starting point)*: 1% for 15-30 min, 10% for 1-2h, 25-50% for 2-4h, 100% with 24h watch. Promote only if error rate and p95 stay within ~1.2x baseline, no new exception types, no crashes.

Rollback criteria are set before the deploy, relative to baseline. Example auto-triggers: 5xx over 2x baseline for 5 min; p95 over 3x for 5 min; 3 failed health checks in a row. The on-call engineer can always roll back. Roll back first, investigate second. Shadow-run new auto-rollback alerts before trusting them.

After deploy: 0-15 min smoke and error rate; 15 min-2h synthetic checks, latency, support volume, business metrics; 2-24h batch jobs, regions waking up, email, integrations.

"It worked on staging" and "CI is green, so skip smoke" do not count as evidence.

## Postmortems

- Blameless means systemic. If the root cause is "human error", keep asking why.
- Classify each escaped bug: root cause (logic, integration, data, race, config, regression, missing requirement, performance); the level that should have caught it (unit, integration, E2E, exploratory, monitoring); the prevention (add or improve a test, add a gate, improve requirements, add monitoring).
- 5 Whys: verify each answer with evidence (PRs, logs); stop at something the team can change.
- Template: summary; impact (users, duration, business); UTC timeline (deploy, first alert, acknowledged, root cause, fix, resolved); root cause; which tests existed and why they missed it; how it was detected and how it could be earlier; actions.
- 1-3 actions, each with deliverable, owner, deadline and how completion is verified. Escalate after 2 sprints undone.
- Track escape rate and action closure rate together. High closure with rising escapes means the team is doing the wrong work.
- After 10+ escapes, look for patterns; recurring ones change the strategy.

## Metrics

Start with three: coverage delta, flakiness, defect escape rate. Every metric has a threshold, an owner and an action. Trends beat snapshots. Retire a metric that triggered no action in 90 days. Use metrics to steer a team's own work, never to rank or punish teams.

| Metric | Formula | Healthy *(starting point)* |
|---|---|---|
| Escape rate | prod defects / all defects | <5% excellent, >20% critical |
| Flakiness | flaky runs / total runs | <2% |
| Pass rate (7-day) | | >95% |
| Skipped tests | | trend to zero; none older than 2 sprints |
| Defect MTTR | report to fix deployed | P0 <4h, P1 <24h |
| CI duration | | PR feedback <15 min |

Leading indicators (flakiness, coverage delta, skip count) are for the team daily. Lagging ones (escape rate, MTTR) are for leadership monthly. Raw counts mislead: a suite of 4,000 tests with 800 disabled is not healthy.
