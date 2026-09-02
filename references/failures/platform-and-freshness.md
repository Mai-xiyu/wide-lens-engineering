# Platform, sandbox, and freshness failures

Load this card only when behavior depends on operating system, client surface, Git topology, sandbox policy, model route, current documentation, or post-cutoff facts.

## Activate on evidence

- The same operation differs across Windows, WSL, Linux, macOS, Desktop, CLI, IDE, or remote execution.
- Git uses a linked worktree, submodule, gitfile, junction, symlink, reparse point, or external metadata path.
- A sandboxed command fails, prompts unexpectedly, or would require broader permission.
- The task depends on a current model, API, tool, package, policy, price, incident, or other time-sensitive fact.

## Minimum response

1. Observe the effective client/runtime version, operating system, shell, working directory, repository root, Git topology, model route, available tool schema, permission mode, and writable roots that matter to the failure. A product name or configured value is not proof of effective capability.
2. Treat linked Git worktrees as file-conflict isolation only: their checkout files are separate, but Git common metadata is shared. Do not use them as an assured security boundary.
3. If a safe sandbox blocks the operation, do not silently widen to unrestricted access. Prefer a narrower supported operation, main-only/read-only evidence, or an explicit authority decision.
4. For current or post-cutoff facts, search authoritative primary sources and record the retrieval date. Keep user reports separate from official behavior and reproducible controls.
5. When configured and effective model, context, tool, or permission values differ, use the observed effective value and report the mismatch.

## Completion evidence

Reproduce on the actual target surface or clearly label the untested platform. Record exact command outcomes and the effective boundary. A successful run on another operating system, client, worktree layout, or model route is counterevidence, not proof for the target.

## Boundary

This card cannot fix client sandbox, Git integration, rollout, model-catalog, or service availability defects. It makes platform-specific uncertainty and freshness visible instead of guessing through it.
