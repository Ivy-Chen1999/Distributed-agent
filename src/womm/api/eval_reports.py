"""Published train/val eval reports (``womm eval publish``) for the console's Overview.

The request model bounds every field; ``womm.eval.publish.check_payload`` refuses the whole
report on any split outside train/val, and the table constraint refuses it again. The response
carries the LangSmith experiment link built from the fixed host and stored ids, never a stored
URL.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from womm.api.traces import HOST

ExcludedReason = Literal["errored", "judge_error", "no_verdict"]


class PublishedRun(BaseModel):
    case_id: str = Field(min_length=1, max_length=200)
    split: str = Field(max_length=20)
    repetition: int = Field(ge=1, le=1000)
    excluded: ExcludedReason | None = None
    covered: int | None = Field(default=None, ge=0)
    expected: int | None = Field(default=None, ge=0)
    addressed: int | None = Field(default=None, ge=0)
    omissions: int | None = Field(default=None, ge=0)
    grounding: float | None = Field(default=None, ge=0, le=1)
    precision: float | None = Field(default=None, ge=0, le=1)
    run_status: str | None = Field(default=None, max_length=40)


class PublishedReport(BaseModel):
    system_version: str = Field(max_length=40)
    git_sha: str | None = Field(default=None, max_length=64)
    judge_version: str | None = Field(default=None, max_length=200)
    repetitions: int = Field(ge=1, le=1000)
    experiment_name: str | None = Field(default=None, max_length=300)
    experiment_tenant_id: str | None = Field(default=None, max_length=36)
    experiment_project_id: str | None = Field(default=None, max_length=36)
    runs: list[PublishedRun] = Field(min_length=1, max_length=5000)

    def as_payload(self) -> dict[str, Any]:
        data = self.model_dump()
        data["runs"] = [r.model_dump(exclude_none=True) for r in self.runs]
        for r in data["runs"]:
            if "excluded" not in r:
                for key in ("covered", "expected", "addressed", "omissions"):
                    r.setdefault(key, 0)
        return data


def experiment_url(tenant_id: str | None, project_id: str | None) -> str | None:
    if not tenant_id or not project_id:
        return None
    return f"{HOST}/o/{tenant_id}/projects/p/{project_id}"


def report_out(row: dict) -> dict[str, Any]:
    return {
        "report_id": row["report_id"],
        "system_version": row["system_version"],
        "created_at": row["created_at"],
        "git_sha": row["git_sha"],
        "judge_version": row["judge_version"],
        "repetitions": row["repetitions"],
        "experiment_name": row["experiment_name"],
        "experiment_url": experiment_url(row["experiment_tenant_id"], row["experiment_project_id"]),
        "summary": row["summary"],
    }
