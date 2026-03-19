# Greenference Compute Agent — Ansible Deployment

## Prerequisites

- Ansible 2.15+
- Target nodes running Ubuntu 22.04+ with Docker installed
- NVIDIA drivers + nvidia-container-toolkit on GPU nodes

## Quick Start

```bash
# Copy and edit inventory
cp inventory.example.yml inventory.yml
vim inventory.yml

# Run deployment
ansible-playbook -i inventory.yml site.yml

# Check status on a node
ansible compute_nodes -i inventory.yml -m command -a "systemctl status greenference-compute"
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `compute_agent_version` | `0.1.0` | Package version to install |
| `compute_agent_port` | `8006` | HTTP port |
| `volume_base_dir` | `/var/greenference/volumes` | Pod volume storage |
| `state_dir` | `/var/greenference/state` | Runtime state file dir |
