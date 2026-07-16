---
name: wide-lens-engineering
description: Deliver software features, fixes, debugging, refactors, migrations, architecture changes, and reviews with a complete externally anchored contract, adaptive main-model-selected subagent collaboration, Ponytail-style minimal implementation, controller receipts, and evidence-gated verification. Use for repository work, multi-agent discussion, adversarial analysis, or forced divergent thinking. Do not use for non-software tasks or explanation-only requests.
---

# Wide-Lens Engineering

Deliver the smallest correct software change without allowing the implementing Agent to redefine the task or certify its own orchestration at the end.

Read [references/protocol.md](references/protocol.md) before creating artifacts. Treat the verifier, controller, external digest channel, and OS sandbox as the trust root.

## Select the workflow

Choose exactly one intent:

- `change`: build, implement, refactor, migrate, or change architecture.
- `debug`: reproduce, locate the earliest shared cause, fix it, and preserve regression evidence.
- `review`: inspect and report; freeze an empty write allowlist.

Use `light` only for isolated low-risk work. Use `full` for cross-module, ambiguous, security, concurrency, persistence, public API, deployment, or adversarial work. Shared coordination requires `full`.

The active main model decides whether coordination is `independent` or `shared` before packet creation. For an anchored shared packet, the active main model then decides the actual participant identities, count, and lane assignments from risk, causal breadth, uncertainty, available concurrency, latency, and cost. Never encode an exact, default, or maximum participant count in the Skill or packet. The protocol requires at least two participants only because one participant is not a shared discussion.

## 1. Discover without mutation

Before a repository write or subagent launch:

1. Read every applicable `AGENTS.md` and repository policy.
2. Inspect manifests, entry points, callers, consumers, invariants, state transitions, persistence, configuration, deployment, rollback, and tests.
3. Separate sourced requirements from inference. Mark unresolved facts as assumptions.
4. Ask for explicit approval when an inference would determine acceptance, write scope, a non-goal, or a safety constraint.

The first named file is evidence, not the analysis boundary.

## 2. Capture the external baseline

Run a trusted installed verifier outside the target repository. Store every workflow artifact outside the target repository.

```bash
python <trusted-skill>/scripts/check_delivery.py \
  --capture-baseline \
  --repo-root <canonical-target-repository> \
  --baseline-manifest <external-artifact-dir>/baseline.json
```

Capture refuses to overwrite an existing manifest. Map the output exactly: `repository_ref` to `baseline.repository_ref`, `state_ref` to `baseline.state_ref`, and `baseline_manifest_sha256` to `baseline.state_sha256`. Pin `verifier_sha256` through a trusted release or controller channel; the value printed by the same untrusted run is not independent authentication.

The v2 snapshot scans twice and fails if state is unstable. It observes the repository root object identity, `.git`, regular file content, directories, mode/Windows attributes, file identity and link count, and Windows named data streams. It rejects external `.git` gitfiles, symlinks, junctions, other reparse points, non-canonical repository roots, unsupported entries, and root-directory named streams.

## 3. Freeze the complete authority contract

Create the complete JSON contract defined in the protocol. Include all normative data directly:

- objective, intent, non-goals, and acceptance IDs with exact commands;
- analysis, allowed-write, and forbidden-write paths;
- `path_case` and `path_flavor` (`posix` or `windows-win32`);
- safety constraints, assumptions, baseline identity, approval, revision, and supersession;
- exact authority records and item-level grants.

Every authority `content` is a JSON string that decodes to exactly `statement` and `grants`. Every cited normative item requires a grant containing its exact target and the SHA-256 of that canonical item. Reject missing, stale, unused, or mismatched grants. Plain authority prose is invalid.

Do not accept blocking assumptions. For `debug`, freeze the reproduction command. For `review`, freeze no allowed writes.

Generate the packet:

```bash
python <trusted-skill>/scripts/diverge.py \
  --contract <external-artifact-dir>/contract.json \
  --risk <low|medium|high> \
  --profile <light|full> \
  --coordination <independent|shared> \
  --format json \
  --output <external-artifact-dir>/packet.json
```

Publish `packet_sha256` outside the writable repository before implementation or delegation. A digest stored only beside the packet is not an external anchor.

Never edit a packet in place. A revision needs a complete new contract, explicit `user-approval`, `revision + 1`, `supersedes.packet_sha256`, and a new packet digest. Preserve the complete prior packet outside the repository; the final gate requires both that artifact and its separately anchored digest, then enforces the same `contract_id` and exact revision increment.

## 4. Map and deliberate

Map each selected lane to concrete repository evidence. For independent analysis, hide proposed solutions and peer outputs until collection completes.

For shared coordination after the packet is anchored:

1. The main model chooses runtime participants and lane assignments; the Skill does not choose their number. Save that choice as a JSON assignments list.
2. Materialize deterministic prompts without changing the choice: `python <trusted-skill>/scripts/diverge.py --packet <packet> --runtime-assignments <assignments> --output <runtime-prompts>`. This command validates identities/lane coverage but never chooses a participant count.
3. Subagents are read-only and never delegate recursively. The main thread is the only editing and integration owner.
4. Collect sealed Round 1 positions before exposing any peer output.
5. Relay the identical canonical peer board to the same participant identities.
6. Require each participant to challenge a peer claim and name the cheapest frozen discriminating command.
7. Let the main thread adjudicate with evidence, never votes or confidence.
8. Apply per-participant limits from the packet: two turns, 600 seconds per round, one retry, and 32768 canonical UTF-8 bytes for that participant's Round 1 positions. These are not aggregate participant-count limits.

Require the external runtime/controller to write a receipt outside the repository and publish its canonical digest after deliberation. The receipt binds the dynamic participant list and complete deliberation. It separates runtime observation from the Agent's report; unsigned receipts still depend on controller trust.

## 5. Minimize with Ponytail full

Use `$ponytail full` when installed. Otherwise apply the same frozen ladder:

1. `not-needed`: do no work when the behavior already satisfies the contract.
2. `reuse`: reuse the existing correction point, helper, type, or pattern.
3. `stdlib`: use the standard library.
4. `native`: use a platform-native capability.
5. `existing-dependency`: reuse an installed dependency.
6. `minimal-custom`: add only the smallest custom code.

The v4 packet freezes `full`; the final report must match it. Never simplify away a trust-boundary check, security control, data-loss guard, required error path, accessibility requirement, explicit acceptance criterion, or the smallest useful regression.

## 6. Implement through one owner

Only the main thread writes. Do not allow parallel subagent writes or recursive subagent delegation, including in isolated worktrees. Preserve unrelated user changes, remain inside frozen scope, handle failure paths, and re-read the integrated diff.

For debugging, fix the earliest shared cause instead of one visible caller. For review, write nothing.

## 7. Produce evidence, not a new contract

The report may record actual implementation, coverage, findings, disagreements, checks, residual risks, and shared deliberation. It must not redeclare objective, scope, acceptance, baseline, or authority. Reported changed paths and check results are claims until compared with controller observations.

For shared coordination, write the external runtime receipt and preserve its digest. For all workflows, preserve raw command output through an authorized channel.

## 8. Gate with pinned inputs

Run the verifier inside an OS sandbox whose write and network policy matches the frozen contract. The verifier itself does not confine commands outside the repository.

```bash
python <trusted-skill>/scripts/check_delivery.py \
  --repo-root <canonical-target-repository> \
  --baseline-manifest <external-artifact-dir>/baseline.json \
  --packet <external-artifact-dir>/packet.json \
  --report <external-artifact-dir>/report.json \
  --expect-packet-sha256 <externally-published-packet-digest> \
  --expect-verifier-sha256 <trusted-release-verifier-bundle-digest> \
  [--runtime-receipt <external-artifact-dir>/runtime-receipt.json \
   --expect-runtime-receipt-sha256 <controller-published-receipt-digest>] \
  [--supersedes-packet <external-artifact-dir>/prior-packet.json \
   --expect-supersedes-sha256 <prior-externally-published-packet-digest>]
```

All audit artifacts, the prior packet, and every verifier-bundle file must be distinct and outside the target repository. Before any acceptance command, the gate reconstructs the complete derived packet, validates revision lineage and receipt, binds the canonical repository path/root object to the baseline, and performs a stable current-state scan that rejects links and unsupported entries.

The gate executes only exact acceptance commands frozen in the anchored contract; it never executes a command added only by the report. It pins the platform shell, removes every inherited `GIT_*` variable before setting controlled Git config, clears high-risk hooks, rejects relative/repository `PATH` entries, then records exit and output digests. Acceptance commands must still run under an external sandbox because code under test can attempt writes or network access outside the observed repository.

After checks, the gate verifies that packet, report, baseline, prior packet, runtime receipt, and verifier bundle did not change. It then takes a stable final snapshot, derives actual changed paths, compares report claims, checks frozen scope and command exits, and emits controller-computed `final_state_sha256` plus `diff_sha256` bound to repository, baseline state, final state, and changed paths.

Do not claim completion unless the gate exits zero. An unsigned authority or runtime receipt does not authenticate identity. For high assurance, use a controller-generated signed attestation and keep the Agent unable to write anchors, receipts, verifier, or audit artifacts.

## Bounds and resources

- Stop when every selected lane has evidence, all frozen checks pass, and no high-impact contradiction remains.
- Never use participant count, token spend, consensus, or report length as a quality metric.
- Run `python -B tests/run_eval.py --threshold 1.0 --json` and `python -B tests/run_forward_eval.py --threshold 1.0 --require-no-skips --json` after changes.
- Keep the fixture threshold at `1.0`, never below `0.98`. Fixture pass rate is not universal defect recall or model accuracy.
- Use [references/protocol.md](references/protocol.md) as the normative schema reference.