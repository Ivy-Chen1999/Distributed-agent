# UI 数据接口（demo 前端）

页面需求见 `docs/brainstorms/2026-09-28-womm-phased-requirements.md` 中的 R35 / R36。

- `schema/*.schema.json`：由后端 Pydantic 模型自动导出，是字段的唯一依据。重新生成：`uv run python scripts/export_ui_contract.py`
- `sample_run.json`：一次运行的示例数据，内容是虚构的，可以直接当 mock 用

## 页面与字段对应

| 页面 | 数据来源 | 关键字段 |
|---|---|---|
| 运行页：结构图与节点状态 | v0.1 的 `GET /runs/{id}` 与 `/events`（U11）；在此之前用 `status` + `decisions` | `status`、`decisions[].subject / decision / probability / mode`（shadow 标记）、`board[].agent`（按专家区分颜色） |
| Impact Dossier 页 | `dossier` | `impacts[].summary`、`impacts[].findings[]`（溯源链：`provision_key` → `mechanism` → `evidence[].quote` → `evidence[].source_id`）、`confidence`、`disagreements`（并排展示）、`open_questions`、`failed_experts`、`chains` |
| 评估页（v1） | LangSmith 实验 | 待定 |
| 进化页（v1） | SystemVersion 谱系 | 待定 |

状态取值：`queued`、`running`、`succeeded`、`degraded`（部分专家失败或 Synthesis 失败）、`failed`、`no_changes`。每个页面都要有加载中、空数据、出错三种状态。

注意：Impact Dossier 里的文字由 LLM 生成，渲染时必须转义，不能当 HTML 插入。
