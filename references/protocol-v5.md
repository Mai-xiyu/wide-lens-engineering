# Wide-Lens Assured Elastic Protocol v5

This document is normative for new `assured` runs. It composes elastic task-DAG execution around the frozen contract and baseline rules from protocol v4 without changing any v4 artifact, schema, digest, command, or verifier behavior.

## 1. Trust claim and required infrastructure

Packet v5 is a high-assurance delivery protocol, not a prompt convention. A run is `assured v5` only when all of the following are real external services or controller-observed properties:

- a controller identity that is not an execution Agent;
- an independent digest channel for packet, verifier bundle, orchestration envelope, execution receipt, and verification receipt;
- a pinned verifier bundle stored outside the target repository;
- external, plain artifact files with role separation, except an unchanged predecessor capability/resource/sandbox file may be reused by exact path;
- OS process isolation for candidates, verifier, and gate;
- an isolated candidate workspace service that does not mount the target repository, shared Git metadata, credentials, or the artifact store;
- canonical-write blocking for every actor except the main integrator;
- complete event capture, atomic controller leases, resource accounting, and orphan detection;
- a fresh-context, no-write verifier whose identity is disjoint from all execution actors.

An Agent-created JSON statement is not proof of those properties. The controller and independent digest channel are the trust roots. If any required service is missing, the gate fails before running an acceptance command. There is no practical-mode downgrade that may retain an `assured` claim.

The gate is not itself an OS sandbox. The controller must launch the pinned gate inside the anchored sandbox profile. Acceptance commands remain executable code; they run only after every structural and receipt check passes, with network and credential access disabled, and the gate rejects any repository-state change they cause.

## 2. Frozen authority inputs

### 2.1 Contract and baseline

Packet v5 embeds the complete `contract v1` and refers to the complete `baseline manifest v2` exactly as defined by [protocol v4](protocol.md). The contract remains the only source of objective, intent, non-goals, analysis scope, allowed and forbidden writes, acceptance criteria, safety constraints, assumptions, repository identity, approval, and revision authority.

The execution Agent may not add, reinterpret, or replace any contract field. A semantic change to objective, write scope, acceptance, non-goal, safety boundary, or authority requires a new user-approved contract revision and packet lineage.

### 2.2 Packet v5

The v5 planner calls the frozen v4 builder with `profile=full`, changes only `version` from `4` to `5`, adds `orchestration_policy`, and recalculates `packet_sha256`. Every other field must equal a fresh v4 reconstruction byte-for-byte under canonical JSON.

`orchestration_policy` has this exact value:

```json
{
  "selection_owner": "active-main-model",
  "participant_count_prescribed": false,
  "runtime_may_narrow_only": true,
  "analysis_worker": "read-only",
  "candidate_worker": "isolated-workspace-only",
  "canonical_writer": "main-integrator",
  "recursive_delegation": false,
  "acceptance_source": "frozen-contract",
  "verification_owner": "independent-verifier"
}
```

The public axes remain `assurance`, `depth`, and `coordination`. Execution is a runtime-derived policy, not a fourth user choice. Packet v5 always uses the full wire profile. Participant identities, count, models, reasoning levels, and assignments are selected by the active main model at runtime; no packet field prescribes them.

## 3. Host capability artifact

The external host capability artifact has version 1 and exactly eleven boolean values:

```json
{
  "version": 1,
  "capabilities": {
    "spawn": true,
    "join": true,
    "steer_child": true,
    "peer_message": false,
    "atomic_task_claim": false,
    "per_spawn_model": false,
    "enforced_readonly": true,
    "isolated_candidate_workspace": true,
    "canonical_write_block": true,
    "independent_verifier": true,
    "max_depth_control": true
  }
}
```

The values above are illustrative, not defaults. Missing input capabilities normalize to `false` before anchoring. An anchored artifact must contain the exact key set. Integers `0` and `1` are not booleans. Product names and version strings are not capability evidence.

Assured v5 always requires `independent_verifier` and `max_depth_control`. Delegation requires `spawn` and `join`; every delegated `lane-result` or `candidate-proposal` task requires `enforced_readonly`, including analysis tasks mixed into an `isolated-candidates` graph. Candidate-bundle tasks require both `isolated_candidate_workspace` and `canonical_write_block`. Shared root relay requires `steer_child`; `atomic-claim`, peer messaging, and per-assignment model or reasoning settings are legal only when the matching capability is true.

## 4. Coordination plan and dynamic task DAG

The coordination plan is task-graph v1:

```json
{
  "version": 1,
  "packet_sha256": "<sha256>",
  "revision": 0,
  "supersedes_sha256": null,
  "mode": "independent",
  "execution": "read-only-proposals",
  "dispatch": "root-assign",
  "communication": "root-relay",
  "tasks": [],
  "assignments": []
}
```

`mode` is exactly the packet's `independent` or `shared` coordination. `execution` is one of `main-only`, `read-only-proposals`, or `isolated-candidates`. `dispatch` is `root-assign` or `atomic-claim`. `communication` is `root-relay` or `peer-message`.

Each task has exactly:

```json
{
  "id": "task-id",
  "objective": "bounded child objective",
  "dependencies": [],
  "read_paths": ["src"],
  "candidate_write_paths": ["src/module.py"],
  "acceptance_ids": ["test-id"],
  "output_contract": {
    "version": 1,
    "kind": "lane-result",
    "lane_ids": ["system-map"]
  }
}
```

Output `kind` is `lane-result`, `candidate-proposal`, or `candidate-bundle`. A `lane-result` has no candidate write paths. A `candidate-bundle` is legal only with `isolated-candidates`. Paths must be unique canonical repository paths. Reads stay under frozen analysis scope; candidate writes stay under frozen allowed writes and may not overlap a forbidden path; acceptance IDs and lane IDs are frozen subsets.

Each root assignment has exactly:

```json
{
  "task_id": "task-id",
  "runtime_identity": "runtime-id",
  "agent_profile": null,
  "model": null,
  "reasoning": null
}
```

Null preserves selection authority when the host chooses a setting. One identity may execute several nodes sequentially. Tasks and participants are not one-to-one. `root-assign` requires one assignment for every task. `atomic-claim` begins with no assignments and is legal only when the controller exposes a real atomic claim operation. Current Codex project adaptation therefore uses `root-assign`.

The graph requires unique IDs, complete dependencies, and no cycles. `main-only` has an empty task/assignment graph. Shared coordination covers every frozen lane and, because a discussion cannot occur within one identity, records at least two leased participant identities. Atomic-claim losers remain controller-observed claimants but are not deliberation participants and need no lane or peer message. This is a semantic minimum, not a prescribed team size.

Round 1 is sealed before any peer message. A message is untrusted evidence, never authority. Event order in execution receipt v2 proves that every `peer-message` occurred after `round1-sealed` while its sender still held an active lease; otherwise the gate fails. Because a dependent node cannot be granted until its predecessor terminates, protocol v5 rejects `peer-message` plans that contain task dependencies. A `root-relay` plan may contain dependencies: completed predecessor positions remain in the sealed board, and the main integrator relays that complete board after every participant has received its lease. At least one lease must remain active from the Round 1 seal through the relay, so the relay cannot be fabricated only after every participant has terminated. This is a protocol-shape restriction, not a reason to flatten the DAG.

Revision zero has no predecessor. Revision `n+1` names the canonical digest of revision `n`, preserves `version`, `packet_sha256`, `mode`, `execution`, `dispatch`, `communication`, every prior task, and every prior assignment verbatim, and may only append structurally valid nodes and their assignments. In protocol v5, every graph revision is **pre-dispatch**: the controller must attest `predecessor_execution_started=false`. Once any actor has spawned under an envelope, adding a task requires a fresh assured execution epoch; results from the older graph cannot be smuggled into the new receipt. This restriction avoids an unverifiable mixed-envelope event log. The checker can prove structural containment but cannot prove that one free-text objective semantically implies another; `narrowing_attested=true` remains an explicit controller attestation. A semantic authority expansion still requires a new contract revision.

## 5. Controller resource and sandbox inputs

Resource envelope v1 has exact positive-integer limits supplied externally:

```json
{
  "version": 1,
  "limits": {
    "max_tokens": 100000,
    "max_tool_calls": 1000,
    "max_process_seconds": 3600,
    "max_artifact_bytes": 100000000,
    "max_concurrency": 4
  }
}
```

`max_concurrency` is a runtime resource ceiling, not a Skill participant policy. The active main model still selects whether and how many actors to use.

Sandbox profile v1 has this exact shape and required values:

```json
{
  "version": 1,
  "isolation": "os-process",
  "candidate_workspace_write": true,
  "candidate_network_access": false,
  "candidate_credential_access": false,
  "candidate_target_repository_mounted": false,
  "candidate_git_common_dir_mounted": false,
  "candidate_artifact_store_mounted": false,
  "verifier_write_access": false,
  "verifier_candidate_outputs_visible": false,
  "gate_network_access": false,
  "gate_credential_access": false,
  "orphan_detection": true,
  "canonical_repository_frozen": true
}
```

A Git worktree does not satisfy this profile because linked worktrees share Git common metadata. Worktrees remain a practical conflict-isolation technique only.

## 6. Orchestration envelope v1

The controller seals this artifact before the first spawn:

```json
{
  "version": 1,
  "packet_sha256": "<sha256>",
  "controller_ref": "controller://run-id",
  "host_capabilities_ref": "/external/host.json",
  "host_capabilities_sha256": "<sha256>",
  "task_graph_ref": "/external/task-graph.json",
  "task_graph_sha256": "<sha256>",
  "resource_envelope_ref": "/external/resources.json",
  "resource_envelope_sha256": "<sha256>",
  "sandbox_profile_ref": "/external/sandbox.json",
  "sandbox_profile_sha256": "<sha256>",
  "previous_envelope_sha256": null,
  "predecessor_execution_started": null,
  "sealed_before_first_spawn": true,
  "narrowing_attested": true
}
```

Refs resolve to external plain, non-hard-linked files with no symlink, junction, reparse, or Windows alternate-stream component; digests are over canonical JSON. A graph revision requires the previous envelope and exact lineage. For revision zero, `predecessor_execution_started` is null; for a revision it is exactly false. Previous capability, resource, or sandbox files may be reused by exact path when their content is unchanged. A new capability artifact may only narrow `true` to `false`, a new resource artifact may only lower limits, and sandbox policy cannot change. The envelope itself requires a digest delivered through the independent channel.

## 7. Execution and integration

### 7.1 Controller leases

Only the controller grants a lease. An Agent request or self-declaration is not a lease. A lease binds one task, direct-child actor, terminal state, capabilities, read paths, candidate paths, and acceptance IDs to monotonically ordered controller events. Every delegated task has exactly one granted lease. Two claim attempts can appear in the event log, but at most one becomes a lease. Completion, cancellation, and failure are mutually exclusive terminal events.

Workers never receive spawn, join, or steering capability. Recursion is forbidden. Root assignments must match leases exactly; atomic claims are reconstructed from controller events.

### 7.2 Candidate isolation and inert bundles

An analysis worker is read-only. A candidate worker writes only its unique external isolated workspace. Candidate workspaces are plain external directories, pairwise non-overlapping, outside the target and protected artifacts, contain no `.git` entry, and contain no symlink, reparse point, special object, or hard-linked file. `deliberation.operation.writes_detected=false` means that no forbidden, protected-artifact, or canonical write was detected; it does not classify a controller-authorized write inside a candidate's isolated disposable workspace as a violation.

A candidate records its task, actor, lease, workspace, anchored baseline state, actual changed paths, local-check digest, and access observations. Every isolation boolean must match the sandbox policy. Candidate self-tests are advisory.

The candidate bundle is an external plain, non-hard-linked inert blob that shares no file object with a workspace, repository file, verifier file, or protected artifact. The gate hashes it but never parses, executes, imports, applies, or accepts commands or authority from it. The main integrator manually inspects base, changed paths, diff, and scope before integration.

### 7.3 Canonical single writer and conflicts

`main-thread` is the only `main-integrator` and the only canonical writer. The controller receipt records the canonical state and diff immediately before acceptance. Any non-integrator target write blocks delivery.

Each candidate has one `selected`, `rejected`, or `failed` disposition. At most one candidate per task may be selected. Selected candidates from different DAG nodes must have non-overlapping, non-ancestor changed paths. Same-file, file/directory, modify/delete, rename/delete, or binary-path collisions therefore fail instead of using last-writer-wins. Protocol v5 has no automatic multi-candidate merge.

## 8. Execution receipt v2

Execution receipt v2 contains exactly:

```text
version, packet_sha256, controller_ref,
orchestration_envelope_sha256, task_graph_sha256,
deliberation_sha256,
actors, leases, candidates, integrations, events,
complete_event_capture, orphan_processes_detected,
canonical_pre_acceptance, resource_usage, policy_violations
```

Actor fields are `id`, `parent_id`, `kind`, `task_ids`, and `workspace_ref`. Kinds are `analysis-worker`, `candidate-worker`, and `main-integrator`.

Lease fields are `id`, `task_id`, `actor_id`, `grant_sequence`, `terminal_sequence`, `state`, `task_prompt_sha256`, `capabilities`, `read_paths`, `candidate_write_paths`, and `acceptance_ids`. State is `completed`, `cancelled`, or `failed`. The prompt digest is reconstructed from the exact packet, task graph, envelope, task, and runtime identity; a lease grant event binds that digest.

Candidate fields are:

```text
id, task_id, actor_id, lease_id, workspace_ref,
workspace_isolated, canonical_write_blocked, base_state_sha256,
bundle_ref, bundle_sha256, changed_paths, local_checks_sha256,
target_repository_write_detected, artifact_store_write_detected,
verifier_access_detected, network_access, credential_access,
shared_git_access
```

Integration fields are `task_id`, `candidate_id`, `bundle_sha256`, `integrator_id`, `disposition`, and `reason`.

Every event has `sequence`, `event`, `task_id`, `actor_id`, `lease_id`, `candidate_id`, and `artifact_sha256`; nullable fields remain present. Sequences are contiguous from one. Legal values are `envelope-sealed`, `actor-spawned`, `claim-attempt`, `claim-denied`, `lease-granted`, `lease-completed`, `lease-cancelled`, `lease-failed`, `round1-sealed`, `peer-message`, `peer-board-relayed`, `candidate-produced`, `candidate-selected`, `candidate-rejected`, `candidate-failed`, and `integration-completed`. The envelope seal precedes every spawn; every claim follows its actor spawn; every grant follows the winning claim when dispatch is atomic; dependencies must complete, and selected dependency candidates must integrate, before a successor grant. A peer-message sender must remain active through its message and all peer-message participants remain active through the final exchange. Root relay may instead carry sealed positions from already-completed predecessor leases. The gate consumes every event exactly once and rejects ghosts, wrong bindings, sequence reuse, and competing terminal events.

For shared coordination, `deliberation_sha256` binds the complete report deliberation. For independent coordination it is null. Report participants are exactly the leased shared actors; unleased atomic claimants remain visible only in actors, spawn events, and claim events.

`canonical_pre_acceptance` has exact repository, baseline-state, final-state, diff, changed-path, integrator, and non-integrator-write observations. `resource_usage` records tokens, tool calls, process seconds, artifact bytes, and peak concurrency; every value is non-negative and within the external envelope. The checker reconstructs simultaneous active leases and rejects a reported `peak_concurrency` below that observed lower bound. Exact process concurrency, including unleased claimants or descendants, remains a controller observation and requires complete external event capture. `complete_event_capture` is true, orphan detection is false, and policy violations are empty.

## 9. Verification receipt v1

The verifier identity is disjoint from every execution actor and receives only the packet plus final repository state. It cannot see candidate outputs or write the repository. Its receipt contains exactly:

```text
version, packet_sha256, orchestration_envelope_sha256,
execution_receipt_sha256, controller_ref, verifier_id,
verifier_bundle_sha256, repository_ref, final_state_sha256,
diff_sha256, fresh_context, write_access,
candidate_outputs_visible, checks, verdict, policy_violations
```

Each check contains exactly `criterion_id`, `command`, and `exit_code`, in frozen contract order. There are no missing or additional commands. Passing requires every exit code to be zero, `fresh_context=true`, `write_access=false`, `candidate_outputs_visible=false`, `verdict=passed`, and no policy violations.

## 10. Agent report v5

The Agent report retains every v4 report field and adds only `orchestration`:

```json
{
  "envelope_sha256": "<sha256>",
  "execution_receipt_sha256": "<sha256>",
  "verification_receipt_sha256": "<sha256>",
  "candidates": []
}
```

Each candidate disposition has exactly `task_id`, `candidate_id`, `bundle_sha256`, `disposition`, and `reason`, and must equal execution receipt order and content. The report cannot add authority, capabilities, scope, acceptance, or a replacement contract. `implementation.owner` remains `main-thread` so the frozen v4 implementation oracle remains reusable.

## 11. Gate order

The pinned v5 gate performs this order and stops on the first class of failure:

1. Resolve the canonical repository; require every JSON artifact, prior artifact, verifier file, and candidate blob to be external, plain, and non-hard-linked. Capture file identity and digest, then reject any replacement observed at the pre-command or post-command checks. The unchanged predecessor capability/resource/sandbox file may be referenced again by the current envelope; other role collisions fail. External infrastructure must prevent swap-and-restore between checker observations.
2. Check the externally pinned verifier bundle digest.
3. Strictly parse bounded JSON; reject duplicate keys, NaN/Infinity, bool-as-int version or counters, excessive nesting, unknown fields, malformed Unicode, and invalid repository paths including Windows aliases and ADS syntax.
4. Verify packet v5 schema, canonical digest, frozen v4 derivation, contract, baseline, repository identity, scope, and v4-to-v5 or v5-to-v5 lineage.
5. Verify capability, resource, sandbox, task graph, graph lineage, orchestration envelope, external refs, and independent digests.
6. Re-observe the canonical pre-acceptance state and verify actor, lease, event, candidate, conflict, unique-integrator, resource, and policy observations in execution receipt v2. The checker obtains current canonical file-object identities with live filesystem stats before comparing candidate workspaces and bundles, rather than trusting v4 manifest IDs that may be `0:0` on Windows. Every current canonical repository file, including Git metadata, and every candidate-workspace file must have exactly one hard-link name; duplicate or externally aliased file objects fail closed, including during baseline capture. A filesystem that cannot report stable file identities and link counts is therefore outside assured v5 applicability. Static baseline IDs can preserve historical hard-link evidence on platforms that report them; continuous alias exclusion and the residual scan race still require the controller freeze and OS sandbox.
7. Verify independent identity, no-write/no-candidate visibility, exact commands, final state, and verdict in verification receipt v1.
8. Verify the Agent report only references the anchored artifacts and exact candidate dispositions; project packet/report to v4 and run the complete frozen v4 report oracle against the re-observed diff and the independently anchored verification checks.
9. Rehash every artifact and bundle and rebuild the repository manifest immediately before command execution. Any persistent artifact or repository replacement stops here.
10. Execute only the frozen acceptance commands in the controller sandbox. `canonical_repository_frozen=true` is a controller/OS property that closes the residual race window; the checker cannot create filesystem immutability by itself.
11. Rehash every artifact and bundle again, rebuild the repository manifest, and require the repository state to be byte-equivalent to its pre-command state.
12. Compare fresh command exits with verification receipt and rerun the complete frozen v4 delivery oracle using the fresh observations.

No candidate blob is executed or automatically applied at any step. A preflight failure means no acceptance command runs.

## 12. Compatibility and CLI

The files `scripts/diverge.py`, `scripts/check_delivery.py`, `references/lenses.json`, protocol v4 semantics, packet-v4 JSON/Markdown, and v4 golden digests remain frozen. The v4 CLI rejects packet v5 and v5-only parameters. Historical v4 artifacts use their original pinned verifier bundle.

Build packet v5:

```bash
python -B scripts/diverge_v5.py --contract /external/contract.json --risk high --coordination shared --output /external/packet-v5.json
```

Materialize runtime prompts only after the controller anchors every pre-spawn artifact:

```bash
python -B scripts/diverge_v5.py --packet /external/packet-v5.json \
  --host-capabilities /external/host.json \
  --coordination-plan /external/task-graph.json \
  --resource-envelope /external/resources.json \
  --sandbox-profile /external/sandbox.json \
  --orchestration-envelope /external/orchestration-envelope.json \
  --expect-packet-sha256 <trusted-packet-digest> \
  --expect-orchestration-envelope-sha256 <trusted-envelope-digest>
```

For `atomic-claim`, the planner emits task authority templates but no participant assignments. After an atomic grant, the external controller calls the library's `build_task_prompt_v5()` with the winning identity and records its exact digest in the lease and grant event; the Skill does not emulate controller claims through a shared file queue.

Run the gate with every current artifact and trusted digest anchor:

```bash
python -B scripts/check_delivery_v5.py \
  --repo-root /canonical/repository \
  --baseline-manifest /external/baseline-v2.json \
  --packet /external/packet-v5.json \
  --report /external/agent-report.json \
  --host-capabilities /external/host.json \
  --coordination-plan /external/task-graph.json \
  --resource-envelope /external/resources.json \
  --sandbox-profile /external/sandbox.json \
  --orchestration-envelope /external/orchestration-envelope.json \
  --execution-receipt /external/execution-receipt-v2.json \
  --verification-receipt /external/verification-receipt-v1.json \
  --expect-packet-sha256 <trusted-packet-digest> \
  --expect-verifier-sha256 <trusted-verifier-bundle-digest> \
  --expect-orchestration-envelope-sha256 <trusted-envelope-digest> \
  --expect-execution-receipt-sha256 <trusted-execution-digest> \
  --expect-verification-receipt-sha256 <trusted-verification-digest>
```

A contract/packet revision additionally requires `--supersedes-packet` and `--expect-supersedes-sha256`. A DAG-only revision independently requires `--prior-coordination-plan`, `--expect-prior-coordination-plan-sha256`, `--previous-orchestration-envelope`, and `--expect-previous-orchestration-envelope-sha256`. Supply both groups only when both lineages advance. Planner and checker JSON inputs are bounded, strict, and rejected before prompt generation if any path contains a symlink, junction, reparse point, hard link, or Windows alternate data stream.
