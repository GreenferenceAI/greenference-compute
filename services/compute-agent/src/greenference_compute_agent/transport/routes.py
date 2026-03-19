"""FastAPI routes for the compute agent."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from greenference_protocol import CapacityUpdate, Heartbeat, MinerRegistration

from greenference_compute_agent.application.services import service
from greenference_compute_agent.config import load_settings
from greenference_compute_agent.transport.security import validate_agent_auth, validate_compute_auth

router = APIRouter()
settings = load_settings()


def _agent_auth(
    x_agent_auth: str | None = Header(default=None, alias="X-Agent-Auth"),
    authorization: str | None = Header(default=None),
) -> None:
    bearer = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
    validate_agent_auth(x_agent_auth, bearer, settings.agent_auth_secret)


def _compute_auth(
    x_compute_auth: str | None = Header(default=None, alias="X-Compute-Auth"),
    authorization: str | None = Header(default=None),
) -> None:
    bearer = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
    validate_compute_auth(x_compute_auth, bearer, settings.compute_auth_secret)


# --- agent lifecycle (mirrors miner routes exactly) ---

@router.post("/agent/v1/register", dependencies=[Depends(_agent_auth)])
def register(payload: MinerRegistration) -> dict:
    return service.onboard(payload).model_dump(mode="json")


@router.post("/agent/v1/capacity", dependencies=[Depends(_agent_auth)])
def capacity(payload: CapacityUpdate) -> dict:
    return service.publish_capacity(payload).model_dump(mode="json")


@router.post("/agent/v1/heartbeat", dependencies=[Depends(_agent_auth)])
def heartbeat(payload: Heartbeat) -> dict:
    return service.publish_heartbeat(payload).model_dump(mode="json")


@router.get("/agent/v1/leases/{hotkey}", dependencies=[Depends(_agent_auth)])
def leases(hotkey: str) -> list[dict]:
    return service.sync_leases(hotkey)


@router.post("/agent/v1/reconcile/{hotkey}", dependencies=[Depends(_agent_auth)])
def reconcile(hotkey: str) -> list[dict]:
    return service.reconcile_once(hotkey)


@router.post("/agent/v1/recovery/{hotkey}", dependencies=[Depends(_agent_auth)])
def recover(hotkey: str) -> dict:
    return service.recover_runtime_state(hotkey)


@router.get("/agent/v1/runtimes", dependencies=[Depends(_agent_auth)])
def runtimes() -> list[dict]:
    return service.list_runtime_records()


@router.get("/agent/v1/runtimes/summary", dependencies=[Depends(_agent_auth)])
def runtime_summary() -> dict:
    return service.runtime_summary()


@router.get("/agent/v1/fleet", dependencies=[Depends(_agent_auth)])
def fleet_status() -> dict:
    return service.fleet_status()


@router.get("/agent/v1/placements", dependencies=[Depends(_agent_auth)])
def placements() -> dict:
    return service.placement_summary()


@router.get("/agent/v1/runtimes/{deployment_id}", dependencies=[Depends(_agent_auth)])
def runtime_detail(deployment_id: str) -> dict:
    try:
        return service.get_runtime_record(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agent/v1/deployments/{deployment_id}/terminate", dependencies=[Depends(_agent_auth)])
def terminate(deployment_id: str) -> dict:
    try:
        return service.terminate_deployment(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- pod access (compute-specific) ---

@router.get("/deployments/{deployment_id}/status", dependencies=[Depends(_compute_auth)])
def deployment_status(deployment_id: str) -> dict:
    try:
        return service.get_runtime_record(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/deployments/{deployment_id}/ssh", dependencies=[Depends(_compute_auth)])
def deployment_ssh(deployment_id: str) -> dict:
    try:
        return service.get_ssh_access(deployment_id, include_private_key=False).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deployments/{deployment_id}/exec", dependencies=[Depends(_compute_auth)])
def deployment_exec(deployment_id: str, payload: dict) -> dict:
    command = payload.get("command", [])
    if not isinstance(command, list):
        raise HTTPException(status_code=422, detail="command must be a list")
    try:
        output = service.exec_in_pod(deployment_id, command)
        return {"output": output}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/deployments/{deployment_id}/logs", dependencies=[Depends(_compute_auth)])
def deployment_logs(deployment_id: str):
    try:
        log_stream = service.stream_pod_logs(deployment_id)
        return StreamingResponse(log_stream, media_type="text/plain")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deployments/{deployment_id}/terminate", dependencies=[Depends(_compute_auth)])
def terminate_pod(deployment_id: str) -> dict:
    try:
        return service.terminate_deployment(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- VM access ---

@router.get("/vms/{deployment_id}/status", dependencies=[Depends(_compute_auth)])
def vm_status(deployment_id: str) -> dict:
    try:
        return service.get_runtime_record(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/vms/{deployment_id}/console", dependencies=[Depends(_compute_auth)])
def vm_console(deployment_id: str) -> dict:
    try:
        url = service.get_vm_console_url(deployment_id)
        return {"console_url": url}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- volume backup/restore (Lium-style) ---

@router.post("/deployments/{deployment_id}/volumes/backup", dependencies=[Depends(_compute_auth)])
def volume_backup(deployment_id: str) -> dict:
    try:
        vol = service.backup_pod_volume(deployment_id)
        return vol.model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/deployments/{deployment_id}/volumes/restore", dependencies=[Depends(_compute_auth)])
def volume_restore(deployment_id: str, payload: dict) -> dict:
    backup_uri = payload.get("backup_uri", "")
    if not backup_uri:
        raise HTTPException(status_code=422, detail="backup_uri required")
    try:
        vol = service.restore_pod_volume(deployment_id, backup_uri)
        return vol.model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# --- telemetry & attestation (Targon-style) ---

@router.get("/agent/v1/telemetry", dependencies=[Depends(_agent_auth)])
def telemetry() -> dict:
    return service.hardware_telemetry()


@router.get("/agent/v1/attestation", dependencies=[Depends(_agent_auth)])
def attestation() -> dict:
    return service.attestation_evidence()


@router.get("/agent/v1/security-tier", dependencies=[Depends(_agent_auth)])
def security_tier() -> dict:
    return service.detected_security_tier()


# --- collateral (Lium-style) ---

@router.post("/agent/v1/collateral", dependencies=[Depends(_agent_auth)])
def post_collateral(payload: dict) -> dict:
    hotkey = payload.get("hotkey", settings.miner_hotkey)
    amount = float(payload.get("amount_tao", 0.0))
    return service.post_collateral(hotkey, amount).model_dump(mode="json")


@router.get("/agent/v1/collateral/{hotkey}", dependencies=[Depends(_agent_auth)])
def get_collateral(hotkey: str) -> dict:
    record = service.get_collateral(hotkey)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no collateral for hotkey={hotkey}")
    return record.model_dump(mode="json")


@router.post("/agent/v1/collateral/{hotkey}/slash", dependencies=[Depends(_agent_auth)])
def slash_collateral(hotkey: str, payload: dict) -> dict:
    reason = payload.get("reason", "operator slash")
    amount = float(payload.get("amount_tao", 0.0))
    try:
        return service.slash_collateral(hotkey, reason, amount).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
