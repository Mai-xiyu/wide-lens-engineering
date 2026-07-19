#!/usr/bin/env python3
"""Deterministically test the live benchmark harness without calling a model."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
sys.path.insert(0, str(TEST_DIR))

from run_codex_live_eval import (  # noqa: E402
    ANCHOR_NAMESPACE,
    ANCHOR_PRINCIPAL,
    ANCHOR_VERSION,
    EXPECTED_INVARIANTS,
    LiveEvalError,
    RELEASE_STRATA,
    RESULTS_VERSION,
    SUITE_VERSION,
    canonical_json,
    exact_one_sided_lower,
    extract_fixture,
    load_json,
    observe_manifest_diff,
    run_local,
    sha256_bytes,
    sha256_json,
    skill_digest,
    tree_digest,
    tree_manifest,
    validate_external_results,
    validate_external_anchor,
    validate_suite,
    verify_anchor_signature,
)


TEST_REPOSITORY = "Mai-xiyu/wide-lens-engineering"
TEST_COMMIT = "d" * 40
TEST_CHALLENGE = "e" * 64


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )


def base_case(fixture_sha: str, tree_sha: str, oracle_sha: str) -> dict[str, Any]:
    return {
        "id": "local-fix-001",
        "stratum": "local",
        "fixture": {
            "ref": "fixture.zip",
            "sha256": fixture_sha,
            "baseline_tree_sha256": tree_sha,
        },
        "task": {
            "prompt": "Change VALUE from 1 to 2.",
            "assurance": "practical",
            "depth": "focused",
            "coordination": "independent",
            "allowed_write_paths": ["src"],
            "non_goals": ["Do not change benchmark support files."],
        },
        "diff_policy": {
            "must_change": True,
            "allowed_paths": ["src"],
            "max_diff_bytes": 4096,
            "reject_links_special_files": True,
        },
        "resources": {
            "wall_seconds": 30,
            "max_input_tokens": 1000,
            "max_output_tokens": 1000,
            "max_reasoning_tokens": 1000,
            "max_tool_calls": 20,
            "max_process_seconds": 30,
            "max_artifact_bytes": 1_000_000,
            "max_concurrency": 4,
        },
        "oracle": {
            "external_ref": "oracle.py",
            "sha256": oracle_sha,
            "command": ["{python}", "{oracle}", "{workspace}"],
            "timeout_seconds": 10,
        },
    }


def make_release_suite(case_template: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for stratum in RELEASE_STRATA:
        for index in range(25):
            case = copy.deepcopy(case_template)
            case["id"] = f"{stratum}-{index + 1:03d}"
            case["stratum"] = stratum
            case["task"]["prompt"] = f"Frozen {stratum} coding scenario {index + 1}."
            cases.append(case)
    return {
        "version": SUITE_VERSION,
        "benchmark_id": "live-harness-release-shape-test",
        "model_request": "frozen-model",
        "reasoning_request": "high",
        "skill_sha256": skill_digest(SKILL_DIR),
        "cli_sha256": "a" * 64,
        "strata": list(RELEASE_STRATA),
        "cases": cases,
    }


def external_results(
    suite: dict[str, Any], baseline_manifest: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    true_assurances = {
        "controller_is_external": True,
        "suite_frozen_before_runs": True,
        "fresh_context_per_case": True,
        "single_attempt_per_case": True,
        "hidden_oracle_isolated": True,
        "reference_solution_prevalidated": True,
        "independent_verifier": True,
        "os_sandbox": True,
        "credentials_brokered": True,
        "complete_event_capture": True,
        "orphan_process_capture": True,
        "resource_observation_complete": True,
        "actual_model_route_attested": True,
    }
    rows = []
    for case in suite["cases"]:
        final_manifest = copy.deepcopy(baseline_manifest)
        final_bytes = f"VALUE = {case['id']!r}\n".encode("utf-8")
        final_manifest["src/value.py"] = {
            "sha256": sha256_bytes(final_bytes),
            "size": len(final_bytes),
        }
        observed = observe_manifest_diff(baseline_manifest, final_manifest)
        rows.append(
            {
                "id": case["id"],
                "stratum": case["stratum"],
                "execution_actor_ids": ["main-thread"],
                "integrator_id": "main-thread",
                "verifier_id": "independent-verifier",
                "baseline_manifest": baseline_manifest,
                "final_manifest": final_manifest,
                "controller_observed": {
                    key: observed[key]
                    for key in (
                        "baseline_tree_sha256",
                        "final_tree_sha256",
                        "diff_sha256",
                        "changed_paths",
                        "diff_bytes",
                    )
                },
                "resource_usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "reasoning_tokens": 25,
                    "tool_calls": 2,
                    "process_seconds": 1,
                    "artifact_bytes": observed["minimum_artifact_bytes"],
                    "peak_concurrency": 1,
                },
                "invariants": copy.deepcopy(EXPECTED_INVARIANTS),
                "oracle_result": {
                    "definition_sha256": sha256_json(case["oracle"]),
                    "exit_code": 0,
                },
                "detail_sha256": sha256_json({"case": case["id"]}),
            }
        )
    return {
        "version": RESULTS_VERSION,
        "release_commit": TEST_COMMIT,
        "suite_sha256": sha256_json(suite),
        "controller_ref": "controller://harness-test",
        "controller_bundle_sha256": "b" * 64,
        "environment_sha256": "c" * 64,
        "skill_sha256": suite["skill_sha256"],
        "cli_sha256": suite["cli_sha256"],
        "model_request": suite["model_request"],
        "reasoning_request": suite["reasoning_request"],
        "assurances": true_assurances,
        "cases": rows,
    }


def external_anchor(
    suite: dict[str, Any], results: dict[str, Any], issued_at: datetime
) -> dict[str, Any]:
    return {
        "version": ANCHOR_VERSION,
        "repository": TEST_REPOSITORY,
        "candidate_commit_sha": TEST_COMMIT,
        "challenge_sha256": TEST_CHALLENGE,
        "suite_sha256": sha256_json(suite),
        "results_sha256": sha256_json(results),
        "skill_sha256": suite["skill_sha256"],
        "benchmark_id": suite["benchmark_id"],
        "controller_ref": results["controller_ref"],
        "controller_bundle_sha256": results["controller_bundle_sha256"],
        "controller_config_sha256": "f" * 64,
        "environment_sha256": results["environment_sha256"],
        "cli_sha256": suite["cli_sha256"],
        "model_request": suite["model_request"],
        "reasoning_request": suite["reasoning_request"],
        "controller_run_id": "controller-run-001",
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (issued_at + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def sign_anchor(
    root: Path,
    anchor: dict[str, Any],
    *,
    stem: str = "controller-anchor",
    namespace: str = ANCHOR_NAMESPACE,
) -> tuple[Path, Path]:
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise RuntimeError("ssh-keygen is unavailable")
    private_key = root / f"{stem}-key"
    generated = subprocess.run(
        [executable, "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        capture_output=True,
        check=False,
        timeout=10,
    )
    if generated.returncode != 0:
        raise RuntimeError(generated.stderr.decode("utf-8", errors="replace"))
    public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    allowed_signers = root / f"{stem}.allowed_signers"
    allowed_signers.write_text(
        f"{ANCHOR_PRINCIPAL} {public_key}\n", encoding="utf-8", newline="\n"
    )
    canonical_path = root / f"{stem}.canonical.json"
    canonical_path.write_bytes(canonical_json(anchor))
    signed = subprocess.run(
        [
            executable,
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            namespace,
            str(canonical_path),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    if signed.returncode != 0:
        raise RuntimeError(signed.stderr.decode("utf-8", errors="replace"))
    return allowed_signers, Path(f"{canonical_path}.sig")


def run_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def record(name: str, check: Callable[[], bool]) -> None:
        try:
            passed = check()
            detail = ""
        except Exception as exc:
            passed = False
            detail = f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "passed": passed, "detail": detail})

    with tempfile.TemporaryDirectory(prefix="wide-lens-live-harness-") as temporary:
        root = Path(temporary)
        fixture_source = root / "fixture-source"
        source = fixture_source / "src" / "value.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
        baseline_tree_sha = tree_digest(tree_manifest(fixture_source))
        fixture_zip = root / "fixture.zip"
        with zipfile.ZipFile(fixture_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(source, "src/value.py")

        oracle = root / "oracle.py"
        oracle.write_text(
            "import pathlib, sys\n"
            "value = pathlib.Path(sys.argv[1], 'src', 'value.py').read_text(encoding='utf-8')\n"
            "raise SystemExit(0 if value == 'VALUE = 2\\n' else 1)\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_codex = root / "fake_codex.py"
        fake_codex.write_text(
            "import json, pathlib, sys\n"
            "workspace = pathlib.Path(sys.argv[sys.argv.index('--cd') + 1])\n"
            "workspace.joinpath('src', 'value.py').write_text('VALUE = 2\\n', encoding='utf-8', newline='\\n')\n"
            "print(json.dumps({'type':'thread.started','thread_id':'fresh-thread-1'}))\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'file_change'}}))\n"
            "print(json.dumps({'type':'turn.completed','thread_id':'fresh-thread-1','usage':{'input_tokens':10,'output_tokens':5,'reasoning_tokens':2}}))\n",
            encoding="utf-8",
            newline="\n",
        )
        case = base_case(
            sha256_bytes(fixture_zip.read_bytes()),
            baseline_tree_sha,
            sha256_bytes(oracle.read_bytes()),
        )
        suite = {
            "version": SUITE_VERSION,
            "benchmark_id": "live-harness-local-test",
            "model_request": "fake-model",
            "reasoning_request": "high",
            "skill_sha256": skill_digest(SKILL_DIR),
            "cli_sha256": "a" * 64,
            "strata": ["local"],
            "cases": [case],
        }
        suite_path = root / "suite.json"
        write_json(suite_path, suite)

        record("valid local suite schema", lambda: validate_suite(suite) is suite)

        local_payload = run_local(
            suite,
            suite_path=suite_path,
            oracle_root=root,
            skill_root=SKILL_DIR,
            codex_command=[sys.executable, str(fake_codex)],
            jobs=1,
        )
        record(
            "local provider executes a fresh functional coding probe",
            lambda: local_payload["passed"] is True
            and local_payload["functional_successes"] == 1,
        )
        record(
            "local provider cannot become release eligible",
            lambda: local_payload["release_eligible"] is False
            and local_payload["formal_task_successes"] == 0
            and local_payload["blind_oracle_proven"] is False,
        )

        duplicate = copy.deepcopy(suite)
        duplicate_case = copy.deepcopy(case)
        duplicate_case["id"] = "local-fix-002"
        duplicate["cases"].append(duplicate_case)

        def rejects_duplicate_semantics() -> bool:
            try:
                validate_suite(duplicate)
            except LiveEvalError as exc:
                return "semantically unique" in str(exc)
            return False

        record("suite rejects duplicate semantics after removing IDs", rejects_duplicate_semantics)

        unsafe_zip = root / "unsafe.zip"
        with zipfile.ZipFile(unsafe_zip, "w") as archive:
            archive.writestr("../outside.py", "bad")

        def rejects_zip_traversal() -> bool:
            with tempfile.TemporaryDirectory(prefix="wide-lens-unsafe-zip-") as destination:
                try:
                    extract_fixture(unsafe_zip, Path(destination))
                except LiveEvalError:
                    return True
            return False

        record("fixture extraction rejects traversal", rejects_zip_traversal)

        duplicate_json = root / "duplicate.json"
        duplicate_json.write_text('{"x":1,"x":2}\n', encoding="utf-8", newline="\n")
        nonfinite_json = root / "nonfinite.json"
        nonfinite_json.write_text('{"x":NaN}\n', encoding="utf-8", newline="\n")

        def rejects_json(path: Path, expected: str) -> bool:
            try:
                load_json(path)
            except LiveEvalError as exc:
                return expected in str(exc)
            return False

        record(
            "live runner rejects duplicate JSON keys",
            lambda: rejects_json(duplicate_json, "duplicate JSON key"),
        )
        record(
            "live runner rejects non-finite JSON",
            lambda: rejects_json(nonfinite_json, "non-finite JSON"),
        )

        reserved_zip = root / "reserved.zip"
        with zipfile.ZipFile(reserved_zip, "w") as archive:
            archive.writestr("CON.txt", "bad")
        symlink_zip = root / "symlink.zip"
        with zipfile.ZipFile(symlink_zip, "w") as archive:
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (0o120777 << 16)
            archive.writestr(link, "target")

        def extraction_rejected(path: Path) -> bool:
            with tempfile.TemporaryDirectory(prefix="wide-lens-bad-zip-") as destination:
                try:
                    extract_fixture(path, Path(destination))
                except LiveEvalError:
                    return True
            return False

        record(
            "fixture extraction rejects Windows reserved aliases",
            lambda: extraction_rejected(reserved_zip),
        )
        record(
            "fixture extraction rejects symbolic-link members",
            lambda: extraction_rejected(symlink_zip),
        )

        release_suite = validate_suite(make_release_suite(case))
        anchored = external_results(release_suite, tree_manifest(fixture_source))
        issued_at = datetime.now(timezone.utc).replace(microsecond=0)
        anchor = external_anchor(release_suite, anchored, issued_at)
        allowed_signers, signature = sign_anchor(root, anchor)
        validate_external_anchor(
            release_suite,
            anchored,
            anchor,
            expected_repository=TEST_REPOSITORY,
            expected_commit=TEST_COMMIT,
            expected_challenge_sha256=TEST_CHALLENGE,
            expected_skill_sha256=release_suite["skill_sha256"],
            now=issued_at,
        )
        verify_anchor_signature(anchor, signature, allowed_signers)
        release_payload = validate_external_results(
            release_suite,
            anchored,
            anchor["results_sha256"],
            TEST_COMMIT,
        )
        record(
            "signed 150-of-150 receipt reaches the statistical threshold",
            lambda: release_payload["external_receipt_valid"] is True
            and "release_eligible" not in release_payload
            and release_payload["successes"] == 150
            and release_payload["exact_lower_bound"] > 0.98,
        )
        record(
            "receipt validator never self-authorizes a release",
            lambda: validate_external_results(
                release_suite,
                anchored,
                sha256_json(anchored),
                TEST_COMMIT,
            ).get("release_eligible")
            is None,
        )

        suite_file = root / "release-suite.json"
        results_file = root / "release-results.json"
        anchor_file = root / "release-anchor.json"
        write_json(suite_file, release_suite)
        write_json(results_file, anchored)
        write_json(anchor_file, anchor)
        external_cli = subprocess.run(
            [
                sys.executable,
                "-B",
                str(TEST_DIR / "run_codex_live_eval.py"),
                "--suite",
                str(suite_file),
                "--provider",
                "external-results",
                "--external-results",
                str(results_file),
                "--external-anchor",
                str(anchor_file),
                "--controller-signature",
                str(signature),
                "--controller-allowed-signers",
                str(allowed_signers),
                "--expected-repository",
                TEST_REPOSITORY,
                "--expected-release-commit",
                TEST_COMMIT,
                "--expect-controller-challenge-sha256",
                TEST_CHALLENGE,
                "--skill-root",
                str(SKILL_DIR),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        external_cli_payload = (
            json.loads(external_cli.stdout) if external_cli.stdout.strip() else {}
        )
        record(
            "external CLI verifies signed anchor without self-authorizing",
            lambda: external_cli.returncode == 0
            and external_cli_payload.get("external_receipt_valid") is True
            and "release_eligible" not in external_cli_payload,
        )

        wrong_allowed_signers, _ = sign_anchor(root, anchor, stem="wrong-controller")

        def rejects_wrong_controller_key() -> bool:
            try:
                verify_anchor_signature(anchor, signature, wrong_allowed_signers)
            except LiveEvalError:
                return True
            return False

        record("signed anchor rejects an untrusted controller key", rejects_wrong_controller_key)

        wrong_principal = root / "wrong-principal.allowed_signers"
        wrong_principal.write_text(
            allowed_signers.read_text(encoding="utf-8").replace(
                ANCHOR_PRINCIPAL, "untrusted-controller", 1
            ),
            encoding="utf-8",
            newline="\n",
        )

        def rejects_wrong_principal() -> bool:
            try:
                verify_anchor_signature(anchor, signature, wrong_principal)
            except LiveEvalError:
                return True
            return False

        record("signed anchor fixes the controller principal", rejects_wrong_principal)

        wrong_namespace_signers, wrong_namespace_signature = sign_anchor(
            root,
            anchor,
            stem="wrong-namespace-controller",
            namespace="untrusted-namespace",
        )

        def rejects_wrong_namespace() -> bool:
            try:
                verify_anchor_signature(
                    anchor, wrong_namespace_signature, wrong_namespace_signers
                )
            except LiveEvalError:
                return True
            return False

        record("signed anchor fixes the SSHSIG namespace", rejects_wrong_namespace)

        tampered_anchor = copy.deepcopy(anchor)
        tampered_anchor["controller_run_id"] = "controller-run-tampered"

        def rejects_anchor_signature_tamper() -> bool:
            try:
                verify_anchor_signature(tampered_anchor, signature, allowed_signers)
            except LiveEvalError:
                return True
            return False

        record("signed anchor rejects post-signature mutation", rejects_anchor_signature_tamper)

        def rejects_commit_replay() -> bool:
            try:
                validate_external_anchor(
                    release_suite,
                    anchored,
                    anchor,
                    expected_repository=TEST_REPOSITORY,
                    expected_commit="0" * 40,
                    expected_challenge_sha256=TEST_CHALLENGE,
                    expected_skill_sha256=release_suite["skill_sha256"],
                    now=issued_at,
                )
            except LiveEvalError:
                return True
            return False

        record("signed anchor rejects cross-commit replay", rejects_commit_replay)

        def rejects_repository_replay() -> bool:
            try:
                validate_external_anchor(
                    release_suite,
                    anchored,
                    anchor,
                    expected_repository="attacker/repository",
                    expected_commit=TEST_COMMIT,
                    expected_challenge_sha256=TEST_CHALLENGE,
                    expected_skill_sha256=release_suite["skill_sha256"],
                    now=issued_at,
                )
            except LiveEvalError:
                return True
            return False

        record("signed anchor rejects cross-repository replay", rejects_repository_replay)

        def rejects_challenge_replay() -> bool:
            try:
                validate_external_anchor(
                    release_suite,
                    anchored,
                    anchor,
                    expected_repository=TEST_REPOSITORY,
                    expected_commit=TEST_COMMIT,
                    expected_challenge_sha256="0" * 64,
                    expected_skill_sha256=release_suite["skill_sha256"],
                    now=issued_at,
                )
            except LiveEvalError:
                return True
            return False

        record("signed anchor rejects a stale controller challenge", rejects_challenge_replay)

        def rejects_skill_substitution() -> bool:
            try:
                validate_external_anchor(
                    release_suite,
                    anchored,
                    anchor,
                    expected_repository=TEST_REPOSITORY,
                    expected_commit=TEST_COMMIT,
                    expected_challenge_sha256=TEST_CHALLENGE,
                    expected_skill_sha256="0" * 64,
                    now=issued_at,
                )
            except LiveEvalError:
                return True
            return False

        record("signed anchor rejects candidate Skill substitution", rejects_skill_substitution)

        expired_anchor = copy.deepcopy(anchor)
        expired_anchor["expires_at"] = issued_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        def rejects_expired_anchor() -> bool:
            try:
                validate_external_anchor(
                    release_suite,
                    anchored,
                    expired_anchor,
                    expected_repository=TEST_REPOSITORY,
                    expected_commit=TEST_COMMIT,
                    expected_challenge_sha256=TEST_CHALLENGE,
                    expected_skill_sha256=release_suite["skill_sha256"],
                    now=issued_at,
                )
            except LiveEvalError:
                return True
            return False

        record("external anchor rejects an expired validity window", rejects_expired_anchor)

        extra_field_anchor = {**anchor, "release_eligible": True}

        def rejects_anchor_smuggling() -> bool:
            try:
                validate_external_anchor(
                    release_suite,
                    anchored,
                    extra_field_anchor,
                    expected_repository=TEST_REPOSITORY,
                    expected_commit=TEST_COMMIT,
                    expected_challenge_sha256=TEST_CHALLENGE,
                    expected_skill_sha256=release_suite["skill_sha256"],
                    now=issued_at,
                )
            except LiveEvalError:
                return True
            return False

        record("external anchor rejects authority-field smuggling", rejects_anchor_smuggling)

        external_mutations = (
            (
                "external gate rejects verifier/actor overlap",
                lambda value: value["cases"][0].__setitem__(
                    "verifier_id", "main-thread"
                ),
            ),
            (
                "external gate rejects a forged controller diff",
                lambda value: value["cases"][0]["controller_observed"].__setitem__(
                    "changed_paths", []
                ),
            ),
            (
                "external gate rejects resource expansion",
                lambda value: value["cases"][0]["resource_usage"].__setitem__(
                    "tool_calls", 10_000
                ),
            ),
            (
                "external gate rejects a hard-invariant violation",
                lambda value: value["cases"][0]["invariants"].__setitem__(
                    "fresh_context", False
                ),
            ),
            (
                "external gate rejects baseline-manifest substitution",
                lambda value: value["cases"][0]["baseline_manifest"][
                    "src/value.py"
                ].__setitem__("sha256", "0" * 64),
            ),
            (
                "external gate rejects a self-declared task_success field",
                lambda value: value["cases"][0].__setitem__("task_success", True),
            ),
        )
        for name, mutate in external_mutations:
            tampered = copy.deepcopy(anchored)
            mutate(tampered)

            def rejects_tamper(value: dict[str, Any] = tampered) -> bool:
                try:
                    validate_external_results(
                        release_suite, value, sha256_json(value), TEST_COMMIT
                    )
                except LiveEvalError:
                    return True
                return False

            record(name, rejects_tamper)

        one_failure = copy.deepcopy(anchored)
        one_failure["cases"][0]["oracle_result"]["exit_code"] = 1

        def rejects_149_of_150() -> bool:
            try:
                validate_external_results(
                    release_suite, one_failure, sha256_json(one_failure), TEST_COMMIT
                )
            except LiveEvalError:
                return True
            return False

        record("external gate rejects 149 of 150", rejects_149_of_150)
        record(
            "exact 150-of-150 lower bound is 98 percent or higher",
            lambda: exact_one_sided_lower(150, 150) > 0.98,
        )
        release_workflow = (
            SKILL_DIR / ".github" / "workflows" / "release-gates.yml"
        ).read_text(encoding="utf-8")
        record(
            "release workflow has no green opt-out and uses a protected trust root",
            lambda: "release_candidate" not in release_workflow
            and "environment: assured-v5-release" in release_workflow
            and "secrets.WIDE_LENS_CONTROLLER_ALLOWED_SIGNERS_B64" in release_workflow
            and "secrets.WIDE_LENS_CONTROLLER_CHALLENGE_SHA256" in release_workflow
            and "--expected-release-commit \"$GITHUB_SHA\"" in release_workflow
            and "--external-anchor live-anchor.json" in release_workflow
            and "--controller-signature live-anchor.sig" in release_workflow
            and "--controller-allowed-signers" in release_workflow
            and "--expect-external-results-sha256" not in release_workflow
            and "if: ${{ always() }}" in release_workflow
            and "receipt validator must not self-authorize" in release_workflow,
        )
        ci_workflow = (SKILL_DIR / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        action_refs = re.findall(
            r"uses:\s+actions/[^@\s]+@([^\s#]+)",
            release_workflow + "\n" + ci_workflow,
        )
        record(
            "workflow actions are pinned and checkout cleanliness includes untracked files",
            lambda: bool(action_refs)
            and all(re.fullmatch(r"[0-9a-f]{40}", value) for value in action_refs)
            and "--untracked-files=all" in release_workflow
            and "--untracked-files=all" in ci_workflow,
        )
        external_job = release_workflow.split("external-live-gate:", 1)[1].split(
            "release-package:", 1
        )[0]
        external_job_header = external_job.split("steps:", 1)[0]
        record(
            "protected trust-root secrets are scoped below third-party actions",
            lambda: "WIDE_LENS_CONTROLLER" not in external_job_header
            and external_job.index("uses: actions/setup-python@")
            < external_job.index("secrets.WIDE_LENS_CONTROLLER_ALLOWED_SIGNERS_B64"),
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 0.98 <= args.threshold <= 1.0:
        raise SystemExit("threshold must be between 0.98 and 1.0")
    results = run_cases()
    passed = sum(item["passed"] is True for item in results)
    payload = {
        "passed": passed == len(results) and passed / len(results) >= args.threshold,
        "threshold": args.threshold,
        "case_pass_rate": passed / len(results),
        "passed_cases": passed,
        "total_cases": len(results),
        "failures": [item for item in results if item["passed"] is not True],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
