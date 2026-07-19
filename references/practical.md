# Practical Workflow

Use this workflow for ordinary local, reversible software work. It provides disciplined planning, minimal implementation, and direct repository evidence without pretending to provide an external trust chain.

## Guarantees and non-guarantees

Practical mode does not create a baseline manifest, authority grant set, packet, digest anchor, controller receipt, or attestation. Do not describe its checkpoint or final report as immutable, controller-observed, attested, or supply-chain secure.

It can detect ordinary scope drift and false completion claims through user-visible checkpoints, exact commands, Git status, and actual diffs. It cannot authenticate who performed an action, prove chronology, confine processes/network/credentials, observe ignored or repository-external side effects, or exclude swap-and-restore behavior.

If those properties matter, stop and route to `assured`.

## 1. Establish the checkpoint before editing

Read every applicable `AGENTS.md` and repository policy. Confirm the canonical Git root:

```bash
git rev-parse --show-toplevel
```

Inspect and preserve pre-existing staged, unstaged, renamed, deleted, and untracked work:

```bash
git status --porcelain=v2 -z --untracked-files=all
git diff --no-ext-diff --
git diff --cached --no-ext-diff --
```

Publish a concise user-visible checkpoint before the first write or subagent launch:

```text
assurance: practical
intent: change | debug | review
depth: focused | full
coordination: independent | shared
execution: main-only | read-only-proposals | isolated-candidates
objective: ...
non-goals: ...
allowed paths: ...
exact acceptance commands: ...
assumptions: ...
pre-existing dirty paths: ...
host capabilities: {all eleven known boolean values}
task DAG revision/digest: ... | none
dispatch: root-assign | atomic-claim | none
communication: root-relay | peer-message | none
downgrade reason: ... | none
```

The checkpoint is procedural, not externally authenticated. If objective, non-goals, allowed paths, acceptance, or a safety boundary must change, publish the proposed revision and obtain user approval before continuing. Never rewrite it only in the final report.

For `review`, allow no writes. For `debug`, include the reproduction command in exact acceptance.

## 2. Map only the necessary system

For `focused`, trace the real input, entry point, callers, shared correction point, state, direct consumers, failure path, and relevant tests. Construct the smallest realistic counterexample and choose an independent verification oracle.

For `full`, do the focused map and then add only relevant contract, compatibility, security, data, concurrency, performance, operability, user-journey, dependency, cost, and privacy lenses from [lenses.json](lenses.json). Include one orthogonal frame. Full depth does not automatically select assured or shared.

For debugging, reproduce first and fix the earliest shared cause. A local symptom is not the analysis boundary.

## 3. Negotiate capabilities and coordinate only when it adds information

The active main model chooses independent or shared coordination, then chooses every participant identity, count, and lane assignment. The Skill supplies no exact, default, or maximum count and no formula that derives count from budget.

If the user, controller, or runtime supplies an aggregate deadline, token, cost, or tool-call envelope, stay within it while preserving the main model's selection authority. Stop when evidence lanes are covered.

Record this exact capability vocabulary before spawning: `spawn`, `join`, `steer_child`, `peer_message`, `atomic_task_claim`, `per_spawn_model`, `enforced_readonly`, `isolated_candidate_workspace`, `canonical_write_block`, `independent_verifier`, and `max_depth_control`. Every value is boolean. Unknown or absent means `false`; product names and version strings are not capability evidence.

Derive execution from evidence:

- `main-only`: use when delegation has no marginal value or spawn/join is unavailable;
- `read-only-proposals`: workers inspect and return findings, plans, or patch text; only the main thread applies changes;
- `isolated-candidates`: use only when the host provides a real isolated candidate workspace and blocks canonical writes. Candidate workers may write their isolated copy, never the canonical checkout.

Git worktrees can reduce practical file conflicts, but linked worktrees share Git common metadata. They are not an assured security boundary. Never describe `read-only-proposals` as isolated candidate execution.

Before dispatch, publish an acyclic `CoordinationPlan` whose tasks have unique `id`, `objective`, `dependencies`, `read_paths`, `candidate_write_paths`, `acceptance_ids`, and `output_contract`. Publish assignments separately with `task_id`, runtime identity, and only host-supported optional model/reasoning choices. Enforce:

```text
child objective         ⊆ checkpoint objective
child read paths        ⊆ frozen analysis scope
child candidate writes  ⊆ frozen allowed writes
child acceptance IDs    ⊆ frozen acceptance
child capabilities      ⊆ host capability ceiling
```

Use `root-assign` unless the host exposes a genuine atomic claim operation. Tasks are not participant slots: an identity may execute several ready nodes sequentially. Do not recursively delegate.

New nodes require a DAG revision that names the prior digest and explains the change. If a revision expands objective, allowed writes, acceptance, or a safety boundary, stop for a user-approved checkpoint revision.

For shared work:

1. collect independent positions before revealing peer conclusions;
2. only after Round 1 is sealed, expose the same complete peer board to participants; use peer messaging only when the host reports it, use root relay only when child steering exists, and otherwise downgrade to sealed independent evidence with the reason recorded;
3. require a challenge or falsification attempt plus the cheapest discriminating check;
4. adjudicate by evidence, never vote or confidence.

A practical peer board, task DAG, and capability record are Agent evidence only. Without an external receipt they do not prove participant identity, read-only behavior, chronology, workspace isolation, or absence of nested delegation.

For isolated candidates, reject concurrent automatic integration when candidate write paths overlap, one path is an ancestor of another, or file/directory, rename/delete, modify/delete, or binary conflicts are possible. Serialize, select one, or return the node to the main thread. Recheck the candidate base, actual changed paths, diff, and scope before manual integration. Candidate tests are advisory; run every frozen acceptance command again against the integrated canonical state.

## 4. Implement with Ponytail full

After understanding the causal surface, stop at the first working rung:

1. `not-needed`
2. `reuse`
3. `stdlib`
4. `native`
5. `existing-dependency`
6. `minimal-custom`

Prefer one correction at the shared cause over repeated caller patches. Add no speculative abstraction, dependency, configuration, or scaffolding. Preserve trust-boundary validation, data-loss guards, required error paths, accessibility, explicit acceptance, and the smallest useful regression.

Do not write through a symlink, junction, reparse point, or linked parent without explicit authorization and an appropriate trusted-root model. A Git worktree may use practical mode, but treat its external Git metadata as outside practical verification.

## 5. Verify actual effects

Run only the exact acceptance commands from the checkpoint. Then inspect both unstaged and staged results:

```bash
git diff --check
git diff --cached --check
git status --porcelain=v2 -z --untracked-files=all
git diff --no-ext-diff --
git diff --cached --no-ext-diff --
```

Inspect every new untracked path directly. Compare final status with the initial status so pre-existing user changes are neither overwritten nor claimed. Require every Agent-created path to remain under the checkpoint allowlist. Re-read the integrated diff and check direct consumers and failure paths.

These checks do not cover ignored files, alternate data streams, external link targets, network effects, credentials, other repositories, or background processes.

## 6. Report without upgrading the claim

Report:

- selected assurance, depth, coordination, and derived execution mode;
- observed host capabilities, final DAG revision, assignments, and every downgrade reason;
- candidate disposition (`selected`, `rejected`, or `failed`) and integration evidence when candidates were used;
- actual Agent-created changed paths, separated from pre-existing changes;
- exact command, exit status, and useful output for each acceptance check;
- counterevidence sought and unresolved risks;
- shared disagreements and evidence-based resolution when applicable.

Do not use assured terminology for a practical result. A passing practical workflow means the scoped checks and observed Git diff agree, not that delivery is independently authenticated.

## Mandatory upgrade triggers

Stop before further editing and route to assured when any of these appears:

- security, authorization, authentication, secrets, credentials, privacy, or compliance;
- persistent-data/schema migration, deletion, recovery, or compatibility risk;
- concurrency, distributed consistency, public API/protocol, deployment, release, or infrastructure;
- irreversible or repository-external effects;
- acceptance that needs network, credentials, or writes outside the repository;
- cross-service/repository scope, material checkpoint revision, or high-impact uncertainty;
- unresolved high-impact subagent contradiction;
- an explicit request for immutable contracts, controller observation, audit, attestation, or high assurance.

If assured is required but its external controller, digest channel, pinned verifier, artifact isolation, or OS sandbox is unavailable, report the unmet precondition. Do not silently fall back to practical.
