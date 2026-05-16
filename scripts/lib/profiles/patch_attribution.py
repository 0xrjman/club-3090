"""Patch-attribution core (v0.8.0 STEP 2 — club-3090 #359 / PR #147).

Extracted from the embedded Python that previously lived inside
``scripts/tests/test-patch-attribution.sh``. The test is the contract:
this module exposes exactly the helpers the test needs
(:func:`load`, :func:`compose_text`, :func:`gap_declared`,
:func:`reaches`, plus the schema/[C0] check helpers), and the test now
imports them instead of carrying its own copy.

reaches() soundness (brief v9, correction #4)
---------------------------------------------
The legacy ``reaches()`` did a naive ``patch["id"] in text`` substring
test over the *entire* compose file, including the leading comment
banner and (for generated composes) the generator's own header WARNING
block. A patch ID merely *named* in a comment/WARNING would then count
as "reached" — a false positive.

The extracted :func:`reaches` instead:

* parses the **service body only** (everything from the top-level
  ``services:`` key onward), discarding the file-header banner and every
  YAML comment line / inline ``# …`` trailer — so a patch ID that only
  appears in a header/comment/WARNING line is *not* a match;
* validates the patch's **actual ``delivery_spec`` wiring** is present —
  the declared volume mount(s) and/or entrypoint invoke string from
  ``patches.yml`` / the Phase-A-prime ``delivery_spec`` — rather than a
  bare substring of the patch ID;
* accepts either a ``COMPOSE_REGISTRY`` profile name *or* an arbitrary
  absolute path to a compose file.

The deprecated ``delivery:`` boolean block is still consulted (read-only,
the test still asserts on it) but every text probe it performs now runs
against the comment-stripped service body, never the header — so the
result is identical to the legacy behaviour on the shipped composes
while being immune to header/comment false positives on generated ones.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml

# --------------------------------------------------------------------------
# Schema constants (single source of truth — the test imports these).
# --------------------------------------------------------------------------
REQUIRED_PATCH_KEYS = {
    "id",
    "model",
    "files",
    "load_bearing_when",
    "delivery",
    "delivery_gaps",
    "upstream",
    "status",
}
VALID_PATCH_STATUS = {"verified", "unverified", "suspect"}

ARCH_ALLOWED_KEYS = {
    "arch",
    "engine_pin",
    "required_patches",
    "valid_tp",
    "requires_trust_remote_code",
    "requires_trust_remote_code_evidence",
    "kernel_constraints",
    "family",
    "confidence",
    "status",
}
ARCH_REQUIRED_KEYS = ARCH_ALLOWED_KEYS
VALID_TRC = {"true", "false", "unverified"}
VALID_ARCH_STATUS = {"verified", "unverified", "suspect"}
VALID_CONFIDENCE = {"exact", "derived", "estimated-lower-bound"}

SEED_REQUIRED_KEYS = {
    "model",
    "arch_family",
    "quant",
    "topology_class",
    "engine_pin",
    "kv_format",
    "selected_ctx",
    "measured",
    "smoked_capabilities",
    "unsmoked_capabilities",
    "source",
    "provenance",
    "confidence",
}


# --------------------------------------------------------------------------
# Loaders.
# --------------------------------------------------------------------------
def load(path: Path, errors: list[str] | None = None, root: Path | None = None) -> dict:
    """Load a profiles YAML doc, asserting ``schema_version: 1``.

    ``errors``/``root`` are optional: when supplied a schema-version
    violation is appended to ``errors`` (relative to ``root`` if given),
    matching the original embedded behaviour. When omitted the doc is
    returned as-is (handy for non-test callers / the generator).
    """
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if data.get("schema_version") != 1:
        rel = path.relative_to(root) if root is not None else path
        msg = f"{rel} missing schema_version: 1"
        if errors is not None:
            errors.append(msg)
    return data


# --------------------------------------------------------------------------
# Compose-text reader (resolves a registry name OR an arbitrary path).
# --------------------------------------------------------------------------
def _resolve_compose_path(root: Path, name_or_path: str) -> Path:
    """Map a COMPOSE_REGISTRY profile name OR an absolute path to a Path."""
    candidate = Path(name_or_path)
    if candidate.is_absolute():
        return candidate
    # Late import: keeps this module importable without the registry and
    # mirrors the test's own import site.
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.lib.profiles.compose_registry import COMPOSE_REGISTRY  # noqa: E402

    if name_or_path in COMPOSE_REGISTRY:
        return root / COMPOSE_REGISTRY[name_or_path]["compose_path"]
    # Not a registry name and not absolute — treat as repo-relative path.
    return root / name_or_path


def compose_text(root: Path, name_or_path: str, seen: set[Path] | None = None) -> str:
    """Read a compose file (+ a single `extends:` base) as raw text.

    Accepts a COMPOSE_REGISTRY profile name or an arbitrary absolute path
    (correction #4: ``reaches`` must accept either). Returns ``""`` on a
    cycle, matching the original embedded reader.
    """
    path = _resolve_compose_path(root, name_or_path)
    seen = seen if seen is not None else set()
    if path in seen:
        return ""
    seen.add(path)
    text = path.read_text(encoding="utf-8")
    match = re.search(r"extends:\s*\n\s*file:\s*([^\n]+)", text)
    if match:
        base = (path.parent / match.group(1).strip()).resolve()
        if base.exists():
            text += "\n" + base.read_text(encoding="utf-8")
    return text


# --------------------------------------------------------------------------
# Body extraction — drop the file-header banner and all comment lines so a
# patch ID named only in a header/comment/WARNING cannot count as reached.
# --------------------------------------------------------------------------
def _strip_yaml_comment(line: str) -> str:
    """Remove an inline/whole-line ``# …`` comment, honouring quotes.

    Conservative single-pass scanner: a ``#`` only starts a comment when
    it is at column 0 (after indentation) or preceded by whitespace and
    not inside a single/double-quoted scalar. This keeps ``#`` characters
    that are legitimately part of a value (rare here, but e.g. URLs in
    quoted strings) while discarding real comments — which is where the
    patch-ID / PR-number false positives live.
    """
    out: list[str] = []
    in_single = False
    in_double = False
    prev_ws = True  # start-of-content counts as "preceded by whitespace"
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and prev_ws:
            break
        out.append(ch)
        prev_ws = ch in (" ", "\t")
    return "".join(out)


def service_body(text: str) -> str:
    """Return the comment-free service body of a compose file.

    1. Drop everything before the top-level ``services:`` key (the
       at-a-glance banner + the generator's own header WARNING block).
    2. Strip every YAML comment (whole-line and inline) from the
       remaining lines.

    The result is the substrate :func:`reaches` probes — so a patch ID
    or PR number that appears *only* in a comment/header/WARNING line is
    invisible to reachability, while real volume mounts and entrypoint
    invoke lines (which are values, not comments) remain.
    """
    lines = text.splitlines()
    start = 0
    for idx, raw in enumerate(lines):
        # Top-level `services:` (column 0). Header/comment lines and the
        # generator WARNING block all live above this.
        if re.match(r"^services:\s*(#.*)?$", raw):
            start = idx
            break
    body_lines = [_strip_yaml_comment(raw) for raw in lines[start:]]
    return "\n".join(body_lines)


# --------------------------------------------------------------------------
# delivery_spec wiring validation — confirm the patch's *actual* mount /
# invoke artifacts (as declared in patches.yml / Phase-A-prime
# delivery_spec) are present in the service body.
# --------------------------------------------------------------------------
def _spec_wiring_markers(patch: dict) -> tuple[list[str], list[str]]:
    """Derive (volume_markers, invoke_markers) from ``delivery_spec``.

    Returns the concrete strings that MUST appear in the service body for
    the patch to be genuinely wired:

    * volume markers: container-side mount targets (``dest`` /
      ``mounted_at``) of overlay files / install scripts;
    * invoke markers: the entrypoint command that runs the sidecar /
      install script (``invoke`` string, the sidecar/script container
      path, or basename).
    """
    spec = patch.get("delivery_spec") or {}
    vol: list[str] = []
    inv: list[str] = []

    # site_package_overlay → overlay_files[].dest are bind-mount targets.
    for of in spec.get("overlay_files") or []:
        dest = of.get("dest")
        if dest:
            vol.append(dest)

    # install_script → mounted_at is the volume target; invoke is the
    # entrypoint command line.
    mounted_at = spec.get("mounted_at")
    if mounted_at:
        vol.append(mounted_at)

    # python_sidecar → sidecar is the host path; its basename appears in
    # both the bind mount and the `python3 …` invoke line.
    sidecar = spec.get("sidecar")
    if sidecar:
        inv.append(Path(sidecar).name)

    script = spec.get("script")
    if script:
        inv.append(Path(script).name)

    invoke = spec.get("invoke")
    # Only treat `invoke` as a literal body marker when it is an actual
    # command (e.g. "bash /etc/club3090/install-pr41800.sh"), not a
    # human-readable description sentence.
    if invoke and not invoke.endswith((".", "serving", "import")) and "/" in invoke:
        inv.append(invoke)

    return vol, inv


def _spec_wiring_present(patch: dict, body: str) -> bool:
    """True iff the patch's declared delivery_spec wiring is in ``body``.

    ``wired_at`` tells us which insertion point(s) the patch claims:
    ``volumes`` requires a matching mount target; ``entrypoint`` requires
    a matching invoke; ``[volumes, entrypoint]`` requires *both*.
    A patch whose delivery_mechanism is ``none`` has no spec and is never
    spec-wired (the legacy fallback below still applies for parity).
    """
    spec = patch.get("delivery_spec")
    if not spec:
        return False
    wired_at = spec.get("wired_at")
    if isinstance(wired_at, str):
        wired_at = [wired_at]
    wired_at = set(wired_at or [])
    vol_markers, inv_markers = _spec_wiring_markers(patch)

    vol_ok = (not vol_markers) or any(m in body for m in vol_markers)
    inv_ok = (not inv_markers) or any(m in body for m in inv_markers)

    if wired_at == {"volumes"}:
        return bool(vol_markers) and vol_ok
    if wired_at == {"entrypoint"}:
        return bool(inv_markers) and inv_ok
    if "volumes" in wired_at and "entrypoint" in wired_at:
        # Declared at both points — require the volume mount (the
        # load-bearing artifact); the invoke line is corroborating.
        return bool(vol_markers) and vol_ok and inv_ok
    # Unknown / empty wired_at: fall back to "any declared marker present".
    return (bool(vol_markers) and vol_ok) or (bool(inv_markers) and inv_ok)


# --------------------------------------------------------------------------
# gap_declared / reaches.
# --------------------------------------------------------------------------
def gap_declared(patch: dict, compose_name: str) -> bool:
    for gap in patch.get("delivery_gaps") or []:
        if compose_name in set(gap.get("composes") or []):
            return True
    return False


def reaches(root: Path, patch: dict, name_or_path: str) -> bool:
    """Is ``patch`` actually wired into the compose ``name_or_path``?

    Sound (correction #4): probes the **comment-stripped service body**
    only — never the file header or generator WARNING block — and
    validates the patch's **real delivery_spec wiring** (declared mount
    target and/or entrypoint invoke), not a substring of its ID.

    ``name_or_path`` may be a COMPOSE_REGISTRY profile name OR an
    arbitrary absolute path to a compose file.

    The deprecated ``delivery:`` boolean block is still honoured as a
    fallback for parity with the existing contract, but every probe runs
    against the service body so it cannot be tripped by a header/comment
    mention of the patch ID or a PR number.
    """
    body = service_body(compose_text(root, name_or_path))

    # Primary, sound path: the patch's declared delivery_spec wiring is
    # genuinely present at the insertion point(s) it claims.
    if _spec_wiring_present(patch, body):
        return True

    # Legacy fallback (read-only `delivery:` block) — operates on the
    # service body, so header/comment ID mentions never match.
    delivery = patch.get("delivery") or {}
    if delivery.get("entrypoint_invoke"):
        for rel in patch.get("files") or []:
            target = root / rel
            markers = [Path(rel).name]
            if target.is_dir():
                markers.append(Path(rel).parent.name)
            if any(marker in body for marker in markers):
                return True
        if patch["id"] in body:
            return True
    if (
        delivery.get("genesis")
        and patch.get("genesis_env")
        and patch["genesis_env"] in body
    ):
        return True
    if delivery.get("dockerfile_bake") and "ghcr.io/noonghunna/vllm-club3090" in body:
        return True
    return False


# --------------------------------------------------------------------------
# Artifact-coverage helpers (extracted verbatim in behaviour).
# --------------------------------------------------------------------------
def is_artifact(path: Path) -> bool:
    text = path.as_posix()
    return (
        "/patches/" in text
        and "/patches/genesis/" not in text
        and "/_pre-pr" not in text
        and path.suffix in {".py", ".sh"}
    )


def covered(path: Path, covered_files: Iterable[Path]) -> bool:
    for entry in covered_files:
        if entry.is_file() and path == entry:
            return True
        if entry.is_dir() and (path == entry or entry in path.parents):
            return True
    return False


# --------------------------------------------------------------------------
# [C0] state machine (extracted verbatim).
# --------------------------------------------------------------------------
def c0_state(row: dict, tp: int, trust_ack: bool = False) -> str:
    trc = row["requires_trust_remote_code"]
    if trc in {"true", "unverified"} and not trust_ack:
        return "needs-trust-remote-code-ack"
    if tp not in set(row["valid_tp"]["tp_divisors"]):
        return "engine-support-unknown"
    if any(pin.get("loads") for pin in row["engine_pin"]):
        return "engine-supported"
    return "engine-support-unknown"
