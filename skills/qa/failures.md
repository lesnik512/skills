# Bugs and failures

Read this to write a bug report, reproduce a bug, triage a CI failure or deal with a flaky test.

## Bug reports

### Template

```
Title: [Component] [what happens] when [condition]   (under ~100 chars, behavior not cause)
Where: interface-specific, see below
Environment: version/commit, OS, browser/runtime + version, URL, UTC timestamp, user/role, request id
Preconditions:
Steps:
  1. one action per step, data in `code`
Expected: ... (source: requirement / spec / previous behavior / assertion)
Actual: ... (error text verbatim)
Evidence: 5-10 relevant log lines, screenshot, trace id, request/response
Reproducibility: Always (5/5) | Intermittent (2/5) | Once
Severity: ... (suggested)   Priority: ... (suggested)
Notes: workaround; scope checked; reporter hypothesis (labeled unconfirmed)
Missing information: ...
```

- Where uses the interface's own terms: UI = screen path or URL; API = method, endpoint, status; DB = table, query, error code; queue = topic, message id, consumer; job = name, run id; CLI = command and exit code. List only that interface's fields.
- Never invent steps, data, environment, ids, messages, impact, frequency or root cause. A required field you do not have stays in the report as `NOT PROVIDED`. Business impact and root-cause guesses go in Notes, labeled as unconfirmed.
- Expected needs a source. Without one, do not decide what correct is. File it as `[SUSPECTED]` with questions for the product owner.
- Steps are actions. What you observed goes in Actual. "1. Open the app" is useless: give URL, role and state.
- From a failing automated test: steps come from the test code, Actual from the assertion message.
- Quantify: "12s to load, target <3s", not "slow".
- Mask passwords, tokens, cookies, API keys, card numbers and customer data, including inside logs. Masking is the only allowed edit to evidence.
- One bug per report. Search for duplicates before filing. Re-read as a skeptical developer: every sentence must trace to evidence.

### Severity (impact) vs priority (urgency)

| Severity | Meaning |
|---|---|
| Critical | System down, data loss or corruption, security breach, wrong money calculation, compliance violation, core flow blocked for everyone |
| Major | Main feature broken or wrong, no reasonable workaround |
| Minor | Partial failure with a workaround, or secondary flow |
| Trivial | Cosmetic, text |

- If money, security or personal data is involved, start at Critical and lower only with a stated reason.
- A workaround a real user would not find counts as none.
- Platform scope (mobile only, one browser) goes in the description; it lowers priority, not severity.

| Reach \ Severity | Critical | Major | Minor | Trivial |
|---|---|---|---|---|
| All users | P0 | P0 | P1 | P2 |
| >10% | P0 | P1 | P2 | P3 |
| <10% | P1 | P1 | P2 | P3 |
| Edge case | P1 | P2 | P3 | P3 |

P0 same day / blocks release; P1 this sprint; P2 next sprint; P3 backlog. Raise for a release deadline, regulation, a key customer, or when it blocks other testing. A fixed bug that comes back is reopened at higher priority. Priority is the product owner's call: suggest it.

## Reproducing bugs

Reproduce before theorizing. A fix without a repro is a guess you cannot falsify.

1. Intake: exact steps, build/commit SHA, environment, input data, expected vs actual values, frequency (N of M), locale/timezone/currency, timestamp, logs. Each blank is a question for the reporter, not a guess. For "wrong total" bugs the decisive missing fields are usually input data, frequency and locale.
2. Confirm the failure on the baseline.
3. Run the `diagnosing-bugs` skill from here: it builds the feedback loop, minimizes the repro, finds the cause and fixes it behind a red-first regression test. Add these QA checks to its loop:
   - Push the minimized repro down to the smallest layer that still fails (UI flow, page, API call, unit).
   - Pin locale and TZ along with time, randomness and network. Prove determinism with 10 identical consecutive runs.
   - Bisect with `git bisect run` and one targeted test. The script exits 0 good, 1 bad, 125 skip (build broke, or the result flipped between two runs on one commit). For a flaky test, run it 10x per commit and call 10 passes good. Finish with `git bisect reset`.
   - Assert the real expected value in the regression test, and keep the test unedited between the red and green runs. After the fix passes, revert the fix, see red, restore, see green.

Cannot reproduce? Diagnose which case it is before closing:

| Finding | Evidence | Next |
|---|---|---|
| Flaky | Passes and fails on the same commit and env (run 50x) | Find the non-determinism |
| Environment-specific | Only under another TZ, locale, OS, browser, viewport, CI | Reproduce there, then minimize |
| Data-dependent | Only with the user's account or input | Get and replicate the data |
| Not reproducible | Nothing after matching env + data + repeats | Document what was tried in the ticket before closing |

Write back to the ticket: minimal repro, environment + SHA, expected vs actual with numbers, introducing commit, regression test path, evidence, determinism notes (seed, frozen time, stubs).

## Triaging CI failures

| Pattern | Likely cause |
|---|---|
| Fails consistently | Test or app bug |
| Intermittent on the same commit | Flaky or environment |
| Many tests at once | Environment or shared component |
| All tests | Environment |
| Random subset | Resource exhaustion |
| Tests pass, job fails | Post-test step (coverage gate, lint) |

- Test bug vs app bug: what changed recently (test or app code)? Do other tests of the feature fail too? Is the failing frame in test or app code?
- Environment vs real: `ECONNREFUSED` / OOM / timeout vs assertion or type error; affects all branches vs only the changed code; passes on retry vs fails again.
- When unsure: run locally (same result points to app), run in isolation (passes points to shared state), check CI resource metrics.
- Dedup by fingerprint, not by LLM similarity. Normalize the error (strip timestamps, UUIDs, PIDs, ports, temp paths, addresses, request ids; drop line numbers), hash exception type + message template + top 3-5 frames. Same fingerprint: merge. Matches a closed ticket: reopen as regression. Same fingerprint intermittently: flaky.
- A human approves creating, closing or merging tickets. A false dedup costs more than manual triage.

## Flaky tests

A test is flaky if it gives different results on the same commit and environment. One red run does not make it flaky.

Thresholds *(starting point)*: under 2% of runs flaky is healthy, 2-5% needs attention, over 5% and people stop trusting the suite.

| Diagnose in order | Signal | Fix |
|---|---|---|
| 1. Passes alone? | Fails only with others or under sharding | Order or data dependency: per-test data, cleanup in fixtures (they run on failure) |
| 2. Worse in CI with timeouts? | Timeouts, passes on retry | Timing: wait for a condition instead of a fixed sleep |
| 2b. Worse in CI with connection, OOM or disk errors? | Correlates with runner or load | Environment: mock externals, more resources |
| 3. Specific times? | Midnight, month end, DST | Mock the clock, compare relatively |
| 4. Visual comparison? | Screenshot flicker | Disable animations, mask dynamic regions, threshold |
| 5. External HTTP? | Correlates with third-party status | Mock the API |
| 6. None of the above | | Timing: find the un-awaited async operation |

Runbook: reproduce with 20 parallel runs (20/20 green locally points to environment); correlate with recent changes to the test, the code, lockfiles and CI config; classify; fix; verify with 50 green runs locally and in CI with the full suite still green and runtime up no more than 20%.

Quarantine: tag with ticket and date, run in a separate non-blocking job, fix within 2 weeks, release after 50 consecutive greens, watch for a week. Every quarantined test has a ticket and a weekly review. Quarantine over 5% of the suite signals a systemic problem. Retries detect flakiness; they do not fix it. `skip("flaky, fix later")` without a ticket is dead code.

Selectors (UI): test id > role + name > label > text > CSS class > positional XPath. Keep the suite mostly in the top three.
