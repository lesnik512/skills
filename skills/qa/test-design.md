# Test design

Read this to decide what to test, design cases, run an exploratory session or write manual test cases.

## What to test: heuristics

Use SFDIPOT to generate ideas, FEW HICCUPPS to recognize problems, tours to navigate.

### SFDIPOT (Bach, HTSM product factors)

| Factor | Look at | Example ideas |
|---|---|---|
| Structure | Code, config, data files, dependencies | Corrupt config should degrade gracefully; upgrade a dependency |
| Function | Calculations, error handling, security, state transitions, transformations | 1000-line order; injection in search; retry after state timeout |
| Data | Input, output, persistence, boundaries, formats | 100MB upload; emoji round-trip; 2038-01-19 |
| Interfaces | UI, APIs, CLI, protocols, integrations | Malformed JSON returns 400 with details; drop WebSocket mid-transaction; 320px viewport |
| Platform | OS, browser, runtime, DB, queues, network | 500ms latency; low-memory host; old browser |
| Operations | Real usage: common, extreme, admin, recovery, install/config | 10k concurrent sessions; revoke admin mid-session; restore yesterday's backup |
| Time | Concurrency, scheduling, ordering, timeouts | Two updates to one record; job at the DST switch; cancel mid-sequence and inspect partial state |

Give the domain up front; SFDIPOT without domain context yields generic ideas.

### FEW HICCUPPS oracles (Bolton)

A problem is an inconsistency with one of these:

- Familiarity: a pattern of problems you have seen before
- Explainability: you cannot explain the behavior
- World: what you know about the real world (time zones, locales, physics, money)
- History: earlier versions of the product
- Image: the brand and quality bar the organization wants
- Comparable products: competitors, sibling products, alternative algorithms
- Claims: what docs, specs, tooltips, marketing or people said
- User desires: what a reasonable user would want
- Product: other parts of the same product (error style, terminology)
- Purpose: explicit and implicit uses of the product
- Statutes and Standards: laws, regulations, WCAG, RFCs, GDPR

### Tours

Business District (critical flows), Money (revenue features), Historical and Bad Neighborhood (where bugs clustered before), Intellectual (most complex features), FedEx (follow one piece of data end to end), Landmark (jump between key features), Guidebook (follow the docs literally), Rained-Out (behavior when things fail), Saboteur (break things deliberately), Obsessive-Compulsive (repeat actions), Couch Potato (do the least possible: defaults, skips).

Assign Bad Neighborhood or Saboteur explicitly; agents drift to the happy path otherwise.

### Idea banks

- Numbers: 0, 1, many; min-1, min, min+1, max-1, max, max+1; negatives; overflow; decimal precision (0.1 + 0.2).
- Strings: empty; one char; max length and max+1; whitespace only; leading/trailing whitespace; emoji, RTL, CJK; control and null chars; SQL and XSS fragments; 10,000 chars.
- Time: 00:00 and 23:59:59; DST transitions; Feb 29; Jan 31 to Feb 1; 1970 epoch; 2038-01-19T03:14:07Z; DD/MM vs MM/DD.
- Collections: empty; one; exactly page size and one over; more than can render; duplicates.
- State: skip a step (deep link to step 3); go back; interrupt (close, network drop); repeat (double submit, refresh during save); two users on the same transition; retry after error; is data corrupted after failure?
- Environment: network loss mid-operation; slow network; full disk; malformed upstream responses; 5xx; rate limited; expired token; wrong file type.

## Test design techniques

| Situation | Technique |
|---|---|
| Numeric or ordered ranges | Equivalence partitioning + boundary values |
| Several interacting conditions | Decision table |
| Workflows, lifecycles | State transition (cover every valid and invalid transition) |
| Many parameters | Pairwise (3 params x 3 values: about 9-12 cases instead of 27) |
| Invariants over large input spaces | Property-based testing |

- Partitions come from the requirements. Edge cases you invent beyond them belong to error guessing, labeled as such.
- Boundary set: min-1, min, min+1, max-1, max, max+1. Add a mid value if the partition has internal behavior.

### Write test ideas as actions

Write each idea as `[action verb] [specific trigger]; [outcome verb] [observable result]` instead of "Verify X".

| Weak | Strong |
|---|---|
| Verify API returns error | Send POST /orders with empty body; assert 400 and field-level error for `items` |
| Verify data saves | Insert record; query by id; confirm every field matches |
| Verify performance | Run 100 concurrent requests; measure p99 < 200ms |
| Verify timeout | Inject 5s upstream delay; confirm client error after 2s |
| Verify validation | Enter `2024-02-30`; observe "invalid date" under the field |

Every idea needs an observable outcome. "Check it works" is as weak as "Verify it works".

## Exploratory testing

Learning, test design and execution at the same time, inside a written charter. Structured freedom: the charter fixes target and goal; inside it, follow leads.

Charter: `Explore [target] with [resources] to discover [information]`. Target narrower than "the app"; resources name specific data, tools or conditions; the goal is something to learn, not to prove; one session can cover it.

| Situation | Charter pattern | Time split (min) |
|---|---|---|
| New feature | Explore [feature] with various roles and data to discover requirement gaps | 15 orient, 40 heuristics, 20 boundaries/errors, 15 notes |
| Regression | Explore [area] after [change] to discover regressions at integration points | 10 read diff, 20 changed area, 20 integrations, 15 smoke, 15 notes |
| Vague bug | Explore [area] with [reported conditions] to discover exact repro steps | 10 read report, 15 reproduce, 20 minimize, 15 related areas, 15 notes |

Session: time-box 45-90 min (agents: 45 max, then debrief, to avoid context overflow). Set up data for empty, typical and large states and accounts for each role before starting. Start with a short happy-path survey as a baseline.

When something odd happens: stop, reproduce, vary one thing (data, user, browser), capture evidence (screenshot, console, network), classify as BUG / QUESTION / IDEA / RISK / NOTE, then decide whether to dig now or note and move on.

The notes are the deliverable. Write them during the session: time, action/input, observation, tag, follow-up. Include *why* you tried each thing; agents tend to omit it.

Debrief: charter, planned vs actual time, build; each charter area marked covered / partial / unexplored (with reason); bugs, questions, test ideas; what surprised you; next charters.

Afterwards: every reproducible bug becomes an automated regression test at the lowest level that catches it (unit for logic, integration for boundaries, E2E for UI flow), referencing the bug id, seen failing on the buggy build and passing on the fix.

Explore when requirements are new or moving, reports are vague, behavior is subjective, or the goal is finding bugs. Automate when behavior is stable, pass/fail is clear, it must run on every change, or it guards a known bug.

## Writing manual test cases

- Parts: title (feature, scenario, outcome), preconditions, discrete steps, one observable expected result per step, concrete test data.
- One case per business rule: valid, expired and used discount codes are three cases.
- Lint for smells: vague action ("click around"), vague data ("some data"), unobservable expected ("works", "looks right", "no problems"), unbounded wait ("wait a bit"; use "within 10s" or "until the row appears"), several assertions in one step, expected that restates the action ("Save is clicked"). A good expected names an exact value, element state, message, status code, count, or time-bounded condition.
- Automate stable, frequent, smoke and regression cases. Keep manual what is exploratory, rarely run, high-churn or judgment-based.
