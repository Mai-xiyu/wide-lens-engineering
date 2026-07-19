#!/usr/bin/env python3
"""Run paired latency/RSS gates for frozen v4 and elastic coordination overhead."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "ebf577269bc8a8393bcaf855810f6dd9cdb82022"
FORMAL_RUNS = 10
WARMUP_RUNS = 1


def peak_rss_bytes() -> int:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        handle = get_current_process()
        if not get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)

    import resource

    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(maximum if sys.platform == "darwin" else maximum * 1024)


def fixed_contract(root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(root / "scripts"))
    sys.path.insert(0, str(root / "tests"))
    from run_eval import bind_authority_grants, valid_contract

    contract = valid_contract(
        "Benchmark the frozen no-change delivery path",
        ["SKILL.md"],
        "change",
        "perf-eval",
    )
    bind_authority_grants(contract)
    return contract


def v4_worker(root: Path, iterations: int) -> dict[str, Any]:
    sys.path.insert(0, str(root / "scripts"))
    from diverge import build_packet

    contract = fixed_contract(root)
    started = time.perf_counter_ns()
    digest = ""
    for _ in range(iterations):
        digest = build_packet(contract, risk="low", coordination="independent")[
            "packet_sha256"
        ]
    elapsed = time.perf_counter_ns() - started
    return {"elapsed_ns": elapsed, "peak_rss_bytes": peak_rss_bytes(), "digest": digest}


def local_kernel() -> str:
    payload = bytes(range(256)) * 2048
    digest = b"wide-lens-main-only"
    for _ in range(256):
        digest = hashlib.sha256(digest + payload).digest()
    return digest.hex()


def elastic_worker(root: Path, treatment: bool) -> dict[str, Any]:
    sys.path.insert(0, str(root / "scripts"))
    from diverge_v5 import (
        build_packet_v5,
        normalize_host_capabilities,
        validate_coordination_plan,
    )

    contract = fixed_contract(root)
    packet = build_packet_v5(contract, risk="medium", coordination="independent")
    started = time.perf_counter_ns()
    digest = local_kernel()
    if treatment:
        capabilities = normalize_host_capabilities(
            {"independent_verifier": True, "max_depth_control": True}
        )
        plan = {
            "version": 1,
            "packet_sha256": packet["packet_sha256"],
            "revision": 0,
            "supersedes_sha256": None,
            "mode": "independent",
            "execution": "main-only",
            "dispatch": "root-assign",
            "communication": "root-relay",
            "tasks": [],
            "assignments": [],
        }
        errors = validate_coordination_plan(packet, capabilities, plan)
        if errors:
            raise RuntimeError("main-only coordination invalid: " + "; ".join(errors))
        digest = hashlib.sha256((digest + json.dumps(plan, sort_keys=True)).encode()).hexdigest()
    elapsed = time.perf_counter_ns() - started
    return {"elapsed_ns": elapsed, "peak_rss_bytes": peak_rss_bytes(), "digest": digest}


def paired_main_worker(root: Path, pairs: int) -> dict[str, Any]:
    sys.path.insert(0, str(root / "scripts"))
    from diverge_v5 import (
        build_packet_v5,
        normalize_host_capabilities,
        validate_coordination_plan,
    )

    contract = fixed_contract(root)
    packet = build_packet_v5(contract, risk="medium", coordination="independent")
    capabilities = normalize_host_capabilities(
        {"independent_verifier": True, "max_depth_control": True}
    )
    plan = {
        "version": 1,
        "packet_sha256": packet["packet_sha256"],
        "revision": 0,
        "supersedes_sha256": None,
        "mode": "independent",
        "execution": "main-only",
        "dispatch": "root-assign",
        "communication": "root-relay",
        "tasks": [],
        "assignments": [],
    }

    def sample(treatment: bool) -> tuple[int, str]:
        started = time.perf_counter_ns()
        digest = local_kernel()
        if treatment:
            errors = validate_coordination_plan(packet, capabilities, plan)
            if errors:
                raise RuntimeError("main-only coordination invalid: " + "; ".join(errors))
            digest = hashlib.sha256(
                (digest + json.dumps(plan, sort_keys=True)).encode()
            ).hexdigest()
        return time.perf_counter_ns() - started, digest

    sample(False)
    sample(True)
    controls: list[int] = []
    treatments: list[int] = []
    for index in range(pairs):
        if index % 2:
            treatment_elapsed, _ = sample(True)
            control_elapsed, _ = sample(False)
        else:
            control_elapsed, _ = sample(False)
            treatment_elapsed, _ = sample(True)
        controls.append(control_elapsed)
        treatments.append(treatment_elapsed)
    return {
        "control_elapsed_ns": controls,
        "treatment_elapsed_ns": treatments,
        "peak_rss_bytes": peak_rss_bytes(),
    }


def shard_kernel(shard: int) -> str:
    payload = bytes(((index + shard) % 256 for index in range(1024 * 1024)))
    digest = hashlib.sha256(f"shard-{shard}".encode()).digest()
    for _ in range(32):
        digest = hashlib.sha256(digest + payload).digest()
    return digest.hex()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--worker", choices=("v4", "control", "treatment", "paired-main", "shard")
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--shard", type=int)
    return parser.parse_args(argv)


def invoke_worker(kind: str, root: Path, *, iterations: int = 50, shard: int | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--worker",
        kind,
        "--root",
        str(root),
        "--iterations",
        str(iterations),
    ]
    if shard is not None:
        command.extend(["--shard", str(shard)])
    completed = subprocess.run(
        command,
        cwd=SKILL_DIR,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def percentile_95(values: list[int]) -> float:
    ordered = sorted(values)
    return float(ordered[math.ceil(0.95 * len(ordered)) - 1])


def metrics(samples: list[dict[str, Any]]) -> dict[str, float]:
    elapsed = [int(item["elapsed_ns"]) for item in samples]
    rss = [int(item["peak_rss_bytes"]) for item in samples]
    return {
        "median_ms": statistics.median(elapsed) / 1_000_000,
        "p95_ms": percentile_95(elapsed) / 1_000_000,
        "median_peak_rss_mib": statistics.median(rss) / 1024 / 1024,
        "p95_peak_rss_mib": percentile_95(rss) / 1024 / 1024,
    }


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("inf")


def extract_baseline(destination: Path) -> None:
    completed = subprocess.run(
        ["git", "archive", "--format=zip", BASELINE_COMMIT],
        cwd=SKILL_DIR,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
    with zipfile.ZipFile(io.BytesIO(completed.stdout)) as archive:
        for info in archive.infolist():
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("unsafe baseline archive path")
        archive.extractall(destination)


def paired_shards() -> dict[str, Any]:
    def sequential() -> tuple[int, list[str]]:
        started = time.perf_counter_ns()
        outputs = [
            invoke_worker("shard", SKILL_DIR, shard=shard)["digest"]
            for shard in range(4)
        ]
        return time.perf_counter_ns() - started, outputs

    def parallel() -> tuple[int, list[str]]:
        started = time.perf_counter_ns()
        with ThreadPoolExecutor(max_workers=4) as executor:
            outputs = list(
                executor.map(
                    lambda shard: invoke_worker("shard", SKILL_DIR, shard=shard)["digest"],
                    range(4),
                )
            )
        return time.perf_counter_ns() - started, outputs

    sequential()
    parallel()
    sequential_samples: list[int] = []
    parallel_samples: list[int] = []
    correct = True
    for index in range(FORMAL_RUNS):
        first, second = (parallel, sequential) if index % 2 else (sequential, parallel)
        first_time, first_outputs = first()
        second_time, second_outputs = second()
        if first is sequential:
            sequential_time, sequential_outputs = first_time, first_outputs
            parallel_time, parallel_outputs = second_time, second_outputs
        else:
            parallel_time, parallel_outputs = first_time, first_outputs
            sequential_time, sequential_outputs = second_time, second_outputs
        sequential_samples.append(sequential_time)
        parallel_samples.append(parallel_time)
        correct = correct and sequential_outputs == parallel_outputs
    sequential_median = statistics.median(sequential_samples) / 1_000_000
    parallel_median = statistics.median(parallel_samples) / 1_000_000
    return {
        "correctness_equal": correct,
        "sequential_median_ms": sequential_median,
        "elastic_median_ms": parallel_median,
        "critical_path_ratio": ratio(parallel_median, sequential_median),
        "passed": correct and parallel_median <= sequential_median,
    }


def run_parent() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="wide-lens-perf-baseline-") as temporary:
        baseline_root = Path(temporary)
        extract_baseline(baseline_root)
        invoke_worker("v4", baseline_root)
        invoke_worker("v4", SKILL_DIR)
        baseline_samples: list[dict[str, Any]] = []
        current_samples: list[dict[str, Any]] = []
        for index in range(FORMAL_RUNS):
            order = ((SKILL_DIR, current_samples), (baseline_root, baseline_samples))
            if index % 2 == 0:
                order = tuple(reversed(order))
            for root, bucket in order:
                bucket.append(invoke_worker("v4", root))
        v4_baseline = metrics(baseline_samples)
        v4_current = metrics(current_samples)
        v4_ratios = {
            "median_latency": ratio(v4_current["median_ms"], v4_baseline["median_ms"]),
            "p95_latency": ratio(v4_current["p95_ms"], v4_baseline["p95_ms"]),
            "peak_rss": ratio(
                v4_current["p95_peak_rss_mib"], v4_baseline["p95_peak_rss_mib"]
            ),
        }
        v4_digest_equal = {
            item["digest"] for item in baseline_samples
        } == {item["digest"] for item in current_samples}
        v4_passed = (
            v4_digest_equal
            and v4_ratios["median_latency"] <= 1.10
            and v4_ratios["p95_latency"] <= 1.20
            and v4_ratios["peak_rss"] <= 1.15
        )

    invoke_worker("paired-main", SKILL_DIR, iterations=4)
    paired_batches = [
        invoke_worker("paired-main", SKILL_DIR, iterations=10)
        for _ in range(FORMAL_RUNS)
    ]
    control_elapsed = [
        elapsed
        for batch in paired_batches
        for elapsed in batch["control_elapsed_ns"]
    ]
    treatment_elapsed = [
        elapsed
        for batch in paired_batches
        for elapsed in batch["treatment_elapsed_ns"]
    ]
    controls = [
        {"elapsed_ns": elapsed, "peak_rss_bytes": batch["peak_rss_bytes"]}
        for batch in paired_batches
        for elapsed in batch["control_elapsed_ns"]
    ]
    treatments = [
        {"elapsed_ns": elapsed, "peak_rss_bytes": batch["peak_rss_bytes"]}
        for batch in paired_batches
        for elapsed in batch["treatment_elapsed_ns"]
    ]
    control_metrics = metrics(controls)
    treatment_metrics = metrics(treatments)
    paired_ratios = [
        ratio(treatment, control)
        for control, treatment in zip(control_elapsed, treatment_elapsed)
    ]
    overhead = {
        "median": ratio(
            treatment_metrics["median_ms"], control_metrics["median_ms"]
        )
        - 1.0,
        "p95": ratio(treatment_metrics["p95_ms"], control_metrics["p95_ms"])
        - 1.0,
        "median_paired_ratio": statistics.median(paired_ratios),
        "paired_samples": len(paired_ratios),
    }
    no_delegation_passed = overhead["median"] <= 0.10 and overhead["p95"] <= 0.20
    shards = paired_shards()
    passed = v4_passed and no_delegation_passed and shards["passed"]
    return {
        "passed": passed,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
            "formal_runs": FORMAL_RUNS,
            "warmup_runs": WARMUP_RUNS,
            "baseline_commit": BASELINE_COMMIT,
        },
        "v4_fixed_path": {
            "baseline": v4_baseline,
            "current": v4_current,
            "ratios": v4_ratios,
            "digest_equal": v4_digest_equal,
            "passed": v4_passed,
        },
        "main_only_overhead": {
            "workload": "128 MiB SHA-256 local kernel plus one coordination decision",
            "control": control_metrics,
            "treatment": treatment_metrics,
            "overhead": overhead,
            "passed": no_delegation_passed,
        },
        "paired_decomposable_kernel": shards,
        "claim_scope": "local protocol/runtime benchmark; excludes model latency and token cost",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.worker:
        root = (args.root or SKILL_DIR).resolve()
        if args.worker == "v4":
            payload = v4_worker(root, args.iterations)
        elif args.worker == "control":
            payload = elastic_worker(root, False)
        elif args.worker == "treatment":
            payload = elastic_worker(root, True)
        elif args.worker == "paired-main":
            payload = paired_main_worker(root, args.iterations)
        else:
            if args.shard is None or not 0 <= args.shard < 4:
                print("--shard 0..3 is required", file=sys.stderr)
                return 2
            started = time.perf_counter_ns()
            digest = shard_kernel(args.shard)
            payload = {
                "elapsed_ns": time.perf_counter_ns() - started,
                "peak_rss_bytes": peak_rss_bytes(),
                "digest": digest,
            }
        print(json.dumps(payload, separators=(",", ":")))
        return 0
    payload = run_parent()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
