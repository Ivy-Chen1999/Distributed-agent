---
date: 2026-09-28
topic: womm-phased-requirements
source: brainstorm/Plan.docx
---

# WOMM — 分阶段需求（v0 本周五 / v1 两个月）

## Problem Frame

WOMM 是面向 EU 法规影响评估（RIA）的自进化多智能体系统，完整愿景见 `brainstorm/Plan.docx`。本文不重复愿景，只把它切成两个可交付阶段，并锁定技术取舍：

- **v0 — 2026-10-02（周五）**：端到端骨架。证明 "Regulatory Change → Provision → Finding → Evidence → Source" 这条链路和评估基线能在线上跑通；自进化只留接口。
- **v1 — 约 2026-11-27**：Plan 的 Must build + Adaptive 全部落地，演示一次完整 improvement cycle，最强形态是"新专家被创建并经 holdout 晋升"（Plan §16）。

约束：EU AI Act 结构化数据由同事负责，尚未交付；v0 不能被它阻塞。

技术栈（已定）：LangGraph · Pydantic v2 · LangSmith · Jev（TypeSafe AI）· Railway · 可插拔 LLM 后端（开发期用本地 Claude Code 订阅，后续接 OpenAI / Anthropic API key）。

---

## Actors

- A1. 分析师 / demo 观众：提交 proposal，阅读 Impact Dossier，（v1）给出 accept/reject/edit 反馈。
- A2. 同事（数据负责人）：交付 EU AI Act 多版本结构化数据，需满足本项目定义的数据契约。
- A3. LLM agents（可插拔后端）：Impact Planner、专家、Synthesis、（v1）Improvement Planner、LLM judge。
- A4. Jev：有界决策（routing、publish/deliver/relate、escalation），返回带校准概率的类型化答案。
- A5. 评估 / 晋升流程：跑 golden cases，产出指标，（v1）决定候选版本 promote/reject。

---

## Key Flows

- F1. RIA 运行（v0 起）
  - **Trigger:** A1 通过 API 提交一个 proposal（v0 为 fixture）
  - **Actors:** A1, A3, A4
  - **Steps:** 结构化版本 → provision 级 diff → Impact Planner 定调查重点 → Jev Router 决策（shadow 下只记录）→ 专家并行产出 ImpactFinding → 写入 Shared Impact Board → Synthesis → 引文校验 → Impact Dossier
  - **Outcome:** Impact Dossier 可查询；每条 finding 可追溯到 provision 和 source；每个 Jev 决策有 DecisionRecord
  - **Failure path:** 单个专家失败时降级（Impact Dossier 标注缺失的专家），不让整次运行失败；引文校验不通过的 finding 降为 open question
  - **Covered by:** R1–R10, R15–R16, R32

- F2. 评估（v0 基线，v1 扩展）
  - **Trigger:** 手动或定时，对某 SystemVersion 跑 golden cases
  - **Actors:** A5, A3
  - **Steps:** 对每个 case 运行 F1（官方 IA 对 agent 不可见）→ 与参考 IA 子节比对 → 计算 coverage / grounding / omissions / reasoning / efficiency → 记录实验并按 system_version 标记 → 失败项写入 Failure 记录
  - **Outcome:** 该版本有可对比的指标基线
  - **Covered by:** R11–R13, R14a, R14b, R22–R24, R34, R37

- F3. 自进化周期（v1）
  - **Trigger:** Failure Memory 中出现重复模式
  - **Actors:** A3, A5
  - **Steps:** Improvement Planner 诊断 → 提出配置 diff（prompt / routing / research policy / agent 组成）→ 生成候选 SystemVersion（记录 parent）→ 在 train/val 上回放 → 在 sealed holdout 上评估 → promote 或 reject（结果写入晋升审计记录，Planner 不可见）
  - **Outcome:** 版本谱系可追溯；只有 holdout 上显著更好的候选被晋升
  - **Covered by:** R25–R30

---

## Requirements

### v0（2026-10-02 起，分两步）

按优先级交付，**周五必须完成 P1–P2**，P3–P5 周五有余力就做，否则进入下周的 v0.1。各项降级方案：

1. P1 本地跑通 RIA 链路，产出 Impact Dossier（R1–R9, R32）
2. P2 评估基线（R11–R13, R14a）。周五在 `claude_code` 后端上跑，定性为开发期冒烟基线。降级：只做 1 个 golden case
3. P3 Railway 部署（R15）。降级：本地演示
4. P4 Jev shadow（R10）。降级：用 stub 记录器写 DecisionRecord
5. P5 Failure 记录（R14b）和噪声测量（R34）。可推迟到 v0.1；正式基线和 R34 在 api 后端上跑，归入 v0.1

R16 tracing 从 P1 起默认开启。


**数据契约与 fixture**
- R1. 定义 Regulation → Version → Provision 的数据契约（字段按 Plan §4），并作为同事数据的交付格式。每条 provision 带跨版本稳定的 provision key（AI Act 从提案到终版大量重新编号，如罚则 Art.71 → Art.99）：v0 手工维护对照表，同事的数据契约中为必填字段；同事数据到位后只需替换数据源，无需改 agent 代码。
- R2. v0 自建最小 fixture，分两类场景：
  - **评估场景**：输入为"无先前版本 → COM(2021)206"（整份提案视为新增变化），与官方 IA SWD(2021)84 的评估对象一致，用于 golden case 打分。
  - **Demo 场景**：COM(2021)206 → Reg (EU) 2024/1689 中少量对应条款（候选：SME / 合规义务 / 罚则相关）的 diff，展示 provision 级 diff 能力；官方 IA 未评估这些修订，因此该场景不做基于 IA 的 coverage 打分。
- R3. provision 级 diff 为确定性工具，按 provision key（而非条号）匹配，输出新增 / 删除 / 修改的 provision 列表，作为 Planner 输入。

**RIA 主链路**
- R4. Impact Planner（LLM）基于 diff 输出调查重点。
- R5. v0 专家为 Legal、Fiscal、Stakeholder 三个，并行执行，各自输出结构化 ImpactFinding（provision、affected_actor、impact、mechanism、evidence、confidence，按 Plan §6；agent 由系统在 provenance 中填写，见 R6）。每个专家自行捕获异常（LLM 错误、超时、结构化输出重试耗尽），以 ExpertFailure 条目写入 Board 而不向上抛出，保证 F1 的降级路径。
- R6. `finding_id` 与 provenance（agent、system_version、prompt 版本、model、round）由系统填写，不由 LLM 生成。
- R7. Shared Impact Board v0 为只追加的共享 findings 集合；无 Jev publish/deliver/relate，Synthesis 读全部。
- R8. Synthesis 合并重复、保留分歧、串联影响链，输出 Impact Dossier（impacts · evidence · open questions）。
- R9. v0 证据来源限定为 COM(2021)206 条款 + 其解释备忘录（剔除引用 IA 结论的章节，如 "Results of impact assessments"），每份来源分配 source_id；专家只能引用这些来源，官方 IA 不在其中。引文校验 v0 为确定性原文匹配：每条 evidence 必须带 source_id + 原文引用，引用能在源文本中（归一化后）找到才算 supported；否则降为 open question。

**Jev 与决策日志**
- R10. Jev Router 以 **shadow** 模式运行：对每个专家给出"是否相关"及概率，写入 DecisionRecord（decision_point、input、decision、probability、mode、system_version），但所有专家照常执行。Router 模式支持 off / shadow / active 配置。shadow 下 Jev 调用设短超时；超时或报错时运行照常继续，DecisionRecord 记 `decision=error`、probability 为空并附错误原因。

**评估基线**
- R11. 2–3 个 golden case，输入为 R2 评估场景，参考答案取自 AI Act 官方影响评估的可管理子节（Plan §9 建议做法）；参考 IA 不进入 agent 可访问的任何数据源。
- R12. v0 指标：coverage（LLM judge 比对预期影响）、grounding（R9 通过率）、efficiency（耗时、token、成本）。omissions 由 judge 按与 v1 门槛相同的形式给出数值分，reasoning 可先定性。v0 的 grounding 只衡量引文存在率，不衡量引文是否支撑结论。
- R13. 每次评估作为一个 LangSmith experiment，按 system_version 打 metadata，可在 UI 中横向对比。
- R14a. 引入 SystemVersion 概念（prompt、后端与模型、router 模式、agent 组成、parent），v0 只有一个版本。
- R14b. 未通过的 case 记录为 Failure 条目（为 v1 预留）。
- R34. 测量运行间噪声：在 v1 评估将使用的 api 后端和模型上，对同一 golden case 重复运行 3 次，记录 coverage / grounding / omissions 的波动，作为 v1 晋升门槛的输入。`claude_code` 上的数据只作开发参考。

**运行与部署**
- R15. 部署到 Railway：API 服务 + Postgres；一次 RIA 运行作为后台任务执行，API 返回任务 ID 可轮询状态和结果（不依赖单个长 HTTP 请求）。
- R16. 全链路 LangSmith tracing。

**LLM 后端（v0 起）**
- R32. 所有 LLM 调用经统一后端接口；v0 至少两个实现：`claude_code`（本地 Claude Code 订阅，经 CLI 的结构化输出，仅本地开发可用）与 `api`（OpenAI / Anthropic key，线上与正式评估使用）。每个角色（Planner、专家、Synthesis、judge）的后端与模型写在 SystemVersion 中，切换只改配置；每个 LangSmith 实验按后端与模型打标签，跨后端的指标不直接比较。
  - `claude_code` 后端必须在隔离模式下调用：禁用全部内置工具与 MCP，不加载用户和项目的 settings 与 CLAUDE.md，使用显式 system prompt，并在不含 golden 参考答案的临时目录下运行，以保证 R9 / R11 的数据隔离。
  - 每次调用手动接入 LangSmith trace，并把 CLI 输出中的 usage 与 cost 写入 metadata。

### v1（约 2026-11-27）

**数据与证据**
- R17. 接入同事交付的 AI Act 多版本数据（提案 → 修订 → 合并版），覆盖全部条款，支持按需检索而非整文入上下文。
- R18. Evidence 专家与 Workforce 专家上线（共 5 个）；外部证据经 Exa 发现，每条保留来源与 provenance。
- R19. 引文校验三层：原文匹配 → 支持度打分（MiniCheck 类小模型）→ 仅模糊情况交 LLM judge。

**协作层**
- R20. Jev publish / deliver / relate 上线：finding 是否发布、投递给哪些专家、与已有 finding 的关系（supports / contradicts / supersedes）；矛盾保留不删。收到投递的专家进入下一轮，直到无新投递或达到轮数上限。
- R21. Router 与协作决策先 shadow，达到可靠性门槛后切 active；门槛基于 DecisionRecord 与评估结果的校准曲线。

**评估**
- R22. golden cases 扩充至约 15–30 个，来源为 EU Better Regulation / Cellar 中"COM 提案 + 官方 IA"配对；RSB 意见作为 omissions 标注来源。
- R23. golden cases 分 train / val / holdout 三份；holdout 参考答案只存于自有数据库，仅晋升流程可访问，Improvement Planner 不可见。
- R24. 输入提案中剥离引用 IA 结论的章节（如解释备忘录中的 "Results of impact assessments"），防止答案泄漏；holdout 优先选模型训练截止之后发布的 IA。
- R33. AI Act 只有一份官方 IA，其余 golden case 来自其他 EU 提案：这些提案按 R1 契约自动导入（Cellar / Formex 解析），输入形态统一为"无先前版本 → 提案"。负责人需在 v1 规划时指定（同事的数据范围只含 AI Act）。

**自进化**
- R25. Failure Memory：只收 train/val 上的失败，结构化记录（哪个 agent、哪类影响、哪些 case、频次），可按模式聚合。
- R26. Improvement Planner 只能修改配置（prompt、routing 表、research policy、agent 注册表），不能改代码。
- R27. 每个候选 SystemVersion 存档（parent、diff、各层指标），不只保留当前最优。
- R28. 晋升门槛：holdout 上总体提升超过重复运行噪声（bootstrap CI），且 coverage / grounding / omissions 的回退均不超过各自的容忍度。holdout 最小规模、每 case 重复次数和容忍度在 v1 规划前根据 R34 的数据确定；如果数据量撑不起统计门槛，就改用弱门槛（方向性提升 + 不超过容忍度的回退），并在 demo 中如实说明。未达门槛的候选 reject。晋升结果（候选 id、通过/失败、聚合差值）只写入仅晋升流程可读的审计记录，不进入 Improvement Planner 可读的 Failure Memory，避免 holdout 信息泄漏。
- R29. 批量回放作为后台任务，可在服务重启后恢复。
- R37. 条款级 diff 回归检查：把 R2 demo 场景（提案 → 终版）手工写好参考答案，每个 SystemVersion 都跑一遍并记录分数。这个检查不参与晋升判定，但分数变差要在评估页和晋升记录中可见。
- R30. Demo：一次完整 cycle，目标为"现有专家反复漏掉某类影响 → 提出新专家 → holdout 改善 → 晋升"；若新专家路线在截止前不稳定，回退为 prompt 级改进 cycle。

**Demo 前端**（设计由用户负责，简单版；v0.1 实现页面 1–2，v1 实现页面 3–4）
- R35. 后端为前端提供运行事件流（节点开始/完成/失败、Jev 决策、finding 写入 Board）和查询接口（Impact Dossier、评估结果、版本谱系）。前端只负责展示，不承载业务逻辑。
- R36. 页面与必须展示的内容：
  1. **运行页**：选择 fixture 场景（评估场景 / diff demo 场景）并提交；多 agent 结构图，每个节点实时显示状态（等待 / 运行中 / 完成 / 失败）；Router 节点显示 Jev 的判断和概率，带 shadow 标记；Board 实时显示 findings 流，按专家区分颜色。
  2. **Impact Dossier 页**：影响按受影响方或领域分组；每条影响可展开溯源链：法规变化 → 条款（条号 + 原文）→ 作用机制 → 证据引用（在来源原文中高亮）→ 来源；显示 confidence；有矛盾的 finding 并排显示；open questions 单独成区；失败的专家要标出（来自 ExpertFailure）。diff demo 场景额外提供提案 / 终版条款对照视图。
  3. **评估页**：每个 SystemVersion 的 coverage / grounding / omissions / efficiency；逐 case 显示命中和漏掉的预期影响。
  4. **进化页（v1）**：版本谱系树；候选 vs 现役的配置 diff（prompt、agent 组成）；train/val 与 holdout 分数；promote / reject 结果；新专家被创建时单独突出显示。
  - 每个页面都要有加载中、空数据、出错三种状态。

**人工审核**
- R31. 分析师可对 finding 做 Accept / Reject / Edit / Missing impact / Weak evidence（Plan §14），反馈进入 golden cases 与 Failure Memory。

---

## Acceptance Examples

- AE1. **Covers R9.** Given 某 finding 的引用文本在 source 中不存在，when 引文校验运行，then 该 finding 不出现在 Impact Dossier 的 impacts 中，而是出现在 open questions 并注明 "evidence unresolved"。
- AE2. **Covers R10.** Given Router 为 shadow 模式且判断 Fiscal 不相关，when 运行，then Fiscal 仍执行并产出 findings，DecisionRecord 中记录 `decision=not_relevant, mode=shadow` 及概率。
- AE3. **Covers R23.** Given Improvement Planner 正在生成候选，when 它查询评估数据，then 只能看到 train/val 的失败摘要，看不到 holdout 的 case 或参考答案。
- AE4. **Covers R25, R28.** Given 候选在 holdout 上 coverage +8% 但 grounding 下降，when 晋升判定，then reject，原因写入晋升审计记录；Improvement Planner 看不到该 holdout 结果。

---

## Success Criteria

- **v0（周五）**：本地提交 fixture proposal，拿到带引文的 Impact Dossier；LangSmith 中能看到完整 trace，以及至少一个带 system_version 的冒烟 baseline experiment（`claude_code` 后端）。
- **v0.1**：同样的流程在 Railway 上可演示；在 api 后端上跑出正式 baseline 和 R34 噪声数据；有 Jev shadow 决策记录；demo 页面 1–2（R36）可用。
- **v1**：一次完整 improvement cycle 可复现演示，晋升决策有 holdout 数据支撑；同事数据接入时 agent 层无需改动（验证 R1 契约）。
- **交接质量**：`ce-plan` 不需要再发明产品行为，只需决定模块划分、schema 细节、任务拆分。

---

## Scope Boundaries

- v0 不做：Jev publish/deliver/relate、Workforce/Evidence 专家、Exa 外部检索、Improvement Planner 及任何自动改进、人工审核 UI、MiniCheck。
- v1 不做（Plan 的 Experimental 中除 R30 外）：agent 合并/删除、图结构重组、评估器自我改进。
- 不做完整产品界面，只做 R36 的 demo 页面；v1 人工审核优先复用 LangSmith annotation queues。开发期观察图结构和状态用 LangGraph Studio。
- 晋升只优化整份提案的评估；条款级 diff 能力只通过 R37 回归检查监控，不参与晋升判定。
- 分析功能与完整多版本数据聚焦 AI Act；其他 EU 提案只作为评估语料（R33），不做它们的多版本支持。
- 不让系统改写自身代码（仅配置级进化）。

---

## Key Decisions

- **v0 = 端到端骨架，不含进化，分步交付**：周五只保证本地主链路和评估基线，部署和 Jev 可以顺延到 v0.1，节奏以做扎实为先；进化 demo 在 case 太少时没有统计意义。
- **自建 fixture + 数据契约解耦同事数据**：避免阻塞，且让 R1 契约成为双方接口。
- **自定义 LangGraph StateGraph，而非 supervisor 模式**：流程固定、Router 是一次分类决策；专家并行用 Send + reducer。v0 专家是经 R32 接口的单次结构化输出调用，不带工具，兼容 `claude_code` 后端；`create_agent` 只在 v1 需要检索工具、并且在 api 后端上时引入。（Context7：LangChain multi-agent 文档 "Custom workflow" 模式）
- **图由 SystemVersion 构建**：agent 组成决定图结构，prompt / 模型 / router 模式通过运行时 context 注入；候选版本回放无需改代码。
- **Shared Impact Board 放在图状态中**，而非跨线程 Store：需被 checkpoint 和回放。
- **结构化输出用 Pydantic v2 严格模型**（禁止额外字段），校验失败自动重试；ID 与 provenance 由系统填。
- **LLM 后端可插拔，不锁定厂商**：开发期用本地 Claude Code 订阅（省 API 成本），拿到 OpenAI key 后按配置切换。两家主力档价格接近（Sonnet 5 $2/$10 vs GPT-5.6 Terra $2/$12 每百万 token），差异主要在轻量档（适合 judge），按效果选即可。限制：订阅后端无法部署到 Railway、有 rate limit、只支持单次调用 + 结构化输出（无 LangChain 原生 tool calling）；个人订阅用于开发需确认公司政策。
- **Jev 置于统一决策接口之后**：产品发布仅两周，性能与校准数据主要为厂商自报；需可切换到备选实现（如开源 openjev 或小模型 logprob 打分），并先 shadow 验证校准。
- **SystemVersion / DecisionRecord / Failure Memory / holdout 答案存自有 Postgres**；LangSmith 负责 trace、实验、judge 打分、prompt 提交与标注 UI。原因：LangSmith dataset split 不做访问隔离，且保留期有限。
- **FastAPI + 后台 worker 部署在 Railway，不用 LangGraph Agent Server**：省去 Redis 与 license；Railway 单请求上限约 15 分钟，长任务必须异步。保留 `langgraph.json` 以便本地用 Studio。
- **v1 prompt 候选优先借鉴 GEPA**（反思失败 trace → 提出修改 → Pareto 选择），晋升门槛与候选存档自建（参考 ADAS / DGM 的存档 + 谱系，MASS 的先 prompt 后拓扑顺序）。

---

## Dependencies / Assumptions

- 同事的 AI Act 数据预计在 v1 期间交付，且能按 R1 契约提供；若字段不符，需一次映射层适配。
- **P1 开工前**：确认公司政策允许用个人 Claude Code 订阅做开发；如果不允许，P1 之前就要拿到 API key。
- OpenAI / Anthropic API key 需要在 v0.1 之前到位（P3 部署、正式 baseline 和 R34 都依赖它）。
- Jev API 可用且在 Railway 可访问；RIA 场景的校准效果未验证（假设）。
- AI Act 官方影响评估 SWD(2021)84 的子节足以支撑 v0 的 2–3 个 golden case（假设，需在规划阶段确认选哪几节）。
- v1 的 15–30 个 IA 配对可从 Cellar / Better Regulation 获取（调研显示可行，数据格式与覆盖年份未实际验证）。
- 本机未安装 `gh`，本次 GitHub 代码搜索未执行；先例调研来自 Web 搜索与 Context7。

---

## Outstanding Questions

### Resolve Before Planning

（无）

### Deferred to Planning

- [Affects R9, R24][Needs research] 解释备忘录除了 "Results of impact assessments"，利益相关方咨询、比例性、预算影响等章节也会转述 IA 结论，需要确定剔除范围。
- [Affects R12][Technical] coverage judge 的匹配粒度：官方 IA 按政策选项和受影响群体描述影响，不按条款描述。
- [Affects R2, R11][Needs research] 评估场景选哪几组条款能对应 SWD(2021)84 的某一子节；demo 场景选哪几条能产生有意义的修订 diff。
- [Affects R10][Technical] Jev routing 用单个 Choice 问题还是每个专家一个 yes/no 问题；multi-label 场景下哪种更合适。
- [Affects R12][Technical] coverage judge 的 rubric 与匹配粒度（按 impact 语义匹配 vs 按 actor+mechanism 匹配）。
- [Affects R15][Technical] v0 任务队列用 Postgres `SKIP LOCKED` 还是 FastAPI 进程内后台任务（v0 规模下后者可能够用）。
- [Affects R6, R32][Technical] 各后端的结构化输出策略（CLI `--json-schema` / 原生 / tool calling），以及 v1 专家需调用检索工具时 `claude_code` 后端的兼容性。
- [Affects R20][Technical] 多轮 board 的终止条件与轮数上限。
- [Affects R28, R34][Needs research] 根据 v0 实测噪声，确定 holdout 最小规模、重复次数和每项指标的容忍度（v1 规划前完成）。
- [Affects 全部][Needs research] 安装的 langgraph / langchain 版本中，Context7 报告的较新 API（Send 超时策略、节点 error_handler、DeltaChannel、graceful drain）是否可用。

---

## Next Steps

→ `/ce-plan` 生成 v0 实现计划（v1 在 v0 交付后单独规划）
