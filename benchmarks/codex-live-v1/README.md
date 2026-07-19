# Codex live coding benchmark v1

This directory defines the public contract for the separate live coding gate. It intentionally does not contain the 150 release cases, reference patches, or hidden oracles. Shipping those inputs beside the Skill would make a blind benchmark unverifiable.

The deterministic `tests/run_stat_eval.py` suite remains the protocol/controller benchmark. It never substitutes for this live gate.

## Evidence levels

- `local`: launches one fresh ephemeral Codex process per case, observes the real workspace diff, runs an external oracle, and records CLI usage. It is useful development evidence but is never release-eligible because the local host cannot prove hidden-oracle isolation, brokered credentials, complete process capture, or an independent verifier.
- `external-results`: validates a controller result set against a canonical anchor signed by an approved controller key. The runner verifies structure and signature relative to the supplied trust root, but never declares its own trust root independent and never self-authorizes a release.

The six release strata are `local`, `security`, `concurrency`, `data`, `api`, and `distributed`.

## Local development run

```powershell
python -B tests/run_codex_live_eval.py `
  --suite C:\external\wide-lens-live-suite.json `
  --provider local `
  --skill-root . `
  --oracle-root C:\external\hidden-oracles `
  --codex-command-json '["npx","--yes","@openai/codex@0.144.6"]' `
  --results dist\codex-live-local.json
```

The runner appends `exec --ephemeral --json --strict-config --ignore-user-config --ignore-rules --sandbox workspace-write`. Runtime `npx` installation is acceptable only for smoke testing; a release controller must pin and attest a preinstalled CLI binary.

## External controller aggregation

```bash
python -B tests/run_codex_live_eval.py \
  --suite /external/wide-lens-live-suite.json \
  --provider external-results \
  --skill-root . \
  --external-results /external/controller-results.json \
  --external-anchor /external/controller-anchor.json \
  --controller-signature /external/controller-anchor.json.sig \
  --controller-allowed-signers /protected/controller.allowed_signers \
  --expected-repository Mai-xiyu/wide-lens-engineering \
  --expected-release-commit <candidate-commit-sha> \
  --expect-controller-challenge-sha256 <protected-release-challenge-digest>
```

The signed canonical anchor binds the repository, candidate commit, current Skill digest, suite, results, controller/config/environment digests, model route, benchmark, run ID, protected release challenge, and a validity window of at most 24 hours. Verification uses OpenSSH `ssh-keygen -Y verify` with the fixed principal `wide-lens-live-controller` and namespace `wide-lens-live-v1`.

The release workflow obtains the allowed-signers file and release challenge only from the protected `assured-v5-release` GitHub Environment. Configure required reviewers, prevent self-review, restrict deployment refs, and rotate or delete the challenge after authorization. The workflow has no boolean opt-out: an unprotected ref, missing secret, bad signature, stale anchor, mismatched commit, or skipped dependency makes the final package gate fail.

The attestation is content-addressed: it may be rechecked for the same immutable commit while the protected challenge remains current. That is not proof of a fresh workflow run. A policy that requires one execution per Release must additionally have the external controller/store enforce one-time consumption of `controller_run_id` and the challenge; a stateless repository checker cannot prove consumption.

The runner validates the signed receipt; it does not manufacture controller identity, isolation, event capture, or verifier independence. The controller signature attests those externally observed facts. A custom caller that supplies its own key has only self-signed evidence.

## Release rule

Local mode always returns `release_eligible=false`. External mode reports `external_receipt_valid=true` only after the signed anchor and all 150 cases validate; it deliberately omits `release_eligible`. Release authority exists only when the protected external environment approves that receipt. A local or self-signed 150/150 result cannot authorize a tag or Release.
