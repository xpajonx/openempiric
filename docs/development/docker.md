# Developing OpenEmpiric in Docker

This guide explains how to set up a containerized development environment for OpenEmpiric. Using Docker ensures your host system remains clean, handles all Python/UV workspace dependencies in isolation, and allows you to commit and push changes safely to GitHub.

---

## 1. Environment Configurations

Create the following files in the root of your OpenEmpiric project directory:

### `Dockerfile.dev`
Create a `Dockerfile.dev` file:
```dockerfile
FROM python:3.12-slim

# Install system dependencies (git, ssh, curl)
RUN apt-get update && apt-get install -y \
    git \
    ssh \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /workspace

# Sync workspace dependencies on build (optional optimization)
# Since we mount the workspace, uv sync will run when the container starts
CMD ["/bin/bash"]
```

### `docker-compose.yml`
Create a `docker-compose.yml` file to handle mounting and SSH agent forwarding:
```yaml
version: "3.8"

services:
  dev:
    build:
      context: .
      dockerfile: Dockerfile.dev
    container_name: openempiric-dev
    volumes:
      - .:/workspace
      # Mount SSH auth socket so git inside Docker can use your host's SSH keys
      - ${SSH_AUTH_SOCK}:/ssh-agent
    environment:
      - SSH_AUTH_SOCK=/ssh-agent
    stdin_open: true
    tty: true
```

---

## 2. Step-by-Step Launch Guide

### Step 1: Start SSH Agent on Host
Before starting Docker, ensure your SSH agent is running on your host system and has your GitHub keys loaded. This allows you to push commits from the container securely without copying keys inside:

```bash
# On your host machine
eval $(ssh-agent -s)
ssh-add ~/.ssh/id_rsa  # Replace with your private key path
```

### Step 2: Spin Up the Container
Build and start the container in the background:
```bash
docker-compose up -d --build
```

### Step 3: Enter the Development Container
Open an interactive bash shell inside the running container:
```bash
docker-compose exec dev bash
```

### Step 4: Synchronize the Workspace
Once inside the container, run the UV sync command to set up the development environment:
```bash
uv sync --all-packages --all-extras --dev
```

---

## 3. Running Common Developer Commands

Inside the container:

- **Run tests**:
  ```bash
  uv run pytest
  ```

- **Run doctor checks**:
  ```bash
  uv run oem doctor
  ```

- **Commit and push safely**:
  ```bash
  # Git will automatically use your host's forwarded SSH keys
  git add .
  git commit -m "feat: my change"
  git push origin main
  ```
