"""v0.8.0 Pull-Gate — STEP P4: the `pull` orchestrator.

This is the keystone STEP. It chains the *frozen* predecessor slices into
the locked 6-stratum abort taxonomy, implements the two P4-owned decision
units (stratum-5 `no-fit-model` + the `[C1]` §4.1 total function), runs the
Path-A stratum-6 `[D]` dry-run, and emits via the *existing* `[D]`
generator. It owns the CLI flags. It NEVER edits a frozen module — it
imports P1 (`tools/kv-calc.py`), P2 (`deriver`), P3 (`gates`) and `[D]`
(`generate_compose`) read-only.

pull.py / pull.sh split
-----------------------
`scripts/pull.sh` is a thin argv pass-through (the established
`generate-compose.sh` / `diagnose-profile.sh` pattern): it resolves
`ROOT_DIR` and `exec`s `python3 scripts/lib/profiles/pull.py "$@"`. ALL
decision logic lives here in `pull.py` so it is unit-testable hermetically
(injected hardware-SM + injected fetcher + injected statvfs + injected
`[D]` runner — no live network, no GPU, no real emit in tests).

Public API (stable; the test consumes `run_pull`)
-------------------------------------------------

    from scripts.lib.profiles import pull

    res = pull.run_pull(
        slug, profile_like, *,
        path=None,                  # None -> auto (A if curated+--out else B)
        dry_run=False,              # force Path B
        yes=False,                  # satisfy `confirm→proceed` --yes
        force_download=False,       # no-op + notice this phase
        experimental_arch=False,    # bypass ONLY no-arch-row
        trust_remote_code=False,    # bypass needs-trust-remote-code-ack
        hf_home=None,
        out=None,                   # Path A emit target
        hardware_sm=None,           # INJECTABLE (real detect when None)
        fetcher=None,               # INJECTABLE (real HTTP when None)
        statvfs=None,               # INJECTABLE (real os.statvfs when None)
        d_runner=None,              # INJECTABLE [D] dry-run/emit (real gc.generate)
        profiles=None,
        root=None,
    ) -> PullResult

`PullResult` is a frozen-ish dataclass carrying the terminal outcome, the
stratum at which the run stopped, the structured reason, and (Path A only,
on success) the emitted compose text. The truth-table test asserts against
its fields; the CLI renders it to stdout + an exit code.

`[C1]` §4.1
-----------
`c1_terminal(confidence, raw_verdict, flags)` reproduces the
`v0.8.x-design.md` §4.1 3×3 table EXACTLY as DATA (`_C1_TABLE`, a
`dict[(confidence, raw_verdict)] -> _Cell`). It is TOTAL over
`{exact, derived, estimated-lower-bound} × {fits-clean, fits-constrained,
wont-fit}`. The table is reproduced from `v0.8.x-design.md` lines 62-66
(the `### 4.1` table block). No cell was ambiguous.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]

# Repo root on sys.path so `scripts.lib.profiles.*` absolute imports resolve
# whether this is imported as a module (tests) OR exec'd as a script
# (pull.sh) — same bootstrap pattern as generate_compose.py.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.profiles import deriver as D  # noqa: E402 (P2, frozen — RO)
from scripts.lib.profiles import gates as G  # noqa: E402  (P3, frozen — RO)


# ---------------------------------------------------------------------------
# P1 — tools/kv-calc.py via the documented sys.modules contract.
# ---------------------------------------------------------------------------
_KV = None


def _kv():
    """Load `tools/kv-calc.py` per the in-file import contract: register in
    `sys.modules["kv_calc"]` BEFORE `exec_module` (kv-calc.py uses
    @dataclass, which resolves `cls.__module__` via sys.modules during class
    creation)."""
    global _KV
    if _KV is not None:
        return _KV
    if "kv_calc" in sys.modules:
        _KV = sys.modules["kv_calc"]
        return _KV
    kv_path = REPO_ROOT / "tools" / "kv-calc.py"
    spec = importlib.util.spec_from_file_location("kv_calc", kv_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kv_calc"] = mod  # MUST precede exec_module
    spec.loader.exec_module(mod)
    _KV = mod
    return _KV


# ---------------------------------------------------------------------------
# [D] — generate_compose, imported read-only (engine-pin resolver, scope-gate,
# and the full generate() path for the stratum-6 dry-run + real emit).
# ---------------------------------------------------------------------------
def _gc():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.lib import generate_compose as gc  # noqa: E402

    return gc


# ===========================================================================
# Terminal + stratum vocabulary (design-locked — never extended)
# ===========================================================================
class Terminal(str, Enum):
    """LOCKED §4.1 / §5.3 terminal set — EXACTLY these four, never more."""

    PROCEED = "proceed"
    CONFIRM_PROCEED = "confirm→proceed"
    HARD_BLOCK = "hard-block"
    OVERRIDE_ACCEPTED = "override-accepted"


# Frozen design-lock assertion target.
LOCKED_TERMINALS = frozenset(t.value for t in Terminal)


class Stratum(int, Enum):
    """Where a run stopped. 0 == ran to a [C1] terminal / Path-B verdict."""

    DERIVER = 1            # stratum-1: deriver structured errors
    PROFILE_LIKE = 2       # stratum-2: --profile-like precondition
    C0 = 3                 # stratum-3: [C0] engine-support / runtime / SM
    C2A_DISK = 4           # stratum-4: [C2a] disk pre-gate
    ELIGIBILITY = 5        # stratum-5: pre-[B] generic-dense eligibility
    D_DRY_RUN = 6          # stratum-6: Path-A [D] dry-run refusal
    DECIDED = 0            # reached [C1] / Path-B verdict (no abort)


@dataclass
class PullResult:
    slug: str
    profile_like: str
    path: str                                   # "A" | "B"
    ok: bool                                    # download-eligible / clean verdict
    stratum: Stratum                            # where it stopped (DECIDED=ran through)
    abort_reason: Optional[str] = None          # structured machine reason
    detail: str = ""
    confidence: Optional[str] = None
    raw_verdict: Optional[str] = None
    terminal: Optional[str] = None              # [C1] terminal (when [B] reached)
    emitted: bool = False                       # Path A only: [D] actually emitted
    compose_text: Optional[str] = None          # Path A only, on emit
    notices: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ===========================================================================
# [C1] — §4.1 3×3 confidence × raw-verdict → terminal TOTAL FUNCTION.
#
# Reproduced EXACTLY (as DATA, not branching prose) from
# /opt/ai/docs/v0.8.x-design.md §4.1, the table block at lines 62-66:
#
#   | Confidence            | fits-clean      | fits-constrained | wont-fit  |
#   | exact                 | proceed(silent) | confirm→proceed  | hard-block|
#   | derived               | confirm→proceed | confirm→proceed  | advisory→ |
#   |                       | (--yes)         | (--yes + notice) | --force-  |
#   |                       |                 |                  | download→ |
#   |                       |                 |                  | override- |
#   |                       |                 |                  | accepted  |
#   | estimated-lower-bound | confirm→proceed | confirm→proceed  | advisory→ |
#   |                       | (--yes + floor) | (--yes + floor + | --force-  |
#   |                       |                 |  notice)         | download→ |
#   |                       |                 |                  | override- |
#   |                       |                 |                  | accepted  |
#
# §4.1 footnote (design line 68): "Never silently gate-pass means precisely:
# only exact × fits-clean reaches proceed without --yes."  No cell was
# ambiguous — every (confidence, raw_verdict) pair has exactly one row text.
# ===========================================================================
class _Need(str, Enum):
    """The flag a cell requires to reach its terminal."""

    NONE = "none"                 # silent (only exact×fits-clean)
    YES = "--yes"                 # confirm→proceed gate: --yes accepts
    FORCE = "--force-download"    # advisory: --force-download → override-accepted
    BLOCK = "block"               # unconditional hard-block (no flag clears it)


@dataclass(frozen=True)
class _Cell:
    """One §4.1 table cell, as data."""

    base_terminal: Terminal       # terminal the cell resolves to *when satisfied*
    need: _Need                   # what flag (if any) the cell requires
    note: str                     # the exact §4.1 parenthetical, surfaced to UX


_C = D.Confidence  # exact / estimated-lower-bound (derived RESERVED, still mapped)

# The 9-cell table. KEY = (confidence-value, raw-verdict-string).
# This dict IS the spec; c1_terminal() is a pure lookup + flag interaction.
_C1_TABLE: dict[tuple[str, str], _Cell] = {
    # --- exact -------------------------------------------------------------
    (_C.EXACT.value, "fits-clean"): _Cell(
        Terminal.PROCEED, _Need.NONE, "proceed (silent)"
    ),
    (_C.EXACT.value, "fits-constrained"): _Cell(
        Terminal.CONFIRM_PROCEED, _Need.YES,
        "constraint changed the requested config — user must accept the "
        "applied ctx/KV constraint even though math is trusted",
    ),
    (_C.EXACT.value, "wont-fit"): _Cell(
        Terminal.HARD_BLOCK, _Need.BLOCK,
        "math trusted; suggest closest-fit",
    ),
    # --- derived (RESERVED for the future override-registry phase; still a
    #     total-function row per §4.1 so the table is exhaustive) -----------
    (_C.DERIVED.value, "fits-clean"): _Cell(
        Terminal.CONFIRM_PROCEED, _Need.YES,
        "best-effort, validate post-boot",
    ),
    (_C.DERIVED.value, "fits-constrained"): _Cell(
        Terminal.CONFIRM_PROCEED, _Need.YES,
        "best-effort + constraint notice",
    ),
    (_C.DERIVED.value, "wont-fit"): _Cell(
        Terminal.OVERRIDE_ACCEPTED, _Need.FORCE,
        "advisory → --force-download → override-accepted",
    ),
    # --- estimated-lower-bound --------------------------------------------
    (_C.ESTIMATED_LOWER_BOUND.value, "fits-clean"): _Cell(
        Terminal.CONFIRM_PROCEED, _Need.YES,
        "VRAM is a floor; likely under-modeled",
    ),
    (_C.ESTIMATED_LOWER_BOUND.value, "fits-constrained"): _Cell(
        Terminal.CONFIRM_PROCEED, _Need.YES,
        "VRAM is a floor + constraint notice",
    ),
    (_C.ESTIMATED_LOWER_BOUND.value, "wont-fit"): _Cell(
        Terminal.OVERRIDE_ACCEPTED, _Need.FORCE,
        "advisory → --force-download → override-accepted",
    ),
}

# Domain (used by the test to assert totality without re-deriving it here).
C1_CONFIDENCE_DOMAIN = (
    _C.EXACT.value,
    _C.DERIVED.value,
    _C.ESTIMATED_LOWER_BOUND.value,
)
C1_RAW_VERDICT_DOMAIN = ("fits-clean", "fits-constrained", "wont-fit")


@dataclass(frozen=True)
class C1Outcome:
    terminal: Terminal
    satisfied: bool          # did the present flags satisfy the cell?
    note: str
    needs: str               # the flag still required (or "" when satisfied/blocked)


def c1_terminal(confidence: str, raw_verdict: str, flags: dict) -> C1Outcome:
    """The §4.1 total function. Pure: (confidence, raw_verdict, flags) ->
    C1Outcome. `flags` carries booleans `yes` / `force_download`.

    Resolution per §4.1 (the dict above is the authority — this only
    encodes the flag interaction the table prescribes, never new policy):

      - `_Need.NONE`  : reaches `proceed` with no flag (only exact×clean).
      - `_Need.YES`   : `confirm→proceed`; reached when `--yes` present,
                        else NOT satisfied (advisory: "re-run with --yes").
      - `_Need.FORCE` : low-confidence wont-fit advisory → `override-accepted`
                        ONLY with `--force-download`; else NOT satisfied.
      - `_Need.BLOCK` : `hard-block`, no flag clears it (exact×wont-fit).
    """
    key = (confidence, raw_verdict)
    cell = _C1_TABLE.get(key)
    if cell is None:  # pragma: no cover — totality is test-asserted
        raise KeyError(f"§4.1 has no cell for {key!r} (table is TOTAL)")

    if cell.need is _Need.NONE:
        return C1Outcome(cell.base_terminal, True, cell.note, "")
    if cell.need is _Need.BLOCK:
        # hard-block is itself the terminal; not a gate-pass, no flag clears.
        return C1Outcome(Terminal.HARD_BLOCK, False, cell.note, "")
    if cell.need is _Need.YES:
        if flags.get("yes"):
            return C1Outcome(cell.base_terminal, True, cell.note, "")
        return C1Outcome(cell.base_terminal, False, cell.note, "--yes")
    if cell.need is _Need.FORCE:
        if flags.get("force_download"):
            # override-accepted is NOT a gate-pass (§5.3 / design line 106):
            # state + telemetry notice only, NO download this phase.
            return C1Outcome(
                Terminal.OVERRIDE_ACCEPTED, True, cell.note, ""
            )
        return C1Outcome(
            Terminal.OVERRIDE_ACCEPTED, False, cell.note, "--force-download"
        )
    raise AssertionError(f"unreachable _Need {cell.need!r}")  # pragma: no cover


# ===========================================================================
# §7 boot-fit ≠ runtime caveat (printed on every download-eligible AND every
# Path-B verdict; presentation-only, decision-logic-neutral).
# ===========================================================================
CAVEAT_S7 = (
    "boot-fit satisfied; this does NOT guarantee stability under "
    "sustained / accumulated-context workloads — validate with "
    "soak-continuous before relying on it (recommend: scripts/soak.sh "
    "SOAK_MODE=continuous)."
)


# ===========================================================================
# Hardware-SM detection (real path, INJECTABLE so tests are hermetic).
# ===========================================================================
def detect_hardware_sm() -> Optional[float]:
    """Real detection via the existing preflight path
    (`nvidia-smi --query-gpu=...,compute_cap`). Returns the MIN sm across
    visible GPUs (the binding constraint for a multi-GPU runtime), or None
    when nvidia-smi is unavailable. NEVER called in tests (they inject)."""
    import shutil
    import subprocess

    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    if out.returncode != 0:
        return None
    caps = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            caps.append(float(line))
        except ValueError:  # pragma: no cover
            continue
    return min(caps) if caps else None


# ===========================================================================
# The orchestrator — chains the frozen slices in the LOCKED stratum order.
# ===========================================================================
def run_pull(
    slug: str,
    profile_like: str,
    *,
    path: Optional[str] = None,
    dry_run: bool = False,
    yes: bool = False,
    force_download: bool = False,
    experimental_arch: bool = False,
    trust_remote_code: bool = False,
    hf_home: Optional[str] = None,
    out: Optional[str] = None,
    hardware_sm: Optional[float] = None,
    fetcher=None,
    statvfs: Optional[Callable[[str], Any]] = None,
    d_runner: Optional[Callable[[Path, str, bool], tuple]] = None,
    profiles=None,
    root: Optional[Path] = None,
) -> PullResult:
    """Execute the 6-stratum Pull-Gate state machine.

    Order is STRICT and monotonic (the design's locked taxonomy):
      stratum-1  deriver structured errors (P2; already structured)
      stratum-2  --profile-like precondition (P3 gates.stratum2)
      stratum-3  [C0] engine-support/runtime/SM (P3 gates.c0) + flag bypass
      stratum-4  [C2a] disk pre-gate (P3 gates.c2a)
      stratum-5  pre-[B] generic-dense eligibility (P4; this module)
      [B]        kv.raw_verdict (P1)
      [C1]       §4.1 total function (P4; this module)
      stratum-6  Path A only: [D] dry-run (read-only existing generator)
      emit       Path A only, on a clean dry-run: real [D] generate()

    Path B (universal evaluate / --dry-run) NEVER reaches stratum-6, NEVER
    calls [D], NEVER downloads. `--force-download` is a no-op + notice this
    phase (emit/download deferred to the Loop phase).
    """
    root = root or REPO_ROOT
    gc = _gc()
    kv = _kv()
    flags = {"yes": yes, "force_download": force_download}

    # ----- Path selection -------------------------------------------------
    # Explicit `path=` wins (test driver). Else: --dry-run forces Path B;
    # otherwise Path A iff a curated tier-1 hit AND an --out target, else B.
    forced_path = path
    if profiles is None:
        from scripts.lib.profiles.compat import load_profiles

        profiles = load_profiles()

    # ----- stratum-1: deriver (P2, frozen) --------------------------------
    der = D.derive(
        slug, hf_home=hf_home, fetcher=fetcher, profiles=profiles
    )
    if der.error is not None:
        return PullResult(
            slug=slug, profile_like=profile_like, path="?",
            ok=False, stratum=Stratum.DERIVER,
            abort_reason=der.error.kind.value, detail=str(der.error),
        )

    is_curated = der.tier1 is not None
    if forced_path in ("A", "B"):
        eff_path = forced_path
    elif dry_run:
        eff_path = "B"
    elif is_curated and out is not None:
        eff_path = "A"
    else:
        eff_path = "B"

    res = PullResult(
        slug=slug, profile_like=profile_like, path=eff_path,
        ok=False, stratum=Stratum.DECIDED,
        confidence=der.confidence.value if der.confidence else None,
    )

    # ----- stratum-2: --profile-like precondition (P3, frozen) ------------
    s2 = G.stratum2_profile_like(
        profile_like, derive_result=der, path=eff_path, root=root,
    )
    if not s2.ok:
        res.ok = False
        res.stratum = Stratum.PROFILE_LIKE
        res.abort_reason = s2.refusal.reason
        res.detail = s2.refusal.detail
        return res

    # ----- stratum-3: [C0] engine-support / runtime / SM (P3, frozen) -----
    if hardware_sm is None:
        hardware_sm = detect_hardware_sm()
    if hardware_sm is None:
        # No GPU detected and none injected: cannot honestly run the SM
        # gate. Fail closed (never fabricate a fit per §1).
        res.ok = False
        res.stratum = Stratum.C0
        res.abort_reason = "hardware-sm-undetermined"
        res.detail = (
            "could not detect GPU compute capability (nvidia-smi absent) "
            "and no --hardware override given; refusing to run the SM gate "
            "blind"
        )
        return res

    c0 = G.c0_engine_support(
        profile_like, der, path=eff_path, hardware_sm=float(hardware_sm),
        root=root,
    )
    if c0.state != G.C0State.ENGINE_SUPPORTED:
        # Apply ONLY the bypasses [C0] explicitly tagged on `.bypassable_by`.
        # --experimental-arch bypasses ONLY no-arch-row (never
        # runtime-incompatible — its bypassable_by is () so the membership
        # test below can never let it through).
        bypassed = False
        provided = set()
        if experimental_arch:
            provided.add(G.BYPASS_EXPERIMENTAL_ARCH)
        if trust_remote_code:
            provided.add(G.BYPASS_TRUST_REMOTE_CODE)
        tags = set(c0.bypassable_by)
        if tags and tags.issubset(provided):
            # Every condition [C0] flagged is covered by a provided flag.
            # (auto_map+no-arch-row tags BOTH; requires BOTH flags — the
            # subset test enforces that automatically.)
            bypassed = True
        if not bypassed:
            res.ok = False
            res.stratum = Stratum.C0
            res.abort_reason = c0.state.value + (
                "/" + c0.sub_reason.value if c0.sub_reason else ""
            )
            res.detail = c0.detail
            res.diagnostics["c0_bypassable_by"] = list(c0.bypassable_by)
            return res
        res.notices.append(
            f"[C0] {c0.state.value}"
            + (f"/{c0.sub_reason.value}" if c0.sub_reason else "")
            + f" bypassed by {sorted(provided & tags)} (Path B only this "
            f"phase; outcome capture deferred to Loop)"
        )

    # ----- stratum-4: [C2a] disk pre-gate (P3, frozen) — AFTER [C0] -------
    c2a = G.c2a_disk(der, hf_home=hf_home, statvfs=statvfs)
    if c2a.state != G.C2aState.DISK_OK:
        res.ok = False
        res.stratum = Stratum.C2A_DISK
        res.abort_reason = c2a.state.value          # "disk-short"
        res.detail = c2a.detail
        return res

    # ----- stratum-5: pre-[B] generic-dense eligibility (P4) --------------
    # Separate pre-[B] abort — NOT a [C0] rewrite ([C0] already emitted
    # engine-supported / a bypassed unknown and stays). Non-bypassable:
    # there is no fit model to force. Tier-1 curated hits are eligible by
    # construction (the curated profile encodes a priced model); for a
    # derived model, ineligible iff deriver said NOT tier-1 AND
    # kv.is_generic_dense_eligible(config) is False (deriver already ran the
    # predicate into .generic_dense_eligible).
    if not is_curated:
        eligible = bool(der.generic_dense_eligible)
        if not eligible:
            res.ok = False
            res.stratum = Stratum.ELIGIBILITY
            res.abort_reason = "no-fit-model"
            res.detail = (
                f"{slug}: not Tier-1 curated and not generic-dense eligible "
                f"(arch {(der.profile or {}).get('arch')!r}); no fit model "
                f"to price — pre-[B] hard-stop (non-bypassable; "
                f"--experimental-arch does NOT apply — there is no model)"
            )
            return res

    # ----- [B]: raw fit verdict (P1 kv.raw_verdict) -----------------------
    entry = s2.registry_entry or {}
    spec = der.spec
    if spec is None:
        # Tier-1 curated hit: build the curated-exact kv-calc spec (real
        # model_family preserved) so [B] prices it through P1's authoritative
        # family branch, not generic-dense's conservative lower-bound.
        spec = _curated_spec(profiles, der)
    rv = kv.raw_verdict(
        spec=spec,
        kv_format=entry.get("kv_format", "fp8_e5m2"),
        max_ctx=int(entry.get("max_ctx") or spec.get("max_ctx_supported")
                    or 131072),
        max_num_seqs=int(entry.get("max_num_seqs") or 1),
        tp=int(entry.get("tp") or 1),
        mem_util=float(entry.get("mem_util") or 0.95),
    )
    raw = rv["raw_verdict"]
    res.raw_verdict = raw
    res.diagnostics["b_breakdown"] = rv.get("breakdown_gb")

    # ----- [C1]: §4.1 total function (P4) ---------------------------------
    conf = der.confidence.value
    c1 = c1_terminal(conf, raw, flags)
    res.terminal = c1.terminal.value
    res.diagnostics["c1_note"] = c1.note

    if c1.terminal is Terminal.HARD_BLOCK:
        res.ok = False
        res.stratum = Stratum.DECIDED
        res.abort_reason = "hard-block"
        res.detail = f"[C1] {conf}×{raw} → hard-block ({c1.note})"
        return res

    if not c1.satisfied:
        # confirm→proceed without --yes, or low-conf wont-fit advisory
        # without --force-download. Honest non-pass: state + the flag the
        # user must add. NEVER a silent gate-pass.
        res.ok = False
        res.stratum = Stratum.DECIDED
        res.abort_reason = f"{c1.terminal.value}:needs {c1.needs}"
        res.detail = (
            f"[C1] {conf}×{raw} → {c1.terminal.value} ({c1.note}); "
            f"re-run with {c1.needs} to accept"
        )
        return res

    if c1.terminal is Terminal.OVERRIDE_ACCEPTED:
        # NOT a gate-pass (design line 106): record the state + a telemetry
        # notice; do NOT download / emit this phase.
        res.ok = True
        res.stratum = Stratum.DECIDED
        res.abort_reason = None
        res.detail = (
            f"[C1] {conf}×{raw} → override-accepted ({c1.note}); "
            f"override-accepted is NOT a fit — telemetry capture + "
            f"download deferred to the Loop phase (no download this phase)"
        )
        res.notices.append(
            "override-accepted: telemetry capture deferred to Loop phase; "
            "no weights downloaded this phase"
        )
        res.notices.append(CAVEAT_S7)
        return res

    # ----- terminal is proceed / confirm→proceed (satisfied) --------------
    if raw == "fits-constrained" and eff_path == "A":
        res.notices.append(
            "known effective-cap warning: vLLM internally caps effective "
            "KV on this hardware; [D] emits the chosen registry profile "
            "UNCHANGED (no compose config rewritten)"
        )

    # ----- Path B: print the §7-caveated verdict, NEVER touch [D] ---------
    if eff_path == "B":
        res.ok = True
        res.stratum = Stratum.DECIDED
        res.detail = (
            f"Path B verdict: [C1] {conf}×{raw} → {c1.terminal.value} "
            f"({c1.note})"
        )
        if force_download:
            res.notices.append(
                "--force-download is a no-op this phase (Path B never "
                "downloads / emits; deferred to a later phase)"
            )
        res.notices.append(CAVEAT_S7)
        return res

    # ----- Path A: stratum-6 [D] dry-run, then real emit ------------------
    runner = d_runner or (lambda r, p, ad: gc.generate(r, p, accept_degraded=ad))
    try:
        compose_text, meta = runner(root, profile_like, False)
    except gc.Refuse as r:
        # [D] refused at one of its LATER points (pin mismatch / TP·KV /
        # trc / foundational-or-degraded patch drift). Surface as a Path-A
        # abort — do NOT report download-eligible (Codex-r5 Med-1: the
        # stratum-2 scope-gate is necessary-not-sufficient for [D] emit).
        res.ok = False
        res.stratum = Stratum.D_DRY_RUN
        res.abort_reason = f"d-refused:{_short_refuse(str(r))}"
        res.detail = (
            f"Path-A [D] dry-run refused: {r} — NOT reported "
            f"download-eligible (stratum-2 scope-gate is necessary but not "
            f"sufficient for [D] emit)"
        )
        return res

    # Clean dry-run: the validated registry key is handed to the existing
    # [D] for real emission. The dry-run already produced the exact compose
    # text (gc.generate is pure); honor --out (COMPOSE_GENERATOR.md
    # --project-directory correctness: the emitted compose's relative
    # overlay mounts resolve from the compose file's own directory, so the
    # consumer must `docker compose --project-directory <repo-root>` — we
    # surface that requirement as a notice and write where --out points).
    res.ok = True
    res.stratum = Stratum.DECIDED
    res.emitted = True
    res.compose_text = compose_text
    res.diagnostics["d_meta"] = meta
    res.detail = (
        f"Path A download-eligible: [C1] {conf}×{raw} → "
        f"{c1.terminal.value}; [D] dry-run clean, compose emitted "
        f"(pin={meta.get('engine_pin')})"
    )
    if out is not None:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(compose_text, encoding="utf-8")
        res.diagnostics["out_written"] = str(out_path)
    res.notices.append(
        "run the emitted compose with `docker compose "
        "--project-directory <repo-root> -f <out> up` so the relative "
        "overlay mounts resolve (see COMPOSE_GENERATOR.md)"
    )
    res.notices.append(CAVEAT_S7)
    return res


def _short_refuse(msg: str) -> str:
    """Compact a [D] Refuse message into a stable machine token."""
    low = msg.lower()
    if "loads" in low or "engine pin" in low:
        return "pin-mismatch"
    if "tp=" in low or "kv_format" in low:
        return "tp-or-kv"
    if "trust_remote_code" in low or "security refusal" in low:
        return "trc"
    if "foundational" in low:
        return "foundational-drift"
    if "degraded" in low:
        return "degraded-drift"
    if "out of scope" in low or "genesis" in low:
        return "scope"
    return "other"


# Per-family map: which weight field(s) `kv._weights_per_card_gb` reads on
# the DEFAULT (weights_variant="default") path that `[B]`/raw_verdict uses.
# We substitute the actually-resolved curated-variant blob size onto exactly
# these field(s) so pricing reflects the resolved variant WITHOUT ever
# touching `model_family` (which selects the authoritative family pricing
# branch in kv-calc). Keyed by the curated-exact spec's own model_family.
_FAMILY_WEIGHT_FIELDS = {
    "qwen3-next-hybrid": ("weights_total_gb",),
    "qwen3-next-moe": ("weights_total_gb",),
    "gemma4-swa-dense": ("weights_int4_gb",),
    "gemma4-swa-moe": ("weights_int4_gb", "weights_awq_gb"),
}


def _curated_spec(profiles, der) -> dict:
    """Build the **curated-exact** kv-calc spec for a Tier-1 curated hit so
    P1's raw_verdict prices it through the model's authoritative
    family-specific branch (NOT generic-dense's conservative lower-bound).

    Start from `kv.MODEL_SPECS[der.tier1.model_id]` — the SAME specs
    `tools/kv-calc.py --calibration` validates at 22/22 — preserving its real
    `model_family` and every family-specific field, then substitute ONLY the
    weight size for the actually-resolved variant (`der.tier1.weights_variant`)
    read from the curated ModelProfile (the ModelProfile remains the weight
    authority; we never recompute weight size). NEVER emit
    `model_family="generic-dense"` for a curated model."""
    t1 = der.tier1
    kv = _kv()
    model_specs = getattr(kv, "MODEL_SPECS", None)
    if not isinstance(model_specs, dict) or t1.model_id not in model_specs:
        raise RuntimeError(
            f"_curated_spec: Tier-1 model_id {t1.model_id!r} not in "
            f"kv.MODEL_SPECS (keys={sorted((model_specs or {}).keys())}). "
            f"This is a hard error — refusing to fall back to generic-dense "
            f"for a curated model."
        )
    # Defensive copy of the authoritative curated-exact spec (preserve
    # model_family + all family fields verbatim — do NOT recompute/adjust).
    spec = dict(model_specs[t1.model_id])
    family = spec.get("model_family")
    if family == "generic-dense" or not family:
        raise RuntimeError(
            f"_curated_spec: curated-exact spec for {t1.model_id!r} has "
            f"unexpected model_family={family!r}; refusing to price a "
            f"curated model as generic-dense."
        )

    # Resolved-variant weight blob size from the curated ModelProfile
    # (authoritative; same source the old code read).
    model = profiles.models[t1.model_id]
    vmeta = model.weights.get(t1.weights_variant, {}) or {}
    size_gb = (
        vmeta.get("size_gb")
        or (der.profile or {}).get("weights_variant_size_gb")
    )
    weight_fields = _FAMILY_WEIGHT_FIELDS.get(family)
    if weight_fields is None:
        raise RuntimeError(
            f"_curated_spec: no weight-field mapping for curated family "
            f"{family!r} ({t1.model_id!r}). STOP — do not silently fall "
            f"back to generic-dense."
        )
    # Only substitute when the curated ModelProfile gives a usable numeric
    # blob size for the resolved variant. If it does not (e.g. "variable"
    # GGUF), keep the authoritative spec's own validated weight size rather
    # than zeroing it — never weaken the curated-exact pricing.
    if isinstance(size_gb, (int, float)) and float(size_gb) > 0:
        for f in weight_fields:
            spec[f] = float(size_gb)
    spec["model_id"] = t1.slug
    return spec


# ===========================================================================
# CLI (thin; pull.sh execs this). Renders PullResult to stdout + exit code.
# ===========================================================================
_EXIT_OK = 0
_EXIT_ABORT = 2          # any honest hard-stop (stratum 1-6 / hard-block)
_EXIT_NEEDS_FLAG = 3     # confirm→proceed / advisory not yet satisfied
_EXIT_USAGE = 64


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="pull.sh",
        description="v0.8.0 Pull-Gate — derive an HF repo, gate it through "
        "the locked 6-stratum taxonomy, and (Path A, curated+emittable) "
        "emit a compose via the #141 generator. Honest about confidence; "
        "never silently gate-passes.",
    )
    ap.add_argument("slug", help="HF repo slug (e.g. org/Model-Name)")
    ap.add_argument(
        "--profile-like", required=True, dest="profile_like",
        help="REQUIRED curated COMPOSE_REGISTRY key supplying the runtime "
        "shape (Path A: must name the curated model+variant & be "
        "[D]-emittable; Path B: any vLLM profile, shape only)",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="force Path B (evaluate only; never emit/download)")
    ap.add_argument("--yes", action="store_true",
                    help="accept a confirm→proceed terminal (§4.1)")
    ap.add_argument(
        "--force-download", action="store_true",
        help="advisory low-confidence wont-fit → override-accepted "
        "(NO-OP + notice this phase; download deferred to Loop)",
    )
    ap.add_argument(
        "--experimental-arch", action="store_true",
        help="bypass ONLY [C0] engine-support-unknown/no-arch-row "
        "(never runtime-incompatible; Path B only this phase)",
    )
    ap.add_argument("--trust-remote-code", action="store_true",
                    help="bypass [C0] needs-trust-remote-code-ack")
    ap.add_argument("--hf-home", help="override the HF_HOME resolution chain")
    ap.add_argument("--out", help="Path A: write the emitted compose here")
    ap.add_argument(
        "--hardware", type=float, default=None,
        help="override detected GPU compute capability (e.g. 8.6 for "
        "RTX 3090); default = nvidia-smi detection",
    )
    args = ap.parse_args(argv)

    res = run_pull(
        args.slug, args.profile_like,
        dry_run=args.dry_run, yes=args.yes,
        force_download=args.force_download,
        experimental_arch=args.experimental_arch,
        trust_remote_code=args.trust_remote_code,
        hf_home=args.hf_home, out=args.out,
        hardware_sm=args.hardware,
    )

    tag = "OK" if res.ok else "ABORT"
    print(f"[pull] {tag} path={res.path} stratum={res.stratum.name} "
          f"slug={res.slug} profile-like={res.profile_like}")
    if res.confidence:
        print(f"[pull] confidence={res.confidence} "
              f"raw_verdict={res.raw_verdict} terminal={res.terminal}")
    if res.abort_reason:
        print(f"[pull] reason={res.abort_reason}")
    if res.detail:
        print(f"[pull] {res.detail}")
    for n in res.notices:
        print(f"[pull] note: {n}")
    if res.emitted and not args.out:
        sys.stdout.write(res.compose_text or "")

    if res.ok:
        return _EXIT_OK
    if res.abort_reason and (
        res.abort_reason.startswith("confirm→proceed")
        or res.abort_reason.startswith("override-accepted")
    ):
        return _EXIT_NEEDS_FLAG
    return _EXIT_ABORT


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
