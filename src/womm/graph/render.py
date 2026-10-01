"""Render graph inputs as prompt text. Kept in one place so prompt shape is easy to review."""

from __future__ import annotations

import json

from womm.diff import RegulatoryDiff
from womm.models.findings import ImpactFinding
from womm.models.regulation import Source


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
