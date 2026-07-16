---
name: wide-lens-engineering
description: Deliver software changes, debugging, refactors, migrations, architecture work, and reviews through a practical low-overhead workflow for ordinary repository work or an assured externally anchored workflow for high-risk and audit-required delivery. Use for coding, code review, root-cause debugging, adversarial analysis, forced divergent thinking, or optional shared-subagent deliberation. Default to practical only for local, reversible, clearly scoped work; require assured for security, credentials, privacy, data migration, concurrency, public APIs, deployment, irreversible effects, or explicit immutable/audit/attestation requests. Do not use for non-software or explanation-only requests.
---

# Wide-Lens Engineering

Deliver the smallest correct change while matching ceremony to risk. Keep ordinary coding fast; reserve the externally anchored v4 protocol for work that needs its trust properties.

## Select intent and three axes

Choose one intent:

- `change`: implement, refactor, migrate, or change architecture.
- `debug`: reproduce, locate the earliest shared cause, fix it, and preserve regression evidence.
- `review`: inspect and report; do not write.

Choose these axes separately before a repository write or subagent launch:

- `assurance`: `practical | assured`
- `depth`: `focused | full`
- `coordination`: `independent | shared`

### Choose assurance

Use `practical` only when all of these hold:

- the change is local, reversible, and has no external side effect;
- objective, allowed paths, and exact acceptance commands are clear before editing;
- no security, authorization, credential, privacy, compliance, persistent-data, schema-migration, deletion/recovery, concurrency, distributed-consistency, public-API, deployment, infrastructure, or irreversible boundary is involved;
- the user did not request an immutable contract, external proof, audit, attestation, or high-assurance delivery.

Use `assured` if any condition above fails, a high-impact classification is uncertain, acceptance touches network/credentials/repository-external state, scope or acceptance must materially change, or shared analysis leaves a high-impact contradiction unresolved. Never silently downgrade an explicit or required `assured` workflow. If its controller, independent digest channel, pinned verifier, artifact isolation, or OS sandbox is unavailable, report that the assured preconditions are unmet.

### Choose depth

Use `focused` for an isolated correction with a short causal surface. Map the real entry point, shared correction point, direct consumers, failure path, smallest counterexample, and independent verification oracle.

Use `full` for cross-module, ambiguous, high-blast-radius, or adversarial work. Add contract boundaries, relevant risk lenses, rollback/operability, and one orthogonal frame. Depth does not choose assurance or coordination.

For the assured v4 adapter only:

- map `focused` to wire `profile=light` only when risk is low, coordination is independent, and no triggered lane would be hidden;
- otherwise map to wire `profile=full` and disclose the compatibility promotion;
- map `full` to wire `profile=full`;
- never add `assurance` or `depth` fields to v4 contract, packet, receipt, or report schemas.

### Choose coordination

The active main model alone decides whether to use subagents and, if used, their identities, count, and lane assignments. Decide from marginal information value, causal breadth, uncertainty, available concurrency, latency, and cost. Never encode an exact, default, or maximum participant count in this Skill or its artifacts. Shared coordination requires at least two identities only because one participant is not a discussion.

Accept an optional aggregate resource envelope only from the user, controller, or runtime. Do not invent one, derive participant count from it, or treat spend as a quality metric. Stop delegation when selected evidence lanes are covered and no high-impact contradiction remains.

Keep every subagent read-only, prohibit recursive delegation, and keep the main thread as the only editing and integration owner. Resolve disagreements with discriminating evidence, never votes or confidence.

## Route with progressive disclosure

Read only the selected workflow reference before acting:

- For `practical`, read [references/practical.md](references/practical.md). Do not load the assured protocol merely because this Skill triggered.
- For `assured`, read [references/protocol.md](references/protocol.md) completely before baseline capture, artifact creation, editing, or delegation. Treat its controller, digest channel, verifier, artifact store, and OS sandbox as the trust root.

Do not mix trust claims. A practical checkpoint is procedural evidence, not an immutable packet or attestation. An assured run that lacks its external trust root is not assured.

## Apply universal engineering rules

Before acting:

1. Read every applicable `AGENTS.md` and repository policy.
2. Inspect the initial repository state and preserve unrelated user changes.
3. Trace inputs, callers, state, outputs, consumers, failure paths, deployment surfaces, and relevant tests in proportion to selected depth.
4. Separate sourced requirements from inference. Ask for approval when an inference would determine acceptance, write scope, a non-goal, or a safety boundary.

Apply Ponytail `full` after understanding the flow. Stop at the first rung that holds:

1. `not-needed`
2. `reuse`
3. `stdlib`
4. `native`
5. `existing-dependency`
6. `minimal-custom`

Never simplify away a trust-boundary check, data-loss guard, required error path, accessibility requirement, explicit acceptance criterion, or the smallest useful regression.

Only the main thread writes. Remain inside the selected workflow's approved scope, handle failure paths, and re-read the integrated diff. For debugging, fix the earliest shared cause rather than one visible caller. For review, write nothing.

Report actual changed paths, exact command outcomes, counterevidence sought, unresolved risks, and the selected assurance/depth/coordination. Do not claim completion while an exact acceptance command fails or a high-impact contradiction remains.

## Validate this Skill after changes

Run:

```bash
python -B tests/run_eval.py --threshold 1.0 --json
python -B tests/run_forward_eval.py --threshold 1.0 --require-no-skips --json
```

Keep the fixture threshold at `1.0`, never below `0.98`. Fixture pass rate is not universal defect recall or model accuracy.
