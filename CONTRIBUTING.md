# Contributing to DreamBench-SWE

DreamBench-SWE welcomes reproducibility reports, benchmark-design discussion,
new trap proposals, analyzer fixes, and documentation improvements.

## Start with an issue

Open an issue before investing in a substantial change. Describe the failure,
research question, or validity concern and include enough evidence for another
person to reproduce it. Security or accidental-disclosure reports must follow
`SECURITY.md` instead of a public issue.

## Local verification

Create a virtual environment, install the public verification dependency, and
run the public gates:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-artifact.txt
python3 scripts/audit_public_tree.py --root .
DIST_DIR=$(mktemp -d)
export SOURCE_DATE_EPOCH=$(python3 -c 'import datetime,json; value=json.load(open("MANIFEST.json"))["generated_at_utc"]; print(int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))')
PYTHONPATH=src python3 scripts/package_artifact.py --dist-dir "$DIST_DIR"
PYTHONPATH=src python3 scripts/validate_submission_package.py --dist-dir "$DIST_DIR" --mode public
python3 scripts/audit_public_tree.py --archive "$DIST_DIR/dreambench-swe-artifact.tar.gz"
python3 scripts/run_smoke.py
python3 -m pytest -q $(tr '\n' ' ' < public-tests.txt)
```

Some publisher-only checks and tests require private experiment inputs,
reviewer-only fixtures, or manuscript sources intentionally absent from this
public repository. A contribution is not expected to recreate those inputs;
`public-tests.txt` is the maintained public selection.

## Pull requests

- Keep each change focused and link the motivating issue.
- Add or update tests for behavioral changes.
- Do not add hidden oracle answers, reference solutions, credentials, raw
  hosted-model logs, private prompts, or reviewer-only packages.
- Preserve the benchmark's evidence boundary: canonical claims come from the
  folded analyzer outputs, not ad hoc live runs or screenshots.
- Explain any effect on comparability, contamination risk, and existing result
  interpretation.
- Use a clear commit and PR title; do not bundle unrelated formatting churn.

Maintainer review considers correctness, reproducibility, claim hygiene,
backward comparability, and whether the public/private artifact boundary stays
intact.

## New trap proposals

Use `docs/trap_skeleton_spec.md` and the trap-proposal issue template. A trap
must specify the target failure mode, admission evidence, scoring behavior,
contamination considerations, and the expected public fixture boundary.

## Attribution

Accepted contributors are credited through Git history, release notes, and the
release artifact's contributor metadata where applicable.
