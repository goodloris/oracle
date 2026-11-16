# v8b 确认性实验预注册（草稿，未冻结）

> **状态：草稿，未冻结。** 冻结由操作员执行：填写附录 A 的冻结清单，提交本文件的最终版本，把该提交中本文件的 SHA256（`sha256sum prereg/preregistration-v8b.md`）和提交时间贴到公开 issue。此后正文不再修改，只能在附录追加。冻结前不得登记任何模型计划。

## 背景

v7（公开仓库 `docs/v7-confirmatory-results.md`）在 2 个私有任务上得到：A 手写 55%、文档 76%、目录 + 内核 99%，H1–H3 都成立。两个任务都由我们设计，形状相近（单目标状态迁移、带参数的字段修改），需求是逐条技术规格。

v8b 检验：**换成更多、形状不同、需求更开放的业务任务后，“平台给定语义 + 内核执行”对完整验收成功率 A 的收益还能不能复现。**

## 设计

### 任务（3 个，不公开）

任务说明、起点、Oracle 只在私有仓库（goodloris/oracle，分支 `v8b`）和验收主机上。公开仓库和公开目录中没有 v8b 条目。

| 任务 | 方向 | 接口 | 隐含约定 |
|---|---|---|---|
| T1 `bulk-close.v8` | 跨实体规则（批量） | `POST /purchases/bulk-close` `{"ids"}`；approver、他人的 approved；accepted 验收单金额之和 ≥ 采购金额才可关闭；全部或全不 | audit、request-hash、outbox `purchase.closed`、uniform-denial |
| T2 `send-back.v8` | 审批链 + 有效期（单目标） | `POST /purchases/{id}/send-back` `{"reason"}`；approver、他人的 pending-second；最近一次一审人本人不能退回；只在最近一次一审后 72 小时内 | audit（含 reason）、request-hash（reason 不进指纹）、outbox `purchase.sent_back`、uniform-denial |
| T3 `bulk-amend.v8` | 全部或全不 + 多字段 + 逐项值 | `POST /purchases/bulk-amend` `{"items":[{"id","amount"}],"reason"}`；requester、本人的 submitted；涨幅 ≤ 10%；不跨越 50000 二级审批线 | history（AmendmentHistory）、request-hash（逐项 `id=amount`）、outbox `purchase.amended`、uniform-denial（金额违规按目标不合格处理） |

三个任务的起点相同：v6 世界的全部操作已经实现（分级批量审批、会签、批量驳回、重新提交、批量改派）。需求用业务语言写成“背景 / 业务规则 / 验收要点”三段，不点名适配器和实现方式。每条约定在每组都有至少 2 条分散、互不冲突的线索（`tasks/<task>/cohort.json` 的 `sources`，静态控制校验）。设计说明见 `docs/v8b-design.md`。

### 两组（cell）

| 组 | 既有 Action | 新操作的语义从哪里来 | 谁执行 |
|---|---|---|---|
| `go-manual`（manual） | 手写 | 只能从散落的代码、SCHEMA.sql、notify、ledger、reconcile 推断 | 作者 |
| `go-action-opdef`（given） | 通过 `opkernel` 执行 | 受保护的 `internal/opdef` 已含本任务条目（私有目录叠加） | 内核按条目执行；作者只写适配 |

两组提示词只有 capability 一行不同（控制 `prompts_differ_only_in_capability`）。不设文档组：v7 已经分离了“事实”和“执行”两种来源，v8b 只检验总收益能否复现。

### 模型与样本

- 模型：claude-opus-5.5、gpt-5.6-luna、qwen3-coder-next；engine kiro，effort medium（不支持 effort 的照实记录），预算 600 秒。
- 每个任务 × 模型 8 块，每块 2 组，组内顺序由计划种子随机。共 **3 × 3 × 8 × 2 = 144 个 trial**，9 个通道（任务 × 模型）。
- 每个 trial 只做一次首次生成，不重试、不修复、不替换。

### 隔离与跨主机流程

- 验收主机 `10.30.11.112`，v8b 的 checkout 在 `/opt/poc-oracle/private-v8b`，runs 在 `/opt/poc-oracle/runs/v8b-*`；执行 `fixtures`、`plan`、`prepare`；私有 seed 只留在主机。
- kit 经 scp 拉到操作员 Mac 上私有仓库之外的目录；`scripts/generate.sh` 在 `isolate_net.py`（文件隔离 + 模型端点白名单）中运行公开 phase3 worker。deny 列表包含两个私有 checkout（`/Users/administrator/poc-v8b-private`、`/Users/administrator/poc-v7-private`）、`~/.paseo`、公开 checkout、`~/.kiro`、`~/.codex`、本 run 的其他 kit 和结果。
- 白名单（顺序固定，runtime 在最后）：`cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`。
- 结果推回主机后执行 `restore`、`evaluate`、`summarize`。
- 审计规则沿用 v6 附录 A/C/F：`retrieval-attempt`、`network-denied`（遥测主机不计）。

## 结局变量（来自冻结 Oracle 的逐 trial 结果，定义同 v7）

- **A**（主要）：全部用例通过（`outcome == passed`）。
- **E**：显式契约（含既有 API 回归 18 例）全部通过。
- **K**：四条隐含约定全部通过。
- **D**：outbox 约定通过（given 组按构造由目录决定）。
- **safety**：七类结构化字段（`unauthorized_write`、`partial_batch`、`side_effect_atomicity`、`double_apply`、`wrong_commit`、`service_error`、`undecidable`），各给计数和用例列表，只依据每一步记录的状态码与数据库前后摘要。T2 是单目标操作，`partial_batch` 按定义不可能出现。
- **S**：生成耗时，worker 的 `generation_seconds`。
- **C**：生成消耗的 credit，worker 的 `usage.credits`。

`scope-violation`、`budget-exceeded`、`candidate-error` 是已观察结局，A=E=K=D=0。`runtime-blocked`、`provider-error`、`protocol-error`、`infrastructure-blocked` 为未测量。

## 假设与检验

| 假设 | 比较 | 检验 | α |
|---|---|---|---|
| **H1（主，唯一的检验）** | A：given（`go-action-opdef`）高于 manual（`go-manual`） | 合并 3 个任务、3 个模型的全部块，按块配对，精确符号检验（McNemar 精确），单侧；不一致块 = given 通过而 manual 未过，对比 manual 通过而 given 未过 | 0.05 |

一个块有效，当且仅当两组在该块中都是已观察结局。报告有效块数（分母），最多 72。

解读规则（事先约定）：

- H1 成立：可以表述为“在三个形状不同、需求更开放的新任务上，平台给定的语义 + 内核执行仍然提高了完整验收成功率，复现了 v7 的 H1”。
- H1 不成立：如实报告“未复现”，不追加子集或单任务检验来寻找显著结果。
- 任务和约定由我们设计，结论不能推广为通用平台的收益。

## 按任务分层与其他描述性分析（不做推断）

- **按任务分层，只做描述**：每个任务、每组的 A、E、K、D 率和不一致块数；附上每个任务的精确符号检验 p 值，明确标为探索性，不用于任何结论。
- 每个通道（任务 × 模型）、每组的 A、E、K、D 率。
- safety：每组七类的 trial 数与用例数；逐个列出 `unauthorized_write`、`partial_batch`、`double_apply`、`wrong_commit` 不为 0 的 trial。
- S：块内差 S_given − S_manual 的单侧 Wilcoxon 符号秩检验，探索性（方法同 v7）。
- 私有 Verifier（`appctl operations`，公开 `fad8adf` + 私有目录叠加）的判定与 A 的对照表，按任务和组列出。T3 manual 组的正确实现按构造会被判 `fail`（`FINGERPRINT_INPUT_MISSING`，公开指纹规则不覆盖逐项请求，见控制），该格单独标注，不计入任何 Verifier 汇总。
- 与 v7 对照：只做方向性对照，不做检验。

## 成本指标（定义同 v7）

对每个“组 × 任务”、“组 × 通道”和每组合计：

- **每个正确结果的 credit** = Σ C（该组所有开始生成的 trial，含 budget-exceeded、scope-violation、candidate-error，以及 usage 已知的 provider-error）÷ 该组 A=1 的 trial 数；
- **每个正确结果的时间** = Σ S（同一集合）÷ A=1 的 trial 数；
- 同时报告只用已观察 trial 的版本；
- 区间：以块为单位做 10 000 次 bootstrap（种子 20261116），95% 百分位区间；A=1 为 0 的格子记为“无正确结果”；
- usage 缺失超过 5% 的组，credit 指标标为不完整。

## 规模与 credit 估算

按 v7 实测每个 trial 的平均 credit（两个 v7 任务的均值）：

| 模型 | manual | given | trial | 估算 credit |
|---|---:|---:|---:|---:|
| opus | 2.42 | 1.23 | 48 | ≈ 87.5 |
| luna | 1.00 | 0.62 | 48 | ≈ 38.7 |
| qwen | 0.89 | 0.54 | 48 | ≈ 34.3 |
| **合计** | | | **144** | **≈ 160** |

v8b 的需求更开放，T3 需要读一个新的适配器，T1/T2 需要查另一张表或审批链，单个 trial 可能更贵。按 +40% 估计约 225 credit，**预算上限 250 credit**。READY 预检（每个 trial 一次）消耗可忽略。

## 用量上限与续跑规则

- **R1**：预检（READY）被拒的 trial 从未开始生成。`generate.sh` 遇到预检被拒即停止该通道；配额恢复后用同一计划、同一 kit、按原顺序续跑，即为首次生成。被拒时的结果目录归档到 `<run>/preflight-blocked-<日期>/`，不删除。
- **R2**：生成途中被上限或认证错误打断的 trial 记为未测量（provider-error），**不重跑**；其所在块对 H1 失效。
- **R3**：服务区域或端点变化时，先用 discover 模式确认主机，设置 `KIRO_ALLOW_HOSTS`，重跑网络隔离控制并全部通过后才续跑，在附录中记录。
- **R4**：首轮与续跑相隔超过 6 小时的，报告按批次拆分描述（主分析不分层）。
- **R5**：通道之间并行不超过 4 个，通道开始时占用任意空闲数据库槽位（`scripts/run_v8b.sh`）；同一块的两组在同一通道连续执行。总消耗达到 250 credit 仍未完成时，停止所有通道并在附录记录；剩余 trial 记为未测量，不改变假设与检验。
- **R6**：v8a 与 v8b 共用主机端口 56701–56704/19701–19704，顺序运行。v8b 开始前确认没有其他 `poc-oracle-pg-*` 容器在运行；v8b 的容器带标签 `poc.owner=v8b`，`host_db.sh down` 只删除带此标签的容器。若运行中发现端口冲突，受影响的 trial 记为 infrastructure-blocked，在附录记录。

## 排除规则

- 主分析不排除任何已观察 trial。
- 敏感性分析：排除 `retrieval-attempt` 的 trial（连同其块中的另一组），重跑 H1。
- 冻结后发现 Oracle 实现错误时：只能在附录中说明，并同时报告修正前后两版结果；不得只报告修正版。

## 附录（只追加）

### 附录 A：冻结清单（冻结前由操作员填写，当前为空）

- [ ] 公开平台：PR #27 `fad8adfa89edb753c55410518d531e21489f7d9b`，公开目录摘要 `sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055`。
- [ ] 私有仓库提交 SHA（= 9 个计划的 `harness_sha`）：`____`；树 hash：harness `____`，tasks `____`。
- [ ] Verifier 私有目录摘要（以 `python3 harness/verifier.py --task <t> digest` 为准，并与每个计划的 `verifier.catalog_digest` 一致）：close `____`，send-back `____`，amend `____`（草稿时的值见 `docs/v8b-design.md`）。
- [ ] 控制：验收主机 close 17、send-back 15、amend 18 项全部通过（含排序负控制），私有内核回归在 ICU 与数据库默认排序下通过；本机 alpine 同样通过（辅助）；证据目录 `____`。
- [ ] 跨主机假 kiro 交接：每个任务两组各 1 个 trial，主机 restore/evaluate/summarize 全部通过；证据目录 `____`。
- [ ] 分析脚本：`harness/analyze_v8b.py`（本文全部结局、假设、成本指标的唯一实现），SHA256 `____`。
- [ ] 验收主机数据库：`postgres@sha256:888402a8cd6075c5dc83a31f58287f13306c318eaad016661ed12e076f3e6341`（PostgreSQL 17.2 Debian/glibc，`en_US.utf8`，语言排序），Docker 默认 seccomp；每个计划的 `database` 字段与之一致。
- [ ] 网络白名单：`cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`（顺序不变），冻结当天重跑网络隔离控制并全部通过，两个私有 checkout 均不可读：`____`。
- [ ] 9 个计划文件的 SHA256（每个 16 个 trial）：`____`。
- [ ] 确认 v8a 当时没有占用 56701–56704/19701–19704：`____`。
