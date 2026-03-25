# greenference-compute

Compute provider daemon for the Greenference subnet. Serves GPU pods and virtual private machines to renters, managed by the control plane and scored by validators.

Runs on port **8006**.

---

## quickstart

Requires sibling repos (`greenference`, `greenference-api`) cloned alongside this repo — see [running on a GPU machine](#running-on-a-gpu-machine) for full setup.

```bash
cd greenference-compute/services/compute-agent
uv sync
uv run uvicorn greenference_compute_agent.main:app --host 0.0.0.0 --port 8006
```

Check it's alive:

```bash
curl http://localhost:8006/healthz
curl http://localhost:8006/readyz
```

---

## prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for `process` pod backend)
- NVIDIA GPU + drivers + `nvidia-container-toolkit` (for GPU workloads)
- SSH server on host (for tenant pod access)

---

## configuration

All settings are controlled via environment variables. Set them in your shell, `.env` file, or pass via Docker/K8s.

### identity & control plane

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_MINER_HOTKEY` | `compute-local` | Bittensor hotkey for this node |
| `GREENFERENCE_MINER_PAYOUT_ADDRESS` | `5FcomputeLocal` | Payout address for emissions |
| `GREENFERENCE_MINER_AUTH_SECRET` | `greenference-compute-local-secret` | Shared secret for control plane auth |
| `GREENFERENCE_MINER_API_BASE_URL` | `http://127.0.0.1:8006` | Public URL of this agent |
| `GREENFERENCE_MINER_VALIDATOR_URL` | `http://127.0.0.1:8002` | URL of the validator/control plane |

### hardware declaration

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_GPU_MODEL` | `a100` | GPU model name |
| `GREENFERENCE_GPU_COUNT` | `1` | Number of GPUs |
| `GREENFERENCE_VRAM_GB_PER_GPU` | `80` | VRAM per GPU in GB |
| `GREENFERENCE_CPU_CORES` | `32` | CPU cores available |
| `GREENFERENCE_MEMORY_GB` | `128` | System RAM in GB |
| `GREENFERENCE_GPU_SPLIT_UNITS` | `100` | Split units per GPU (100 = 1 full GPU) |
| `GREENFERENCE_PERFORMANCE_SCORE` | `1.0` | Self-declared performance score |

### backends

| Variable | Default | Options | Description |
|---|---|---|---|
| `GREENFERENCE_POD_BACKEND` | `process` | `process`, `stub`, `k8s` | How pods are started. `process` uses Docker subprocess, `stub` is for testing |
| `GREENFERENCE_VM_BACKEND` | `stub` | `stub`, `firecracker` | VM backend. `stub` for testing, `firecracker` for real microVMs |

### SSH & networking

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_SSH_HOST` | `127.0.0.1` | Public IP/hostname for tenant SSH access |
| `GREENFERENCE_SSH_PORT_RANGE_START` | `30000` | Start of port range for pod SSH |
| `GREENFERENCE_SSH_PORT_RANGE_END` | `31000` | End of port range for pod SSH |

### security & attestation

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_SECURITY_TIER` | `standard` | `standard`, `cpu_tee`, `cpu_gpu_attested` |
| `GREENFERENCE_ATTESTATION_ENABLED` | `false` | Enable TEE attestation checks |

### workloads & storage

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_SUPPORTED_WORKLOAD_KINDS` | `pod,vm` | Comma-separated workload kinds to advertise |
| `GREENFERENCE_VOLUME_BASE_DIR` | `/tmp/greenference-compute-volumes` | Where pod volumes are stored |
| `GREENFERENCE_RUNTIME_STATE_PATH` | `/tmp/greenference-compute-runtime-state.json` | JSON file for persisting runtime state |

### agent loop

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_BOOTSTRAP_MINER` | `false` | Enable bootstrap (register + first heartbeat + capacity + recovery) |
| `GREENFERENCE_ENABLE_BACKGROUND_WORKERS` | `false` | Start the reconcile/heartbeat worker loop |
| `GREENFERENCE_WORKER_POLL_INTERVAL_SECONDS` | `1.0` | Seconds between worker loop iterations |

### auth secrets

| Variable | Default | Description |
|---|---|---|
| `GREENFERENCE_AGENT_AUTH_SECRET` | none | HMAC secret for agent-to-control-plane auth |
| `GREENFERENCE_COMPUTE_AUTH_SECRET` | none | HMAC secret for compute-specific endpoints |
| `GREENFERENCE_INFERENCE_AUTH_SECRET` | none | HMAC secret for inference endpoints |

---

## running on a GPU machine

### 1. install dependencies

```bash
# NVIDIA drivers (if not installed)
sudo apt update && sudo apt install -y nvidia-driver-550

# Docker + NVIDIA container toolkit
curl -fsSL https://get.docker.com | sh
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

### 2. clone and install

All repos must be cloned as siblings — they reference each other as local dependencies.

```bash
mkdir greenference && cd greenference
git clone https://github.com/your-org/greenference.git
git clone https://github.com/your-org/greenference-api.git
git clone https://github.com/your-org/greenference-compute.git

cd greenference-compute/services/compute-agent
uv sync
```

### 3. configure

```bash
export GREENFERENCE_MINER_HOTKEY="your-hotkey"
export GREENFERENCE_MINER_AUTH_SECRET="shared-secret-with-control-plane"
export GREENFERENCE_MINER_VALIDATOR_URL="http://control-plane-host:8002"
export GREENFERENCE_MINER_API_BASE_URL="http://your-public-ip:8006"

export GREENFERENCE_GPU_MODEL="a100"
export GREENFERENCE_GPU_COUNT=8
export GREENFERENCE_VRAM_GB_PER_GPU=80
export GREENFERENCE_CPU_CORES=64
export GREENFERENCE_MEMORY_GB=512

export GREENFERENCE_POD_BACKEND=process
export GREENFERENCE_VM_BACKEND=stub

export GREENFERENCE_SSH_HOST="your-public-ip"
export GREENFERENCE_SSH_PORT_RANGE_START=30000
export GREENFERENCE_SSH_PORT_RANGE_END=31000

export GREENFERENCE_VOLUME_BASE_DIR=/var/greenference/volumes
export GREENFERENCE_RUNTIME_STATE_PATH=/var/greenference/runtime-state.json

export GREENFERENCE_BOOTSTRAP_MINER=true
export GREENFERENCE_ENABLE_BACKGROUND_WORKERS=true
```

### 4. run

```bash
uv run uvicorn greenference_compute_agent.main:app --host 0.0.0.0 --port 8006
```

### 5. verify

```bash
# Health
curl http://localhost:8006/healthz
curl http://localhost:8006/readyz

# Security tier auto-detected from hardware
curl http://localhost:8006/agent/v1/security-tier

# Hardware telemetry
curl http://localhost:8006/agent/v1/telemetry

# Active runtimes
curl http://localhost:8006/agent/v1/runtimes/summary
```

---

## running with docker compose (local dev)

The full stack is in `greenference-api/infra/local/docker-compose.yml`. The compute agent is pre-configured with stub backends:

```bash
cd greenference-api/infra/local
docker compose up -d
```

The compute agent runs on port **28006** in local dev.

---

## architecture

```
greenference-compute/
  services/compute-agent/
    src/greenference_compute_agent/
      main.py               # FastAPI app, lifespan, worker loop
      config.py             # Settings from env vars
      application/
        services.py         # ComputeAgentService — core business logic
      domain/
        pod.py              # Pod backends (process/stub)
        vm.py               # VM backends (stub/firecracker)
        volume.py           # Volume create/delete/backup/restore
        ssh.py              # Ephemeral SSH keypair generation
        collateral.py       # Collateral post/slash/reclaim
        telemetry.py        # GPU/CPU telemetry snapshots
        attestation.py      # TEE attestation (TDX/SEV/NVIDIA CC)
        templates.py        # Built-in pod templates
      infrastructure/
        repository.py       # JSON-persisted state store
      transport/
        routes.py           # FastAPI routes (agent + compute endpoints)
        security.py         # HMAC auth validation
```

### agent lifecycle

1. **Bootstrap** — on startup, registers with control plane, sends first heartbeat + capacity update, recovers any persisted runtime state
2. **Worker loop** — every `poll_interval` seconds:
   - Send heartbeat
   - Publish capacity update (GPU availability, split units, security tier)
   - Sync leases from control plane
   - Reconcile: start new workloads, check TTL expirations
3. **Workload dispatch** — when a lease arrives:
   - `pod` → apply template, create volume, generate SSH keypair, pick port, start container via pod backend
   - `vm` → start VM via firecracker or stub backend

### GPU splitting

Each GPU has 100 split units. A pod requesting `gpu_fraction: 0.5` consumes 50 split units. The agent tracks reserved units across all active runtimes and reports available capacity to the control plane.

---

## pod templates

| Template | Image | Port | GPU | Description |
|---|---|---|---|---|
| `jupyter` | `jupyter/scipy-notebook:latest` | 8888 | full | Jupyter with scipy |
| `vscode` | `codercom/code-server:latest` | 8080 | half | VS Code in browser |
| `pytorch` | `pytorch/pytorch:2.2.0-cuda12.1` | 8888 | full | PyTorch + CUDA 12.1 |
| `vllm` | `vllm/vllm-openai:latest` | 8000 | full | vLLM inference server |
| `comfyui` | `ghcr.io/ai-dock/comfyui:latest` | 8188 | full | ComfyUI diffusion |
| `ubuntu-ssh` | `ghcr.io/greenference/ubuntu-ssh:22.04` | 22 | none | Bare Ubuntu with SSH |
| `pytorch-jupyter` | `pytorch/pytorch:2.2.0-cuda12.1` | 8888 | full | PyTorch + JupyterLab |

---

## API endpoints

### health

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Basic health check |
| GET | `/livez` | Liveness probe |
| GET | `/readyz` | Readiness probe (includes DB, worker, runtime summary) |

### agent lifecycle (control plane ↔ agent)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/agent/v1/register` | agent | Register with control plane |
| POST | `/agent/v1/capacity` | agent | Publish capacity update |
| POST | `/agent/v1/heartbeat` | agent | Send heartbeat |
| GET | `/agent/v1/leases/{hotkey}` | agent | Fetch lease assignments |
| POST | `/agent/v1/reconcile/{hotkey}` | agent | Trigger reconcile |
| POST | `/agent/v1/recovery/{hotkey}` | agent | Recover runtime state |
| GET | `/agent/v1/runtimes` | agent | List all runtimes |
| GET | `/agent/v1/runtimes/summary` | agent | Runtime count/status summary |
| GET | `/agent/v1/fleet` | agent | Fleet overview |
| GET | `/agent/v1/placements` | agent | List placements |
| GET | `/agent/v1/runtimes/{id}` | agent | Get runtime by ID |
| DELETE | `/agent/v1/deployments/{id}/terminate` | agent | Terminate a deployment |

### pod operations (tenant-facing)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/deployments/{id}/status` | compute | Pod status |
| GET | `/deployments/{id}/ssh` | compute | SSH connection details |
| POST | `/deployments/{id}/exec` | compute | Execute command in pod |
| GET | `/deployments/{id}/logs` | compute | Stream pod logs |
| DELETE | `/deployments/{id}/terminate` | compute | Stop and clean up pod |

### VM operations

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/vms/{id}/status` | compute | VM status |
| GET | `/vms/{id}/console` | compute | VM console access |

### volumes

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/deployments/{id}/volumes/backup` | compute | Backup pod volume |
| POST | `/deployments/{id}/volumes/restore` | compute | Restore pod volume from backup |

### telemetry & security

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/agent/v1/telemetry` | agent | GPU/CPU telemetry snapshot |
| GET | `/agent/v1/attestation` | agent | TEE attestation evidence |
| GET | `/agent/v1/security-tier` | agent | Detected security tier |

### collateral

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/agent/v1/collateral` | agent | Post collateral |
| GET | `/agent/v1/collateral/{hotkey}` | agent | Get collateral for hotkey |
| POST | `/agent/v1/collateral/{hotkey}/slash` | agent | Slash collateral |

---

## deployment options

### bare metal (Ansible)

```bash
cp infra/ansible/inventory.example.yml infra/ansible/inventory.yml
# Edit with your GPU node IPs and SSH keys
ansible-playbook -i infra/ansible/inventory.yml infra/ansible/site.yml
```

### Kubernetes (Helm)

```bash
helm install greenference-compute infra/helm/greenference-compute \
  --set env.GREENFERENCE_MINER_HOTKEY=your-hotkey \
  --set env.GREENFERENCE_MINER_VALIDATOR_URL=http://validator:8002 \
  --set env.GREENFERENCE_POD_BACKEND=k8s \
  --set env.GREENFERENCE_GPU_MODEL=a100 \
  --set env.GREENFERENCE_GPU_COUNT=8
```

### systemd (manual)

```bash
sudo tee /etc/systemd/system/greenference-compute.service <<EOF
[Unit]
Description=Greenference Compute Agent
After=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/greenference-ai/greenference-compute/services/compute-agent
EnvironmentFile=/etc/greenference/compute.env
ExecStart=/usr/local/bin/uv run uvicorn greenference_compute_agent.main:app --host 0.0.0.0 --port 8006
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now greenference-compute
```

---

## testing

```bash
cd greenference-compute
uv run pytest tests/ -v
```

Tests use stub backends — no Docker or GPU required.
