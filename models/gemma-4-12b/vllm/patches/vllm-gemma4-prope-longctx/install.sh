#!/bin/bash
# ===========================================================================
# Gemma-4 unified p-RoPE long-context cache fix for the preview vLLM image.
#
# vllm/vllm-openai:gemma4-unified builds Gemma4 RoPE caches from the HF
# config.max_position_embeddings value (131072). With --max-model-len above
# that, large prefill positions index past the fixed cos/sin cache and crash
# with a CUDA device-side assert. This patch sizes the cache from the runtime
# vLLM max_model_len when it is larger.
# ===========================================================================
set -euo pipefail

VLLM=/usr/local/lib/python3.12/dist-packages/vllm
SITE=/usr/local/lib/python3.12/dist-packages
PATCHDIR=/etc/club3090/gemma4-prope-longctx
TARGET="$VLLM/model_executor/models/gemma4.py"
CACHE_ROOT=/root/.cache/vllm/torch_compile_cache
CACHE_MARKER="$CACHE_ROOT/.gemma4-prope-longctx-cache-v2"

purge_compile_cache_once() {
  if [ ! -f "$CACHE_MARKER" ]; then
    echo "[gemma4-prope-longctx] purging stale Gemma4 torch_compile cache for patched RoPE shapes."
    mkdir -p "$CACHE_ROOT"
    shopt -s dotglob nullglob
    rm -rf "$CACHE_ROOT"/*
    shopt -u dotglob nullglob
    touch "$CACHE_MARKER"
  fi
}

verify_patch_health() {
  grep -q "club-3090 Gemma4 p-RoPE long-context cache fix" "$TARGET"
  grep -q "rope_max_position_embeddings=rope_max_position_embeddings" "$TARGET"
  grep -q 'runtime_max_model_len = getattr(vllm_config.model_config, "max_model_len", None)' "$TARGET"
}

if grep -q "club-3090 Gemma4 p-RoPE long-context cache fix" "$TARGET" 2>/dev/null; then
  if verify_patch_health; then
    purge_compile_cache_once
    echo "[gemma4-prope-longctx] patch already present — skipping overlay."
    exit 0
  fi
  echo "[gemma4-prope-longctx] FATAL: partial/stale patch marker found without required pass-through — refusing to boot." >&2
  exit 1
fi

if ! grep -q "max_position_embeddings=config.max_position_embeddings" "$TARGET" 2>/dev/null; then
  echo "[gemma4-prope-longctx] FATAL: expected Gemma4 RoPE anchor not found — refusing to boot." >&2
  exit 1
fi

cd "$SITE"
if patch -p1 --forward --batch --fuzz=0 --reject-file=/tmp/gemma4-prope-longctx.rej < "$PATCHDIR/gemma4-prope-longctx.patch"; then
  if ! verify_patch_health; then
    echo "[gemma4-prope-longctx] FATAL: patch reported success but required anchors are missing — refusing to boot." >&2
    exit 1
  fi
  purge_compile_cache_once
  echo "[gemma4-prope-longctx] runtime max_model_len RoPE cache fix applied cleanly."
else
  echo "[gemma4-prope-longctx] FATAL: diff did not apply to this image — refusing to boot." >&2
  cat /tmp/gemma4-prope-longctx.rej >&2 2>/dev/null || true
  exit 1
fi
