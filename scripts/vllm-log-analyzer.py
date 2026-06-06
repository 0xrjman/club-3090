#!/usr/bin/env python3
"""vLLM log analyzer — extract TPS, MTP acceptance, KV cache stats."""

import re
import sys
import json
from collections import defaultdict
from datetime import datetime

def parse_vllm_logs(log_text):
    """Parse vLLM logs and extract performance metrics."""
    
    results = {
        "generation_throughput": [],
        "prompt_throughput": [],
        "mtp_acceptance": [],
        "mtp_mean_length": [],
        "kv_cache_usage": [],
        "prefix_cache_hit": [],
        "running_reqs": [],
        "timestamps": []
    }
    
    # Pattern for generation throughput
    gen_pattern = re.compile(
        r'(\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*'
        r'Avg generation throughput: ([\d.]+) tokens/s.*'
        r'Running: (\d+) reqs.*'
        r'GPU KV cache usage: ([\d.]+)%.*'
        r'Prefix cache hit rate: ([\d.]+)%'
    )
    
    # Pattern for prompt throughput
    prompt_pattern = re.compile(
        r'Avg prompt throughput: ([\d.]+) tokens/s'
    )
    
    # Pattern for MTP metrics
    mtp_pattern = re.compile(
        r'Mean acceptance length: ([\d.]+).*'
        r'Avg Draft acceptance rate: ([\d.]+)%'
    )
    
    for line in log_text.split('\n'):
        # Extract generation throughput
        gen_match = gen_pattern.search(line)
        if gen_match:
            timestamp = gen_match.group(1)
            gen_tps = float(gen_match.group(2))
            running = int(gen_match.group(3))
            kv_usage = float(gen_match.group(4))
            prefix_hit = float(gen_match.group(5))
            
            results["timestamps"].append(timestamp)
            results["generation_throughput"].append(gen_tps)
            results["running_reqs"].append(running)
            results["kv_cache_usage"].append(kv_usage)
            results["prefix_cache_hit"].append(prefix_hit)
        
        # Extract prompt throughput
        prompt_match = prompt_pattern.search(line)
        if prompt_match:
            prompt_tps = float(prompt_match.group(1))
            results["prompt_throughput"].append(prompt_tps)
        
        # Extract MTP metrics
        mtp_match = mtp_pattern.search(line)
        if mtp_match:
            mean_length = float(mtp_match.group(1))
            acceptance_rate = float(mtp_match.group(2))
            results["mtp_mean_length"].append(mean_length)
            results["mtp_acceptance"].append(acceptance_rate)
    
    return results

def calculate_stats(values):
    """Calculate statistics for a list of values."""
    if not values:
        return {"min": 0, "max": 0, "avg": 0, "std": 0, "count": 0}
    
    avg = sum(values) / len(values)
    variance = sum((x - avg) ** 2 for x in values) / len(values)
    std = variance ** 0.5
    
    return {
        "min": min(values),
        "max": max(values),
        "avg": avg,
        "std": std,
        "count": len(values)
    }

def format_stats(stats, unit="", precision=1):
    """Format statistics for display."""
    return (
        f"min={stats['min']:.{precision}f}{unit} "
        f"avg={stats['avg']:.{precision}f}{unit} "
        f"max={stats['max']:.{precision}f}{unit} "
        f"std={stats['std']:.{precision}f}{unit} "
        f"(n={stats['count']})"
    )

def analyze_concurrency(results):
    """Analyze performance by concurrency level."""
    concurrency_data = defaultdict(list)
    
    for i, running in enumerate(results["running_reqs"]):
        if i < len(results["generation_throughput"]):
            concurrency_data[running].append(results["generation_throughput"][i])
    
    return concurrency_data

def print_report(results):
    """Print formatted analysis report."""
    print("=" * 70)
    print("vLLM Performance Analysis Report")
    print("=" * 70)
    
    # Time range
    if results["timestamps"]:
        print(f"\nTime range: {results['timestamps'][0]} → {results['timestamps'][-1]}")
    
    # Generation Throughput
    print("\n📊 Generation Throughput (TPS)")
    print("-" * 40)
    gen_stats = calculate_stats(results["generation_throughput"])
    print(f"  {format_stats(gen_stats, ' t/s')}")
    
    # By concurrency
    concurrency_data = analyze_concurrency(results)
    if concurrency_data:
        print("\n  By concurrency:")
        for concurrency, tps_list in sorted(concurrency_data.items()):
            stats = calculate_stats(tps_list)
            print(f"    {concurrency} reqs: {format_stats(stats, ' t/s')}")
    
    # Prompt Throughput
    if results["prompt_throughput"]:
        print("\n📊 Prompt Throughput (PP)")
        print("-" * 40)
        pp_stats = calculate_stats(results["prompt_throughput"])
        print(f"  {format_stats(pp_stats, ' t/s')}")
    
    # MTP Performance
    if results["mtp_acceptance"]:
        print("\n📊 MTP Speculative Decoding")
        print("-" * 40)
        acc_stats = calculate_stats(results["mtp_acceptance"])
        print(f"  Acceptance rate: {format_stats(acc_stats, '%')}")
        
        if results["mtp_mean_length"]:
            len_stats = calculate_stats(results["mtp_mean_length"])
            print(f"  Mean accept len: {format_stats(len_stats, '', 2)}")
    
    # KV Cache
    if results["kv_cache_usage"]:
        print("\n📊 KV Cache Usage")
        print("-" * 40)
        kv_stats = calculate_stats(results["kv_cache_usage"])
        print(f"  Usage: {format_stats(kv_stats, '%')}")
    
    # Prefix Cache
    if results["prefix_cache_hit"]:
        prefix_stats = calculate_stats(results["prefix_cache_hit"])
        if prefix_stats["avg"] > 0:
            print(f"  Prefix cache hit: {format_stats(prefix_stats, '%')}")
    
    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    
    if results["generation_throughput"]:
        total_tokens = sum(results["generation_throughput"])
        print(f"  Total samples: {len(results['generation_throughput'])}")
        print(f"  Avg TPS: {gen_stats['avg']:.1f} t/s")
        print(f"  Peak TPS: {gen_stats['max']:.1f} t/s")
        
        if results["mtp_acceptance"]:
            print(f"  Avg MTP acceptance: {acc_stats['avg']:.1f}%")
    
    print()

def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        # Read from file
        with open(sys.argv[1], 'r') as f:
            log_text = f.read()
    else:
        # Read from stdin
        log_text = sys.stdin.read()
    
    results = parse_vllm_logs(log_text)
    print_report(results)
    
    # Optionally output JSON
    if '--json' in sys.argv:
        stats = {
            "generation_throughput": calculate_stats(results["generation_throughput"]),
            "mtp_acceptance": calculate_stats(results["mtp_acceptance"]),
            "mtp_mean_length": calculate_stats(results["mtp_mean_length"]),
            "kv_cache_usage": calculate_stats(results["kv_cache_usage"]),
        }
        print(json.dumps(stats, indent=2))

if __name__ == "__main__":
    main()
