"""Sealed holdout store and its only scoring entry point (U6; R23, R28 interface, AE3).

Holdout cases never live in this repository, in LangSmith or anywhere the Improvement Planner
can read:

- They live in their own Postgres database, named by ``HOLDOUT_DATABASE_URL``. The deployed API
  is never given that URL (``womm.config.Settings`` has no field for it), and a holdout URL equal
  to ``DATABASE_URL`` is refused.
- The holdout schema has its own migrations (``holdout_migrations/``), applied only here, by the
  holdout scripts. ``womm.api.db.Database.migrate()`` never sees them.
- Holdout scenarios are not in ``data/fixtures/``. Their article sets are supplied locally in
  ``evals/private/holdout_scenarios.yaml`` (gitignored) when a case is imported, stored with the
  case, and injected into the public proposal fixture only at scoring time.
- ``compare`` is the only reader. It runs both system versions inside
  ``langsmith.tracing_context(enabled=False)``, never through ``evaluate_cases`` (whose traced
  ``womm:eval_case`` wrapper and ``runs/`` files are for train/val), and returns only per-metric
  mean deltas, paired bootstrap CIs clustered by proposal and pooled noise: no case ids and no
  per-case values. The result is written only to ``holdout.compare_audit``.
- The R27 SystemVersion archive excludes holdout metrics: whatever archives candidates reads
  train/val reports, never this module's output or audit table.

``evals/private/holdout_scenarios.yaml`` maps a fixture id to its holdout scenarios::

    space_act:
      - scenario_id: eval_launch_authorisation
        description: Authorisation of space operators (proposal only)
        articles: ['5', '6', '7']
        after_version: com2025_335   # optional; defaults to the fixture's last version
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import yaml
from langsmith import tracing_context
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError

from womm.config import REPO_ROOT
from womm.data.fixtures import (
    DEFAULT_FIXTURE,
    Fixture,
    FixtureError,
    fixture_dir,
    load_fixture,
    validate_fixture,
)
from womm.data.ia_index import IaRecord
from womm.decisions.service import DecisionService
from womm.eval.evaluators import CaseScore, score_case
from womm.eval.golden import GOLDEN_DIR, GoldenCase, GoldenError, check_against_fixture
from womm.eval.run_eval import _hit_rate_limit
from womm.graph.build import run_scenario
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel
from womm.models.regulation import Scenario
from womm.models.run import CodeIdentity
from womm.models.system_version import SystemVersion

HOLDOUT_URL_ENV = "HOLDOUT_DATABASE_URL"
MIGRATIONS_DIR = Path(__file__).parent / "holdout_migrations"
PRIVATE_DIR = REPO_ROOT / "evals" / "private"
SCENARIOS_PATH = PRIVATE_DIR / "holdout_scenarios.yaml"
HANDOFF_FORMAT = "womm-holdout-handoff/1"
METRICS = ("coverage", "omissions_addressed", "grounding")
_LOCK_ID = 727002  # distinct from the API migrations' advisory lock


class HoldoutError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- configuration


def holdout_database_url(env: Mapping[str, str] | None = None) -> str:
    """``HOLDOUT_DATABASE_URL``; refused when unset or when it is the API's ``DATABASE_URL``."""
    env = os.environ if env is None else env
    url = (env.get(HOLDOUT_URL_ENV) or "").strip()
    if not url:
        raise HoldoutError(f"{HOLDOUT_URL_ENV} is not set; the holdout has its own database")
    refuse_api_database(url, env)
    return url


def refuse_api_database(url: str, env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    api = (env.get("DATABASE_URL") or "").strip()
    if api and api.rstrip("/") == url.strip().rstrip("/"):
        raise HoldoutError(
            f"{HOLDOUT_URL_ENV} must name a database the API is never given, not DATABASE_URL"
        )


# ----------------------------------------------------------------------------- scenarios


class HoldoutScenarioSpec(StrictModel):
    scenario_id: str = Field(pattern=r"^eval_[a-z0-9_]+$")
    description: str = Field(min_length=1)
    articles: list[str] = Field(min_length=1)
    after_version: str | None = None


def _refuse_outside(path: Path, directory: Path, what: str) -> None:
    if not path.resolve().is_relative_to(directory.resolve()):
        raise HoldoutError(f"{what} must come from {directory}, not {path}")


def load_holdout_scenarios(
    path: Path = SCENARIOS_PATH, private_dir: Path = PRIVATE_DIR
) -> dict[str, list[HoldoutScenarioSpec]]:
    """Fixture id -> holdout scenario specs, read only from the local private directory."""
    _refuse_outside(path, private_dir, "holdout scenario definitions")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise HoldoutError(f"no holdout scenario definitions at {path}") from None
    except (OSError, yaml.YAMLError) as exc:
        raise HoldoutError(f"cannot read {path}: {exc}") from None
    if not isinstance(raw, dict) or not all(isinstance(v, list) for v in raw.values()):
        raise HoldoutError(f"{path}: expected a mapping of fixture id to a list of scenarios")
    try:
        return {str(k): [HoldoutScenarioSpec.model_validate(s) for s in v] for k, v in raw.items()}
    except ValidationError as exc:
        raise HoldoutError(f"{path}: {exc}") from None


def build_scenario(fixture: Fixture, spec: HoldoutScenarioSpec) -> Scenario:
    """The evaluation scenario for ``spec``'s articles; never one of the public scenarios."""
    if spec.scenario_id in fixture.scenarios:
        raise HoldoutError(
            f"scenario {spec.scenario_id!r} is in the public fixture "
            f"{fixture.regulation.regulation_id!r}; holdout scenarios are defined only locally"
        )
    versions = fixture.regulation.versions
    try:
        version = fixture.version(spec.after_version) if spec.after_version else versions[-1]
    except FixtureError as exc:
        raise HoldoutError(f"{spec.scenario_id}: {exc}") from None
    by_article = {p.article: p.provision_key for p in version.provisions}
    missing = [a for a in spec.articles if a not in by_article]
    if missing:
        raise HoldoutError(
            f"{spec.scenario_id}: articles {missing} are not in {version.version_id} of "
            f"fixture {fixture.regulation.regulation_id!r}"
        )
    return Scenario(
        scenario_id=spec.scenario_id,
        kind="evaluation",
        description=spec.description,
        before_version=None,
        after_version=version.version_id,
        provision_keys=[by_article[a] for a in spec.articles],
    )


def with_scenarios(fixture: Fixture, scenarios: list[Scenario]) -> Fixture:
    """A copy of ``fixture`` holding the injected holdout scenarios (scoring time only)."""
    clash = [s.scenario_id for s in scenarios if s.scenario_id in fixture.scenarios]
    if clash:
        raise HoldoutError(f"holdout scenarios {clash} clash with public scenarios")
    injected = Fixture(
        fixture.regulation,
        fixture.sources,
        {**fixture.scenarios, **{s.scenario_id: s for s in scenarios}},
    )
    validate_fixture(injected)
    return injected


# ----------------------------------------------------------------------------- import


@dataclass(frozen=True)
class ImportBundle:
    case: GoldenCase
    spec: HoldoutScenarioSpec
    scenario: Scenario
    ia: IaRecord
    verified_by: str
    verified_on: str
    draft_sha256: str | None

    @property
    def body(self) -> dict[str, Any]:
        return self.case.model_dump(mode="json", exclude_none=True)

    @property
    def body_sha256(self) -> str:
        return _sha(self.body)


def _sha(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def prepare_import(
    handoff: dict[str, Any],
    scenarios: dict[str, list[HoldoutScenarioSpec]],
    ia_index: dict[str, IaRecord],
    *,
    golden_dir: Path = GOLDEN_DIR,
) -> ImportBundle:
    """Validate one verified handoff against its locally supplied scenario and IA record."""
    if handoff.get("format") != HANDOFF_FORMAT:
        raise HoldoutError(f"not a holdout handoff (format {handoff.get('format')!r})")
    try:
        case = GoldenCase.model_validate(handoff.get("case"))
    except ValidationError as exc:
        raise HoldoutError(f"the handoff case is not a valid golden case: {exc}") from None
    if case.split != "holdout":
        raise HoldoutError(f"{case.case_id} is a {case.split} case; only holdout cases are sealed")
    verified_by = str(handoff.get("verified_by") or "").strip()
    verified_on = str(handoff.get("verified_on") or "").strip()
    if not verified_by or not verified_on:
        raise HoldoutError(f"{case.case_id}: the handoff names no reviewer or verification date")
    public = {p.stem for p in golden_dir.glob("case_*.yaml")}
    if case.case_id in public:
        raise HoldoutError(f"{case.case_id} is a public golden case id")
    for path in sorted(golden_dir.glob("case_*.yaml")):
        other = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if other.get("fixture", DEFAULT_FIXTURE) == case.fixture:
            # Splits are per proposal: a public case on this fixture would expose it.
            raise HoldoutError(
                f"fixture {case.fixture!r} already backs public golden case {path.stem}; "
                "all cases of one proposal share a split"
            )
    spec = next((s for s in scenarios.get(case.fixture, []) if s.scenario_id == case.scenario_id),
                None)  # fmt: skip
    if spec is None:
        raise HoldoutError(
            f"{case.case_id}: no local holdout scenario {case.scenario_id!r} for fixture "
            f"{case.fixture!r} in {SCENARIOS_PATH.name}"
        )
    ia = ia_index.get(case.fixture)
    if ia is None or not ia.ia_celex:
        raise HoldoutError(f"{case.case_id}: no IA record for fixture {case.fixture!r}")
    try:
        fixture = load_fixture(fixture_dir(case.fixture))
    except FixtureError as exc:
        raise HoldoutError(f"{case.case_id}: {exc}") from None
    scenario = build_scenario(fixture, spec)
    try:
        check_against_fixture(case, with_scenarios(fixture, [scenario]))
    except (GoldenError, FixtureError) as exc:
        raise HoldoutError(str(exc)) from None
    return ImportBundle(
        case=case,
        spec=spec,
        scenario=scenario,
        ia=ia,
        verified_by=verified_by,
        verified_on=verified_on,
        draft_sha256=handoff.get("draft_sha256"),
    )


# ----------------------------------------------------------------------------- store


class HoldoutStore:
    """The holdout database. Every connection is short-lived; nothing is cached in memory."""

    def __init__(self, url: str) -> None:
        refuse_api_database(url)
        self.url = url

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(
            self.url, autocommit=True, row_factory=dict_row
        )

    async def migrate(self) -> list[str]:
        """Apply pending holdout migrations; returns the ones applied now."""
        applied_now: list[str] = []
        async with await self._connect() as conn:
            await conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
            try:
                await conn.execute("CREATE SCHEMA IF NOT EXISTS holdout")
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS holdout.schema_migrations ("
                    " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                rows = await (
                    await conn.execute("SELECT version FROM holdout.schema_migrations")
                ).fetchall()
                done = {r["version"] for r in rows}
                for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if path.stem in done:
                        continue
                    async with conn.transaction():
                        await conn.execute(path.read_text(encoding="utf-8"))
                        await conn.execute(
                            "INSERT INTO holdout.schema_migrations (version) VALUES (%s)",
                            (path.stem,),
                        )
                    applied_now.append(path.stem)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
        return applied_now

    async def import_case(self, bundle: ImportBundle) -> str:
        """Upsert the case, its scenario and its IA ids; 'inserted', 'updated' or 'unchanged'."""
        case, sc = bundle.case, bundle.scenario
        async with await self._connect() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    "SELECT c.body_sha256, s.scenario FROM holdout.cases c JOIN holdout.scenarios"
                    " s USING (fixture, scenario_id) WHERE c.case_id = %s",
                    (case.case_id,),
                )
            ).fetchone()
            scenario_json = sc.model_dump(mode="json")
            same = row and row["scenario"] == scenario_json
            if same and row["body_sha256"] == bundle.body_sha256:
                return "unchanged"
            ia = bundle.ia
            await conn.execute(
                "INSERT INTO holdout.ia_references"
                " (fixture, celex, ia_reference, ia_celex, ia_date, rsb_ref)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (fixture) DO UPDATE SET"
                " celex = EXCLUDED.celex, ia_reference = EXCLUDED.ia_reference,"
                " ia_celex = EXCLUDED.ia_celex, ia_date = EXCLUDED.ia_date,"
                " rsb_ref = EXCLUDED.rsb_ref, updated_at = now()",
                (case.fixture, ia.celex, case.ia_reference, ia.ia_celex, ia.ia_date, ia.rsb_ref),
            )
            await conn.execute(
                "INSERT INTO holdout.scenarios (fixture, scenario_id, articles, scenario)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (fixture, scenario_id) DO UPDATE SET"
                " articles = EXCLUDED.articles, scenario = EXCLUDED.scenario, updated_at = now()",
                (case.fixture, sc.scenario_id, Jsonb(bundle.spec.articles), Jsonb(scenario_json)),
            )
            await conn.execute(
                "INSERT INTO holdout.cases (case_id, fixture, scenario_id, split, body,"
                " body_sha256, verified_by, verified_on, draft_sha256)"
                " VALUES (%s, %s, %s, 'holdout', %s, %s, %s, %s, %s)"
                " ON CONFLICT (case_id) DO UPDATE SET fixture = EXCLUDED.fixture,"
                " scenario_id = EXCLUDED.scenario_id, body = EXCLUDED.body,"
                " body_sha256 = EXCLUDED.body_sha256, verified_by = EXCLUDED.verified_by,"
                " verified_on = EXCLUDED.verified_on, draft_sha256 = EXCLUDED.draft_sha256,"
                " updated_at = now()",
                (case.case_id, case.fixture, sc.scenario_id, Jsonb(bundle.body),
                 bundle.body_sha256, bundle.verified_by, bundle.verified_on,
                 bundle.draft_sha256),
            )  # fmt: skip
        return "updated" if row else "inserted"

    async def _sealed(self) -> list[tuple[GoldenCase, Scenario]]:
        """Every holdout case with its scenario. Private to this module: only ``compare``."""
        async with await self._connect() as conn:
            rows = await (
                await conn.execute(
                    "SELECT c.body, s.scenario FROM holdout.cases c JOIN holdout.scenarios s"
                    " USING (fixture, scenario_id) ORDER BY c.case_id"
                )
            ).fetchall()
        return [
            (GoldenCase.model_validate(r["body"]), Scenario.model_validate(r["scenario"]))
            for r in rows
        ]

    async def record_audit(self, result: HoldoutComparison, git_sha: str | None) -> int:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO holdout.compare_audit"
                    " (candidate_version, baseline_version, repetitions, git_sha, result)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (
                        result.candidate_version,
                        result.baseline_version,
                        result.repetitions,
                        git_sha,
                        Jsonb(result.model_dump(mode="json")),
                    ),
                )  # fmt: skip
            ).fetchone()
        return row["id"]


# ----------------------------------------------------------------------------- compare


class MetricDelta(StrictModel):
    mean_delta: float | None = Field(description="candidate minus baseline, mean over cases")
    ci95_low: float | None
    ci95_high: float | None
    n_cases: int


class PooledNoise(StrictModel):
    """Within-case run-to-run standard deviation, pooled over all holdout cases."""

    candidate_sd: float | None
    baseline_sd: float | None


class HoldoutComparison(StrictModel):
    """Aggregates only: no case ids, scenario ids or per-case values, by construction."""

    candidate_version: str
    baseline_version: str
    judge_version: str = Field(description="Both versions are scored by the baseline's judge.")
    repetitions: int
    n_cases: int
    n_proposals: int
    scored_runs: dict[str, int]
    errored_runs: dict[str, int]
    aborted: bool = Field(description="True when a rate limit stopped the comparison early.")
    deltas: dict[str, MetricDelta]
    noise: dict[str, PooledNoise]
    bootstrap: dict[str, Any]


Backends = Mapping[str, LLMBackend]


def _per(value: Any, sv: SystemVersion) -> Any:
    """A shared object, or a factory taking the system version."""
    return value(sv) if callable(value) else value


def _means(scores: list[CaseScore], metric: str) -> dict[str, float]:
    """Internal case key -> mean over its scored repetitions (never leaves this module)."""
    by_case: dict[str, list[float]] = {}
    for s in scores:
        v = getattr(s, metric)
        if s.outcome == "scored" and v is not None:
            by_case.setdefault(s.case_id, []).append(v)
    return {k: statistics.fmean(v) for k, v in by_case.items()}


def _pooled_sd(scores: list[CaseScore], metric: str) -> float | None:
    by_case: dict[str, list[float]] = {}
    for s in scores:
        v = getattr(s, metric)
        if s.outcome == "scored" and v is not None:
            by_case.setdefault(s.case_id, []).append(v)
    dof = sum(len(v) - 1 for v in by_case.values() if len(v) > 1)
    if dof == 0:
        return None
    ss = sum(statistics.variance(v) * (len(v) - 1) for v in by_case.values() if len(v) > 1)
    return math.sqrt(ss / dof)


def paired_cluster_bootstrap(
    diffs: dict[str, float], cluster_of: dict[str, str], n_boot: int, seed: int
) -> tuple[float | None, float | None, float | None]:
    """Mean paired difference and its 95% percentile CI, resampling whole proposals."""
    if not diffs:
        return None, None, None
    point = statistics.fmean(diffs.values())
    clusters: dict[str, list[float]] = {}
    for key, d in diffs.items():
        clusters.setdefault(cluster_of[key], []).append(d)
    groups = list(clusters.values())
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        sample = [d for g in rng.choices(groups, k=len(groups)) for d in g]
        stats.append(statistics.fmean(sample))
    stats.sort()
    lo = stats[int(0.025 * (n_boot - 1))]
    hi = stats[int(math.ceil(0.975 * (n_boot - 1)))]
    return point, lo, hi


async def compare(
    candidate: SystemVersion,
    baseline: SystemVersion,
    repetitions: int = 1,
    *,
    store: HoldoutStore,
    backends: Backends | Callable[[SystemVersion], Backends],
    decisions: DecisionService | Callable[[SystemVersion], DecisionService],
    code: CodeIdentity,
    n_boot: int = 2000,
    seed: int = 0,
) -> HoldoutComparison:
    """Run ``candidate`` and ``baseline`` on every sealed holdout case, ``repetitions`` times
    each, and return only aggregate deltas (R28 interface). Both are scored by the baseline's
    judge so a candidate cannot change the yardstick. ``backends`` and ``decisions`` are either
    shared or a factory per system version. The result is also written to the audit table."""
    if repetitions < 1:
        raise HoldoutError("repetitions must be at least 1")
    # Holdout material never reaches LangSmith: no traces, no datasets, no experiments.
    with tracing_context(enabled=False):
        sealed = await store._sealed()
        if not sealed:
            raise HoldoutError("the holdout store holds no cases")
        fixtures = _fixtures_with_scenarios(sealed)
        judge_role = baseline.spec.judge
        judge_backend = _per(backends, baseline)[judge_role.backend]
        judge_prompt = baseline.prompt_text(judge_role)
        keys = {case.case_id: f"k{i}" for i, (case, _) in enumerate(sealed)}
        cluster_of = {keys[c.case_id]: c.fixture for c, _ in sealed}
        scores: dict[str, list[CaseScore]] = {"candidate": [], "baseline": []}
        aborted = False
        for label, sv in (("candidate", candidate), ("baseline", baseline)):
            sv_backends, sv_decisions = _per(backends, sv), _per(decisions, sv)
            for case, _scenario in sealed:
                for _ in range(repetitions):
                    run = await run_scenario(
                        case.scenario_id, sv=sv, fixture=fixtures[case.fixture],
                        backends=sv_backends, decisions=sv_decisions, code_identity=code,
                        tags=["holdout"],
                    )  # fmt: skip
                    score, _ = await score_case(case, run, judge_backend, judge_role, judge_prompt)
                    score = score.model_copy(update={"case_id": keys[case.case_id]})
                    scores[label].append(score)
                    if _hit_rate_limit(run, score):
                        aborted = True
                        break
                if aborted:
                    break
            if aborted:
                break
        result = _summarise(
            candidate, baseline, repetitions, scores, cluster_of, aborted, n_boot, seed
        )
        await store.record_audit(result, code.git_sha)
    return result


def _fixtures_with_scenarios(sealed: list[tuple[GoldenCase, Scenario]]) -> dict[str, Fixture]:
    by_fixture: dict[str, list[Scenario]] = {}
    for case, scenario in sealed:
        group = by_fixture.setdefault(case.fixture, [])
        if scenario.scenario_id not in {s.scenario_id for s in group}:
            group.append(scenario)
    out: dict[str, Fixture] = {}
    for name, scenarios in by_fixture.items():
        try:
            out[name] = with_scenarios(load_fixture(fixture_dir(name)), scenarios)
        except FixtureError as exc:
            raise HoldoutError(f"a holdout proposal fixture failed to load: {exc}") from None
    return out


def _summarise(
    candidate: SystemVersion,
    baseline: SystemVersion,
    repetitions: int,
    scores: dict[str, list[CaseScore]],
    cluster_of: dict[str, str],
    aborted: bool,
    n_boot: int,
    seed: int,
) -> HoldoutComparison:
    deltas: dict[str, MetricDelta] = {}
    noise: dict[str, PooledNoise] = {}
    for metric in METRICS:
        cand, base = _means(scores["candidate"], metric), _means(scores["baseline"], metric)
        diffs = {} if aborted else {k: cand[k] - base[k] for k in cand.keys() & base.keys()}
        point, lo, hi = paired_cluster_bootstrap(diffs, cluster_of, n_boot, seed)
        deltas[metric] = MetricDelta(mean_delta=point, ci95_low=lo, ci95_high=hi,
                                     n_cases=len(diffs))  # fmt: skip
        noise[metric] = PooledNoise(
            candidate_sd=_pooled_sd(scores["candidate"], metric),
            baseline_sd=_pooled_sd(scores["baseline"], metric),
        )
    return HoldoutComparison(
        candidate_version=candidate.version_id,
        baseline_version=baseline.version_id,
        judge_version=baseline.version_id,
        repetitions=repetitions,
        n_cases=len(cluster_of),
        n_proposals=len(set(cluster_of.values())),
        scored_runs={k: sum(s.outcome == "scored" for s in v) for k, v in scores.items()},
        errored_runs={k: sum(s.outcome != "scored" for s in v) for k, v in scores.items()},
        aborted=aborted,
        deltas=deltas,
        noise=noise,
        bootstrap={"n_boot": n_boot, "seed": seed, "cluster": "proposal", "ci": 0.95},
    )
