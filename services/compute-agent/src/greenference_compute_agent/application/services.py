"""ComputeAgentService — mirrors MinerAgentService for POD/VM workloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from greenference_control_plane.application.services import (
    ControlPlaneService,
    service as default_control_plane_service,
)
from greenference_protocol import (
    CapacityUpdate,
    CollateralRecord,
    ComputePlacementRecord,
    ComputeRuntimeRecord,
    DeploymentState,
    DeploymentStatusUpdate,
    Heartbeat,
    LeaseAssignment,
    MinerRegistration,
    NodeCapability,
    PodConfig,
    SSHAccessRecord,
    VolumeRecord,
    WorkloadKind,
)

from greenference_compute_agent.config import load_settings
from greenference_compute_agent.domain.attestation import AttestationEngine
from greenference_compute_agent.domain.collateral import CollateralError, CollateralManager
from greenference_compute_agent.domain.pod import PodBackend, PodError, ProcessPodBackend, StubPodBackend
from greenference_compute_agent.domain.ssh import (
    _fingerprint_from_public_key,
    build_ssh_access,
    choose_free_port,
    generate_ssh_keypair,
)
from greenference_compute_agent.domain.telemetry import TelemetryAgent
from greenference_compute_agent.domain.templates import get_template
from greenference_compute_agent.domain.vm import FirecrackerVMBackend, StubVMBackend, VMBackend, VMError
from greenference_compute_agent.domain.volume import LocalVolumeManager, VolumeError
from greenference_compute_agent.infrastructure.repository import ComputeAgentRepository


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ComputeRuntimeError(RuntimeError):
    def __init__(self, message: str, *, failure_class: str, stage: str) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.stage = stage


class ComputeAgentService:
    def __init__(
        self,
        repository: ComputeAgentRepository | None = None,
        control_plane: ControlPlaneService | None = None,
        pod_backend: PodBackend | None = None,
        vm_backend: VMBackend | None = None,
        volume_manager: LocalVolumeManager | None = None,
        telemetry: TelemetryAgent | None = None,
        attestation: AttestationEngine | None = None,
        collateral: CollateralManager | None = None,
    ) -> None:
        self.settings = load_settings()
        self.repository = repository or ComputeAgentRepository(state_path=self.settings.runtime_state_path)
        self.control_plane = control_plane or default_control_plane_service
        self.telemetry = telemetry or TelemetryAgent(
            gpu_count=self.settings.gpu_count,
            vram_gb_per_gpu=self.settings.vram_gb_per_gpu,
        )
        self.attestation = attestation or AttestationEngine()
        self.collateral = collateral or CollateralManager()
        self.volume_manager = volume_manager or LocalVolumeManager(self.settings.volume_base_dir)

        if pod_backend is not None:
            self.pod_backend = pod_backend
        elif self.settings.pod_backend == "k8s":
            try:
                from greenference_compute_agent.domain.k8s_pod_backend import K8sPodBackend  # type: ignore[import]

                self.pod_backend = K8sPodBackend()
            except ImportError:
                self.pod_backend = ProcessPodBackend()
        elif self.settings.pod_backend == "stub":
            self.pod_backend = StubPodBackend()
        else:
            self.pod_backend = ProcessPodBackend()

        if vm_backend is not None:
            self.vm_backend = vm_backend
        elif self.settings.vm_backend == "firecracker":
            self.vm_backend = FirecrackerVMBackend()
        else:
            self.vm_backend = StubVMBackend()

        self._recovery_state: dict[str, object | None] = {
            "last_recovery_at": None,
            "resumed_runtimes": 0,
            "terminated_stale_runtimes": 0,
            "machine_loss_events": 0,
            "last_recovery_error": None,
        }

    # --- agent lifecycle ---

    def onboard(self, payload: MinerRegistration) -> MinerRegistration:
        self.repository.registrations[payload.hotkey] = payload
        return self.control_plane.register_miner(payload)

    def publish_capacity(self, payload: CapacityUpdate) -> CapacityUpdate:
        self.repository.capacities[payload.hotkey] = payload
        self._handle_machine_loss(payload.hotkey)
        return self.control_plane.update_capacity(payload)

    def publish_heartbeat(self, payload: Heartbeat) -> Heartbeat:
        self.repository.heartbeats[payload.hotkey] = payload
        return self.control_plane.record_heartbeat(payload)

    def sync_leases(self, hotkey: str) -> list[dict]:
        leases = self.control_plane.list_leases(hotkey)
        active_ids = {lease.deployment_id for lease in leases}
        for lease in leases:
            self.repository.leases[lease.deployment_id] = lease
        for deployment_id, runtime in list(self.repository.runtimes.items()):
            if runtime.hotkey != hotkey:
                continue
            if deployment_id in active_ids:
                continue
            if self._runtime_should_remain_active(runtime):
                continue
            if runtime.status in {"ready", "starting", "preparing", "accepted"}:
                self.terminate_deployment(deployment_id, reason="lease lost during sync")
            self.repository.leases.pop(deployment_id, None)
        return [lease.model_dump(mode="json") for lease in leases]

    def reconcile_once(self, hotkey: str) -> list[dict]:
        leases = self.control_plane.list_leases(hotkey)
        active_ids = {lease.deployment_id for lease in leases}
        reconciled: list[dict] = []
        for lease in leases:
            self.repository.leases[lease.deployment_id] = lease
            result = self._reconcile_workload(lease)
            if result is not None:
                reconciled.append(result.model_dump(mode="json"))
        for deployment_id, runtime in list(self.repository.runtimes.items()):
            if runtime.hotkey != hotkey:
                continue
            if deployment_id in active_ids:
                continue
            if self._runtime_should_remain_active(runtime):
                continue
            if runtime.status not in {"terminated", "failed"}:
                self.terminate_deployment(deployment_id, reason="lease not present during reconcile")
        self._check_ttl_terminations()
        self._refresh_capacity(hotkey)
        self._refresh_heartbeat(hotkey)
        return reconciled

    def recover_runtime_state(self, hotkey: str) -> dict[str, object | None]:
        resumed = 0
        terminated = 0
        leases = self.control_plane.list_leases(hotkey)
        active_leases = {lease.deployment_id for lease in leases}
        lease_by_id = {lease.deployment_id: lease for lease in leases}
        for runtime in list(self.repository.list_runtimes()):
            if runtime.hotkey != hotkey:
                continue
            if runtime.deployment_id not in active_leases:
                if self._runtime_should_remain_active(runtime):
                    runtime = runtime.model_copy(
                        update={
                            "restart_count": runtime.restart_count + 1,
                            "metadata": {**runtime.metadata, "recovered": True, "reused_runtime": True},
                            "updated_at": _utcnow(),
                        }
                    )
                    self.repository.save_runtime(runtime)
                    resumed += 1
                    continue
                if runtime.status not in {"terminated", "failed"}:
                    self.terminate_deployment(runtime.deployment_id, reason="stale runtime recovered without lease")
                    terminated += 1
                continue
            if runtime.status in {"ready", "starting", "preparing", "accepted"}:
                lease = lease_by_id.get(runtime.deployment_id)
                if lease is not None:
                    self._reconcile_workload(lease)
                resumed += 1
        self._recovery_state = {
            "last_recovery_at": _utcnow(),
            "resumed_runtimes": resumed,
            "terminated_stale_runtimes": terminated,
            "machine_loss_events": self._recovery_state.get("machine_loss_events", 0),
            "last_recovery_error": None,
        }
        self._refresh_capacity(hotkey)
        self._refresh_heartbeat(hotkey)
        return dict(self._recovery_state)

    def recovery_status(self) -> dict[str, object | None]:
        return dict(self._recovery_state)

    # --- reconcile ---

    def _reconcile_workload(self, lease: LeaseAssignment):
        deployment = self.control_plane.repository.get_deployment(lease.deployment_id)
        if deployment is None:
            return None
        workload = self.control_plane.repository.get_workload(deployment.workload_id)
        if workload is None:
            return None

        runtime = self.repository.get_runtime(lease.deployment_id) or ComputeRuntimeRecord(
            deployment_id=lease.deployment_id,
            workload_id=lease.workload_id,
            hotkey=lease.hotkey,
            node_id=lease.node_id,
            workload_kind=workload.kind.value,
        )

        # Skip if already ready
        if runtime.status == "ready":
            return deployment

        runtime = self._mark_runtime(runtime, status="accepted", stage="accepted_lease")

        try:
            if workload.kind == WorkloadKind.POD:
                runtime = self._start_pod(runtime, workload)
            elif workload.kind == WorkloadKind.VM:
                runtime = self._start_vm(runtime, workload)
            else:
                raise ComputeRuntimeError(
                    f"unsupported workload kind: {workload.kind}",
                    failure_class="unsupported_workload_kind",
                    stage="reconcile_workload",
                )

            self._ensure_active_placement(runtime, status="active")
            saved = self.control_plane.update_deployment_status(
                DeploymentStatusUpdate(
                    deployment_id=lease.deployment_id,
                    state=DeploymentState.READY,
                    endpoint=runtime.endpoint,
                    ready_instances=1,
                    observed_at=_utcnow(),
                )
            )
            return saved

        except (PodError, VMError, VolumeError, ComputeRuntimeError) as exc:
            failure_class = getattr(exc, "failure_class", "compute_error")
            stage = getattr(exc, "stage", "unknown")
            self._stop_runtime_backend(runtime)
            runtime = self._mark_runtime(
                runtime,
                status="failed",
                stage=stage,
                error=str(exc),
                failure_class=failure_class,
            )
            self._update_active_placement(runtime, status="failed", reason=str(exc))
            self.control_plane.update_deployment_status(
                DeploymentStatusUpdate(
                    deployment_id=lease.deployment_id,
                    state=DeploymentState.FAILED,
                    endpoint=runtime.endpoint,
                    error=str(exc),
                    observed_at=_utcnow(),
                )
            )
            return self.control_plane.repository.get_deployment(lease.deployment_id)

    def _start_pod(
        self,
        runtime: ComputeRuntimeRecord,
        workload: Any,
    ) -> ComputeRuntimeRecord:
        # Parse pod config from workload metadata
        pod_config_dict = workload.metadata.get("pod_config", {}) if hasattr(workload, "metadata") else {}
        pod_config = PodConfig(**pod_config_dict)

        # Apply template defaults
        template_spec = get_template(pod_config.template or "")
        image = workload.image
        if template_spec:
            image = template_spec.image

        runtime = self._mark_runtime(runtime, status="preparing", stage="prepare_pod")

        # Create volume
        volume = self.volume_manager.create_volume(
            deployment_id=runtime.deployment_id,
            hotkey=runtime.hotkey,
            node_id=runtime.node_id,
            size_gb=pod_config.volume_size_gb,
        )
        self.repository.save_volume(volume)

        # Generate ephemeral SSH keypair
        try:
            private_key, public_key = generate_ssh_keypair()
            ssh_fingerprint: str | None = _fingerprint_from_public_key(public_key)
        except Exception as exc:  # noqa: BLE001
            private_key, public_key, ssh_fingerprint = None, "", None

        # Pick SSH port
        ssh_port = choose_free_port(
            start=self.settings.ssh_port_range_start,
            end=self.settings.ssh_port_range_end,
        )

        # Merge SSH keys (ephemeral + user-supplied)
        all_ssh_keys = list(pod_config.ssh_public_keys)
        if public_key:
            all_ssh_keys.append(public_key)

        # Compute env vars (template defaults + user-supplied)
        env_vars = {}
        if template_spec:
            env_vars.update(template_spec.env_vars)
        env_vars.update(pod_config.env_vars)

        runtime = runtime.model_copy(
            update={
                "ssh_host": self.settings.ssh_host,
                "ssh_port": ssh_port,
                "ssh_username": "user",
                "ssh_fingerprint": ssh_fingerprint,
                "volume_id": volume.volume_id,
                "volume_path": volume.path,
                "volume_size_gb": pod_config.volume_size_gb,
                "gpu_fraction": pod_config.gpu_fraction,
                "template": pod_config.template,
                "ttl_seconds": pod_config.shutdown_after_seconds,
                "metadata": {
                    **runtime.metadata,
                    "image": image,
                    "ssh_public_keys": all_ssh_keys,
                    "ssh_private_key": private_key,
                    "env_vars": env_vars,
                    "capacity_type": pod_config.capacity_type,
                    "template": pod_config.template,
                    "pod_started_at": _utcnow().isoformat(),
                },
                "updated_at": _utcnow(),
            }
        )
        self.repository.save_runtime(runtime)

        # Start the pod
        runtime = self._mark_runtime(runtime, status="starting", stage="start_pod")
        workload_spec = workload
        runtime = self.pod_backend.start_pod(runtime, workload_spec)

        # Set endpoint
        registration = self.repository.registrations.get(runtime.hotkey)
        if registration is not None:
            runtime = runtime.model_copy(
                update={
                    "endpoint": f"{registration.api_base_url.rstrip('/')}/deployments/{runtime.deployment_id}",
                    "updated_at": _utcnow(),
                }
            )

        runtime = self._mark_runtime(runtime, status="ready", stage="ready")
        return runtime

    def _start_vm(
        self,
        runtime: ComputeRuntimeRecord,
        workload: Any,
    ) -> ComputeRuntimeRecord:
        runtime = self._mark_runtime(runtime, status="preparing", stage="prepare_vm")

        # Apply template defaults if specified in workload metadata
        pod_config_dict = workload.metadata.get("pod_config", {}) if hasattr(workload, "metadata") else {}
        pod_config = PodConfig(**pod_config_dict)
        template_spec = get_template(pod_config.template or "")
        image = workload.image
        if template_spec:
            image = template_spec.image

        ssh_port = choose_free_port(
            start=self.settings.ssh_port_range_start,
            end=self.settings.ssh_port_range_end,
        )
        runtime = runtime.model_copy(
            update={
                "ssh_host": self.settings.ssh_host,
                "ssh_port": ssh_port,
                "ssh_username": "root",
                "ttl_seconds": pod_config.shutdown_after_seconds,
                "metadata": {
                    **runtime.metadata,
                    "image": image,
                    "template": pod_config.template,
                    "vm_started_at": _utcnow().isoformat(),
                },
                "updated_at": _utcnow(),
            }
        )
        self.repository.save_runtime(runtime)

        runtime = self._mark_runtime(runtime, status="starting", stage="start_vm")
        runtime = self.vm_backend.start_vm(runtime, workload)

        registration = self.repository.registrations.get(runtime.hotkey)
        if registration is not None:
            runtime = runtime.model_copy(
                update={
                    "endpoint": f"{registration.api_base_url.rstrip('/')}/vms/{runtime.deployment_id}",
                    "updated_at": _utcnow(),
                }
            )

        runtime = self._mark_runtime(runtime, status="ready", stage="ready")
        return runtime

    # --- TTL termination (Lium-style) ---

    def _check_ttl_terminations(self) -> None:
        now = _utcnow()
        for runtime in list(self.repository.list_runtimes()):
            if runtime.status != "ready":
                continue
            if runtime.ttl_seconds <= 0:
                continue
            elapsed = (now - runtime.last_transition_at).total_seconds()
            if elapsed >= runtime.ttl_seconds:
                self.terminate_deployment(
                    runtime.deployment_id,
                    reason=f"TTL expired after {runtime.ttl_seconds}s",
                )

    # --- terminate ---

    def terminate_deployment(self, deployment_id: str, reason: str = "terminated by operator") -> dict[str, str]:
        runtime = self.repository.get_runtime(deployment_id)
        lease = self.repository.leases.pop(deployment_id, None)

        if runtime is None and lease is None:
            raise KeyError(f"deployment not found: {deployment_id}")

        hotkey = runtime.hotkey if runtime is not None else (lease.hotkey if lease is not None else "unknown")

        if runtime is not None:
            self._stop_runtime_backend(runtime)
            # Clean up volume
            volume = self.repository.get_volume_for_deployment(deployment_id)
            if volume is not None:
                try:
                    self.volume_manager.delete_volume(volume)
                except Exception:  # noqa: BLE001
                    pass
                self.repository.delete_volume(volume.volume_id)

            runtime = runtime.model_copy(
                update={
                    "status": "terminated",
                    "current_stage": "terminated",
                    "last_error": reason,
                    "container_id": None,
                    "vm_id": None,
                    "updated_at": _utcnow(),
                }
            )
            self.repository.save_runtime(runtime)
            self._update_active_placement(runtime, status="released", reason=reason)

        deployment = self.control_plane.repository.get_deployment(deployment_id)
        if deployment is not None and deployment.state not in {DeploymentState.FAILED, DeploymentState.TERMINATED}:
            self.control_plane.update_deployment_status(
                DeploymentStatusUpdate(
                    deployment_id=deployment_id,
                    state=DeploymentState.TERMINATED,
                    endpoint=deployment.endpoint,
                    error=reason,
                    observed_at=_utcnow(),
                )
            )

        if hotkey:
            self._refresh_capacity(hotkey)
            self._refresh_heartbeat(hotkey)

        return {"status": "terminated", "deployment_id": deployment_id}

    # --- SSH access ---

    def get_ssh_access(self, deployment_id: str, *, include_private_key: bool = False) -> SSHAccessRecord:
        runtime = self.repository.get_runtime(deployment_id)
        if runtime is None:
            raise KeyError(f"deployment not found: {deployment_id}")
        private_key = runtime.metadata.get("ssh_private_key") if include_private_key else None
        return build_ssh_access(runtime, include_private_key=include_private_key, private_key=private_key)

    # --- pod operations ---

    def exec_in_pod(self, deployment_id: str, command: list[str]) -> str:
        runtime = self._get_ready_runtime(deployment_id)
        return self.pod_backend.exec_command(runtime, command)

    def stream_pod_logs(self, deployment_id: str):
        runtime = self._get_ready_runtime(deployment_id)
        return self.pod_backend.stream_logs(runtime)

    # --- volume backup/restore ---

    def backup_pod_volume(self, deployment_id: str) -> VolumeRecord:
        volume = self.repository.get_volume_for_deployment(deployment_id)
        if volume is None:
            raise KeyError(f"no volume for deployment: {deployment_id}")
        updated = self.volume_manager.backup_volume(volume)
        return self.repository.save_volume(updated)

    def restore_pod_volume(self, deployment_id: str, backup_uri: str) -> VolumeRecord:
        volume = self.repository.get_volume_for_deployment(deployment_id)
        if volume is None:
            raise KeyError(f"no volume for deployment: {deployment_id}")
        updated = self.volume_manager.restore_volume(volume, backup_uri)
        return self.repository.save_volume(updated)

    # --- VM console ---

    def get_vm_console_url(self, deployment_id: str) -> str:
        runtime = self.repository.get_runtime(deployment_id)
        if runtime is None:
            raise KeyError(f"deployment not found: {deployment_id}")
        return str(runtime.metadata.get("console_url", ""))

    # --- telemetry & attestation ---

    def hardware_telemetry(self) -> dict[str, Any]:
        snapshot = self.telemetry.collect()
        reserved_split_units = self._reserved_split_units()
        available = self.telemetry.available_split_units(
            self.settings.gpu_count,
            self.settings.gpu_split_units,
            reserved_split_units,
        )
        return {
            **snapshot.model_dump(mode="json"),
            "reserved_split_units": reserved_split_units,
            "available_gpus_fractional": available,
        }

    def attestation_evidence(self) -> dict[str, Any]:
        return self.attestation.generate_evidence()

    def detected_security_tier(self) -> dict[str, str]:
        tier = self.attestation.detect_security_tier()
        return {"tier": tier.value}

    # --- collateral ---

    def post_collateral(self, hotkey: str, amount_tao: float) -> CollateralRecord:
        record = self.collateral.post_collateral(hotkey, amount_tao)
        self.repository.save_collateral(record)
        return record

    def slash_collateral(self, hotkey: str, reason: str, amount_tao: float) -> CollateralRecord:
        try:
            record = self.collateral.slash(hotkey, reason, amount_tao)
        except CollateralError:
            raise
        self.repository.save_collateral(record)
        return record

    def get_collateral(self, hotkey: str) -> CollateralRecord | None:
        return self.repository.get_collateral(hotkey)

    # --- listing / summary ---

    def list_runtime_records(self) -> list[dict]:
        return [r.model_dump(mode="json") for r in self.repository.list_runtimes()]

    def get_runtime_record(self, deployment_id: str) -> dict:
        runtime = self.repository.get_runtime(deployment_id)
        if runtime is None:
            raise KeyError(f"runtime not found: {deployment_id}")
        return runtime.model_dump(mode="json")

    def runtime_summary(self) -> dict[str, Any]:
        runtimes = self.repository.list_runtimes()
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        by_stage: dict[str, int] = {}
        failed = 0
        restart_total = 0
        for runtime in runtimes:
            by_status[runtime.status] = by_status.get(runtime.status, 0) + 1
            by_kind[runtime.workload_kind] = by_kind.get(runtime.workload_kind, 0) + 1
            by_stage[runtime.current_stage] = by_stage.get(runtime.current_stage, 0) + 1
            if runtime.failure_class is not None or runtime.status == "failed":
                failed += 1
            restart_total += runtime.restart_count
        return {
            "total": len(runtimes),
            "failed": failed,
            "by_status": by_status,
            "by_kind": by_kind,
            "by_stage": by_stage,
            "restart_total": restart_total,
            "latest_recovery": self.recovery_status(),
        }

    def placement_summary(self) -> dict[str, Any]:
        placements = self.repository.list_placements()
        by_status: dict[str, int] = {}
        for p in placements:
            by_status[p.status] = by_status.get(p.status, 0) + 1
        return {
            "total": len(placements),
            "by_status": by_status,
            "placements": [p.model_dump(mode="json") for p in placements],
        }

    def fleet_status(self) -> dict[str, Any]:
        return {
            "runtime_summary": self.runtime_summary(),
            "placements": self.placement_summary(),
            "recovery": self.recovery_status(),
        }

    def build_capacity_update(self) -> CapacityUpdate:
        reserved_split_units = self._reserved_split_units()
        available_gpus_frac = self.telemetry.available_split_units(
            self.settings.gpu_count,
            self.settings.gpu_split_units,
            reserved_split_units,
        )
        node = self.telemetry.build_node_capability(
            hotkey=self.settings.miner_hotkey,
            node_id=self.settings.node_id,
            gpu_model=self.settings.gpu_model,
            gpu_count=self.settings.gpu_count,
            vram_gb_per_gpu=self.settings.vram_gb_per_gpu,
            cpu_cores=self.settings.cpu_cores,
            memory_gb=self.settings.memory_gb,
            performance_score=self.settings.performance_score,
            security_tier=self.attestation.detect_security_tier(),
            available_gpus=available_gpus_frac,
        )
        return CapacityUpdate(hotkey=self.settings.miner_hotkey, nodes=[node])

    # --- internal helpers ---

    def _get_ready_runtime(self, deployment_id: str) -> ComputeRuntimeRecord:
        runtime = self.repository.get_runtime(deployment_id)
        if runtime is None or runtime.status != "ready":
            raise KeyError(f"deployment not ready: {deployment_id}")
        return runtime

    def _stop_runtime_backend(self, runtime: ComputeRuntimeRecord) -> None:
        try:
            if runtime.workload_kind == WorkloadKind.VM.value:
                self.vm_backend.stop_vm(runtime)
            else:
                self.pod_backend.stop_pod(runtime)
        except Exception:  # noqa: BLE001
            pass

    def _mark_runtime(
        self,
        runtime: ComputeRuntimeRecord,
        *,
        status: str,
        stage: str,
        error: str | None = None,
        failure_class: str | None = None,
    ) -> ComputeRuntimeRecord:
        runtime = runtime.model_copy(
            update={
                "status": status,
                "current_stage": stage,
                "last_error": error,
                "failure_class": failure_class,
                "last_transition_at": _utcnow(),
                "updated_at": _utcnow(),
            }
        )
        return self.repository.save_runtime(runtime)

    def _runtime_should_remain_active(self, runtime: ComputeRuntimeRecord) -> bool:
        deployment = self.control_plane.repository.get_deployment(runtime.deployment_id)
        if deployment is None:
            return False
        if deployment.hotkey != runtime.hotkey:
            return False
        return deployment.state == DeploymentState.READY

    def _reserved_split_units(self) -> int:
        total = 0
        for runtime in self.repository.list_runtimes():
            if runtime.status in {"ready", "starting", "preparing", "accepted"}:
                from greenference_compute_agent.domain.pod import gpu_split_units_for_fraction
                total += gpu_split_units_for_fraction(runtime.gpu_fraction, self.settings.gpu_split_units)
        return total

    def _handle_machine_loss(self, hotkey: str) -> None:
        capacity = self.repository.capacities.get(hotkey)
        active_nodes = {node.node_id for node in capacity.nodes} if capacity is not None else set()
        for runtime in list(self.repository.list_runtimes()):
            if runtime.hotkey != hotkey:
                continue
            if runtime.status in {"failed", "terminated"}:
                continue
            if runtime.node_id in active_nodes:
                continue
            self._stop_runtime_backend(runtime)
            runtime = self._mark_runtime(
                runtime,
                status="failed",
                stage="machine_lost",
                error=f"machine lost for node={runtime.node_id}",
                failure_class="machine_loss",
            )
            self.control_plane.update_deployment_status(
                DeploymentStatusUpdate(
                    deployment_id=runtime.deployment_id,
                    state=DeploymentState.FAILED,
                    endpoint=runtime.endpoint,
                    error=runtime.last_error,
                    observed_at=_utcnow(),
                )
            )
            self._recovery_state["machine_loss_events"] = int(self._recovery_state.get("machine_loss_events", 0) or 0) + 1

    def _ensure_active_placement(
        self,
        runtime: ComputeRuntimeRecord,
        *,
        status: str,
    ) -> ComputePlacementRecord:
        active = self.repository._active_placement(runtime.deployment_id)
        if active is not None and (
            active.hotkey != runtime.hotkey
            or active.node_id != runtime.node_id
            or active.runtime_id != runtime.runtime_id
        ):
            active = active.model_copy(
                update={
                    "status": "replaced",
                    "reason": "superseded by new machine assignment",
                    "released_at": _utcnow(),
                    "updated_at": _utcnow(),
                }
            )
            self.repository.save_placement(active)
            active = None
        if active is None:
            active = ComputePlacementRecord(
                deployment_id=runtime.deployment_id,
                workload_id=runtime.workload_id,
                runtime_id=runtime.runtime_id,
                hotkey=runtime.hotkey,
                node_id=runtime.node_id,
                status=status,
            )
        else:
            active = active.model_copy(
                update={
                    "runtime_id": runtime.runtime_id,
                    "status": status,
                    "updated_at": _utcnow(),
                }
            )
        return self.repository.save_placement(active)

    def _update_active_placement(
        self,
        runtime: ComputeRuntimeRecord,
        *,
        status: str,
        reason: str | None = None,
    ) -> None:
        active = self.repository._active_placement(runtime.deployment_id)
        if active is None:
            active = self._ensure_active_placement(runtime, status=status)
        now = _utcnow()
        updates: dict[str, Any] = {
            "runtime_id": runtime.runtime_id,
            "status": status,
            "reason": reason,
            "updated_at": now,
        }
        if status == "active" and active.activated_at is None:
            updates["activated_at"] = now
        if status in {"terminated", "failed", "released", "replaced"}:
            updates["released_at"] = now
        self.repository.save_placement(active.model_copy(update=updates))

    def _refresh_heartbeat(self, hotkey: str) -> None:
        heartbeat = self.repository.heartbeats.get(hotkey)
        if heartbeat is None:
            return
        leases = self.control_plane.list_leases(hotkey)
        active_deployments = len([
            r for r in self.repository.runtimes.values()
            if r.hotkey == hotkey and r.status in {"preparing", "starting", "ready"}
        ])
        refreshed = heartbeat.model_copy(
            update={
                "active_leases": len(leases),
                "active_deployments": active_deployments,
            }
        )
        self.repository.heartbeats[hotkey] = refreshed
        self.control_plane.record_heartbeat(refreshed)

    def _refresh_capacity(self, hotkey: str) -> None:
        capacity = self.repository.capacities.get(hotkey)
        if capacity is None:
            return
        reserved_split_units = self._reserved_split_units()
        available_gpus_frac = self.telemetry.available_split_units(
            self.settings.gpu_count,
            self.settings.gpu_split_units,
            reserved_split_units,
        )
        refreshed_nodes = [
            node.model_copy(update={"available_gpus": max(0, int(available_gpus_frac))})
            for node in capacity.nodes
        ]
        refreshed = capacity.model_copy(update={"nodes": refreshed_nodes})
        self.repository.capacities[hotkey] = refreshed
        self.control_plane.update_capacity(refreshed)


service = ComputeAgentService()
