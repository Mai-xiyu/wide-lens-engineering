---
name: wide-lens-engineering
description: "Opt-in engineering workflow for explicit Wide-Lens requests: practical or externally anchored assured delivery, elastic task-DAG subagents, sealed adversarial analysis, and isolated candidates. Do not invoke implicitly for ordinary coding, review, explanation-only, or non-software tasks."
---

# Wide-Lens Engineering

Use this Skill only after the user explicitly invokes `$wide-lens-engineering` or explicitly asks for the Wide-Lens workflow. The Codex metadata disables implicit invocation. If this Skill is selected accidentally, return to the host's ordinary workflow without loading any reference.

This file is a router, not the complete protocol. Load exactly one current workflow reference after choosing the route. Keep legacy v4 material unloaded unless an existing v4 artifact must be reproduced or verified.

## Select intent and independent axes

Choose one intent:

- `change`: implement, refactor, migrate, or change architecture;
- `debug`: reproduce, find the earliest shared cause, fix it, and keep regression evidence;
- `review`: inspect and report without writing.

Choose these axes independently before a repository write or subagent launch:

- `assurance`: `practical | assured`
- `depth`: `focused | full`
- `coordination`: `independent | shared`

Derive execution from observed host capabilities; it is not a fourth user axis:

- `main-only`
- `read-only-proposals`
- `isolated-candidates`

Depth does not choose assurance or coordination. Coordination does not prescribe execution or participant count.

### Assurance route

Use `practical` for local, reversible, clearly scoped work whose acceptance can be observed directly in the repository.

Use `assured` for security or credential boundaries, privacy/compliance, persistent-data or schema-migration risk, concurrency/distributed consistency, a public-api or deployment boundary, irreversible effects, an uncertain high-impact classification, or an explicit immutable/audit/attestation request. Never silently downgrade required or requested assurance. When the external trust root is unavailable, report that the assured preconditions are unmet rather than manufacturing a claim.

### Depth route

Use `focused` for an isolated correction with a short causal surface. Trace the real entry point, shared correction point, direct consumers, failure path, smallest counterexample, and an independent oracle.

Use `full` for cross-module, ambiguous, high-blast-radius, or adversarial work. Add only relevant risk lenses and one orthogonal frame.

### Coordination route

The active main model alone decides whether to use subagents and, if used, their identities, count, and lane assignments. This Skill contains no exact, default, or maximum participant count. Tasks and agents are not one-to-one.

Select `shared` only when sealed independent positions followed by peer challenge are likely to add discriminating evidence. Otherwise select `independent`. Do not create a team merely because the host can spawn one.

## Load one workflow

- For `practical`, read [references/practical.md](references/practical.md) completely and follow it. Do not load an assured protocol.
- For new `assured` work, read [references/protocol-v5.md](references/protocol-v5.md) completely before baseline capture, artifact creation, editing, or delegation.
- Read frozen [references/protocol.md](references/protocol.md) only to reproduce or verify a legacy v4 artifact. Never add v5 fields to v4 artifacts.

The selected reference owns detailed capability negotiation, task-DAG rules, candidate isolation, receipts, validation, and reporting. Do not duplicate those details here.

## Preserve these invariants

1. Read applicable repository policy and preserve pre-existing work.
2. Treat unknown host capabilities as unavailable; never infer them from a product or version name.
3. Only the main integrator writes the canonical checkout. Analysis workers are read-only. Candidate workers may write only a host-proven isolated disposable workspace and never the canonical checkout.
4. Do not recursively delegate in this release.
5. A shared round seals independent positions before cross-agent challenge. Resolve contradictions with discriminating evidence, never votes or confidence.
6. A practical checkpoint is procedural evidence, not an attestation. Assured claims require the external controller, independent digest channel, pinned verifier, artifact isolation, and OS sandbox described by protocol v5.
7. For debugging, fix the earliest shared cause rather than patching visible callers one by one.
8. Verify the final integrated state with the frozen acceptance checks and inspect the actual diff before claiming completion.

Apply the embedded Ponytail convergence rule after understanding the causal surface:

```text
not-needed -> reuse -> stdlib -> native -> existing-dependency -> minimal-custom
```

Stop at the first rung that satisfies acceptance. Do not load another minimalism Skill merely because this rule was inspired by Ponytail; load `$ponytail` only when the user explicitly asks for it. Never simplify away a trust-boundary check, data-loss guard, required failure path, accessibility requirement, explicit acceptance criterion, or the smallest useful regression.

## Report

Report the selected intent, assurance, depth, coordination, derived execution mode, actual changed paths, exact command outcomes, counterevidence sought, and unresolved risks. When delegation was used, also report the observed capabilities, task-DAG revision, assignments, downgrade reasons, and candidate dispositions required by the selected workflow.

Do not claim completion while an exact acceptance command fails or a high-impact contradiction remains. Do not upgrade practical evidence into an assured claim.
