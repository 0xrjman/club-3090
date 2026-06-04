# vLLM Gemma-4 unified p-RoPE long-context cache fix

**Scope:** `vllm/vllm-openai:gemma4-unified` for Gemma-4-12B unified.

The preview `gemma4_unified` model code builds Gemma4 RoPE caches from
`config.max_position_embeddings` (131072). The model card advertises 256K via
p-RoPE, and vLLM accepts `--max-model-len 262144` with
`VLLM_ALLOW_LONG_MAX_MODEL_LEN=1`, but prefill past position 131071 indexes the
fixed 131072-row `cos_sin_cache` and trips a CUDA device-side assert.

This overlay keeps the original 131072 cache for normal runs, and sizes the
Gemma4 RoPE cache to `vllm_config.model_config.max_model_len` when the runtime
max exceeds the HF config value. It avoids dynamic cache growth during forward
and keeps the fix local to Gemma4 model construction.

## Validation on club-3090

Validated 2026-06-04 on 2x RTX 3090 PCIe, TP=2, `vllm/vllm-openai:gemma4-unified` (`0.1.dev17235+gf52870f26.d20260603`):

| Compose | Probe | Result |
|---|---|---|
| `vllm/gemma-12b` | boot `--max-model-len 262144` | PASS, `/v1/models` reports `max_model_len=262144` |
| `vllm/gemma-12b` | short chat coherence | PASS (`ok`) |
| `vllm/gemma-12b` | 132,501-token completion | PASS, HTTP 200 |
| `vllm/gemma-12b` | NIAH 154,931 prompt tokens | PASS, exact recall `crimson quartz 47` |
| `vllm/gemma-12b` | NIAH 199,912 prompt tokens | PASS, exact recall `amber circuit 82` |
| `vllm/gemma-12b-mtp` | boot `--max-model-len 262144` + assistant drafter | PASS, KV pool 426,382 tokens |
| `vllm/gemma-12b-mtp` | short decode | PASS, SpecDec metrics show AL 2.55 / 38.6% draft acceptance |
| `vllm/gemma-12b-mtp` | 132,501-token completion | PASS, HTTP 200 |

Fresh TorchInductor cache inspection after the fix found no `rand_strided((131072, ...)` RoPE cache tensors and did find `rand_strided((262144, 256/512), ...)` tensors for the Gemma4 RoPE paths.

Drop when vLLM fixes the Gemma-4 p-RoPE large-prefill bug upstream and the
`gemma4-unified` image no longer crashes past 131072. Related upstream tracker:
vllm-project/vllm#39914.
