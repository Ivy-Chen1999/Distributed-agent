"""HTTP API (U11): submit runs, poll status, read live node events.

Every endpoint except /health requires `Authorization: Bearer <WOMM_API_TOKEN>`; the app refuses
to start without a token. Run with: `uvicorn --factory womm.api.app:create_app`.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from langsmith import tracing_context
from pydantic import BaseModel, Field

from womm.api.db import Database
from womm.api.evolution import (
    PANEL_SPLITS,
    CandidateDetail,
    CandidateDiff,
    EvolutionView,
    Lineage,
    read_publish_summary,
)
from womm.api.jobs import JobRunner, QueueFull
from womm.backends import prepare_backends
from womm.config import REPO_ROOT, ConfigError, Settings, load_settings
from womm.data.fixtures import Fixture, FixtureError, load_fixture
from womm.decisions.factory import make_decision_service
from womm.diff import diff_versions
from womm.identity import code_identity
from womm.llm.base import LLMBackend, LLMError
from womm.models.base import StrictModel
from womm.models.system_version import (
    Backend,
    RoleConfig,
    SystemVersion,
    derive_system_version,
    load_system_version,
)

MIN_TOKEN_LENGTH = 16
WEB_DIST = REPO_ROOT / "web" / "dist"
ASK_PROMPT = REPO_ROOT / "prompts" / "ask.md"
# Read only for its publish_summary flag; absent in the API image (no evals/), which is fine.
PROMOTION_POLICY = REPO_ROOT / "evals" / "promotion_policy.yaml"
FINISHED = {"succeeded", "degraded", "failed", "no_changes"}
WITH_DOSSIER = {"succeeded", "degraded", "no_changes"}


class RunOverrides(BaseModel):
    router_mode: Literal["shadow", "active"] | None = None
    backends: dict[str, Backend] = Field(default_factory=dict)


class RunRequest(BaseModel):
    scenario_id: str
    overrides: RunOverrides | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AskAnswer(StrictModel):
    answer: str
    cites: list[str]
    covered: bool


class RunAccepted(BaseModel):
    run_id: str
    status: str
    system_version: str


_bearer = HTTPBearer(auto_error=False)


def create_app(
    settings: Settings | None = None,
    *,
    sv: SystemVersion | None = None,
    fixture: Fixture | None = None,
    backends: dict[str, LLMBackend] | None = None,
    max_concurrent_runs: int = 2,
    max_pending_runs: int = 20,
    orphan_stale_after_s: float = 120.0,
    heartbeat_s: float = 30.0,
    max_concurrent_asks: int = 4,
    ask_timeout_s: float = 120.0,
    web_dist: Path | None = WEB_DIST,
    promotion_policy_path: Path | None = PROMOTION_POLICY,
) -> FastAPI:
    settings = settings or load_settings()
    if not settings.api_token or len(settings.api_token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"WOMM_API_TOKEN must be set to a random value of at least {MIN_TOKEN_LENGTH} chars"
        )
    if not settings.database_url:
        raise ConfigError("DATABASE_URL must be set to run the API")
    sv = sv or load_system_version(settings.system_version_path, REPO_ROOT)
    fixture = fixture or load_fixture()
    ask_slots = asyncio.Semaphore(max_concurrent_asks)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        await db.open()
        await db.migrate()
        orphans = await db.reconcile_orphans(orphan_stale_after_s)
        await db.upsert_system_version(sv)
        runner_backends, cli_version = backends, None
        backend_error = None
        if runner_backends is None:
            try:
                runner_backends, cli_version, self_check = await prepare_backends(
                    sv, settings=settings
                )
                app.state.self_check = self_check
            except Exception as exc:  # noqa: BLE001 - serve /health and report the problem
                runner_backends, backend_error = {}, f"{type(exc).__name__}: {exc}"
        app.state.db = db
        app.state.self_check = getattr(app.state, "self_check", None)
        app.state.backend_error = backend_error
        app.state.orphans_reconciled = orphans
        app.state.runner = JobRunner(
            db=db, sv=sv, fixture=fixture, backends=runner_backends,
            decisions=make_decision_service(sv, settings), code=code_identity(cli_version),
            max_concurrent_runs=max_concurrent_runs, max_pending_runs=max_pending_runs,
            heartbeat_s=heartbeat_s, stale_after_s=orphan_stale_after_s,
        )  # fmt: skip
        app.state.runner.start()
        try:
            yield
        finally:
            await app.state.runner.shutdown()
            await db.close()

    app = FastAPI(title="WOMM", version="0.1.0", lifespan=lifespan)

    def require_token(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        if creds is None or not hmac.compare_digest(
            creds.credentials.encode(), settings.api_token.encode()
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )  # fmt: skip

    auth = [Depends(require_token)]

    @app.get("/livez")
    async def livez() -> dict:
        """Liveness only (the process serves HTTP). The platform health check uses this, so a
        deploy without LLM keys still comes up; /health reports actual readiness."""
        return {"status": "alive"}

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        """503 while backends are unavailable, so a deploy that cannot run anything is not
        promoted by the platform health check."""
        ready = request.app.state.backend_error is None
        body = {"status": "ok" if ready else "degraded", "system_version": sv.version_id,
                "backend_ready": ready}  # fmt: skip
        return JSONResponse(body, status_code=200 if ready else 503)

    @app.get("/scenarios", dependencies=auth)
    async def scenarios() -> list[dict]:
        # Explore scenarios run through POST /runs (API and CLI) but are not in the console's
        # picker yet: they have no fixed provision list for the sources view.
        return [s.model_dump(mode="json") for s in fixture.scenarios.values() if s.mode == "preset"]

    @app.post("/runs", dependencies=auth, status_code=status.HTTP_202_ACCEPTED)
    async def submit(body: RunRequest, request: Request) -> RunAccepted:
        if err := request.app.state.backend_error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"backend unavailable: {err}")
        runner: JobRunner = request.app.state.runner
        run_sv = sv
        if body.overrides:
            unavailable = sorted(set(body.overrides.backends.values()) - set(runner.backends))
            if unavailable:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"backend(s) {unavailable} not available here; available: "
                    f"{sorted(runner.backends)}",
                )
            try:
                run_sv = derive_system_version(
                    sv, REPO_ROOT, router_mode=body.overrides.router_mode,
                    backends=body.overrides.backends,
                )  # fmt: skip
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        try:
            run_id = await runner.submit(body.scenario_id, run_sv)
        except FixtureError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
        except QueueFull as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from None
        return RunAccepted(run_id=run_id, status="queued", system_version=run_sv.version_id)

    @app.get("/runs/{run_id}", dependencies=auth)
    async def get_run(run_id: str, request: Request) -> dict:
        db: Database = request.app.state.db
        row = await db.get_run(run_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run {run_id}")
        nodes: dict[str, str] = {}
        for e in await db.list_events(run_id, limit=10_000):
            nodes[e["node"]] = e["event"]
        out = {
            "run_id": run_id,
            "scenario_id": row["scenario_id"],
            "status": row["status"],
            "system_version": row["system_version"],
            "error_kind": row["error_kind"],
            "error": row["error"],
            "nodes": nodes,
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }
        result = row["result"] or {}
        out["started_at"] = row["started_at"]
        if row["status"] in FINISHED:
            for key in (
                "grounding", "decisions", "board", "failures", "usage", "code_identity",
                "citable_sources",
            ):  # fmt: skip
                out[key] = result.get(key)
        if row["status"] in WITH_DOSSIER:
            out["dossier"] = result.get("dossier")
        return out

    @app.get("/runs/{run_id}/events", dependencies=auth)
    async def run_events(
        run_id: str, request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> dict:  # fmt: skip
        db: Database = request.app.state.db
        if await db.get_run(run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run {run_id}")
        events = await db.list_events(run_id, after_seq=after, limit=limit)
        return {"run_id": run_id, "events": events, "next_after": events[-1]["seq"] if events
                else after}  # fmt: skip

    @app.get("/system", dependencies=auth)
    async def system(request: Request) -> dict:
        state = request.app.state
        report = getattr(state, "self_check", None)
        return {
            "version_id": sv.version_id,
            "name": sv.spec.name,
            "source_path": sv.source_path,
            "description": sv.spec.description,
            "roles": {
                name: {
                    "backend": r.backend,
                    "model": r.model,
                    "prompt": r.prompt,
                    "prompt_hash": sv.prompt_hashes.get(r.prompt),
                }
                for name, r in sv.spec.roles().items()
            },  # fmt: skip
            "experts": [
                {"id": e.id, "domain": e.domain, "backend": e.role.backend, "model": e.role.model}
                for e in sv.spec.experts
            ],
            "router": {"mode": sv.spec.router.mode, "decider": sv.spec.router.decider},
            "max_parallel_llm_calls": sv.spec.max_parallel_llm_calls,
            "available_backends": sorted(state.runner.backends),
            "backend_ready": state.backend_error is None,
            "backend_error": state.backend_error,
            "code": state.runner.code.model_dump(),
            "self_check": (
                {"passed": report.passed, "checks": report.checks, "problems": report.problems}
                if report
                else None
            ),
        }

    @app.get("/scenarios/{scenario_id}/sources", dependencies=auth)
    async def scenario_sources(scenario_id: str) -> dict:
        try:
            scenario = fixture.scenario(scenario_id)
        except FixtureError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
        if scenario.mode == "explore":
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"scenario {scenario_id!r} is an explore scenario and has no fixed sources",
            )
        before, after = fixture.scenario_versions(scenario_id)
        diff = diff_versions(before, after, keys=scenario.provision_keys)

        def ref(p):
            return {"article": p.article, "source_id": p.source_id} if p else None

        return {
            "scenario_id": scenario_id,
            "changes": [
                {
                    "provision_key": c.provision_key,
                    "kind": c.kind,
                    "before": ref(c.before),
                    "after": ref(c.after),
                }
                for c in diff.changes
            ],  # fmt: skip
            "sources": [
                {"source_id": s.source_id, "title": s.title, "kind": s.kind, "text": s.text}
                for s in fixture.scenario_sources(scenario_id)
            ],
        }

    async def evolution(request: Request) -> EvolutionView:
        """The lineage index: ids, origins, diff-check metrics and published decisions; no
        spec, diff or train/val bodies (those are read per candidate)."""
        db: Database = request.app.state.db
        return EvolutionView(
            await db.evolution_archive(), await db.evolution_metrics(),
            await db.promotion_decisions(), read_publish_summary(promotion_policy_path),
        )  # fmt: skip

    def unknown(version_id: str) -> HTTPException:
        return HTTPException(status.HTTP_404_NOT_FOUND, f"unknown version {version_id}")

    @app.get("/evolution/lineage", dependencies=auth)
    async def evolution_lineage(request: Request) -> Lineage:
        return (await evolution(request)).lineage()

    @app.get("/evolution/candidates/{version_id}", dependencies=auth)
    async def evolution_candidate(version_id: str, request: Request) -> CandidateDetail:
        db: Database = request.app.state.db
        view = await evolution(request)
        if version_id not in view.rows:
            raise unknown(version_id)
        vid = view.logical(version_id)
        row = await db.evolution_candidate(vid)
        metrics = await db.evolution_metrics(version_ids=[vid], splits=PANEL_SPLITS)
        return view.detail(vid, row, metrics)

    @app.get("/evolution/candidates/{version_id}/diff", dependencies=auth)
    async def evolution_diff(version_id: str, request: Request) -> CandidateDiff:
        row = await request.app.state.db.evolution_candidate(version_id)
        if row is None:
            raise unknown(version_id)
        return EvolutionView.diff(row)

    @app.get("/runs", dependencies=auth)
    async def list_runs(request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> dict:
        return {"runs": await request.app.state.db.list_runs(limit)}

    @app.post("/runs/{run_id}/ask", dependencies=auth)
    async def ask(run_id: str, body: AskRequest, request: Request) -> dict:
        row = await request.app.state.db.get_run(run_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run {run_id}")
        dossier = (row["result"] or {}).get("dossier")
        if not dossier or row["status"] not in WITH_DOSSIER:
            raise HTTPException(status.HTTP_409_CONFLICT, "this run has no impact dossier")
        # Answer with the run's own synthesis role (overrides included) when this server can.
        stored = await request.app.state.db.get_role(row["system_version"], "synthesis")
        role = RoleConfig.model_validate(stored) if stored else sv.spec.synthesis
        backends = request.app.state.runner.backends
        if role.backend not in backends:
            role = sv.spec.synthesis
        backend = backends.get(role.backend)
        if backend is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no backend to answer with")
        user = (
            "Dossier:\n" + json.dumps(_dossier_for_ask(dossier), ensure_ascii=False)
            + "\n\nQuestion: " + body.question
        )  # fmt: skip
        if ask_slots.locked():
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many questions in flight")
        try:
            async with ask_slots:
                with tracing_context(metadata={"run_id": run_id, "system_version": sv.version_id},
                                     tags=["ask"]):  # fmt: skip
                    out, _ = await asyncio.wait_for(
                        backend.call("ask", ASK_PROMPT.read_text(encoding="utf-8"), user,
                                     AskAnswer, role),
                        ask_timeout_s,
                    )  # fmt: skip
        except TimeoutError:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT, f"no answer within {ask_timeout_s:.0f}s"
            ) from None
        except LLMError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not answer: {exc}") from None
        except Exception as exc:  # noqa: BLE001 - any backend fault is a bad gateway, not a 500
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"could not answer: {type(exc).__name__}"
            ) from None
        known = _dossier_ids(dossier)
        return {"answer": out.answer, "cites": [c for c in out.cites if c in known],
                "covered": out.covered}  # fmt: skip

    if web_dist is not None and (web_dist / "index.html").is_file():
        # The console is a single-page app served from the same origin as the API.
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="console")
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse("/docs")

    return app


def _dossier_for_ask(d: dict) -> dict:
    """Compact dossier for question answering: drop provenance noise, keep ids and quotes."""
    return {
        "status": d.get("status"),
        "impacts": [
            {
                "impact_id": i["impact_id"],
                "summary": i["summary"],
                "findings": [
                    {
                        k: f.get(k)
                        for k in (
                            "finding_id",
                            "agent",
                            "provision_key",
                            "affected_actor",
                            "mechanism",
                            "impact",
                        )
                    }
                    | {"quotes": [e["quote"] for e in f.get("evidence", [])]}
                    for f in i.get("findings", [])
                ],
            }
            for i in d.get("impacts", [])
        ],  # fmt: skip
        "chains": d.get("chains", []),
        "disagreements": d.get("disagreements", []),
        "open_questions": [q.get("question") for q in d.get("open_questions", [])],
    }


def _dossier_ids(d: dict) -> set[str]:
    ids = {i["impact_id"] for i in d.get("impacts", [])}
    ids |= {f["finding_id"] for i in d.get("impacts", []) for f in i.get("findings", [])}
    return ids
