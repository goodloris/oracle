# v7 确认性实验预注册（草稿，未冻结）

> **状态：草稿。** 本文尚未冻结。冻结由操作员执行：提交本文件的最终版本，把该提交的 SHA256（`sha256sum prereg/preregistration-v7.md`）和提交时间贴到公开 issue，此后正文不再修改，只能在“附录”追加。冻结前必须填写附录 A 的冻结清单。

## 背景

v6（公开仓库 `docs/v6-confirmatory-results.md`）显示：内核封装的约定（K）显著改善，但完整验收成功率 A 两组持平（42/120 对 44/120）。原因是作者必须声明的约定（D，主要是 outbox 事件）在 action 组只有 0.39：模型照抄与新操作最相似、但不发事件的既有声明。PR #27（`poc/operation-semantics`，`fad8adf`）把事件、指纹字段、审计/历史、职责分离和拒绝语义移进经审查的操作目录（`internal/opdef`），内核（`internal/opkernel`）只按目录名执行。

v7 检验：**由平台预先给定的权威业务语义，能否把局部收益转化为 A 的提升**；并把收益拆成两种来源：

- 权威事实本身（作者知道正确语义）；
- 内核执行（作者不需要实现这些语义）。

## 设计

### 任务（2 个，不公开）

任务说明、起点、Oracle 只存在于私有仓库；公开仓库和公开目录中没有 v7 条目。

| 任务 | 类型 | 接口 | 隐含约定 |
|---|---|---|---|
| T1 `withdraw.v7` | 状态迁移（单目标） | `POST /purchases/{id}/withdraw`，`{"reason"}`，requester 本人、submitted → withdrawn | audit（ApprovalAudit 行含 reason）、request-hash（reason 不进指纹）、outbox（`purchase.withdrawn`）、uniform-denial |
| T2 `bulk-cost-center.v7` | 字段修改（批量、带参数） | `POST /purchases/bulk-cost-center`，`{"ids","costCenter"}`，approver、他人的 submitted，值须为本租户 active 的成本中心 | history（CostCenterHistory 行与并发链）、request-hash（costCenter 进指纹）、outbox（`purchase.cost_center_changed`）、uniform-denial |

两个任务的起点相同：v6 全部操作已经实现（分级批量审批、批量驳回、重新提交、会签、批量改派）。线索分散且互不冲突（每条约定的线索清单见 `tasks/<task>/cohort.json`，控制 `clues_present` 校验）。

### 三组（cell）

| 组 | 既有 Action | 新操作的语义从哪里来 | 谁执行语义 |
|---|---|---|---|
| `go-manual` | 手写 | 只能从散落的代码、SCHEMA.sql、notify/ledger 中推断 | 作者手写 |
| `go-manual-doc` | 手写（与 go-manual 字节相同） | 另附只读的 `docs/OPERATIONS.md`：目录全部条目（JSON 与 go-action-opdef 的 `opdef.All()` 逐字段相同）及字段语义说明 | 作者手写 |
| `go-action-opdef` | 通过 `opkernel` 执行 | 受保护的 `internal/opdef` 已含本任务条目 | 内核按条目执行；作者只写适配，不能选择事件或豁免 |

三组提示词只有 capability 一行不同（控制 `prompts_differ_only_in_capability`）。go-manual-doc 的导出等于 go-manual 加一份文档（控制 `doc_cell_is_manual_plus_document`），文档由 `harness/opdoc.py` 从目录编译生成（控制 `document_equals_catalog`）。

### 模型与样本

- 模型：claude-opus-5.5、claude-haiku-4.5、gpt-5.6-luna、qwen3-coder-next；engine kiro，effort medium（不支持 effort 的照实记录），预算 600 秒。
- 每个任务 × 模型 10 块，每块 3 组，组内顺序由计划种子随机。共 **2 × 4 × 10 × 3 = 240 个 trial**，8 个通道（任务 × 模型）。
- 每个 trial 只做一次首次生成，不重试、不修复、不替换。

### 隔离与跨主机流程

- 验收主机 `10.30.11.112:/opt/poc-oracle` 执行 `fixtures`、`plan`、`prepare`；私有 seed 只留在主机（`runs/<run>/private/`）。
- kit 经 scp 拉到操作员 Mac 上私有仓库之外的目录；`scripts/generate.sh` 在 `isolate_net.py`（文件隔离 + 模型端点白名单）中运行公开 phase3 worker。deny 列表包含私有仓库 `/Users/administrator/poc-v7-private`、`~/.paseo`、公开 checkout、`~/.kiro`、`~/.codex`、本 run 的其他 kit 和结果。
- 结果（bundle、receipt、network.log）推回主机后执行 `restore`、`evaluate`、`summarize`。
- 审计规则沿用 v6 附录 A/C/F：`retrieval-attempt`、`network-denied`（遥测主机不计）。

## 结局变量（来自冻结 Oracle 的逐 trial 结果）

- **A**（主要）：全部用例通过（`outcome == passed`）。
- **E**：显式契约（含既有 API 回归 18 例）全部通过。
- **K**：四条隐含约定全部通过。
- **D**：outbox 约定通过（与 v6 的 D 同名同义，便于对照；在 go-action-opdef 中按构造由目录决定）。
- **safety**：每个 trial 的结构化字段，七类各给计数和用例列表（定义见 `docs/v7-design.md`）：`unauthorized_write`、`partial_batch`、`side_effect_atomicity`、`double_apply`、`wrong_commit`、`service_error`、`undecidable`。分类只依据每一步记录的状态码与数据库前后摘要（逐表行集合 hash 与差异，并发步骤同样记录），不读错误字符串。
- **S**：生成耗时，worker 的 `generation_seconds`。
- **C**：生成消耗的 credit，worker 的 `usage.credits`。

`scope-violation`、`budget-exceeded`、`candidate-error` 是已观察结局，A=E=K=D=0。`runtime-blocked`、`provider-error`、`protocol-error`、`infrastructure-blocked` 为未测量。

## 假设与检验

所有检验都把 8 个通道的块合并，按块配对。一个块对某个假设有效，当且仅当该假设涉及的两组在这一块中都是已观察结局。**每个假设各自报告有效块数（分母）**，最多 80。

| 假设 | 比较 | 检验 | α 与校正 |
|---|---|---|---|
| **H1（主）** | A：go-action-opdef 高于 go-manual | 精确配对符号检验（McNemar 精确），单侧；不一致块 = opdef 通过而 manual 未过，对比 manual 通过而 opdef 未过 | α = 0.05，单独检验，不参与校正 |
| H2（次） | A：go-manual-doc 高于 go-manual | 同上，单侧 | H2、H3 组成次要族，Holm 校正，族 α = 0.05 |
| H3（次） | A：go-action-opdef 高于 go-manual-doc | 同上，单侧 | 同上 |

解读规则（事先约定）：

- H1 成立：可以表述为“在本设计下，平台给定的权威语义 + 内核执行提高了完整验收成功率”。H1 不成立时如实报告，不追加子集分析来寻找显著结果。
- H2 成立而 H3 不成立：收益主要来自权威事实本身；H3 成立而 H2 不成立：收益主要来自内核执行；两者都成立：两种来源都有贡献。H2、H3 的解读与 H1 是否成立无关，但不能用来替代 H1。
- 任务和约定由我们设计，结论不能推广为通用平台的收益。

## 次要与描述性分析（不校正，仅供参考）

- 每个通道、每组的 A、E、K、D 率；各层的配对符号检验 p 值（探索性）。
- safety：每组七类的 trial 数与用例数；逐个列出 `unauthorized_write`、`partial_batch`、`double_apply`、`wrong_commit` 不为 0 的 trial。
- S：块内差的单侧 Wilcoxon 符号秩检验（opdef 对 manual），探索性。
- Verifier 样本外评估：私有构建的 `appctl operations`（公开 `fad8adf` + 私有目录叠加，目录摘要见附录 A）对全部已评分候选的判定，与 Oracle 隐含约定结果对照，报告 TP/FP/FN 与 unverifiable 数。规则不再调整。
- 与 v6 对照：T1/T2 不是 v6 任务，只做方向性对照，不做检验。

## 成本指标

对每个“组 × 通道”和每组合计：

- **每个正确结果的 credit** = Σ C（该组所有**开始生成**的 trial，含 budget-exceeded、scope-violation、candidate-error，以及 usage 已知的 provider-error）÷ 该组 A=1 的 trial 数。
- **每个正确结果的时间** = Σ S（同一集合）÷ A=1 的 trial 数。
- 同时报告只用已观察 trial 的版本（`ctl.py summarize` 的 `credits_per_correct`、`seconds_per_correct`）。
- 区间：以块为单位做 10 000 次 bootstrap（种子 20261116），报告 95% 百分位区间。A=1 为 0 的格子记为“无正确结果”，不报比值。
- usage 缺失的 trial 单独计数；某组缺失超过 5% 时，该组的 credit 指标标为不完整。

## 规模与 credit 估算

v6 每个 trial 的平均 credit：opus ≈ 2.35，其余三个模型 ≈ 0.7。v7 每个模型 60 个 trial：

| 模型 | trial | 估算 credit |
|---|---:|---:|
| opus | 60 | ≈ 141 |
| haiku / luna / qwen | 3 × 60 | ≈ 126 |
| **合计** | 240 | **≈ 267** |

v7 的起点比 v6 多一个会签实现，go-manual-doc 还要多读一份文档，单个 trial 可能更贵。按 +30% 的余量，**预算上限按 350 credit 准备**；另加 READY 预检（每个 trial 一次，消耗可忽略）。

## 用量上限与续跑规则

v6 中途触发过月度上限（v6 附录 E/F）。事先约定：

- **R1**：预检（READY）被拒的 trial 从未开始生成。`generate.sh` 遇到预检被拒即停止该通道；配额恢复后用同一计划、同一 kit、按原顺序续跑，即为首次生成。被拒时的结果目录归档到 `<run>/preflight-blocked-<日期>/`，不删除。
- **R2**：生成途中被上限打断的 trial 记为未测量（provider-error），**不重跑**；其所在块只对涉及该组的假设失效。
- **R3**：服务区域或端点变化时，先用 discover 模式确认主机，设置 `KIRO_ALLOW_HOSTS`，重跑网络隔离控制并全部通过后才续跑，在附录中记录。
- **R4**：首轮与续跑相隔超过 6 小时的，报告按批次拆分描述（主分析不分层）。
- **R5**：通道之间并行不超过 4 个；同一块的三组在同一通道连续执行。若总消耗达到 350 credit 仍未完成，停止所有通道并在附录中记录；剩余 trial 记为未测量，不改变假设与检验。

## 排除规则

- 主分析不排除任何已观察 trial。
- 敏感性分析：排除 `retrieval-attempt` 的 trial（连同其块中参与同一假设的另一组），重跑 H1–H3。
- 冻结后发现 Oracle 实现错误时：只能在附录中说明，并同时报告修正前后两版结果；不得只报告修正版。

## 附录（只追加）

### 附录 A：冻结清单（冻结前由操作员填写）

- 私有仓库提交 SHA：`________`；`git ls-files harness tasks benchmark` 的树 hash：`________`。
- Verifier：公开 commit `fad8adfa89edb753c55410518d531e21489f7d9b`（公开目录摘要 `sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055`）；私有目录摘要 withdraw `sha256:7a6ebca3d84695e38e6ef90733a64227c9a2cf2b0d8cb61a894efc098b87e262`、cost-center `sha256:ab1f5e79cd6f8bd01a004012f7d832ae329193356da687c12f7490ef64efae5c`（以 `python3 harness/verifier.py --task <t> digest` 的输出为准，并与每个计划的 `verifier.catalog_digest` 一致）。
- 控制：本机与验收主机各 2 × 16 项全部通过（证据目录 `docs/evidence/v7-controls-2026-11-16/`）。
- 网络隔离控制在冻结当天重跑并通过，白名单为 `________`。
- 8 个计划文件的 SHA256：`________`。
