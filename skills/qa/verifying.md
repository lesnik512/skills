# Verifying a change and regression

Read this when a ticket or PR lands in testing, or when choosing, tiering or pruning regression tests.

## Verifying a change

Use this when a ticket or PR lands in testing and you have to say whether it does what it claims.

1. Confirm the environment runs the change. Check the deployed build or commit SHA against the PR. A green run on a stale deploy is a false pass.
2. Map each acceptance criterion to a check. If a criterion is vague or missing, ask the product owner; an expected result needs a source ([failures.md](failures.md#bug-reports)).
3. Run the targeted checks, then the regression for the mechanics the change touches ([Regression and suite curation](#regression-and-suite-curation)).
4. Write a new test only for a criterion whose user outcome no existing test checks. A new ticket number reuses the existing test.
5. Review new or changed test code against [test-code.md](test-code.md).

Classify every failure into one of four groups:

| Group | Meaning | What to do |
|---|---|---|
| Confirmed | Targeted checks passed | Report with counts |
| Regression of this change | Reproduces on the same build and traces to the changed logic | Return the change with a bug report |
| Independent problem | Belongs to other functionality, shown by a separate symptom or an existing ticket | Report it with the ticket and why it is unrelated |
| Flaky or environment | Passes on retry on the same build, or the environment is down | Diagnose ([failures.md](failures.md#flaky-tests)); do not count it as a pass or a regression |

Report an independent problem separately, and still give the verdict on this change. When a criterion that matters stayed unchecked, the verdict is blocked: name the blocker and list what did pass.

The verification is finished when each acceptance criterion maps to a check with a recorded result, the regression for the touched mechanics has run and appears in the summary, any new test fills a real gap and passed review, and every line of the summary comes from a run.

Summary template (start blank and fill each status from the run):

```
Verification of <ticket> on <environment>, build <SHA>, <date>

Targeted checks: <passed>/<total>
- <criterion or user outcome>: <passed | failed: link to bug>
Regression: <passed>/<total>
- <affected mechanic>: <result>
Tests: ran <paths>; added <link to PR or "none needed: covered by <path>">
Test code review: <findings, or "no blocking findings">
Independent known issues: <ticket, symptom, why unrelated>
Run artifacts: <link>
```

When the change fails, the summary opens with the failure in place of the pass counts: the steps, what was expected and what happened, the environment, and a link to the run artifacts or the bug filed.

## Regression and suite curation

- Tiers by business risk and defect history, not runtime. Smoke: every push, minutes. Core: PR gate. Extended: nightly and pre-release. Encode tiers as markers or tags, not file moves.
- Change-based selection misses transitive dependencies; keep a full run nightly and watch for escapes traced to skipped tests.
- "0 regressions" can mean nothing ran. Check the executed test count.
- Every production bug becomes a regression test, written red first.
- Regress the mechanics next to the change, not only the changed screen:

| Change | Also regress |
|---|---|
| Table or list | Pagination, filters, sorting, empty state |
| Navigation between screens | URL and query params, target page load, back button |
| API contract | Error handling, unknown or incompatible params, existing clients |
| Booking, slots, availability | Availability display, edit and cancel, calendar navigation |
| Permissions | Each role, including the one that must be denied |

Pruning a suite (destructive, go slow):

- Equal coverage is not redundancy. Use per-test branch coverage. A test that uniquely covers any line or branch stays.
- Subsumption (A covers everything B does) is only a candidate. Confirm with mutation testing: delete B only if every mutant B kills, A kills too.
- Never-failing tests are not low-value by default; they may guard stable code. Flaky tests get quarantined and fixed, not deleted to clean up CI.
- Near-duplicate tests often differ in the one assertion that matters. Review each test in a cluster by hand; never auto-delete one.
- Process: quarantine first; named approver; observe 2 sprints or releases; restore on any escaped defect in code only those tests covered; then delete in small batches with a record per test (reason, superseded by, coverage delta, mutation check, approver, restore command). If you cannot fill the record, you cannot delete the test.
