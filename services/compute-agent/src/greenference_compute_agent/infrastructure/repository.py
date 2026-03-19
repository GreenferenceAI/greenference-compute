"""In-memory state store with JSON persistence for the compute agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from greenference_protocol import (
    CapacityUpdate,
    CollateralRecord,
    ComputePlacementRecord,
    ComputeRuntimeRecord,
    Heartbeat,
    LeaseAssignment,
    MinerRegistration,
    VolumeRecord,
)


def _default_state_path() -> str:
    return os.getenv("GREENFERENCE_RUNTIME_STATE_PATH", "/tmp/greenference-compute-runtime-state.json")


@dataclass
class ComputeAgentRepository:
    state_path: str = field(default_factory=_default_state_path)
    registrations: dict[str, MinerRegistration] = field(default_factory=dict)
    capacities: dict[str, CapacityUpdate] = field(default_factory=dict)
    heartbeats: dict[str, Heartbeat] = field(default_factory=dict)
    leases: dict[str, LeaseAssignment] = field(default_factory=dict)
    runtimes: dict[str, ComputeRuntimeRecord] = field(default_factory=dict)
    placements: dict[str, ComputePlacementRecord] = field(default_factory=dict)
    volumes: dict[str, VolumeRecord] = field(default_factory=dict)
    collateral: dict[str, CollateralRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load_state()

    # --- runtimes ---

    def save_runtime(self, runtime: ComputeRuntimeRecord) -> ComputeRuntimeRecord:
        self.runtimes[runtime.deployment_id] = runtime
        self._persist_state()
        return runtime

    def get_runtime(self, deployment_id: str) -> ComputeRuntimeRecord | None:
        return self.runtimes.get(deployment_id)

    def delete_runtime(self, deployment_id: str) -> ComputeRuntimeRecord | None:
        runtime = self.runtimes.pop(deployment_id, None)
        self._persist_state()
        return runtime

    def list_runtimes(self) -> list[ComputeRuntimeRecord]:
        return sorted(self.runtimes.values(), key=lambda r: r.updated_at, reverse=True)

    # --- placements ---

    def save_placement(self, placement: ComputePlacementRecord) -> ComputePlacementRecord:
        self.placements[placement.placement_id] = placement
        self._persist_state()
        return placement

    def list_placements(self) -> list[ComputePlacementRecord]:
        return sorted(self.placements.values(), key=lambda p: p.updated_at, reverse=True)

    def _active_placement(self, deployment_id: str) -> ComputePlacementRecord | None:
        for placement in self.placements.values():
            if placement.deployment_id == deployment_id and placement.status in {"assigned", "active"}:
                return placement
        return None

    # --- volumes ---

    def save_volume(self, volume: VolumeRecord) -> VolumeRecord:
        self.volumes[volume.volume_id] = volume
        self._persist_state()
        return volume

    def get_volume(self, volume_id: str) -> VolumeRecord | None:
        return self.volumes.get(volume_id)

    def get_volume_for_deployment(self, deployment_id: str) -> VolumeRecord | None:
        for vol in self.volumes.values():
            if vol.deployment_id == deployment_id:
                return vol
        return None

    def delete_volume(self, volume_id: str) -> VolumeRecord | None:
        vol = self.volumes.pop(volume_id, None)
        self._persist_state()
        return vol

    def list_volumes(self) -> list[VolumeRecord]:
        return sorted(self.volumes.values(), key=lambda v: v.created_at, reverse=True)

    # --- collateral ---

    def save_collateral(self, record: CollateralRecord) -> CollateralRecord:
        self.collateral[record.hotkey] = record
        self._persist_state()
        return record

    def get_collateral(self, hotkey: str) -> CollateralRecord | None:
        return self.collateral.get(hotkey)

    def list_collateral(self) -> list[CollateralRecord]:
        return sorted(self.collateral.values(), key=lambda r: r.updated_at, reverse=True)

    # --- persistence ---

    def _persist_state(self) -> None:
        state_file = Path(self.state_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "runtimes": [r.model_dump(mode="json") for r in self.runtimes.values()],
            "placements": [p.model_dump(mode="json") for p in self.placements.values()],
            "volumes": [v.model_dump(mode="json") for v in self.volumes.values()],
            "collateral": [c.model_dump(mode="json") for c in self.collateral.values()],
        }
        state_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def _load_state(self) -> None:
        state_file = Path(self.state_path)
        if not state_file.exists():
            return
        try:
            payload = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.runtimes = {
            item["deployment_id"]: ComputeRuntimeRecord(**item)
            for item in payload.get("runtimes", [])
            if isinstance(item, dict) and item.get("deployment_id")
        }
        self.placements = {
            item["placement_id"]: ComputePlacementRecord(**item)
            for item in payload.get("placements", [])
            if isinstance(item, dict) and item.get("placement_id")
        }
        self.volumes = {
            item["volume_id"]: VolumeRecord(**item)
            for item in payload.get("volumes", [])
            if isinstance(item, dict) and item.get("volume_id")
        }
        self.collateral = {
            item["hotkey"]: CollateralRecord(**item)
            for item in payload.get("collateral", [])
            if isinstance(item, dict) and item.get("hotkey")
        }
