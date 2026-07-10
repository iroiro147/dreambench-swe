# Trap Skeleton Secret-Injection Spec

This is the authoring contract for PREREGISTRATION-V2 rule D3 / Section 2.6:
new traps are authored as skeletons with placeholders, then finalized by
`scripts/inject_secrets.py` using Python's `secrets` module. Authors do not
choose non-inferable literals by hand.

## Skeleton Bundle

A trap skeleton is a UTF-8 directory or a JSON/JSONL sequence file.

Directory layout:

```text
<skeleton>/
  manifest.json
  sequence.json            # or sequence.jsonl / sequences.jsonl
  oracles/<seq_id>/s1_test.py
  oracles/<seq_id>/s2_test.py
  oracles/<seq_id>/s3_test.py
  refsol/<seq_id>/s1.py-diff
  refsol/<seq_id>/s2.py-diff
  refsol/<seq_id>/s3.py-diff
```

The sequence record, oracle files, and reference-solution diffs may contain
placeholder tokens:

```text
{{SECRET_A}}
{{SECRET_B}}
{{SECRET_EXPORT_MARKER}}
```

The same placeholder name binds to the same generated value everywhere it
appears in that trap. A placeholder must not be reused across two different
`seq_id` values; use separate names so cross-trap uniqueness can be checked.

## Manifest

Every skeleton has exactly one manifest named `manifest.json`,
`skeleton_manifest.json`, or `trap_skeleton_manifest.json`.

Minimal manifest:

```json
{
  "skeleton_id": "skel-config-export-v1",
  "placeholders": {
    "SECRET_A": {
      "format_class": "marker-row EXPORT-{{5A}}-{{3A}}"
    },
    "SECRET_B": {
      "format_class": "id-format RID-{{5A}}-{{4D}}"
    },
    "SECRET_KEY": {
      "format_class": "config key cfg_{{6L}}"
    },
    "SECRET_THRESHOLD": {
      "format_class": "numeric threshold THR-{{3D}}"
    }
  }
}
```

The `placeholders` keys are authoring placeholders without outer braces. The
values declare the format class; they are not concrete secrets.

## Format Classes

Format classes may be written as strings or as objects with `format_class`.
Classes can include literal text plus CSPRNG atoms:

| Atom | Generated characters |
|---|---|
| `{{5A}}` | 5 alphanumeric characters, `A-Z`, `a-z`, or `0-9` |
| `{{3D}}` | 3 decimal digits, including leading zeros |
| `{{4L}}` | 4 lowercase ASCII letters |
| `{{4U}}` | 4 uppercase ASCII letters |
| `{{8H}}` | 8 lowercase hex characters |
| `{{8X}}` | 8 uppercase hex characters |

Known class prefixes are stripped before rendering:

```text
marker-row EXPORT-{{5A}}-{{3A}}  -> EXPORT-x7Qa2-19K
id-format RID-{{5A}}-{{4D}}      -> RID-Q4mZ8-0291
config key cfg_{{6L}}            -> cfg_kdzwqa
numeric threshold THR-{{3D}}     -> THR-407
```

The generated examples above are illustrative only. Actual values come from a
cryptographic random source at injection time.

For bare generated classes:

```json
{
  "SECRET_CONFIG_KEY": {"format_class": "config key"},
  "SECRET_THRESHOLD": {"format_class": "numeric threshold", "min": 1000, "max": 9999}
}
```

`config key` generates `cfg_` plus eight lowercase letters. Bare numeric
thresholds generate a random bounded integer with `secrets.randbelow`; prefer
an affixed template such as `THR-{{4D}}` when the threshold must be audited by
the leakage checker without accidental short-number collisions.

## Injection

Run:

```bash
python3 scripts/inject_secrets.py <skeleton-dir-or-jsonl> <output-dir-or-jsonl>
```

Optional flags:

```text
--manifest <path>       Use an explicit manifest for file input or nonstandard layout.
--leakage-root <path>   Scan a broader finalized tree for accidental leaks.
--provenance-name <n>   Override the sidecar filename; default secret_provenance.json.
```

There is deliberately no `--seed`, `--deterministic`, or replay option.
Regenerating the same skeleton produces fresh secrets.

The injector writes:

- the finalized sequence record(s);
- finalized hidden oracles;
- finalized reference-solution diffs;
- `secret_provenance.json`.

## Provenance Sidecar

The sidecar records audit metadata, not secrets:

```json
{
  "schema_version": "dreamforge-secret-provenance-v1",
  "skeleton_id": "skel-config-export-v1",
  "seq_ids": ["config-export-row"],
  "generated_at": "2026-07-05T00:00:00Z",
  "generator": {
    "path": "scripts/inject_secrets.py",
    "sha256": "...",
    "deterministic": false,
    "rng": "python-secrets"
  },
  "placeholders": {
    "SECRET_A": {
      "format_class": "marker-row EXPORT-{{5A}}-{{3A}}"
    }
  }
}
```

The sidecar must never contain the RNG seed, because no seed exists. It must
also not contain generated secret values.

## Post-Injection Gates

The injector fails the run if:

- any declared placeholder is unused;
- any skeleton placeholder lacks a manifest format class;
- any `{{...}}` token remains in finalized trap files;
- one generated value collides with another generated value in the same run;
- one placeholder is bound to multiple `seq_id` values;
- a generated secret appears outside that trap's own sequence entry, hidden
  oracle files, or reference-solution files.

The leakage scan treats `secret_provenance.json`, notes, reports, aggregate
files, and unrelated trap assets as disallowed locations for generated secret
values.
