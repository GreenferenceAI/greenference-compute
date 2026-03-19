"""Unit tests for TelemetryAgent."""

from __future__ import annotations

from greenference_protocol import SecurityTier


def test_collect_returns_snapshot(telemetry):
    snapshot = telemetry.collect()
    assert isinstance(snapshot.gpu_utilization_pct, list)
    assert isinstance(snapshot.cpu_utilization_pct, float)
    assert snapshot.memory_total_gb >= 0.0
    assert snapshot.observed_at is not None


def test_available_split_units(telemetry):
    # 1 GPU, 100 units, 50 reserved → 0.5 available
    available = telemetry.available_split_units(gpu_count=1, gpu_split_units=100, reserved_units=50)
    assert available == 0.5


def test_available_split_units_full_reservation(telemetry):
    available = telemetry.available_split_units(gpu_count=1, gpu_split_units=100, reserved_units=100)
    assert available == 0.0


def test_available_split_units_over_reservation(telemetry):
    available = telemetry.available_split_units(gpu_count=1, gpu_split_units=100, reserved_units=150)
    assert available == 0.0


def test_build_node_capability(telemetry):
    node = telemetry.build_node_capability(
        hotkey="test-hotkey",
        node_id="node-001",
        gpu_model="NVIDIA H100",
        gpu_count=1,
        vram_gb_per_gpu=80,
        cpu_cores=64,
        memory_gb=256,
        performance_score=1.0,
        security_tier=SecurityTier.STANDARD,
        available_gpus=1.0,
    )
    assert node.hotkey == "test-hotkey"
    assert node.gpu_model == "NVIDIA H100"
    assert node.security_tier == SecurityTier.STANDARD
