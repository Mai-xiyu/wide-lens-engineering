# Contributing to Wide-Lens Engineering

This document is for repository maintainers. It is intentionally excluded from the packaged runtime Skill so ordinary Skill use does not load release procedures or test commands.

## Version domains

Three independent version domains exist:

- **Plugin/package SemVer:** starts at `0.1.0` while the public interface and live release process are still under development.
- **Current assured wire protocol:** packet `version: 5`, described as protocol v5.
- **Frozen legacy wire protocol:** packet `version: 4`, retained for byte-compatible verification.

Never derive a package version from a protocol version. A future `1.0.0` package release means the public installation, workflow, and compatibility contract are stable; it does not imply protocol v1. `0.0.1` is valid SemVer, but this project uses the conventional `0.1.0` initial-development baseline.

## Repository boundaries

- `SKILL.md` is the small opt-in router.
- `references/practical.md` and `references/protocol-v5.md` hold the selected runtime workflow.
- `references/protocol.md`, `scripts/diverge.py`, `scripts/check_delivery.py`, `references/lenses.json`, and the v4 golden digests are frozen compatibility surfaces.
- `README.md` and `README_CN.md` are reader documentation.
- This file owns maintainer-only validation and release instructions.

Do not add repository tests, packaging tools, or this guide to the runtime Plugin allowlist.

## Validation

Run every deterministic gate before proposing a package:

```bash
python -B tests/run_eval.py --threshold 1.0 --json
python -B tests/run_forward_eval.py --threshold 1.0 --require-no-skips --json
python -B tests/run_v5_eval.py --threshold 1.0 --json
python -B tests/run_distribution_eval.py --threshold 1.0 --json
python -B tests/run_platform_eval.py --json
python -B tests/run_codex_live_harness_eval.py --threshold 1.0 --json
python -B tests/run_stat_eval.py --require-all --json
python -B tests/run_perf_eval.py --json
python -B scripts/validate_skill.py .
git diff --check
```

Build and independently validate the current preview archive:

```bash
python -B scripts/build_codex_plugin.py --version 0.1.0 --output-dir dist \
  --validator scripts/validate_codex_plugin.py --force
python -B scripts/validate_codex_plugin.py \
  dist/wide-lens-engineering-marketplace-0.1.0.zip \
  --expected-version 0.1.0
```

Build the archive twice and require identical bytes and SHA-256. Update the validator's version-keyed control hashes only after the canonical runtime and Plugin control files are final, then rerun the full distribution suite.

## Release policy

A passing same-repository suite is necessary but cannot authorize a formal assured release. Do not create a tag or GitHub Release unless all of these hold:

1. every deterministic, forward, platform, distribution, statistical, and performance gate passes;
2. 150/150 externally controlled fresh-context live coding tasks pass for the release commit;
3. the controller-signed anchor validates against the protected challenge and allowed signers;
4. the protected `assured-v5-release` GitHub environment authorizes the receipt;
5. the release ref is protected and the final checkout is clean.

Until those conditions exist, publish only a branch or draft pull request and describe `0.1.0` as an unreleased preview target.
