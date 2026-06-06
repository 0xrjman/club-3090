#!/bin/bash
# 测试 200K + 子 agent 的并发边界
# 用法: bash scripts/test-boundary.sh

set -euo pipefail

URL="${URL:-http://localhost:8020}"
MODEL="${MODEL:-qwen3.6}"

# 生成长 prompt（约 N tokens）
generate_prompt() {
    local tokens=$1
    local words=$((tokens * 3 / 4))  # 粗略估算: 1 token ≈ 0.75 words
    python3 -c "print('请用中文详细解释机器学习的基本概念，包括监督学习、无监督学习、强化学习的区别。' * ($words / 50))"
}

echo "=== 测试并发边界 ==="
echo "URL: $URL"
echo "Model: $MODEL"
echo ""

# 测试 1: 单个 200K 请求
echo "--- 测试 1: 单个 200K 请求 ---"
PROMPT_200K=$(generate_prompt 200000)
curl -s "$URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT_200K\"}],
        \"max_tokens\": 100,
        \"stream\": false
    }" | python3 -m json.tool 2>/dev/null | grep -E "\"content\"" | head -1
echo ""

# 测试 2: 并发两个请求（200K + 50K）
echo "--- 测试 2: 并发 200K + 50K ---"
PROMPT_50K=$(generate_prompt 50000)

# 后台发送 200K
curl -s "$URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT_200K\"}],
        \"max_tokens\": 100,
        \"stream\": false
    }" > /tmp/bench_200k.json 2>&1 &

sleep 2

# 后台发送 50K
curl -s "$URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT_50K\"}],
        \"max_tokens\": 100,
        \"stream\": false
    }" > /tmp/bench_50k.json 2>&1 &

wait

echo "200K 结果:"
cat /tmp/bench_200k.json | python3 -m json.tool 2>/dev/null | grep -E "\"content\"" | head -1
echo ""
echo "50K 结果:"
cat /tmp/bench_50k.json | python3 -m json.tool 2>/dev/null | grep -E "\"content\"" | head -1
echo ""

# 测试 3: 并发两个 200K 请求（应该 OOM）
echo "--- 测试 3: 并发 200K + 200K (应该 OOM) ---"
curl -s "$URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT_200K\"}],
        \"max_tokens\": 100,
        \"stream\": false
    }" > /tmp/bench_200k_1.json 2>&1 &

sleep 2

curl -s "$URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT_200K\"}],
        \"max_tokens\": 100,
        \"stream\": false
    }" > /tmp/bench_200k_2.json 2>&1 &

wait

echo "200K #1 结果:"
cat /tmp/bench_200k_1.json | python3 -m json.tool 2>/dev/null | grep -E "\"content\"" | head -1
echo ""
echo "200K #2 结果:"
cat /tmp/bench_200k_2.json | python3 -m json.tool 2>/dev/null | grep -E "\"content\"" | head -1
echo ""

# 检查 KV cache 状态
echo "--- KV Cache 状态 ---"
docker logs vllm-qwen36-27b-nvfp4-mtp 2>&1 | grep "GPU KV cache" | tail -5
