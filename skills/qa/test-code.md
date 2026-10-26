# Test code

Read this to write or review test code, set up test data, or judge coverage and mutation results.

## Test code quality

- For writing tests (public interfaces, seams, what to mock, red before green), use the `tdd` skill. The rules below are the QA review checks on top of it.
- Unit tests are fast, isolated and deterministic: no network, disk or real DB; time and randomness frozen or seeded; no module-level mutable state.
- Arrange / Act / Assert. One concept per test (several asserts on that concept are fine). Name: "should [behavior] when [condition]".
- Assert specific values. `toBeTruthy` / `assert result` / `toBeDefined` are coverage theater.
- Doubles: prefer stubs, and use fakes for stateful dependencies. A suite that mocks internal collaborators stays green over broken wiring.
- Snapshots for render output and serialized structures, small (inline under ~20 lines), with matchers for ids and timestamps. CI must fail on a missing snapshot instead of writing it.
- An async test without `await` passes vacuously. Guard with assertion counts or linting.
- Prove the suite passes with the network disabled.

End-to-end tests:

- Each test covers a single user outcome, and its steps read as what the user does.
- Check the result the user gets: the URL they land on, the request that went out, the data on screen and what was stored. A click that fired proves little.
- Selectors live in page objects, one place each. Code used once and briefly stays inline.
- Make an unstable test stable with a sturdier locator, a wait on a real condition, or data of its own. Raising the timeout only hides the problem.

## Test data

- Each test owns its data: creates it, asserts on it, cleans up. This makes tests parallel-safe and order-independent.
- Factories for entities with a lifecycle (defaults + per-test overrides, sequences for unique fields, traits like `admin` / `inactive`). Static fixtures only for read-only reference data, recorded responses and golden files.
- Override only the fields the test cares about. Generate ids and emails.
- Seed every random source; fix timestamps.
- Never use production PII. Anonymization must be irreversible: no stored mapping back, and no partial masking (`j***@x.com`, last 4 card digits) presented as anonymization. Use provider test card numbers and synthetic identities. Watch for re-identification through combined fields.
- Seeds are idempotent (upsert on a natural key). Run the seed twice and confirm row counts do not change.
- Cleanup: transaction rollback for unit and integration tests with direct DB access; API deletes in reverse creation order for E2E (the app uses its own connections, so rollback cannot undo its writes).
- In a shared environment (staging, preview), give each parallel worker its own accounts and data. Tests that must share an account or change the same shared data run serially.
- Check: suite green with parallelism and random order; DB row counts return to baseline after the run.

## Coverage and mutation

- Coverage shows code ran, not that a regression would be caught. Branch coverage beats line coverage.
- Targets *(starting point)*: 75-85% overall, 90%+ on critical paths (payments, auth), 90%+ on new code in a PR. Past ~85% returns fall fast; 100% forces tests of impossible states.
- Ratchet: commit a baseline, fail PRs that lower it, raise it when coverage improves. Per-directory thresholds for critical code so a global number cannot hide payments at 30%.
- Exclude only generated code, type-only modules, migrations, stories and re-export barrels, each with a written reason. Excluding hard-to-test code to raise the number is cheating.
- Mutation testing shows whether tests verify anything. A surviving mutant is code that ran with no assertion constraining it. Run it incrementally on business logic and critical paths. Under ~70% mutation score: tests execute but do not verify. The key warning sign is high coverage with low mutation score.
- Triage gaps by risk: payments/auth now; validation and error paths next sprint; utilities when touched; UI formatting rarely.

## Reviewing tests

Structure each finding: what is broken; why (technical, not taste); what correct looks like; how to fix and how to prevent recurrence. Critique the work, not the person.

| Criterion | Not ready | Mergeable | Strong |
|---|---|---|---|
| Edge cases | Happy path only | Common failures | Boundaries, states, concurrency |
| Assertions | Truthy/defined checks | Specific values | Survives mutation testing |
| Speed (unit) | Minutes | Under 10s suite | Under 1s |
| Stability | Any known flake | No flakes | Deterministic by construction |
| Isolation | Shared state | Independent | Pure |

When someone proposes a practice ("we should do X, it's best practice"), ask: does it help in this context, who benefits, is the effect measurable, does it promote thinking or compliance, what happens if we skip it? Watch for coverage theater, checkbox QA and test-script slavery.

## Python and TypeScript tools

| Need | Python | TypeScript |
|---|---|---|
| Runner, tiers | pytest + markers | Vitest / Jest + tags or projects |
| Property-based | Hypothesis | fast-check |
| Mutation | mutmut, cosmic-ray | Stryker |
| Frozen time | time-machine, freezegun | `vi.useFakeTimers()` / `jest.useFakeTimers()` |
| Factories | factory_boy, polyfactory | fishery, @faker-js/faker |
| HTTP stubs | respx, responses | MSW (`onUnhandledRequest: "error"`) |
| Random order, repeats | pytest-randomly, pytest-repeat | `--sequence.shuffle`, Playwright `--repeat-each` |
| Branch coverage | coverage.py with `branch = true` | v8 / istanbul |
| E2E | Playwright for Python | Playwright |
