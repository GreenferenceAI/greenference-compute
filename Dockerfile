FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app

# copy all sibling repos — build context must be the parent directory
COPY greenference/ /app/greenference/
COPY greenference-api/ /app/greenference-api/
COPY greenference-compute/ /app/greenference-compute/

WORKDIR /app/greenference-compute/services/compute-agent

RUN uv sync --frozen

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "greenference_compute_agent.main:app", "--host", "0.0.0.0", "--port", "8006"]
