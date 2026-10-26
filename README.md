# skills

Agent skills by Artur Shiriev, packaged for Claude Code and other agents that read `SKILL.md` files.

| Skill | What it does |
|---|---|
| [qa](skills/qa/SKILL.md) | QA practitioner playbook: risk and strategy, test design and exploratory sessions, test code review, verifying a ticket or PR, regression, bug reports, CI and flaky-test triage, release go/no-go, postmortems |

## Install

Claude Code, as a plugin:

```
/plugin marketplace add lesnik512/skills
/plugin install lesnik512-skills@lesnik512
```

Other agents, through [skills.sh](https://skills.sh):

```bash
npx skills@latest add lesnik512/skills
```

Or copy `skills/qa` into `~/.claude/skills/` by hand.

## qa

The skill loads a short `SKILL.md` with ground rules and a routing table. Each task (planning, verifying a change, writing a bug report, and so on) points to one reference file, so the agent reads only what the task needs. Numbers marked *(starting point)* are defaults to replace with your own data.

It hands two jobs to skills from [mattpocock/skills](https://github.com/mattpocock/skills) when they are installed: writing new tests goes to `tdd`, and finding the cause of a bug goes to `diagnosing-bugs`. Without them, the agent does that work unguided.

The content is aggregated and edited from three MIT-licensed QA skill collections, with credits in [SOURCES.md](skills/qa/SOURCES.md).

### Trigger eval

[evals/qa/eval_set.json](evals/qa/eval_set.json) holds 20 queries: 10 that should load the skill and 10 near-misses that belong to other skills or to no skill. With skill-creator's `run_eval` (3 runs per query), the current description passes all 20: 28 of 30 should-trigger runs loaded `qa`, and 1 of 30 near-miss runs did.

To rerun it, use `--num-workers 1`. In parallel, the workers' temporary copies of the skill compete with each other and most hits are miscounted. Move any installed copy of `qa` aside for the run, for the same reason.

## License

MIT, including the notices of the upstream works. See [LICENSE](LICENSE).
