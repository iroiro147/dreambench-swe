## Problem and evidence

<!-- Link the issue and explain the reproducible problem or research need. -->

## Change

<!-- Describe the smallest complete change. -->

## Verification

```text
python3 scripts/audit_public_tree.py --root .
DIST_DIR=$(mktemp -d)
export SOURCE_DATE_EPOCH=$(python3 -c 'import datetime,json; value=json.load(open("MANIFEST.json"))["generated_at_utc"]; print(int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))')
PYTHONPATH=src python3 scripts/package_artifact.py --dist-dir "$DIST_DIR"
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir "$DIST_DIR" --mode public
python3 scripts/audit_public_tree.py --archive "$DIST_DIR/dreambench-swe-artifact.tar.gz"
python3 scripts/run_smoke.py
python3 -m pytest -q $(tr '\n' ' ' < public-tests.txt)
```

## Benchmark integrity

- [ ] No credentials, hidden answers, reference solutions, private prompts, or reviewer-only artifacts are included.
- [ ] Tests cover behavioral changes.
- [ ] Any effect on comparability, scoring, contamination, or claims is documented.
- [ ] Public documentation and citation metadata are updated where relevant.
