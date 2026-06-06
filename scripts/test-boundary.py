#!/usr/bin/env python3
"""
测试 200K + 子 agent 的并发边界
从 10K 开始，每次增加 10K，直到 OOM
"""

import requests
import json
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

URL = "http://localhost:8020/v1/chat/completions"
MODEL = "qwen3.6"

# 生成长 prompt
def generate_prompt(tokens):
    """生成约 N tokens 的中文 prompt"""
    # 粗略估算: 1 中文字 ≈ 2 tokens
    chars = tokens // 2
    return "请详细解释机器学习的基本概念，包括监督学习、无监督学习、强化学习的区别。" * (chars // 50)

def send_request(prompt, max_tokens=50):
    """发送请求并返回结果"""
    try:
        resp = requests.post(URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False
        }, timeout=300)
        
        if resp.status_code == 200:
            data = resp.json()
            tokens_used = data.get("usage", {}).get("total_tokens", 0)
            return {"success": True, "tokens": tokens_used, "status": resp.status_code}
        else:
            return {"success": False, "error": resp.text[:200], "status": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}

def test_concurrent(main_tokens, sub_tokens):
    """测试并发: main_tokens + sub_tokens"""
    print(f"\n{'='*60}")
    print(f"测试: 主请求 {main_tokens//1000}K + 子 agent {sub_tokens//1000}K")
    print(f"{'='*60}")
    
    # 生成 prompt
    main_prompt = generate_prompt(main_tokens)
    sub_prompt = generate_prompt(sub_tokens)
    
    # 并发发送
    with ThreadPoolExecutor(max_workers=2) as executor:
        # 主请求
        future_main = executor.submit(send_request, main_prompt, 100)
        time.sleep(2)  # 延迟 2 秒，模拟真实场景
        
        # 子 agent 请求
        future_sub = executor.submit(send_request, sub_prompt, 50)
        
        # 等待结果
        result_main = future_main.result()
        result_sub = future_sub.result()
    
    # 输出结果
    print(f"主请求: {'✅ 成功' if result_main['success'] else '❌ 失败'}")
    if result_main['success']:
        print(f"  tokens: {result_main['tokens']}")
    else:
        print(f"  错误: {result_main['error'][:100]}")
    
    print(f"子 agent: {'✅ 成功' if result_sub['success'] else '❌ 失败'}")
    if result_sub['success']:
        print(f"  tokens: {result_sub['tokens']}")
    else:
        print(f"  错误: {result_sub['error'][:100]}")
    
    return result_main['success'] and result_sub['success']

def main():
    # 固定主请求 200K
    MAIN_TOKENS = 200000
    
    # 子 agent 从 10K 开始，每次增加 10K
    sub_tokens = 10000
    step = 10000
    max_sub = 100000  # 最大测试到 100K
    
    results = []
    
    print("="*60)
    print("开始边界测试: 主请求 200K + 子 agent 逐步增加")
    print("="*60)
    
    while sub_tokens <= max_sub:
        success = test_concurrent(MAIN_TOKENS, sub_tokens)
        results.append((sub_tokens, success))
        
        if not success:
            print(f"\n{'='*60}")
            print(f"❌ OOM 边界找到!")
            print(f"主请求 {MAIN_TOKENS//1000}K + 子 agent {sub_tokens//1000}K 失败")
            print(f"安全上限: 子 agent {(sub_tokens - step)//1000}K")
            print(f"{'='*60}")
            break
        
        sub_tokens += step
        time.sleep(5)  # 等待 KV cache 释放
    
    # 汇总
    print("\n" + "="*60)
    print("测试结果汇总:")
    print("="*60)
    for sub, success in results:
        status = "✅" if success else "❌"
        print(f"  {status} 200K + {sub//1000}K = {(200000+sub)//1000}K")

if __name__ == "__main__":
    main()
