"""The dossier cost section (EU cost plan R5): records, hotspot rankings and change marks.

Hotspots rank by band counts, never by money: per provision, payer and primary effort type, the
number of records at ``medium`` or ``high``, then at ``low``, with one-off and recurring bands
ranked separately (a record with both counts in both). Ties keep corpus order. Only estimated
records count.
"""

from __future__ import annotations

from womm.cost.estimate import coverage
from womm.models.cost import (
    CostHotspot,
    CostRecord,
    CostSection,
    HotspotDimension,
    Recurrence,
)

NO_PAYER = "payer not identified"
# Every unit delta in the corpus compares these two texts; a consolidated run reads the 2024
# records for unamended units, so its marks compare the same pair.
DELTA_BASIS = "COM(2021) 206 proposal → Regulation (EU) 2024/1689 as adopted"
DIMENSIONS: tuple[HotspotDimension, ...] = ("provision", "payer", "effort_type")
RECURRENCES: tuple[Recurrence, ...] = ("one_off", "recurring")


def _value(r: CostRecord, dimension: HotspotDimension) -> str:
    if dimension == "provision":
        return r.provision_key
    if dimension == "payer":
        return r.payer or NO_PAYER
    return r.effort_type or "none"


def hotspots(records: list[CostRecord]) -> list[CostHotspot]:
    out: list[CostHotspot] = []
    estimated = [r for r in records if r.status == "estimated"]
    for dimension in DIMENSIONS:
        for recurrence in RECURRENCES:
            rows: dict[str, CostHotspot] = {}
            for r in estimated:
                band = r.band(recurrence)
                if band is None:
                    continue
                value = _value(r, dimension)
                row = rows.setdefault(
                    value,
                    CostHotspot(
                        dimension=dimension, value=value, recurrence=recurrence,
                        medium_or_high=0, low=0, records=0,
                    ),
                )  # fmt: skip
                row.records += 1
                if band in ("medium", "high"):
                    row.medium_or_high += 1
                elif band == "low":
                    row.low += 1
                if r.provision_key not in row.provision_keys:
                    row.provision_keys.append(r.provision_key)
            # sorted() is stable: equal counts keep first-seen (corpus) order.
            out.extend(sorted(rows.values(), key=lambda h: (-h.medium_or_high, -h.low)))
    return out


def build_section(
    records: list[CostRecord],
    not_covered: list[str],
    *,
    invalid: int = 0,
    notes: list[str] | None = None,
) -> CostSection:
    marked = any(r.unit_delta is not None for r in records)
    return CostSection(
        records=records,
        hotspots=hotspots(records),
        coverage=coverage(records, not_covered, invalid),
        late_added=[r.obligation_id for r in records if r.late_added],
        delta_basis=DELTA_BASIS if marked else None,
        notes=list(notes or []),
    )
