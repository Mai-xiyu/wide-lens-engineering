---
name: wide-lens-review
description: Map and review complex software changes with independent adversarial lenses, repository-wide dependency coverage, evidence-backed synthesis, and a deterministic report-consistency gate. Use for cross-cutting implementation, migrations, risky PRs, repository-wide audits, multi-module ambiguous failures, security/concurrency/data-integrity changes, explicit global-view or devil's-advocate requests, and tasks that benefit from carefully managed subagents or forced divergent thinking. Do not use implicitly for simple questions, isolated low-risk edits, or ordinary single-file fixes.
---

# Wide-Lens Review

Prevent locally polished changes from hiding system-level failures. Separate exploration, criticism, synthesis, implementation, and verification so agreement never substitutes for evidence.

## Choose the review depth

Use the full workflow when any condition holds:

- Touch three or more modules, services, data stores, or user journeys.
- Change authentication, authorization, concurrency, persistence, migrations, public APIs, deployment, billing, privacy, or destructive behavior.
- Review an unfamiliar repository or an ambiguous failure.
- Receive a request for global review, adversarial review, divergent thinking, multiple agents, or high confidence.

For a small isolated change, keep the same evidence rules but use the light profile for exactly one mapping lane and one counterexample lane without subagents.

## Run the workflow

### 1. Freeze the contract

Write the objective, explicit non-goals, acceptance checks, safety constraints, and unresolved assumptions before proposing a patch. Label inferred requirements as assumptions. Do not silently widen scope.

### 2. Map the terrain

Inspect applicable `AGENTS.md` files, the diff or requested surface, manifests, entry points, callers, consumers, persistence boundaries, deployment files, and nearby tests. Produce a compact map containing:

- Changed or suspected surfaces.
- Upstream inputs and downstream consumers.
- State transitions and invariants.
- Failure boundaries, rollback path, and observability.
- Unknowns that could invalidate the plan.

Search broadly first, then read risk-ranked paths deeply. Do not equate the changed-file list with the impact surface.

### 3. Generate independent lenses

Run the bundled deterministic planner from this skill directory:

```bash
python scripts/diverge.py --task "<objective>" --path <changed-path> --risk <low|medium|high> --format markdown
```

Pass `--path` repeatedly. Omit paths only when none are known. Add `--profile light` for a small isolated change. The full profile emits every triggered risk lane by default; an explicit `--max-lenses` cap fails instead of silently dropping a matched risk. Use the emitted lanes as review packets; do not merge their missions before evidence is collected.

### 4. Preserve independence

For medium- or high-complexity work, explicitly spawn up to three reviewer runs total when subagents are available and the lanes can run independently. This skill requests that delegation. Group related lanes when more than three are emitted. Do not spawn nested reviewers. Keep the main thread responsible for the system map, decisions, edits, and final verification.

Start reviewers with fresh context (`fork_turns="none"` or the closest surface equivalent), and use an enforced read-only sandbox/custom agent when the surface supports one. A prompt saying "read-only" is not a security boundary. Give each reviewer only the frozen contract, relevant repository location, and one or more emitted lanes. Do not reveal the proposed solution, expected finding, or another reviewer's output.

If fresh context or read-only enforcement is unavailable, disclose that limitation and hash every in-scope file before and after review, including tracked, untracked, and ignored files. A Git diff alone is insufficient. Keep reviewers from editing by instruction and reject results if the hash manifest changes. If the scope cannot be hashed safely, do not delegate without an enforced read-only sandbox and do not claim strict isolation.

Require the lane JSON described in [references/protocol.md](references/protocol.md). Wait at most ten minutes for a reviewer, then explicitly stop, interrupt, or cancel it before fallback so stale work cannot keep consuming budget or return later. Retry one failed or malformed reviewer once across the entire workflow, then reassign its lanes to a sequential main-thread pass. Stop with `blocked` only when required evidence remains inaccessible; never wait indefinitely.

Use sequential isolated passes when subagents are unavailable. Never parallelize overlapping writes, dependent steps, trivial tasks, or work that requires continuous shared context.

### 5. Synthesize by causal evidence

Normalize every claim into the report schema in [references/protocol.md](references/protocol.md). Deduplicate findings only when they share the same causal chain, not merely the same file. For disagreements:

1. State the competing claims.
2. Identify evidence that would falsify each claim.
3. Run the cheapest discriminating check.
4. Record the resolution and remaining uncertainty.

Treat consensus as a signal of correlation, never as proof. Prefer a failing test, reproduction, trace, type error, static-analysis result, or authoritative contract over reviewer confidence.

### 6. Implement through one owner

Choose the smallest patch that satisfies the frozen contract and preserves unrelated behavior. Keep one editing owner unless files are fully disjoint and integration order is explicit. Re-read the complete diff after integration; reviewers may have inspected an earlier state.

### 7. Verify from narrow to broad

Run, in order where applicable:

1. A targeted regression or reproduction.
2. Tests for changed invariants and failure paths.
3. Static analysis, lint, and type checks.
4. The relevant broader suite.
5. A final diff and repository-status review.

Record exact commands, integer exit codes, and outcomes. If a check cannot run, record a null exit code, the blocker, and reduce confidence; never translate `not run` into `passed`.

### 8. Enforce the completion gate

Write the final review record as JSON using [references/protocol.md](references/protocol.md), then run:

```bash
python scripts/check_review.py --packet <packet.json> --report <report.json>
```

Do not claim completion unless the command exits zero and the recorded commands were actually run. Resolve missing lane coverage, evidence-free claims, coverage/finding contradictions, open critical/high findings, unresolved disagreements, and insufficient verification. The gate validates only internal consistency of a self-reported record: it does not execute commands, authenticate evidence, enforce reviewer sandboxes, or prove correctness. Describe these limits and residual risk separately.

## Keep the process bounded

- Default to at most three reviewer runs total, one retry total, ten minutes of reviewer wall time, and no nested reviewers. Honor a lower user budget; request permission before exceeding these defaults.
- Stop expanding when all selected lanes have concrete evidence, acceptance checks pass, and no unresolved high-impact contradiction remains.
- Add a new lane only when new evidence exposes an uncovered causal surface, and keep it within the same three-run budget by regrouping or using a main-thread pass.
- Do not ask multiple identical agents the same broad question and vote on the answer.
- Do not use reviewer count, token spend, or report length as a quality metric.

## Resources

- Run `scripts/diverge.py` to force risk-sensitive, orthogonal review packets.
- Run `scripts/check_review.py` to reject incomplete review records.
- Read [references/protocol.md](references/protocol.md) before emitting reviewer or final report JSON.
- Run `tests/run_eval.py` after modifying the skill or scripts. Keep its fixed-case pass-rate threshold at `0.98` or higher; never present that rate as real-world defect recall or general workflow accuracy.
