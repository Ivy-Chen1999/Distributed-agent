## Summary

<!-- What changes and why. -->

## Test plan

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check src tests scripts` and `uv run ruff format --check src tests scripts`

## Golden-case review (only for PRs that add drafts under `evals/golden/drafts/`)

New reviewer? Start with [docs/eval/reviewer-quickstart.md](../docs/eval/reviewer-quickstart.md);
the full rules are in [docs/eval/golden-review-guide.md](../docs/eval/golden-review-guide.md).
`uv run python scripts/review_draft.py` lists what to decide and `--check` runs the CI check.
Drafts come from
a `review/<case>` branch that contains only the draft; a code PR must never contain one. Keep the
PR a **draft** while the review is open. CI requires every item that needs a human to be decided
once the PR is marked ready.

- [ ] I reviewed every `pending` item, every `audit: true` item and every `possibly_missing`
      candidate. If CI reports the proposal as escalated, I reviewed every item.
- [ ] Each decision is `verified`, `edited`, `rejected` or `unclear`, with my GitHub username in
      `review.reviewer`. Rejected and unclear items have a note.
- [ ] I checked each item against its IA anchor in context for the actor, the mechanism and the
      impact. No number was changed or invented.
- [ ] No item rests only on annex content of the proposal. The `provision_keys` are inside the
      scenario.
- [ ] `uv run python scripts/review_draft.py --check` says Ready.
- [ ] I posted my minutes as a PR comment (the case owner logs them).
- [ ] No holdout case or holdout identifier appears anywhere in this PR. The only IA identifier
      is the public EUR-Lex link in `review_links` of this train/val draft.
