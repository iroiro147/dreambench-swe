## Problem and evidence

<!-- Link the issue and explain the reproducible problem or research need. -->

## Change

<!-- Describe the smallest complete change. -->

## Verification

```text
python3 scripts/audit_public_tree.py --root .
python3 scripts/run_smoke.py
python3 -m pytest -q $(tr '\n' ' ' < public-tests.txt)
```

## Benchmark integrity

- [ ] No credentials, hidden answers, reference solutions, private prompts, or reviewer-only artifacts are included.
- [ ] Tests cover behavioral changes.
- [ ] Any effect on comparability, scoring, contamination, or claims is documented.
- [ ] Public documentation and citation metadata are updated where relevant.
