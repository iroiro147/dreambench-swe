# Security and Disclosure Policy

## Supported version

Security and accidental-disclosure fixes target the latest published release.

## Report privately

Do not open a public issue for:

- exposed credentials or tokens;
- hidden oracle answers or reference solutions;
- reviewer-only artifacts published accidentally;
- prompt or result material that violates the documented public boundary;
- a vulnerability that could execute untrusted benchmark content or overwrite
  files outside an intended output directory.

Use GitHub's private vulnerability reporting for this repository. If that
surface is unavailable, contact the repository owner through the email on the
GitHub profile and include `DreamBench-SWE security report` in the subject.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Please allow time for validation and a coordinated fix before
public disclosure.

## Public correctness reports

Ordinary benchmark errors, reproducibility failures, methodology concerns, and
claim discrepancies should use the public issue templates. Those reports are
part of the scientific audit trail and are not security vulnerabilities unless
they expose restricted material.
