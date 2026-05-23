# LOCAL_CHANGELOG - 本地优化知识积累

> 这不是上游的 CHANGELOG.md (auto-generated commit 流水账)。
> 这是一份思维档案：每次 fork 联动上游、做本地配置迭代时的完整上下文。
> 最终目标：成为一份 24GB 单卡部署效率最大化的实作教材。

---

# 总架构

```
LOCAL_CHANGELOG.md
+-- 卷一：基础篇 (Foundation)
+-- 卷二：本地配置迭代 (Config Iterations)     <- 核心
+-- 卷三：知识沉淀 (Discovered Principles)
+-- 卷四：实验与数据 (Experiments & Data)
+-- 附录 (Appendix)
```

写作守则：
- 卷二只追加，不修改。即使后来的发现推翻了之前的判断，旧条目原地保留。
- 卷三是卷二提炼出的原则。每条原则应从至少一条卷二条目可追溯。
- 每次碰到「哦原来是这样」的时刻，写进卷二。日积月累就是教材。

---

# 卷一：基础篇

## 1.1 硬件上下文

| 维度 | 值 |
|------|-----|
| GPU | 2x NVIDIA RTX 3090 (24 GB each, Ampere SM 8.6) |
| 互联 | PCIe only - 无 NVLink |
| 自定义 all-reduce | 必须禁用 |
| FP8 计算 | 原生不支持 - 仅作为存储优化 |
| 推理引擎 | llama.cpp (主), vLLM (辅) |
| 目标模型 | Qwen3.6-27B (Unsloth MTP Q4_K_M GGUF) |
| 权重尺寸 | ~17.0 GB (Q4_K_M) |

## 1.2 Fork 起点

- **Fork**: 2026-05-23, 上游 noonghunna/club-3090
- **上游版本**: v0.8.3 -> master (9be237d)
- **目标**: 追踪上游更新，本地适应性配置，积累部署知识

## 1.3 术语

| 术语 | 含义 |
|------|------|
| CTX_SIZE / -c | 上下文窗口 (KV cache 地址空间) |
| UBATCH_SIZE / -ub | 每 pass 激活峰值控制杠杆 |
| MTP | Multi-Token Prediction |
| boots != fills | 能启动 != 能填满 |
| cliff-survival | -ub=1024 后 24GB 稳定过 verify-stress |

---

# 卷二：本地配置迭代

---

## 2.1 [2026-05-23] 初始本地修改 + 上游同步

### 修改
- concurrent.yml: Q3_K_XL -> Q4_K_M, slots 4->2
- 新增 mtp-concurrent.yml: MTP(2) + 2 slots + 131K ctx
- mtp.yml: MTP n=2->4, +spec-draft-p-min, reasoning off->on, +--alias
- compose_registry.py: 注册 llamacpp/mtp-concurrent

### 上游状态
19 个新 commit，rebase 后 2 个冲突 (switch.sh header + mtp.yml alias 行)，手动解决。

### 决策理由
| 参数 | 本地值 | 上游 | 理由 |
|------|--------|------|------|
| MTP n | 4 | 2 | 扩大 speculative 搜索空间 |
| reasoning | on | off | mtp = agentic 场景，thinking 有用 |
| --alias | 添加 | 无 | API 调用便利 |
| weights | Q4_K_M | Q3_K_XL | 品质优先，slots 4->2 抵消代价 |

### -> commit: cf991a6

---

## 2.2 [2026-05-23] 上游 262K 假上限发现

### 背景
rebase 中发现 upstream PR #181 把 CTX_SIZE 从 131K -> 262K。调查后发现 PR #200 揭示了 "boots != fills"。

### 关键发现
- 262K 启动、serve、91K needle 全过，但填充到 ~125K 时 flash-attention scratch OOM
- 根源：paged KV 只管理地址空间，flash-attention 运行时分配是隐形开销

### 阶梯探测数据
| -c | 填充到 | 结果 |
|----|--------|------|
| 262144 | ~125K | OOM |
| 200000 | 183K (91%) | OK (1.1 GB headroom) |

### 启示
- 固定深度 needle (91K) 不够 -> 阶梯探测到 0.92xCTX_SIZE
- 启动 != 可填满 - 这个教训直接驱动了本地决策

---

## 2.3 [2026-05-23] 本地 CTX_SIZE 决策: 131K + 1024

### 修改文件: mtp.yml

### 决策: CTX_SIZE=131072, UBATCH_SIZE=1024

为什么不要 200K？
1. MTP n=4 比上游探测用的 n=2 多 ~0.2 GB
2. reasoning on 下 thinking tokens 也占 KV cache
3. agentic 场景极少 >100K，131K prefill 快得多

### 变更汇总
| 位置 | 旧 | 新 |
|------|----|----|
| Max ctx | 262144 | 131072 |
| UBATCH | 512 | 1024 |
| -c 命令行 | 262144 | 131072 |
| -ub 命令行 | 512 | 1024 |

### VRAM 预算
weights (Q4_K_M):            ~17.0 GB
KV at 131K (q4_0 K+V):       ~5.0 GB
MTP draft head + overhead:   ~0.5 GB
total:                       ~22.5 GB
headroom:                    ~1.6 GB OK

### -> commit: 本次

---

# 卷三：知识沉淀

## 3.1 VRAM 守恒法则

24 GB = weights + KV + activations + draft heads + headroom

- Weights 固定 (17 GB for Q4_K_M)
- KV 由 -c + --cache-type 决定
- Activations 由 -ub 控制
- Headroom 必须 >= 1 GB 给 FA scratch

## 3.2 "Boots != Fills" 假上限

- 启动验证地址空间，不验证运行时 FA 分配
- 阶梯探测必须到 0.92xCTX_SIZE 才可靠
- 安全条件：剩余显存 >= 1024 MB

## 3.3 Cliff Survival

- -ub 2048 -> 5/7 verify-stress
- -ub 1024 -> 7/7
- -ub 是最被低估的 VRAM 杠杆

## 3.4 MTP 调优

- n=2 sweet spot, n=4 更大搜索空间
- 每个额外 draft head ~0.1 GB
- -np > 1 自动禁用 MTP

## 3.5 并发的代价

- -np 2 = TPS 减半 (~14 vs ~51)
- concurrent + MTP = 物理 VRAM 边界

---

# 卷四：实验与数据

## 4.1 阶梯探测

| CTX | -ub | 填充 | 结果 | Headroom |
|-----|-----|------|------|----------|
| 262K | 512 | ~125K | OOM | ~353 MB |
| 131K | 1024 | 131K | OK | 1.6 GB |
| 200K | 512 | 183K | OK | 1.1 GB |

## 4.2 质量基准 (8-pack)

| Profile | Score |
|---------|-------|
| vanilla | 102/150 (68%) |
| + froggeric | 95/150 (63%) |
| mtp.yml | 102/150 (68%) |

## 4.3 VRAM 快照

| Profile | Total | Headroom |
|---------|-------|----------|
| mtp.yml 131K | 22.5 GB | 1.6 GB |
| mtp-concurrent | 22.5 GB | 1.6 GB |
| concurrent 192K | 21.1 GB | 2.9 GB |
| upstream 262K | ~23.0 GB | ~353 MB |

---

# 附录

## A. 上游索引

| PR/Issue | 状态 | 影响 |
|----------|------|------|
| PR #181 (131K->262K) | Merged | 后被修正 |
| #197 OOM 报告 | - | 触发探测 |
| PR #200 (262K->200K) | Open | boots!=fills 文档 |
| CLIFFS | PR #200 | 200K = max-safe |

## B. 缩略语

| 缩写 | 全称 |
|------|------|
| MTP | Multi-Token Prediction |
| GGUF | GPT-Generated Unified Format |
| Q4_K_M | 4-bit K-quant, medium |
| FA | Flash Attention |
| TPS | Tokens Per Second |
| -ub | unpaged batch size |

## C. Branch 快照

```
9be237d (upstream/master) docs(CLIFFS): boots != fills
  +-- cf991a6 (HEAD -> master) feat: upgrade concurrent + add MTP concurrent
       (rebase 了 19 个 upstream commit)
```
