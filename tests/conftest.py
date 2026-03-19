"""Shared test fixtures for greenference-compute-agent tests."""

from __future__ import annotations

import pytest

from greenference_protocol import (
    ComputeRuntimeRecord,
    LeaseAssignment,
    MinerRegistration,
    VolumeRecord,
    WorkloadKind,
)

from greenference_compute_agent.domain.attestation import AttestationEngine
from greenference_compute_agent.domain.collateral import CollateralManager
from greenference_compute_agent.domain.pod import StubPodBackend
from greenference_compute_agent.domain.telemetry import TelemetryAgent
from greenference_compute_agent.domain.vm import StubVMBackend
from greenference_compute_agent.domain.volume import LocalVolumeManager
from greenference_compute_agent.infrastructure.repository import ComputeAgentRepository


@pytest.fixture()
def tmp_volume_dir(tmp_path):
    return str(tmp_path / "volumes")


@pytest.fixture()
def tmp_state_path(tmp_path):
    return str(tmp_path / "state.json")


@pytest.fixture()
def repository(tmp_state_path):
    return ComputeAgentRepository(state_path=tmp_state_path)


@pytest.fixture()
def volume_manager(tmp_volume_dir):
    return LocalVolumeManager(tmp_volume_dir)


@pytest.fixture()
def pod_backend():
    return StubPodBackend()


@pytest.fixture()
def vm_backend():
    return StubVMBackend()


@pytest.fixture()
def attestation():
    return AttestationEngine()


@pytest.fixture()
def telemetry():
    return TelemetryAgent(gpu_count=1, vram_gb_per_gpu=80)


@pytest.fixture()
def collateral():
    return CollateralManager()


@pytest.fixture()
def sample_runtime() -> ComputeRuntimeRecord:
    return ComputeRuntimeRecord(
        deployment_id="deploy-001",
        workload_id="workload-001",
        hotkey="miner-hotkey",
        node_id="node-001",
        workload_kind=WorkloadKind.POD.value,
        status="accepted",
        current_stage="accepted_lease",
        ssh_host="127.0.0.1",
        ssh_port=30001,
        gpu_fraction=1.0,
    )


@pytest.fixture()
def sample_volume(tmp_volume_dir) -> VolumeRecord:
    return VolumeRecord(
        deployment_id="deploy-001",
        hotkey="miner-hotkey",
        node_id="node-001",
        path=f"{tmp_volume_dir}/deploy-001",
        size_gb=50,
    )


@pytest.fixture()
def sample_lease() -> LeaseAssignment:
    return LeaseAssignment(
        deployment_id="deploy-001",
        workload_id="workload-001",
        hotkey="miner-hotkey",
        node_id="node-001",
    )


@pytest.fixture()
def sample_registration() -> MinerRegistration:
    return MinerRegistration(
        hotkey="miner-hotkey",
        payout_address="compute-payout",
        auth_secret="test-secret",
        api_base_url="http://localhost:8006",
        validator_url="http://localhost:8002",
    )
