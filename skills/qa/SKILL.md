---
name: qa
description: QA practitioner playbook. Use when scoring risk or setting a test strategy or plan, deciding what to test or running an exploratory session, reviewing test code or judging what coverage misses, verifying a ticket or PR, choosing or pruning regression tests, writing a bug report, triaging a CI failure or flaky test, deciding release go/no-go, or running a postmortem.
---

# QA

Practitioner rules for testing work in any ecosystem, with Python and TypeScript tool notes. Numbers marked *(starting point)* are defaults with no traceable source: use them until the project's own data says otherwise.

## Ground rules

- Fit the practice to the context. For each practice, name the risk it addresses, what it costs, and whether it fits this project. A regulated product needs protocols and traceability; a young startup needs session notes and a risk log.
- The mission is finding important problems fast enough to matter. A count of executed test cases says little about that.
- Testing vs checking. Checking compares output against a known reference and can be automated. Testing (deciding what to look at and whether something is a problem) needs judgment. Do checking freely; flag testing judgments for a human.
- Every claim in a report, a plan or a go/no-go traces to a run, a log, a diff or a requirement.
- Measure outcomes, such as escape rate and time to detect, rather than activity such as test count or meetings held.
- Agents drift toward the easy version of each task. Hold these lines: explore error paths and hostile input on purpose; diagnose the pyramid shape before adding tests; select regression by what the change touches; grade severity with the table; put only evidenced facts in a bug report; check the executed test count before reporting zero regressions.

## Pick the reference

Read every file the task touches. A ticket verification that turns up a bug reads both `verifying.md` and `failures.md`.

| Task | Read | Done when |
|---|---|---|
| Score risk, choose a strategy, set CI gates, plan a sprint | [strategy.md](strategy.md) | Every item scoring 10+ has all five risk lines; every requirement has a test or a recorded decision |
| Decide what to test, design cases, explore, write manual cases | [test-design.md](test-design.md) | Each test idea is an action with an observable outcome; each exploratory session ends in a debrief |
| Write or review test code, test data, coverage, mutation | [test-code.md](test-code.md) | Each review finding states what is broken, why, the correct form and the fix |
| Verify a ticket or PR; choose, tier or prune regression | [verifying.md](verifying.md) | Every acceptance criterion has a check and a result; the summary is filled from real runs |
| Write a bug report, reproduce a bug, triage CI, fix a flaky test | [failures.md](failures.md) | Every report field traces to evidence or reads `NOT PROVIDED`; the repro is written back to the ticket |
| Release go/no-go, postmortem, metrics | [release.md](release.md) | Every checklist item has evidence; every postmortem action has an owner, a deadline and a completion check |

Two neighbouring skills own their parts of this work. Write new tests with `tdd`. Find the cause of a bug with `diagnosing-bugs`.
