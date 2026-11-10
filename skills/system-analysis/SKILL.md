---
name: system-analysis
description: Analyze business requirements and turn them into implementation specs. Use when a brief, ticket, or PRD needs clarification, system behavior, technical contracts, and acceptance criteria before development.
---

# System analysis

Turn the business requirements into a reviewable handoff. Follow the requested format and destination or the project's conventions. When neither supplies a destination, return one Markdown spec in the response.

1. **Scope.** Extract the business outcome, actors, in-scope changes, exclusions, and constraints. Keep source links or IDs. For an existing system, inspect affected behavior, contracts, data, and ADRs. Record what changes and what callers already rely on.
2. **Clarify.** Flag conflicts and gaps that would change scope, business rules, contracts, or acceptance. Ask focused questions and draft the independent portions while awaiting answers. Record each unresolved decision and what it blocks. Label inferred business rules and proposed technical choices as provisional until confirmed.
3. **Specify.** Use the sections below, combining or omitting sections as scope permits. Specify behavior at the boundary where it can be verified. Record technical choices that affect contracts, data, or coordination and leave local implementation details to the implementer.
4. **Check readiness.** Map every in-scope business requirement to observable behavior and acceptance evidence. Check that each required behavior has a source or confirmed decision. Record any unconfirmed assumption that changes an expected acceptance result as a material open decision. Mark the handoff **Draft** while any material decision is open and **Ready for implementation** once the mapping is complete and all material decisions are resolved. End at the spec handoff.

## Spec contents

- **Scope and sources.** List the outcome, actors, boundaries, and business requirement references.
- **Behavior.** Define triggers, preconditions, rules, outcomes, and relevant failure paths. Use a decision table or state transitions when outcomes depend on combinations or prior state.
- **Implementation contracts.** Identify affected system boundaries and required interface, data, or integration changes. Specify inputs, outputs, validation, errors, and compatibility where affected. Cover permissions, retries, partial failure, concurrent operations, and migration where they can change outcomes. State measurable quality targets when agreed.
- **Acceptance.** Write scenarios with initial conditions, an action, and observable results. Reference the behavior covered and include consequential edge cases.
- **Open decisions.** List each question and the part of the spec or implementation that depends on it.
