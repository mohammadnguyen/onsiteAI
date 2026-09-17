# Task brief template

Copy to a TOML file, fill in every field, and get the founder's approval on
that text **before** starting a run. The run archives the file verbatim, so
what a reviewer reads later is what was actually approved.

Every field is required. A brief that omits the prohibitions or the
verification commands is not an approval, and `start` refuses it.

```toml
# One work package. The name appears in the run directory and the review.
name = "short name of the work package"

# What was approved, in enough detail that a reviewer can judge the diff
# against it without asking anyone.
requirements = [
  "the behaviour that must exist when this is done",
  "any constraint that shapes it (a ruling, an existing contract)",
]

# How anyone decides it is done. Observable, not aspirational.
acceptance_criteria = [
  "the condition someone can check",
  "the evidence that will be produced for it",
]

# Paths the run may change. Anything outside this list is out of scope, and
# needing it is a reason to stop and ask, not to widen it quietly.
allowed_paths = [
  "backend/app/services/example.py",
  "backend/tests/test_example.py",
]

# What must not happen, stated plainly. These are read by the reviewer too.
prohibitions = [
  "no schema migration",
  "no API status-code change",
  "no merging, no deploying",
]

# The repository's own gates for this package, as argument lists (never a
# shell string). They run IN THIS ORDER and stop at the first failure;
# suites sharing a database must not be parallelised.
verification_commands = [
  ["python", "-m", "ruff", "check", "backend/app", "backend/tests"],
  ["python", "-m", "pytest", "-q"],
]

[limits]
# Automatic fix+re-review rounds AFTER the first review. The command line
# may lower these but never raise them above what this file approved.
max_review_rounds = 3
max_total_seconds = 14400
```
