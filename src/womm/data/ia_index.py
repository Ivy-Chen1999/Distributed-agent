"""Local index of the impact assessments that golden cases score against (R23, R24).

``evals/private/ia_index.yaml`` maps a fixture id to the proposal's CELEX, the IA staff working
document's CELEX and publication date, and the Regulatory Scrutiny Board opinion reference. The
directory is gitignored: which IA backs which proposal reveals holdout membership, so none of these
identifiers are committed. Train/val entries may be published later; holdout entries never.

``scripts/import_proposal.py`` writes entries (``--ia-celex/--ia-date/--rsb-ref``); golden-case
drafting and the holdout store read them with ``load_ia_index``. A missing file is an empty index.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from pydantic import Field, ValidationError

from womm.config import REPO_ROOT
from womm.models.base import StrictModel

IA_INDEX_PATH = REPO_ROOT / "evals" / "private" / "ia_index.yaml"
IA_FIELDS = ("ia_celex", "ia_date", "rsb_ref")

_HEADER = (
    "# LOCAL ONLY: evals/private/ is gitignored. IA identifiers per fixture id, written by\n"
    "# scripts/import_proposal.py. Never commit holdout entries; train/val may be published\n"
    "# later.\n"
)


class IaIndexError(ValueError):
    """The IA index file is malformed."""


class IaRecord(StrictModel):
    celex: str = Field(pattern=r"^5\d{4}PC\d{4}$", description="CELEX of the COM proposal.")
    ia_celex: str | None = Field(
        default=None,
        pattern=r"^5\d{4}SC\d{4}(\(\d{2}\))?$",
        description="CELEX of the IA staff working document (not its executive summary).",
    )
    ia_date: dt.date | None = Field(default=None, description="Publication date of the IA.")
    rsb_ref: str | None = Field(default=None, description="Regulatory Scrutiny Board opinion.")


def load_ia_index(path: Path = IA_INDEX_PATH) -> dict[str, IaRecord]:
    """Fixture id -> IaRecord; {} when the file does not exist."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise IaIndexError(f"cannot read {path}: {exc}") from None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise IaIndexError(f"{path}: expected a mapping of fixture id to IA record")
    try:
        return {str(k): IaRecord.model_validate(v) for k, v in raw.items()}
    except ValidationError as exc:
        raise IaIndexError(f"{path}: {exc}") from None


def update_ia_index(fixture_id: str, record: IaRecord, path: Path = IA_INDEX_PATH) -> None:
    """Insert or replace ``fixture_id``'s entry, keeping every other entry."""
    index = load_ia_index(path)
    index[fixture_id] = record
    payload = {k: index[k].model_dump(mode="json") for k in sorted(index)}
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(_HEADER + body, encoding="utf-8")
