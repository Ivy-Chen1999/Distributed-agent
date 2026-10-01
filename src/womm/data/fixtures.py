"""Load and validate the committed regulation fixture (data/fixtures/<regulation>/).

Files:
- ``proposal.json`` (and optionally ``final.json``): a Regulation holding one or more versions
- ``sources.json``: list[Source], the only texts experts may cite (R9)
- ``scenarios.yaml``: ``{"scenarios": list[Scenario]}``
- ``crosswalk.yaml``: hand-maintained ``{"entries": list[CrosswalkEntry]}`` mapping each stable
  provision key to its article number in every version (build input, not needed at runtime)
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import Field, ValidationError

from womm.config import REPO_ROOT
from womm.models.base import StrictModel
from womm.models.regulation import Provision, Regulation, RegulationVersion, Scenario, Source

DEFAULT_FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "ai_act"
VERSION_FILES = ("proposal.json", "final.json")


class FixtureError(ValueError):
    """The fixture files are missing, malformed or inconsistent."""


class CrosswalkEntry(StrictModel):
    provision_key: str = Field(min_length=1)
    articles: dict[str, str] = Field(
        min_length=1, description="version_id -> article number as printed in that version"
    )


@dataclass(frozen=True)
class Crosswalk:
    entries: tuple[CrosswalkEntry, ...]

    def __post_init__(self) -> None:
        keys = [e.provision_key for e in self.entries]
        if len(set(keys)) != len(keys):
            raise FixtureError("duplicate provision_key in crosswalk")
        seen: dict[tuple[str, str], str] = {}
        for e in self.entries:
            for version, article in e.articles.items():
                other = seen.setdefault((version, article), e.provision_key)
                if other != e.provision_key:
                    raise FixtureError(
                        f"{version} Art {article} mapped to both {other!r} and {e.provision_key!r}"
                    )

    def key_for(self, version_id: str, article: str) -> str:
        for e in self.entries:
            if e.articles.get(version_id) == article:
                return e.provision_key
        raise FixtureError(f"Art {article} of {version_id} is not in the crosswalk")

    def article_for(self, provision_key: str, version_id: str) -> str | None:
        for e in self.entries:
            if e.provision_key == provision_key:
                return e.articles.get(version_id)
        raise FixtureError(f"provision key {provision_key!r} is not in the crosswalk")

    def resolve(self, scenario_id: str, version_id: str, articles: Iterable[str]) -> list[str]:
        """Provision keys for a scenario's articles; fails naming the scenario and article."""
        try:
            return [self.key_for(version_id, a) for a in articles]
        except FixtureError as exc:
            raise FixtureError(f"scenario {scenario_id!r}: {exc}") from None


def load_crosswalk(path: Path) -> Crosswalk:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Crosswalk(tuple(CrosswalkEntry.model_validate(e) for e in raw.get("entries", [])))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise FixtureError(f"cannot load crosswalk {path}: {exc}") from exc


@dataclass(frozen=True)
class Fixture:
    regulation: Regulation
    sources: dict[str, Source]
    scenarios: dict[str, Scenario]

    def version(self, version_id: str) -> RegulationVersion:
        for v in self.regulation.versions:
            if v.version_id == version_id:
                return v
        raise FixtureError(f"unknown version {version_id!r}")

    def scenario(self, scenario_id: str) -> Scenario:
        try:
            return self.scenarios[scenario_id]
        except KeyError:
            raise FixtureError(
                f"unknown scenario {scenario_id!r}; known: {sorted(self.scenarios)}"
            ) from None

    def scenario_versions(
        self, scenario_id: str
    ) -> tuple[RegulationVersion | None, RegulationVersion]:
        """(before, after) restricted to the scenario's provision keys."""
        s = self.scenario(scenario_id)
        keys = set(s.provision_keys)

        def restrict(v: RegulationVersion) -> RegulationVersion:
            provisions = [p for p in v.provisions if p.provision_key in keys]
            return v.model_copy(update={"provisions": provisions})

        before = restrict(self.version(s.before_version)) if s.before_version else None
        return before, restrict(self.version(s.after_version))

    def scenario_sources(self, scenario_id: str) -> list[Source]:
        """Sources an expert may cite for this scenario: the provisions' own texts (both
        versions) followed by every memorandum source."""
        before, after = self.scenario_versions(scenario_id)
        provisions: list[Provision] = [*(before.provisions if before else []), *after.provisions]
        ids = list(dict.fromkeys(p.source_id for p in provisions))
        memos = [sid for sid, src in self.sources.items() if src.kind == "memorandum"]
        return [self.sources[sid] for sid in ids + memos if sid in self.sources]


def validate_fixture(fixture: Fixture) -> None:
    """Raise FixtureError naming the first scenario/key/source that does not resolve."""
    versions = {v.version_id: v for v in fixture.regulation.versions}
    if len(versions) != len(fixture.regulation.versions):
        raise FixtureError("duplicate version_id in regulation")
    for v in versions.values():
        for p in v.provisions:
            if p.source_id not in fixture.sources:
                raise FixtureError(
                    f"provision {p.provision_key!r} in {v.version_id} cites unknown source "
                    f"{p.source_id!r}"
                )
    for s in fixture.scenarios.values():
        wanted = [s.after_version] + ([s.before_version] if s.before_version else [])
        for vid in wanted:
            if vid not in versions:
                raise FixtureError(f"scenario {s.scenario_id!r} references unknown version {vid!r}")
        known = set(versions[s.after_version].by_key())
        if s.before_version:
            known |= set(versions[s.before_version].by_key())
        missing = [k for k in s.provision_keys if k not in known]
        if missing:
            raise FixtureError(
                f"scenario {s.scenario_id!r} references provision keys not in "
                f"{'/'.join(wanted)}: {missing}"
            )
        if s.kind == "evaluation" and s.before_version is not None:
            raise FixtureError(f"evaluation scenario {s.scenario_id!r} must have no before_version")
        if s.kind == "demo" and s.before_version is None:
            raise FixtureError(f"demo scenario {s.scenario_id!r} needs a before_version")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read {path}: {exc}") from exc


def load_fixture(directory: Path = DEFAULT_FIXTURE_DIR) -> Fixture:
    try:
        regulation: Regulation | None = None
        for name in VERSION_FILES:
            path = directory / name
            if not path.exists():
                continue
            reg = Regulation.model_validate(_read_json(path))
            if regulation is None:
                regulation = reg
            elif reg.regulation_id != regulation.regulation_id:
                raise FixtureError(
                    f"{name} is for {reg.regulation_id!r}, not {regulation.regulation_id!r}"
                )
            else:
                regulation = regulation.model_copy(
                    update={"versions": [*regulation.versions, *reg.versions]}
                )
        if regulation is None:
            raise FixtureError(f"no {' or '.join(VERSION_FILES)} in {directory}")

        sources = [Source.model_validate(s) for s in _read_json(directory / "sources.json")]
        raw = yaml.safe_load((directory / "scenarios.yaml").read_text(encoding="utf-8")) or {}
        scenarios = [Scenario.model_validate(s) for s in raw.get("scenarios", [])]
    except ValidationError as exc:
        raise FixtureError(f"fixture in {directory} does not match the models: {exc}") from exc
    except OSError as exc:
        raise FixtureError(f"cannot read fixture in {directory}: {exc}") from exc

    source_map = {s.source_id: s for s in sources}
    scenario_map = {s.scenario_id: s for s in scenarios}
    if len(source_map) != len(sources):
        raise FixtureError("duplicate source_id in sources.json")
    if len(scenario_map) != len(scenarios):
        raise FixtureError("duplicate scenario_id in scenarios.yaml")
    fixture = Fixture(regulation, source_map, scenario_map)
    validate_fixture(fixture)
    return fixture
