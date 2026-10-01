from womm.api.db import Database
from womm.eval.evaluators import CaseScore
from womm.eval.run_eval import EvalReport, persist_failures


async def test_persist_failures(database_url):
    report = EvalReport(
        system_version="sv_x",
        metadata={"git_sha": "abc", "repetitions": 1},
        scores=[CaseScore(case_id="c", scenario_id="s", outcome="scored", coverage=0.1,
                          grounding=1.0, omissions_addressed=1.0, run_id="r1")],
    )  # fmt: skip
    assert await persist_failures(report, database_url) == 1
    db = Database(database_url)
    await db.open()
    try:
        (row,) = await db.list_failures("sv_x")
        assert (row["category"], row["run_id"], row["git_sha"]) == ("low_coverage", "r1", "abc")
    finally:
        await db.close()
