"""Deterministic provision-level diff keyed on provision_key, not article number (R3)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from womm.models.regulation import Provision, RegulationVersion

ChangeKind = Literal["added", "removed", "modified"]

_WS = re.compile(r"\s+")


class ProvisionChange(BaseModel):
    provision_key: str
    kind: ChangeKind
    before: Provision | None
    after: Provision | None


class RegulatoryDiff(BaseModel):
    before_version: str | None
    after_version: str
    changes: list[ProvisionChange]

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def keys(self) -> list[str]:
        return [c.provision_key for c in self.changes]


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def diff_versions(
    before: RegulationVersion | None,
    after: RegulationVersion,
    keys: list[str] | None = None,
) -> RegulatoryDiff:
    """Diff two versions. `before=None` means no prior version: every provision is 'added'.

    `keys` restricts the diff to a scenario's provision subset; order follows `keys` when given,
    otherwise the after-version's provision order followed by removed keys.
    """
    old = before.by_key() if before else {}
    new = after.by_key()
    order = keys if keys is not None else list(new) + [k for k in old if k not in new]

    changes: list[ProvisionChange] = []
    for key in order:
        a, b = old.get(key), new.get(key)
        if a is None and b is None:
            raise KeyError(f"provision_key {key!r} not found in either version")
        if a is None:
            changes.append(ProvisionChange(provision_key=key, kind="added", before=None, after=b))
        elif b is None:
            changes.append(ProvisionChange(provision_key=key, kind="removed", before=a, after=None))
        elif _norm(a.text) != _norm(b.text):
            changes.append(ProvisionChange(provision_key=key, kind="modified", before=a, after=b))
    return RegulatoryDiff(
        before_version=before.version_id if before else None,
        after_version=after.version_id,
        changes=changes,
    )
