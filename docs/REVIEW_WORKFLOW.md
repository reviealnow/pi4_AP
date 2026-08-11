# Cross-review workflow — Opus 5 × GPT-5.6

Two LLM coding agents implement this repo. Neither model merges its own work;
Nelson merges after the cross-review passes.

## Roles per milestone

| Milestone | Author | Reviewer |
|---|---|---|
| M1 scaffold + serial core | Opus 5 | GPT-5.6 |
| M2 console UX + handoff | GPT-5.6 | Opus 5 |
| M3 parsers + Overview/CPU pages | Opus 5 | GPT-5.6 |
| M4 Wi-Fi/SSID/Survey pages | GPT-5.6 | Opus 5 |
| M5 fleet + deploy + perf | Opus 5 | GPT-5.6 |

Swap freely if one model is clearly stronger on a given area — but author and
reviewer must always be different models.

## Rules

1. **One milestone = one branch = one PR** into `main`. Conventional Commits.
2. The author must run the milestone's acceptance test (SPEC §5) and paste
   the evidence (command + output) into the PR description. "It should work"
   is not evidence.
3. The reviewer reviews against the checklist below, replies with
   `APPROVE` or `REQUEST_CHANGES` + concrete `file:line` findings, and must
   actually run the acceptance test independently when feasible.
4. Max two review rounds; unresolved disagreements escalate to Nelson with
   both positions stated in ≤ 5 lines each.
5. Scope discipline: anything not in SPEC.md is a new decision — raise it in
   the PR as a question, don't silently implement it.

## Reviewer checklist

- [ ] Acceptance criteria of the milestone met, with evidence.
- [ ] Raw serial logging can never be stopped by a parser/UI failure (P0).
- [ ] Perf constraints (SPEC §2) not obviously violated (no busy loops,
      no per-line WS sends, no heavy deps added).
- [ ] `requirements.txt` / `package.json` diff contains no new runtime deps
      beyond SPEC §2 without a D-decision.
- [ ] Event contract (WS message names/shapes) stays compatible with
      DUT_browser conventions; changes are called out explicitly.
- [ ] Tests updated; fixtures used for parser changes.
- [ ] Works on Pi constraints: no Node on the Pi, dist/ story intact,
      systemd/install.sh still idempotent (M5).

## Prompt template — author

```
You are implementing milestone M<N> of the pi4_AP repo.
Read docs/SPEC.md fully; it is the contract. Do not implement anything
outside M<N> scope. Reuse code from https://github.com/reviealnow/DUT_browser
(Apache-2.0, same owner) instead of rewriting where SPEC says to port.
Deliver: branch m<N>-<slug>, passing tests, and a PR description containing
the acceptance evidence required by SPEC §5.
Open questions -> list them under "Decisions needed" in the PR; pick the
SPEC §8 default and proceed.
```

## Prompt template — reviewer

```
You are reviewing PR #<X> (milestone M<N>) of pi4_AP. You did not write it.
Judge only against docs/SPEC.md and docs/REVIEW_WORKFLOW.md checklist.
Run the milestone acceptance test yourself if the environment allows.
Output: APPROVE or REQUEST_CHANGES, then findings as `file:line — issue —
suggested fix`, ordered by severity. Do not restyle working code.
```
