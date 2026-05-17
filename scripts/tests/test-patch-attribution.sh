#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))

from scripts.lib.profiles.compose_registry import COMPOSE_REGISTRY  # noqa: E402
from scripts.lib.profiles import patch_attribution as pa  # noqa: E402

patches_path = root / "scripts/lib/profiles/patches.yml"
arches_path = root / "scripts/lib/profiles/arch_patches.yml"
seed_path = root / "scripts/lib/profiles/calibration_seed.yml"

errors: list[str] = []
known_gaps: list[str] = []


def load(path: Path) -> dict:
    return pa.load(path, errors=errors, root=root)


patch_doc = load(patches_path)
arch_doc = load(arches_path)
seed_doc = load(seed_path)

patches = patch_doc.get("patches", [])
arches = arch_doc.get("arches", [])
seeds = seed_doc.get("anchors", [])

patch_ids: set[str] = set()
covered_files: list[Path] = []
genesis_envs: set[str] = set()

required_patch_keys = pa.REQUIRED_PATCH_KEYS
valid_patch_status = pa.VALID_PATCH_STATUS

for patch in patches:
    missing = required_patch_keys - set(patch)
    if missing:
        errors.append(f"patch {patch.get('id', '<missing>')} missing keys: {sorted(missing)}")
    pid = patch.get("id")
    if not pid:
        errors.append("patch entry missing id")
        continue
    if pid in patch_ids:
        errors.append(f"duplicate patch id: {pid}")
    patch_ids.add(pid)
    if patch.get("status") not in valid_patch_status:
        errors.append(f"{pid} has invalid status {patch.get('status')!r}")
    delivery = patch.get("delivery") or {}
    for key in ("dockerfile_bake", "entrypoint_invoke", "genesis"):
        if key not in delivery or not isinstance(delivery.get(key), bool):
            errors.append(f"{pid}.delivery.{key} must be boolean")
    upstream = patch.get("upstream") or {}
    for key in ("ref", "status", "drop_when"):
        if not upstream.get(key):
            errors.append(f"{pid}.upstream.{key} missing")
    for rel in patch.get("files") or []:
        target = root / rel
        if not target.exists():
            errors.append(f"{pid} references missing file/dir: {rel}")
        else:
            covered_files.append(target)
    if patch.get("genesis_env"):
        genesis_envs.add(patch["genesis_env"])


for artifact in sorted(p for p in (root / "models").rglob("*") if p.is_file() and pa.is_artifact(p)):
    if not pa.covered(artifact, covered_files):
        errors.append(f"orphan patch artifact lacks patches.yml entry: {artifact.relative_to(root)}")

compose_files = sorted((root / "models").glob("**/compose/**/*.yml"))
found_genesis = set()
for compose in compose_files:
    text = compose.read_text(encoding="utf-8")
    found_genesis.update(re.findall(r"GENESIS_ENABLE_[A-Z0-9_]+", text))
missing_genesis = found_genesis - genesis_envs
if missing_genesis:
    errors.append(f"Genesis env flags missing patches.yml entries: {sorted(missing_genesis)}")


def compose_text(compose_name: str, seen: set[Path] | None = None) -> str:
    return pa.compose_text(root, compose_name, seen)


def gap_declared(patch: dict, compose_name: str) -> bool:
    return pa.gap_declared(patch, compose_name)


def reaches(patch: dict, compose_name: str) -> bool:
    return pa.reaches(root, patch, compose_name)


for patch in patches:
    for lb in patch.get("load_bearing_when") or []:
        for compose_name in lb.get("composes") or []:
            if compose_name not in COMPOSE_REGISTRY:
                errors.append(f"{patch['id']} load_bearing_when references unknown compose {compose_name}")
                continue
            if reaches(patch, compose_name):
                continue
            msg = f"{patch['id']} does not reach {compose_name}"
            if gap_declared(patch, compose_name):
                known_gaps.append(msg)
            else:
                errors.append(msg)

arch_allowed_keys = pa.ARCH_ALLOWED_KEYS
arch_required = pa.ARCH_REQUIRED_KEYS
valid_trc = pa.VALID_TRC
valid_arch_status = pa.VALID_ARCH_STATUS
valid_confidence = pa.VALID_CONFIDENCE


def c0_state(row: dict, tp: int, trust_ack: bool = False) -> str:
    return pa.c0_state(row, tp, trust_ack)


for row in arches:
    arch = row.get("arch", "<missing>")
    unknown = set(row) - arch_allowed_keys
    missing = arch_required - set(row)
    if unknown:
        errors.append(f"arch {arch} has unknown keys: {sorted(unknown)}")
    if missing:
        errors.append(f"arch {arch} missing keys: {sorted(missing)}")
        continue
    if row["status"] not in valid_arch_status:
        errors.append(f"arch {arch} invalid status {row['status']!r}")
    if row["confidence"] not in valid_confidence:
        errors.append(f"arch {arch} invalid confidence {row['confidence']!r}")
    trc = row["requires_trust_remote_code"]
    if trc not in valid_trc:
        errors.append(f"arch {arch} invalid requires_trust_remote_code={trc!r}")
    evidence = row.get("requires_trust_remote_code_evidence")
    if not evidence:
        errors.append(f"arch {arch} missing requires_trust_remote_code_evidence")
    if trc == "unverified" and evidence != "none":
        errors.append(f"arch {arch} has unverified trust_remote_code but evidence is not none")
    if trc in {"true", "false"} and evidence == "none":
        errors.append(f"arch {arch} has {trc} trust_remote_code without real evidence")
    for pid in row.get("required_patches") or []:
        if pid not in patch_ids:
            errors.append(f"arch {arch} references unknown patch id {pid}")
    valid_tp = row.get("valid_tp") or {}
    divisors = valid_tp.get("tp_divisors")
    if not isinstance(divisors, list) or not divisors or not all(isinstance(tp, int) for tp in divisors):
        errors.append(f"arch {arch} valid_tp.tp_divisors must be a non-empty integer list")
        continue
    if not isinstance(valid_tp.get("marlin_alignment_required"), bool):
        errors.append(f"arch {arch} valid_tp.marlin_alignment_required must be boolean")
    if valid_tp.get("moe_layout") not in {"dense", "moe"}:
        errors.append(f"arch {arch} valid_tp.moe_layout must be dense|moe")
    states = {c0_state(row, tp) for tp in divisors}
    if len(states) != 1:
        errors.append(f"arch {arch} C0 declared TP states not singular: {sorted(states)}")
    negative_tp = max(divisors) + 1
    if c0_state({**row, "requires_trust_remote_code": "false"}, negative_tp) != "engine-support-unknown":
        errors.append(f"arch {arch} negative TP did not resolve engine-support-unknown")
    if trc == "unverified" and c0_state(row, divisors[0]) != "needs-trust-remote-code-ack":
        errors.append(f"arch {arch} unverified trust_remote_code did not fail closed")

seed_required = pa.SEED_REQUIRED_KEYS
for seed in seeds:
    label = f"{seed.get('model', '<missing>')}:{seed.get('kv_format', '<missing>')}:{seed.get('selected_ctx', '<missing>')}"
    missing = seed_required - set(seed)
    if missing:
        errors.append(f"seed {label} missing keys: {sorted(missing)}")
        continue
    if seed["provenance"] != "seed-from-measured-corpus":
        errors.append(f"seed {label} has invalid provenance {seed['provenance']!r}")
    if seed["confidence"] == "exact" and not seed["source"].startswith("BENCHMARKS.md#"):
        errors.append(f"seed {label} exact confidence lacks BENCHMARKS source")
    source_file = seed["source"].split("#", 1)[0]
    if not (root / source_file).exists():
        errors.append(f"seed {label} source file missing: {source_file}")
    measured = seed["measured"] or {}
    for key in ("vram_mib_per_card", "tps_short", "tps_loaded_ctx", "soak_continuous"):
        if key not in measured:
            errors.append(f"seed {label} measured.{key} missing")
    if measured.get("soak_continuous") not in {"pass", "fail", "not-run"}:
        errors.append(f"seed {label} invalid soak_continuous {measured.get('soak_continuous')!r}")
    smoked = set(seed.get("smoked_capabilities") or [])
    unsmoked = set(seed.get("unsmoked_capabilities") or [])
    if smoked & unsmoked:
        errors.append(f"seed {label} capabilities appear in both smoked and unsmoked: {sorted(smoked & unsmoked)}")
    if "tool-call-stream" in smoked:
        errors.append(f"seed {label} claims tool-call-stream smoked; #145 guard forbids that without explicit working-source evidence")

if known_gaps:
    print("[patch-attribution] known delivery gaps:")
    for gap in sorted(known_gaps):
        print(f"  - {gap}")

if errors:
    print("[patch-attribution] FAIL")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)

print(f"[patch-attribution] PASS: {len(patches)} patch entries, {len(arches)} arch rows, {len(seeds)} calibration seeds")
PY
