# Contributing

Changes to schemas or canonicalization are protocol changes. They require a
versioned migration note, positive and negative fixtures, cross-language hash
verification, and proof that existing v1 documents retain their meaning.

Adapters must preserve `event_time <= knowledge_time <= as_of`, retain source
hashes and rights, and never widen the all-false financial authority boundary.
Run `python -m pytest`, `ruff check .`, and `mypy` before opening a pull request.

By submitting a contribution, you agree that it is licensed under Apache-2.0.
