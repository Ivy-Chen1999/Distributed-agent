# Golden-case review: quickstart

A 5-minute read before your first review. The full rules are in the
[golden-review guide](golden-review-guide.md).

## What you are doing, and why

WOMM reads an EU legislative proposal and predicts who is affected and how. We score it against
**golden cases**: reference answers taken from the Commission's own impact assessment (IA). An
LLM drafted each case and three separate LLM judges checked it. You check only the items where
the judges disagreed, a check flagged something, or a random 20% audit sample landed. A wrong
reference answer teaches WOMM the wrong lesson, so your call matters.

Each review PR holds one file, `evals/golden/drafts/<case>.yaml`. The PR description says how
many decisions you need to make and links the IA and the proposal on EUR-Lex. The same links are
in the draft's `review_links` block.

## For each item: read, check, decide

1. Find the item's `ia_section` in the IA. If the IA has several parts, the helper tells you
   which part. Search (Ctrl+F) for the start of the `ia_anchor` quote, then read the paragraph
   around it.
2. Check four things against the IA: the **actor**, the **mechanism** (which provision causes
   which behaviour), the **impact** (cost or benefit, one-off or recurring, who bears it), and
   **numbers** (exactly the IA's, never rounded or invented).
3. Decide:

| Decision | When | Example |
|---|---|---|
| `verified` | All four checks hold | The IA's cost table gives users of high-risk AI a recurring EUR 5 000–8 000 a year for human oversight, and so does the item |
| `edited` | Right in substance, one field wrong: fix it in place | The item says "about EUR 7 000"; the IA says "EUR 6 000–7 000". Restore the range. Note: "range restored" |
| `rejected` | Wrong, unsupported, a duplicate, or outside the scenario's articles (note required) | The item says sandboxes exempt SMEs; the IA says there are no derogations. Note: "contradicts the IA" |
| `unclear` | Two careful readers could disagree (note required) | The IA says costs "may" deter SMEs "at the margin"; the item says SMEs "will leave". Note: "IA is hedged" |

`possibly_missing` candidates always need a decision, even when they show `auto_accepted`.
Accept a candidate (`verified`) only if the IA supports it and no other item already covers the
same actor and mechanism; otherwise reject it with a note such as "covered by c12_e03". Rejected
and unclear items are dropped from the published case.

## The helper

You need Python and [uv](https://docs.astral.sh/uv/). Then:

```bash
gh pr checkout <PR number>          # or: git fetch origin && git switch review/<case>
uv sync
uv run python scripts/review_draft.py                 # what to decide: claim, IA section, quote to search, judges' reasons
uv run python scripts/review_draft.py --interactive   # decide item by item; saves into the draft
uv run python scripts/review_draft.py --check         # what CI will say, in plain words
```

Prefer an editor to prompts? `--template` writes a decisions file to fill in, and
`--apply <file>` writes it into the draft. The helper sets `review.reviewer` (from `--reviewer`,
or `git config github.user`), changes only the lines of the items you decide, and keeps the
tool digests valid.

**No push access to the branch?** Fill in the decisions file and paste it into a PR comment; the
case owner applies it with `--apply`. Or skip the helper and use GitHub suggestions: in
**Files changed**, click `+` on the item's `review:` lines, choose **Add a suggestion**, and set:

```yaml
  review:
    decision: rejected
    audit: false              # leave as it is
    reviewer: your-github-username
    note: contradicts the IA; sandboxes grant no exemptions
```

## When CI complains

| CI says | Fix |
|---|---|
| `... has no reviewer` | Set `review.reviewer` to your GitHub username |
| `... is not a GitHub username` | Use the name in your `github.com/<username>` URL, not your full name |
| `review.decision is 'verifed'` | Use `verified`, `edited`, `rejected` or `unclear` |
| `not valid YAML at or just above line N` | Indentation: `review:` has 2 spaces, its fields 4 |
| `tool-written fields changed but the decision is 'auto_accepted'` | You changed an item you did not need to decide: undo it, or make it `edited` with a note |
| `needs a short note saying why` | Rejected and unclear items need `review.note` |

## Finish

1. Run `uv run python scripts/review_draft.py --check` until it says **Ready**.
2. Commit and push (or post your decisions file as a PR comment).
3. Mark the PR **Ready for review**. CI then requires every decision.
4. Post the minutes you spent as a PR comment. The case owner logs them.
