# Codex host adapter

This adapter maps Wide-Lens elastic coordination onto current Codex subagents without turning runtime limits into Skill policy.

## What it installs

The tracked project adapter contains:

- `.codex/config.toml`, with only `agents.max_depth = 1` to prevent recursive delegation;
- `.codex/agents/wide-lens-peer.toml`, one neutral read-only profile with a structured result contract.

It deliberately omits `agents.max_threads`, model, reasoning effort, nickname candidates, MCP servers, fixed roles, and participant counts. The active main model selects identities, models, reasoning, and assignments from the task and the runtime's real resource envelope.

The plugin does not install `.codex/agents`. Install the project adapter separately when the repository should expose the `wide_lens_peer` profile.

## Capability negotiation

Construct `HostCapabilities` from current runtime evidence. Never copy a static example into an assured envelope. Unknown means `false`.

Current public Codex subagent configuration provides root-managed spawning and joining, direct steering, custom read-only profiles, per-profile model settings, and `agents.max_depth`. It does not document an atomic shared task-claim primitive. Therefore the Codex adapter uses `root-assign`; `atomic-claim` is invalid unless a future host exposes a real controller-observed claim operation.

Codex reapplies the parent turn's live sandbox and permission overrides to child agents. Therefore `sandbox_mode = "read-only"` in the profile is only a requested baseline; set `enforced_readonly=true` only after observing the child's effective runtime boundary.

The adapter profile is suitable for `read-only-proposals`. Its sandbox setting is runtime guardrail evidence, not an assured receipt. A candidate worker is not distributed because a generic `workspace-write` profile could write the canonical checkout. Hosts that provide a genuinely isolated candidate workspace must create that worker dynamically and prove both workspace isolation and canonical write blocking.

## Dispatch sequence

1. The main model inspects the repository read-only, records capabilities, and publishes the frozen checkpoint or v5 packet reference.
2. It creates the task DAG without a participant-count prescription.
3. It assigns ready nodes to `wide_lens_peer` identities with `root-assign`.
4. Round 1 remains sealed. After sealing, use direct peer messaging only when the runtime reports that capability; otherwise relay one complete peer board through the root.
5. The main model adjudicates evidence, applies any selected proposal, and reruns frozen acceptance against the integrated canonical state.

Codex hooks bundled by the generated plugin inject the output headings at `SubagentStart` and request one correction at `SubagentStop` when headings are missing. Hooks do not cover every tool path and do not prove read-only behavior, isolation, chronology, identity, or absence of nested delegation. They are formatting guardrails only.

Plugin installation does not trust hooks automatically. Review their source, command, and hash with `/hooks`, then trust explicitly. Do not use `--dangerously-bypass-hook-trust` for normal installation. The hook matcher runs only when a `wide_lens_peer` profile exists, so install this project adapter separately.

## Installation

Preview the project-scoped adapter installation:

```bash
python scripts/install_codex_adapter.py --target /path/to/repository
```

Apply it after reviewing conflicts:

```bash
python scripts/install_codex_adapter.py --target /path/to/repository --apply
```

The installer refuses symlink, junction, or reparse-point destinations. It never overwrites a different
`.codex/config.toml`; `--force` applies only to a different peer profile.
