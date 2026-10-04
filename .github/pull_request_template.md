## Summary

<!-- What changes and why. -->

## Test plan

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check src tests scripts` and `uv run ruff format --check src tests scripts`

## Golden-case review (only for PRs that add drafts under `evals/golden/drafts/`)

Follow [docs/eval/golden-review-guide.md](../docs/eval/golden-review-guide.md). Keep the PR a
**draft** while the review is open. CI requires every item that needs a human to be decided
once the PR is marked ready.

- [ ] I reviewed every `pending` item, every `audit: true` item and every `possibly_missing`
      candidate. If CI reports the proposal as escalated, I reviewed every item.
- [ ] Each decision is `verified`, `edited`, `rejected` or `unclear`, with my GitHub username in
      `review.reviewer`. Rejected and unclear items have a note.
- [ ] I checked each item against its IA anchor in context for the actor, the mechanism and the
      impact. No number was changed or invented.
- [ ] No item rests only on annex content of the proposal. The `provision_keys` are inside the
      scenario.
- [ ] I logged my minutes in `evals/annotation-assignments.md`.
- [ ] No holdout case, and no IA or RSB identifier, appears anywhere in this PR.
