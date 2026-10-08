"""R7 (EU cost plan U7): costs the ex-ante impact assessment could not see.

Cost records of a final-act sweep (``reg2024_1689``) on obligations whose unit was added after
the proposal (``unit_delta: added``). Reported, never scored: SWD(2021) 84 assessed the
proposal COM(2021) 206, so it cannot be a benchmark for text it never saw. ``modified`` and
``split_merge`` units are "changed after the proposal" (R5) and appear only as a context count.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from womm.models.cost import CostRecord, band_rank

NOT_SCORED = (
    "Not scored: SWD(2021) 84 assessed the proposal COM(2021) 206, not these obligations, "
    "which were added after it."
)
COSTLY = band_rank("medium")
_WITHHELD = {
    "amended_2026": "amended 2026",
    "date_moved_2026": "moved 2026",
    "annex": "annex: applies through its articles",
    "proposal": "a proposal never applied",
}


def date_text(r: CostRecord) -> str:
    """The date a record applies from, only with its label; a withheld date is never shown."""
    if r.date_withheld:
        return f"date withheld ({_WITHHELD.get(r.date_withheld, r.date_withheld)})"
    if r.applies_from and r.date_label:
        return f"{r.applies_from} ({r.date_label})"
    return "no date"


@dataclass
class LateAddedReport:
    records: list[CostRecord]
    by_payer: dict[str, int]
    by_effort: dict[str, int]
    by_band: dict[str, int]
    by_date: dict[str, int]
    added_not_costed: int
    added_not_estimated: int
    changed_not_added: int
    costly_total: int
    costly_added: int
    by_payer_costly: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def costly_share(self) -> float | None:
        return self.costly_added / self.costly_total if self.costly_total else None


def late_added_report(records: list[CostRecord]) -> LateAddedReport:
    added_all = [r for r in records if r.unit_delta == "added"]
    added = [r for r in added_all if r.status == "estimated"]
    costly = [r for r in records if r.status == "estimated" and band_rank(r.top_band) >= COSTLY]
    by_payer_costly: dict[str, dict[str, int]] = {}
    for r in costly:
        row = by_payer_costly.setdefault(r.payer or "payer not identified",
                                         {"added": 0, "ia_could_see": 0})  # fmt: skip
        row["added" if r.unit_delta == "added" else "ia_could_see"] += 1
    return LateAddedReport(
        records=added,
        by_payer=dict(Counter(r.payer or "payer not identified" for r in added)),
        by_effort=dict(Counter(r.effort_type or "none" for r in added)),
        by_band=dict(Counter(r.top_band or "none" for r in added)),
        by_date=dict(Counter(date_text(r) for r in added)),
        added_not_costed=sum(r.status == "not_costed" for r in added_all),
        added_not_estimated=sum(r.status == "not_estimated" for r in added_all),
        changed_not_added=sum(r.unit_delta in ("modified", "split_merge") for r in records),
        costly_total=len(costly),
        costly_added=sum(r.unit_delta == "added" for r in costly),
        by_payer_costly=by_payer_costly,
    )


def _counts(title: str, counts: dict[str, int]) -> list[str]:
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [f"{title}:", *(f"- {k}: {n}" for k, n in rows)] if rows else [f"{title}: none"]


def format_late_added(report: LateAddedReport, header: str | None = None) -> str:
    share = report.costly_share
    lines = [NOT_SCORED]
    if header:
        lines.append(header)
    lines += [
        "",
        f"{len(report.records)} estimated cost records sit on obligations added after the "
        f"proposal ({report.added_not_costed} more not costed, {report.added_not_estimated} not "
        "estimated).",
        f"{report.changed_not_added} more records were changed after the proposal (modified or "
        "split/merged); they are context, not part of this report.",
        f"Share of all records at medium or high that sit in added text: "
        f"{report.costly_added}/{report.costly_total}"
        + (f" ({share:.0%})" if share is not None else ""),
        "",
        *_counts("By payer", report.by_payer),
        *_counts("By effort type", report.by_effort),
        *_counts("By highest band", report.by_band),
        *_counts("Applies from", report.by_date),
        "",
        "Comparison context, not a score (records at medium or high, per payer): added text vs "
        "text the IA could see",
    ]
    for payer, row in sorted(report.by_payer_costly.items()):
        lines.append(f"- {payer}: {row['added']} vs {row['ia_could_see']}")
    lines += ["", "Records added after the proposal:"]
    for r in report.records:
        lines.append(
            f"- {r.obligation_id} [{r.provision_key}] payer={r.payer or 'not identified'} "
            f"({r.payer_basis}) effort={r.effort_type} one-off={r.one_off or '-'} "
            f"recurring={r.recurring or '-'} applies from {date_text(r)}"
        )
    return "\n".join(lines)
