"""Unit tests for pod backend (stub)."""

from __future__ import annotations

import pytest

from greenference_protocol import ComputeRuntimeRecord, WorkloadKind, WorkloadSpec, WorkloadRequirements


def _make_workload() -> WorkloadSpec:
    return WorkloadSpec(
        name="test-pod",
        image="ubuntu:22.04",
        kind=WorkloadKind.POD,
        requirements=WorkloadRequirements(gpu_count=1),
    )


def test_stub_pod_start(pod_backend, sample_runtime):
    workload = _make_workload()
    result = pod_backend.start_pod(sample_runtime, workload)
    assert result.status == "ready"
    assert result.container_id is not None
    assert result.metadata.get("stub") is True


def test_stub_pod_stop(pod_backend, sample_runtime):
    workload = _make_workload()
    started = pod_backend.start_pod(sample_runtime, workload)
    stopped = pod_backend.stop_pod(started)
    assert stopped.status == "terminated"
    assert stopped.container_id is None


def test_stub_pod_health(pod_backend, sample_runtime):
    health = pod_backend.health(sample_runtime)
    assert health["healthy"] is True


def test_stub_pod_exec(pod_backend, sample_runtime):
    output = pod_backend.exec_command(sample_runtime, ["echo", "hello"])
    assert "echo" in output or "stub" in output


def test_stub_pod_stream_logs(pod_backend, sample_runtime):
    lines = list(pod_backend.stream_logs(sample_runtime))
    assert len(lines) >= 1
