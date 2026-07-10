#!/usr/bin/env python3
"""CSPRNG secret injection for DreamBench-SWE trap skeletons.

Skeleton authors write PLACEHOLDER tokens such as ``{{SECRET_A}}`` into the
sequence record, hidden oracles, reference solutions, and decisive-literal
metadata.  This script generates the concrete values with ``secrets`` and writes
the finalized trap bundle plus a provenance sidecar that records classes, not
values.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import string
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
FORMAT_ATOM_RE = re.compile(r"\{\{([1-9][0-9]*)([ADLUHX])\}\}")
ANY_BRACED_TOKEN_RE = re.compile(r"\{\{[^{}\s]+\}\}")
MANIFEST_FILENAMES = {"manifest.json", "skeleton_manifest.json", "trap_skeleton_manifest.json"}
SEQUENCE_FILENAMES = {"sequence.json", "sequence.jsonl", "sequences.jsonl"}
PROVENANCE_SCHEMA_VERSION = "dreamforge-secret-provenance-v1"

ALPHABETS = {
    "A": string.ascii_letters + string.digits,
    "D": string.digits,
    "L": string.ascii_lowercase,
    "U": string.ascii_uppercase,
    "H": string.hexdigits.lower()[:16],
    "X": string.hexdigits.upper()[:16],
}

CLASS_PREFIXES = (
    "marker-row ",
    "id-format ",
    "config key ",
    "config-key ",
    "numeric threshold ",
    "numeric-threshold ",
)


class SecretInjectionError(ValueError):
    """Base error for invalid skeletons or failed injection."""


class SecretLeakageError(SecretInjectionError):
    """Raised when a generated secret appears outside its trap assets."""


@dataclass(frozen=True)
class PlaceholderSpec:
    name: str
    format_class: str
    min_value: int | None = None
    max_value: int | None = None


@dataclass(frozen=True)
class InjectionResult:
    output_path: Path
    provenance_path: Path
    seq_ids: tuple[str, ...]
    placeholder_values: Mapping[str, str]


def inject_path(
    skeleton_path: Path | str,
    output_path: Path | str,
    *,
    manifest_path: Path | str | None = None,
    leakage_root: Path | str | None = None,
    provenance_name: str = "secret_provenance.json",
) -> InjectionResult:
    """Inject secrets into a skeleton directory or JSON/JSONL skeleton file."""

    src = Path(skeleton_path)
    dst = Path(output_path)
    if src.is_dir():
        return inject_directory(
            src,
            dst,
            manifest_path=manifest_path,
            leakage_root=leakage_root,
            provenance_name=provenance_name,
        )
    return inject_file(
        src,
        dst,
        manifest_path=manifest_path,
        leakage_root=leakage_root,
        provenance_name=provenance_name,
    )


def inject_directory(
    skeleton_dir: Path | str,
    output_dir: Path | str,
    *,
    manifest_path: Path | str | None = None,
    leakage_root: Path | str | None = None,
    provenance_name: str = "secret_provenance.json",
) -> InjectionResult:
    src = Path(skeleton_dir)
    dst = Path(output_dir)
    if not src.is_dir():
        raise SecretInjectionError(f"skeleton directory does not exist: {src}")

    manifest = load_manifest(_resolve_manifest(src, manifest_path))
    source_files = _source_files_for_directory(src)
    file_texts = _read_text_files(source_files)
    specs = _placeholder_specs(manifest)
    values = _generate_values(specs)
    _validate_placeholder_coverage(file_texts.values(), specs)

    occurrence_map = _placeholder_occurrences(file_texts, specs.keys())
    placeholder_seq_ids = _placeholder_seq_ids(file_texts, occurrence_map)
    _reject_cross_trap_placeholder_reuse(placeholder_seq_ids)

    if dst.exists() and any(dst.iterdir()):
        raise SecretInjectionError(f"output directory must be empty or absent: {dst}")
    dst.mkdir(parents=True, exist_ok=True)
    output_files: dict[Path, str] = {}
    for src_file, text in file_texts.items():
        rel = src_file.relative_to(src)
        out_file = dst / rel
        rendered = _substitute_text(text, values)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(rendered, encoding="utf-8")
        output_files[out_file] = rendered

    seq_ids = _sequence_ids_from_output(output_files)
    provenance_path = dst / provenance_name
    provenance_path.write_text(
        json.dumps(_provenance_payload(manifest, specs, seq_ids), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _assert_no_placeholders(output_files)
    _assert_no_secret_leakage(
        root=Path(leakage_root) if leakage_root else dst,
        values=values,
        placeholder_seq_ids=placeholder_seq_ids,
        sequence_files={path for path in output_files if path.name in SEQUENCE_FILENAMES},
    )
    return InjectionResult(
        output_path=dst,
        provenance_path=provenance_path,
        seq_ids=tuple(seq_ids),
        placeholder_values=dict(values),
    )


def inject_file(
    skeleton_file: Path | str,
    output_file: Path | str,
    *,
    manifest_path: Path | str | None = None,
    leakage_root: Path | str | None = None,
    provenance_name: str = "secret_provenance.json",
) -> InjectionResult:
    src = Path(skeleton_file)
    dst = Path(output_file)
    if not src.is_file():
        raise SecretInjectionError(f"skeleton file does not exist: {src}")

    manifest = load_manifest(_resolve_manifest(src.parent, manifest_path))
    specs = _placeholder_specs(manifest)
    text = src.read_text(encoding="utf-8")
    file_texts = {src: text}
    values = _generate_values(specs)
    _validate_placeholder_coverage(file_texts.values(), specs)

    occurrence_map = _placeholder_occurrences(file_texts, specs.keys())
    placeholder_seq_ids = _placeholder_seq_ids(file_texts, occurrence_map)
    _reject_cross_trap_placeholder_reuse(placeholder_seq_ids)

    dst.parent.mkdir(parents=True, exist_ok=True)
    rendered = _substitute_text(text, values)
    dst.write_text(rendered, encoding="utf-8")
    output_files = {dst: rendered}
    seq_ids = _sequence_ids_from_output(output_files)

    provenance_path = dst.parent / provenance_name
    provenance_path.write_text(
        json.dumps(_provenance_payload(manifest, specs, seq_ids), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _assert_no_placeholders(output_files)
    _assert_no_secret_leakage(
        root=Path(leakage_root) if leakage_root else dst.parent,
        values=values,
        placeholder_seq_ids=placeholder_seq_ids,
        sequence_files={dst},
    )
    return InjectionResult(
        output_path=dst,
        provenance_path=provenance_path,
        seq_ids=tuple(seq_ids),
        placeholder_values=dict(values),
    )


def load_manifest(path: Path | str) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SecretInjectionError(f"invalid manifest JSON {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SecretInjectionError(f"manifest must be a JSON object: {manifest_path}")
    if not str(payload.get("skeleton_id") or "").strip():
        raise SecretInjectionError("manifest missing skeleton_id")
    placeholders = payload.get("placeholders")
    if not isinstance(placeholders, dict) or not placeholders:
        raise SecretInjectionError("manifest must declare non-empty placeholders object")
    return payload


def generate_secret(spec: PlaceholderSpec, used_values: set[str] | None = None) -> str:
    """Generate one value from a placeholder format class using ``secrets``."""

    used = used_values if used_values is not None else set()
    max_attempts = 1000
    for _ in range(max_attempts):
        candidate = _generate_candidate(spec)
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise SecretInjectionError(
        f"could not generate a unique value for {spec.name} after {max_attempts} attempts; "
        "format class space is probably too small"
    )


def _generate_values(specs: Mapping[str, PlaceholderSpec]) -> dict[str, str]:
    used: set[str] = set()
    values: dict[str, str] = {}
    for name in sorted(specs):
        values[name] = generate_secret(specs[name], used)
    return values


def _generate_candidate(spec: PlaceholderSpec) -> str:
    normalized = spec.format_class.strip()
    lower = normalized.lower()
    if spec.min_value is not None or spec.max_value is not None:
        return str(_randint(spec.min_value, spec.max_value))
    if lower in {"numeric threshold", "numeric-threshold", "threshold"}:
        return str(_randint(10, 99))
    if lower in {"config key", "config-key"}:
        return "cfg_" + _random_chars(8, ALPHABETS["L"])

    template = _template_from_format_class(normalized)
    if not FORMAT_ATOM_RE.search(template):
        raise SecretInjectionError(
            f"{spec.name} format class must include CSPRNG atoms like {{{{5A}}}} "
            "or use an explicit generated class"
        )
    return FORMAT_ATOM_RE.sub(lambda match: _render_atom(match), template)


def _template_from_format_class(format_class: str) -> str:
    lower = format_class.lower()
    for prefix in CLASS_PREFIXES:
        if lower.startswith(prefix):
            return format_class[len(prefix) :].strip()
    return format_class


def _render_atom(match: re.Match[str]) -> str:
    count = int(match.group(1))
    alphabet_key = match.group(2)
    return _random_chars(count, ALPHABETS[alphabet_key])


def _random_chars(count: int, alphabet: str) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(count))


def _randint(min_value: int | None, max_value: int | None) -> int:
    low = 0 if min_value is None else int(min_value)
    high = 999 if max_value is None else int(max_value)
    if high < low:
        raise SecretInjectionError(f"invalid numeric range: min {low} > max {high}")
    return low + secrets.randbelow(high - low + 1)


def _placeholder_specs(manifest: Mapping[str, Any]) -> dict[str, PlaceholderSpec]:
    raw_placeholders = manifest["placeholders"]
    specs: dict[str, PlaceholderSpec] = {}
    for name, raw_spec in raw_placeholders.items():
        placeholder = str(name).strip()
        if not PLACEHOLDER_RE.fullmatch("{{" + placeholder + "}}"):
            raise SecretInjectionError(f"invalid placeholder name {name!r}; use names like SECRET_A")
        specs[placeholder] = _parse_placeholder_spec(placeholder, raw_spec)
    return specs


def _parse_placeholder_spec(name: str, raw_spec: Any) -> PlaceholderSpec:
    if isinstance(raw_spec, str):
        return PlaceholderSpec(name=name, format_class=raw_spec)
    if not isinstance(raw_spec, dict):
        raise SecretInjectionError(f"{name} placeholder spec must be a string or object")

    format_class = (
        raw_spec.get("format_class")
        or raw_spec.get("class")
        or raw_spec.get("format")
        or raw_spec.get("template")
    )
    if not format_class:
        raise SecretInjectionError(f"{name} placeholder spec missing format_class")
    min_value = raw_spec.get("min")
    max_value = raw_spec.get("max")
    return PlaceholderSpec(
        name=name,
        format_class=str(format_class),
        min_value=None if min_value is None else int(min_value),
        max_value=None if max_value is None else int(max_value),
    )


def _resolve_manifest(base_dir: Path, explicit: Path | str | None) -> Path:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise SecretInjectionError(f"manifest does not exist: {path}")
        return path
    candidates = [base_dir / name for name in MANIFEST_FILENAMES if (base_dir / name).is_file()]
    if len(candidates) != 1:
        raise SecretInjectionError(
            f"expected exactly one manifest in {base_dir} named one of "
            f"{sorted(MANIFEST_FILENAMES)}, found {len(candidates)}"
        )
    return candidates[0]


def _source_files_for_directory(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in MANIFEST_FILENAMES or path.name == "secret_provenance.json":
            continue
        files.append(path)
    if not files:
        raise SecretInjectionError(f"skeleton directory contains no renderable files: {root}")
    return files


def _read_text_files(paths: Iterable[Path]) -> dict[Path, str]:
    texts: dict[Path, str] = {}
    for path in paths:
        try:
            texts[path] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SecretInjectionError(f"non-UTF-8 skeleton file is not supported: {path}") from exc
    return texts


def _validate_placeholder_coverage(texts: Iterable[str], specs: Mapping[str, PlaceholderSpec]) -> None:
    discovered: set[str] = set()
    for text in texts:
        discovered.update(PLACEHOLDER_RE.findall(text))
    declared = set(specs)
    missing_specs = discovered - declared
    unused_specs = declared - discovered
    if missing_specs:
        raise SecretInjectionError(f"placeholders missing manifest specs: {sorted(missing_specs)}")
    if unused_specs:
        raise SecretInjectionError(f"manifest declares unused placeholders: {sorted(unused_specs)}")


def _placeholder_occurrences(
    file_texts: Mapping[Path, str],
    placeholder_names: Iterable[str],
) -> dict[str, set[Path]]:
    occurrences = {name: set() for name in placeholder_names}
    for path, text in file_texts.items():
        for name in PLACEHOLDER_RE.findall(text):
            if name in occurrences:
                occurrences[name].add(path)
    return occurrences


def _placeholder_seq_ids(
    file_texts: Mapping[Path, str],
    occurrence_map: Mapping[str, set[Path]],
) -> dict[str, set[str]]:
    placeholder_seq_ids = {name: set() for name in occurrence_map}
    for name, paths in occurrence_map.items():
        token = "{{" + name + "}}"
        for path in paths:
            seq_ids = _seq_ids_for_placeholder(path, file_texts[path], token)
            placeholder_seq_ids[name].update(seq_ids)
    return placeholder_seq_ids


def _seq_ids_for_placeholder(path: Path, text: str, token: str) -> set[str]:
    rel_parts = path.parts
    seq_from_asset = _seq_id_from_asset_parts(rel_parts)
    if seq_from_asset:
        return {seq_from_asset}
    if path.name in SEQUENCE_FILENAMES:
        seq_ids: set[str] = set()
        for record in _json_records_from_text(text, path):
            if token in json.dumps(record, sort_keys=True):
                seq_id = str(record.get("seq_id") or record.get("sequence_id") or "").strip()
                if seq_id:
                    seq_ids.add(seq_id)
        return seq_ids
    return set()


def _reject_cross_trap_placeholder_reuse(placeholder_seq_ids: Mapping[str, set[str]]) -> None:
    reused = {
        name: sorted(seq_ids)
        for name, seq_ids in placeholder_seq_ids.items()
        if len(seq_ids) > 1
    }
    if reused:
        raise SecretInjectionError(
            "one placeholder may not bind multiple traps; use distinct placeholders per trap: "
            + json.dumps(reused, sort_keys=True)
        )


def _substitute_text(text: str, values: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            return values[name]
        except KeyError as exc:
            raise SecretInjectionError(f"unknown placeholder {name}") from exc

    return PLACEHOLDER_RE.sub(replace, text)


def _sequence_ids_from_output(output_files: Mapping[Path, str]) -> list[str]:
    seq_ids: set[str] = set()
    for path, text in output_files.items():
        if path.name not in SEQUENCE_FILENAMES:
            continue
        for record in _json_records_from_text(text, path):
            seq_id = str(record.get("seq_id") or record.get("sequence_id") or "").strip()
            if seq_id:
                seq_ids.add(seq_id)
    return sorted(seq_ids)


def _json_records_from_text(text: str, path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix == ".jsonl":
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            payload = json.loads(text)
            if isinstance(payload, list):
                records = payload
            elif isinstance(payload, dict) and isinstance(payload.get("sequences"), list):
                records = list(payload["sequences"])
            elif isinstance(payload, dict):
                records = [payload]
            else:
                raise SecretInjectionError(f"{path} must contain JSON object/list records")
    except json.JSONDecodeError as exc:
        raise SecretInjectionError(f"invalid sequence JSON in {path}: {exc}") from exc
    if not all(isinstance(record, dict) for record in records):
        raise SecretInjectionError(f"{path} sequence records must be JSON objects")
    return records


def _provenance_payload(
    manifest: Mapping[str, Any],
    specs: Mapping[str, PlaceholderSpec],
    seq_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "skeleton_id": str(manifest["skeleton_id"]),
        "seq_ids": list(seq_ids),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generator": {
            "path": "scripts/inject_secrets.py",
            "sha256": _script_hash(),
            "deterministic": False,
            "rng": "python-secrets",
        },
        "placeholders": {
            name: {
                "format_class": spec.format_class,
            }
            for name, spec in sorted(specs.items())
        },
    }


def _script_hash() -> str:
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _assert_no_placeholders(output_files: Mapping[Path, str]) -> None:
    leftovers: dict[str, list[str]] = {}
    for path, text in output_files.items():
        tokens = sorted(set(ANY_BRACED_TOKEN_RE.findall(text)))
        if tokens:
            leftovers[str(path)] = tokens
    if leftovers:
        raise SecretInjectionError(f"unsubstituted placeholders remain: {json.dumps(leftovers, sort_keys=True)}")


def _assert_no_secret_leakage(
    *,
    root: Path,
    values: Mapping[str, str],
    placeholder_seq_ids: Mapping[str, set[str]],
    sequence_files: set[Path],
) -> None:
    if not root.exists():
        return
    secret_to_placeholder = {value: name for name, value in values.items()}
    leaks: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for secret_value, placeholder in secret_to_placeholder.items():
            if secret_value not in text:
                continue
            allowed_seq_ids = placeholder_seq_ids.get(placeholder, set())
            if _secret_occurrence_is_allowed(path, text, secret_value, allowed_seq_ids, sequence_files):
                continue
            leaks.append(f"{placeholder} leaked to {path}")
    if leaks:
        raise SecretLeakageError("generated secret leakage detected: " + "; ".join(leaks))


def _secret_occurrence_is_allowed(
    path: Path,
    text: str,
    secret_value: str,
    allowed_seq_ids: set[str],
    sequence_files: set[Path],
) -> bool:
    if path in sequence_files:
        for record in _json_records_from_text(text, path):
            record_text = json.dumps(record, sort_keys=True)
            if secret_value not in record_text:
                continue
            seq_id = str(record.get("seq_id") or record.get("sequence_id") or "").strip()
            if seq_id not in allowed_seq_ids:
                return False
        return True

    seq_id = _seq_id_from_asset_parts(path.parts)
    return bool(seq_id and seq_id in allowed_seq_ids)


def _seq_id_from_asset_parts(parts: Sequence[str]) -> str | None:
    for marker in ("oracles", "refsol"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inject CSPRNG-generated secrets into DreamBench-SWE trap skeletons. "
            "No deterministic seed option is provided by design."
        )
    )
    parser.add_argument("skeleton", help="Skeleton directory or JSON/JSONL skeleton file.")
    parser.add_argument("output", help="Finalized output directory or JSON/JSONL file.")
    parser.add_argument("--manifest", help="Manifest path; defaults to manifest.json in the skeleton directory.")
    parser.add_argument("--leakage-root", help="Root to scan for generated secret leakage after injection.")
    parser.add_argument(
        "--provenance-name",
        default="secret_provenance.json",
        help="Sidecar filename written beside the finalized trap bundle.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = inject_path(
            args.skeleton,
            args.output,
            manifest_path=args.manifest,
            leakage_root=args.leakage_root,
            provenance_name=args.provenance_name,
        )
    except SecretInjectionError as exc:
        print(f"inject_secrets: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {result.output_path}")
    print(f"wrote {result.provenance_path}")
    if result.seq_ids:
        print("seq_ids: " + ",".join(result.seq_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
