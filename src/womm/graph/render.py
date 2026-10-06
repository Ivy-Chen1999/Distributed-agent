"""Render graph inputs as prompt text. Kept in one place so prompt shape is easy to review."""

from __future__ import annotations

import json
from collections import Counter

from womm.data.corpus import PRE_OMNIBUS_NOTE, PRE_OMNIBUS_VERSIONS, Corpus, index_line
from womm.diff import RegulatoryDiff
from womm.models.findings import ImpactFinding
from womm.models.regulation import Source
from womm.models.run import RetrievalRecord


def changes_with_text(diff: RegulatoryDiff) -> str:
    """For the planner: each change with its provision text."""
    blocks = []
    for c in diff.changes:
        head = f"[{c.kind}] provision_key={c.provision_key}"
        if c.kind == "modified":
            blocks.append(
                f"{head}\n--- before (Art {c.before.article}) ---\n{c.before.text}\n"
                f"--- after (Art {c.after.article}) ---\n{c.after.text}"
            )
        else:
            p = c.after or c.before
            blocks.append(f"{head} (Art {p.article})\n{p.text}")
    return "\n\n".join(blocks)


def corpus_index_lines(diff: RegulatoryDiff, corpus: Corpus) -> dict[str, str]:
    """For the explore Planner: one corpus index line per changed provision, in diff order,
    never any text. The delta column is the run's own change kind, not the index's stored
    delta, so a no-prior-version run does not learn how a text was later amended."""
    lines: dict[str, str] = {}
    for c in diff.changes:
        version = diff.after_version if c.after is not None else diff.before_version
        row = corpus.row(version, c.provision_key) if version else None
        if row is None:
            p = c.after or c.before
            lines[c.provision_key] = f"{c.provision_key} | Art {p.article} | {c.kind}"
            continue
        lines[c.provision_key] = index_line({**row.model_dump(), "delta": c.kind})
    return lines


def corpus_index_header(diff: RegulatoryDiff, corpus: Corpus) -> str:
    """What the explore index covers: the law analysed, the comparison and the change counts."""
    after = corpus.version_info(diff.after_version)
    lines = [f"Law analysed: {after.label} (CELEX {after.celex}, {after.date})."]
    if diff.after_version in PRE_OMNIBUS_VERSIONS:
        lines.append(PRE_OMNIBUS_NOTE)
    if diff.before_version is None:
        lines.append("Compared with: no prior version; every provision is new.")
    else:
        before = corpus.version_info(diff.before_version)
        lines.append(f"Compared with: {before.label} (CELEX {before.celex}, {before.date}).")
    counts = Counter(c.kind for c in diff.changes)
    summary = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
    lines.append(f"{len(diff.changes)} changed provisions ({summary}).")
    lines.append(
        "One line per article or annex: provision_key | number and heading | change kind | "
        "obligation records by primary actor. Provision texts are not shown."
    )
    return "\n".join(lines)


def explore_planner_input(header: str, index_lines: dict[str, str], max_provisions: int) -> str:
    """The explore Planner's user content: the index header and rows, and the key cap."""
    return (
        "Regulatory changes (corpus index, no provision texts):\n\n"
        + header
        + "\n\n"
        + "\n".join(index_lines.values())
        + f"\n\nSelect at most {max_provisions} provision keys in total across all focus areas."
    )


def changes_index(diff: RegulatoryDiff) -> str:
    """For experts: changes as references into the source list (texts appear once, as sources)."""
    lines = []
    for c in diff.changes:
        refs = []
        if c.before:
            refs.append(f"before: Art {c.before.article}, source_id={c.before.source_id}")
        if c.after:
            refs.append(f"after: Art {c.after.article}, source_id={c.after.source_id}")
        lines.append(f"- [{c.kind}] provision_key={c.provision_key} ({'; '.join(refs)})")
    return "\n".join(lines)


def scoped_changes_index(
    diff: RegulatoryDiff, records: dict[str, RetrievalRecord], sees_delta: bool
) -> str:
    """For scoped experts: only the changes whose key the scope granted. A key granted as an
    obligation view points at the view's source ids, never at the text the expert cannot see.

    With ``sees_delta`` a text grant keeps the ``[kind]`` tag and its ``before:`` / ``after:``
    references. Without it, each granted key gets one neutral reference: its granted source ids,
    sorted, with no side labels and no article numbers (a renumbering would hint at a change).

    What stays inherent without ``sees_delta``: the expert sees which versions a key has sources
    in. Text grants list both versions' sources whenever both exist, so a source id's version
    prefix only tells the expert what it would learn from the texts anyway; but a key with one
    source only (added or removed, or a proposal-only key such as ``ai_act/proposal/art/4``)
    still shows that one side is missing. An obligation view lists only the versions with
    records, so a missing view is ambiguous between "no such provision" and "no records"."""
    lines = []
    for c in diff.changes:
        record = records.get(c.provision_key)
        if record is None or not record.granted:
            continue
        if record.status == "granted_text" and sees_delta:
            refs = []
            if c.before:
                refs.append(f"before: Art {c.before.article}, source_id={c.before.source_id}")
            if c.after:
                refs.append(f"after: Art {c.after.article}, source_id={c.after.source_id}")
            ref = "; ".join(refs)
        elif record.status == "granted_text":
            ref = f"sources: {', '.join(sorted(record.source_ids))}"
        elif sees_delta:
            ref = "; ".join(f"obligation records: source_id={sid}" for sid in record.source_ids)
        else:
            ref = f"obligation records: {', '.join(sorted(record.source_ids))}"
        tag = f"[{c.kind}] " if sees_delta else ""
        lines.append(f"- {tag}provision_key={c.provision_key} ({ref})")
    return "\n".join(lines) or "(no changed provision is within your data scope)"


def sources_block(sources: list[Source]) -> str:
    return "\n\n".join(
        f'<source id="{s.source_id}" kind="{s.kind}" title="{s.title}">\n{s.text}\n</source>'
        for s in sources
    )


def findings_for_synthesis(findings: list[ImpactFinding]) -> str:
    rows = [
        {
            "finding_id": f.finding_id,
            "agent": f.agent,
            "provision_key": f.provision_key,
            "affected_actor": f.affected_actor,
            "mechanism": f.mechanism,
            "impact": f.impact,
            "confidence": f.confidence,
        }
        for f in findings
    ]
    return json.dumps(rows, indent=1, ensure_ascii=False)
