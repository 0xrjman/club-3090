# Club-3090 CI GPU Runner Setup

The vLLM image workflow builds on GitHub-hosted Ubuntu, then optionally smokes the
fresh image on a self-hosted GPU runner. If no runner is registered, the workflow
still pushes the dated `nightly-YYYYMMDD-clubXXXX` image and leaves `latest` /
`nightly-stable` untouched.

## Runner Requirements

- Linux x86_64 host with Docker Engine and Docker Compose v2.
- NVIDIA driver and NVIDIA Container Toolkit installed.
- At least two 24 GB NVIDIA GPUs for the canonical `qwen3.6-27b/vllm/dual`
  smoke. The production validation target is 2x RTX 3090.
- Enough local storage for the vLLM image, model cache, Docker layers, and
  compile caches. Plan for at least 250 GB free.
- A dedicated runner host. Do not run untrusted pull-request jobs on this
  machine.

## Labels

Register the runner with the normal self-hosted labels plus `gpu`:

```text
self-hosted
linux
x64
gpu
```

The workflow checks for an online runner with `self-hosted` and `gpu`; the smoke
job itself targets `[self-hosted, linux, x64, gpu]`.

## Registration

1. Open the GitHub repository.
2. Go to **Settings -> Actions -> Runners -> New self-hosted runner**.
3. Choose Linux x64 and follow GitHub's generated commands.
4. Add the `gpu` label during configuration, or add it later from the runner UI.
5. Install the runner as a service:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
```

The runner user must be able to run Docker commands. On a typical Ubuntu host:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

Restart the runner service after changing group membership.

## Host Preflight

Run these on the runner host before enabling the smoke job:

```bash
nvidia-smi
docker compose version
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

Then clone this repository at the path used by the runner workspace once and
make sure the model cache is present or mounted at the compose default:

```bash
ls -ld models-cache
```

If your cache lives elsewhere, set `MODEL_DIR` in the runner service
environment. The canonical compose reads `${MODEL_DIR:-../../../../../models-cache}`.

## What The Smoke Job Does

On a green build, the workflow:

1. Pulls `ghcr.io/noonghunna/vllm-club3090:nightly-YYYYMMDD-clubXXXX`.
2. Boots `models/qwen3.6-27b/vllm/compose/dual/docker-compose.yml` with a
   temporary compose override that points at the dated image.
3. Waits for `http://localhost:8010/v1/models`.
4. Runs `bash scripts/verify-full.sh`.
5. Runs a three-prompt OpenAI-compatible smoke bench.
6. Only then retags the image as `latest` and `nightly-stable`.

If any smoke step fails, the dated image remains available for debugging and the
rolling aliases do not move.

## Existing Containers

The runner should be dedicated to CI. Before booting the canonical compose, the
workflow tears down the default club-3090 estate if `~/.club3090/estate.yml`
exists, then runs `docker compose down` for the CI project name. Avoid running
manual workloads on the same host while the workflow is active.

## Registry Permissions

The workflow uses `GITHUB_TOKEN` with `packages: write` to push GHCR images and
move aliases. No personal access token is required for the repository-owned
package.

## Retention

The scheduled workflow keeps:

- `latest`
- `nightly-stable`
- every `club-v*` release tag
- dated `nightly-YYYYMMDD-clubXXXX` tags from the last four weeks

Older dated nightly package versions are deleted by the retention job.
