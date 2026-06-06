#!/usr/bin/env bash
# vLLM log analyzer — quick TPS and MTP stats from docker logs
#
# Usage:
#   bash scripts/vllm-analyze.sh [container_name]
#   docker logs vllm-qwen36-27b-nvfp4-mtp 2>&1 | python3 scripts/vllm-log-analyzer.py
#   python3 scripts/vllm-log-analyzer.py logfile.txt

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${1:-vllm-qwen36-27b-nvfp4-mtp}"

echo "Analyzing logs from: $CONTAINER"
echo ""

docker logs "$CONTAINER" 2>&1 | python3 "$ROOT_DIR/scripts/vllm-log-analyzer.py"
