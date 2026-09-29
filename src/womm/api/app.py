"""HTTP API (U11): submit runs, poll status, read live node events.

Every endpoint except /health requires `Authorization: Bearer <WOMM_API_TOKEN>`; the app refuses
to start without a token. Run with: `uvicorn --factory womm.api.app:create_app`.
"""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from womm.api.db import Database
from womm.api.jobs import JobRunner, QueueFull
from womm.backends import prepare_backends
from womm.config import REPO_ROOT, ConfigError, Settings, load_settings
from womm.data.fixtures import Fixture, FixtureError, load_fixture
from womm.decisions.factory import make_decision_service
from womm.identity import code_identity
from womm.llm.base import LLMBackend
from womm.models.system_version import SystemVersion, load_system_version

MIN_TOKEN_LENGTH = 16
FINISHED = {"succeeded", "degraded", "failed", "no_changes"}
WITH_DOSSIER = {"succeeded", "degraded", "no_changes"}


class RunRequest(BaseModel):
    scenario_id: str


class RunAccepted(BaseModel):
    run_id: str
    status: str


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
                runner_backends, cli_version, _ = await prepare_backends(sv, settings=settings)
            except Exception as exc:  # noqa: BLE001 - serve /health and report the problem
                runner_backends, backend_error = {}, f"{type(exc).__name__}: {exc}"
        app.state.db = db
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
        return [s.model_dump(mode="json") for s in fixture.scenarios.values()]

    @app.post("/runs", dependencies=auth, status_code=status.HTTP_202_ACCEPTED)
    async def submit(body: RunRequest, request: Request) -> RunAccepted:
        if err := request.app.state.backend_error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"backend unavailable: {err}")
        try:
            run_id = await request.app.state.runner.submit(body.scenario_id)
        except FixtureError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
        except QueueFull as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from None
        return RunAccepted(run_id=run_id, status="queued")

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
        if row["status"] in FINISHED:
            out["grounding"] = result.get("grounding")
            out["decisions"] = result.get("decisions")
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

    return app
