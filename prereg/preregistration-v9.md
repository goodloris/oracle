# v9 确认性实验预注册（草稿，未冻结）

> **状态：草稿，未冻结。** 冻结由操作员执行：填写附录 A 的冻结清单，提交本文件的最终版本，把该提交中本文件的 SHA256（`sha256sum prereg/preregistration-v9.md`）和提交时间贴到公开 issue。此后正文不再修改，只能在附录中追加。
>
> **冻结前必须先决定两件事**（见“功效”和“规模、credit 估算与预算”）：按当前规模，H1 的功效约 0.48，低于 0.8；估算消耗约 445 credit，高于上限 350。下文给出调整方案，由操作员选定后修改正文再冻结。

## 背景与问题

v8a（公开 `docs/v8a-confirmatory-results.md`）中，Agent 起草的条目只在 event 一个字段出错：drafted 组 11 次，drafted-review 组 4 次，其余 17 个字段全部写对；条目写对的 80 个 trial 下游全部通过。H1 不成立，drafted-review 比 given 低 7.3 个百分点。

v9 测：**平台把事件规则做成确定性校验之后，Agent 起草的条目能否达到平台给定条目的水平？**

## 平台规则（私有叠加，之后移植到公开平台）

在 `opdef.Validate()` 上加两条规则（`harness/platform_rules.py` 对 `opdef.go` 做确定性的补丁，`docs/v9-design.md` 附完整差异）：

- **R1**：每个条目必须声明 `Event`，该事件必须出现在**消费者注册表** `consumedEvents` 中，且不能与其他条目的事件重复（重复检查是既有规则）。
- **R2**：`EventExemption` 只能出现在**受保护的豁免名单** `exemptOperations` 列出的操作上，目前只有 `bulk-approve.v3`；新条目不能自己声明豁免。

注册表 `internal/opdef/registry.go` 由平台维护、受保护，由 `internal/notify/notify.go` 中 `audience()` 的 `case "<事件>":` 分支生成（控制 `registry_is_generated_from_notify`）。它也被列为目录包的源文件，私有 Verifier 要求它与平台版本逐字节相同。

公开 5 个条目在规则下仍然有效：`bulk-approve.v3` 在豁免名单中；其余 4 个事件（rejected、reopened、countersigned、reassigned）都有消费者。为此起点的 notify 增加了 `purchase.countersigned` 的消费者分支（v8a 起点没有）。

## 设计

### 任务（2 个，不公开）

两个任务的操作名、业务对象都不与 v1–v8 重复（v8a 的 return/delivery-site、v8b 的 close/send-back/amend 已公开）。形状与 v8a 相同。

| 任务 | 类型 | 接口 | 隐含约定 |
|---|---|---|---|
| T1 `hold.v9` | 状态迁移，单目标 | `POST /purchases/{id}/hold` `{"reason"}`；approver；他人创建、`submitted` → `on-hold` | audit（ApprovalAudit 行含 reason）、request-hash（reason 不进指纹）、outbox（`purchase.held`）、uniform-denial |
| T2 `bulk-project.v9` | 字段修改，批量，带参数 | `POST /purchases/bulk-project` `{"ids","project"}`；approver；他人创建、`submitted`，改 `projectId`，值须为本租户 active 的 Project | history（ProjectHistory 行与并发链）、request-hash（project 进指纹）、outbox（`purchase.project_changed`）、uniform-denial |

参考条目（`tasks/<task>/catalog.go.txt`，公开 5 个条目之后追加一项）：

```go
{Name: "hold.v9", Kind: Transition, Role: "approver", Subject: Others, From: "submitted", To: "on-hold",
 Inputs: []string{IDs, Reason}, Fingerprint: []string{IDs}, Record: ApprovalAudit,
 Event: "purchase.held", Denial: UniformDenial}

{Name: "bulk-project.v9", Kind: FieldUpdate, Role: "approver", Subject: Others, Status: "submitted",
 Column: "projectId", Param: "project", Response: "taggedIds", Inputs: []string{IDs, "project"},
 Fingerprint: []string{IDs, "project"}, Record: ProjectHistory, Event: "purchase.project_changed", Denial: UniformDenial}
```

notify 消费者中预先放好新事件的分支，线索规则与 v8a 相同（`tasks/<task>/cohort.json` 的 `sources`，控制 `clues_present`）。需求不出现目录字段名、常量和规则用词（禁词表另加 registry、consumer、exempt、消费者、豁免）。T2 的平台版本带受管列叠加（`ProjectHistory`、`projectId`），与 v8a T2 做法相同。

### 三组（cell）

| 组 | 平台 | 会话 | 可改路径 |
|---|---|---|---|
| `given` | 规则平台，目录已含参考条目 | 1 次：写适配（公开 worker 原样） | `internal/candidate/*.go` |
| `drafted-norule` | v8a 平台（Validate 不带 R1、R2，没有 registry.go） | ① 起草；② 适配（同一模型，新会话） | ① 只有 `internal/opdef/catalog.go`；② 只有 `internal/candidate/*.go`，目录冻结 |
| `drafted-rule` | 规则平台（Validate 带 R1、R2，registry.go） | 同上 | 同上 |

- **起草时可以运行 Validate**：kit 中有受保护的命令 `cmd/opcheck`（`go run ./cmd/opcheck`，调用 `opdef.Validate()`），两个起草组的提示词逐字相同，都写明可以随时运行它（控制 `drafted_cells_get_identical_prompts`）。理由：这代表平台在 CI 里给出即时反馈，属于这次被测试的处理条件；norule 组拿到同一个反馈通道，只是没有新规则，所以 H2 的差别只来自规则本身。
- 起草结束后目录必须能编译并通过 `Validate()`，否则 trial 以 **`draft-invalid`** 结束（已观察结局，A=0，不重试）。生成端用 `harness/catalogcheck.py` 检查，主机用同一程序复核，两边不一致记为 `infrastructure-blocked`。
- 适配阶段的提示词与 given 组逐字相同。不设审查组。
- 与 v8a drafted 的差别：两个起草组都能运行 Validate；起点 notify 多一个 countersigned 消费者。所以 drafted-norule 是同批次对照，不与 v8a 直接比较。

### 模型与样本

- 模型：claude-opus-5.5、gpt-5.6-luna、qwen3-coder-next；engine kiro，effort medium；每个会话预算 600 秒。
- 单阶段。每个任务 × 模型 16 块，每块 3 组，组内顺序由计划种子 20261118 随机。共 **2 × 3 × 16 × 3 = 288 个 trial**，6 个通道。
- 每个 trial 只做一次生成，不重试、不修复、不替换。

### 隔离与跨主机流程

沿用 v8a：主机 checkout `/opt/poc-oracle/private-v9`，运行目录 `/opt/poc-oracle/runs/v9-*`；生成端在 `isolate_net.py` 沙箱中运行，deny 两个私有仓库（`/Users/administrator/poc-v7-private`、`/Users/administrator/poc-v8b-private`）、`~/.paseo`、公开 checkout、`~/.kiro`、`~/.codex` 和本 run 的其他 kit 与结果；kit 中的计划是按组裁剪的视图。网络白名单（顺序固定）：`cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`。

## 结局变量

- **A**（主要）：完整验收通过。
- **C**（描述性）：起草组冻结的条目与参考条目逐字段一致，等价规则 E1–E6 与 v8a 相同（`harness/entry_oracle.py`）；按 19 个逻辑字段和 10 个类别报告错误分布。
- **E、K、D、safety**：沿用 v7/v8a 的结构化定义。
- **S**、**credits**：所有会话之和。

`draft-invalid`、`scope-violation`、`budget-exceeded`、`candidate-error` 是已观察结局；`runtime-blocked`、`provider-error`、`protocol-error`、`infrastructure-blocked` 为未测量。

## 假设与检验

合并 6 个通道的块，按块配对。一个块对某个假设有效，当且仅当两组都是已观察结局；每个假设各自报告有效块数（最多 96）。

| 假设 | 比较 | 检验 | α |
|---|---|---|---|
| **H1（主）** | A：drafted-rule **不劣于** given，界值 δ = 5 个百分点 | Tango（1998）配对比例差的 score 检验：H0：p(rule) − p(given) ≤ −0.05；z(−0.05) > z₀.₉₇₅ = 1.960 时拒绝 H0。报告差值估计与单侧 97.5% score 置信下界、上界 | 单侧 α = 0.025，单独检验 |
| H2（次） | A：drafted-rule 高于 drafted-norule | 精确配对符号检验（McNemar 精确），单侧 | α = 0.05（唯一的次要假设，不校正） |

实现：`harness/analyze_v9.py`（唯一实现）；分析控制 `harness/analyze_v9_check.py`（Tango 性质、合成数据上的 H1 成立/不成立/临界值两侧、分母、H2、检索审计、界值处 I 类错误、功效）。

### 功效（模拟，`analyze_v9_check.py`，3000 次）

规划依据：v8a 中 given 在 96 块里失败 2 次（约 2%，都与条目无关）；条目写对的起草 trial 下游全部通过。如果 R1、R2 消除了 event 错误，drafted-rule 的成功率应接近 given，不一致主要来自两组各自独立的偶发失败。

| 真实情形（每对“只有 rule 过”/“只有 given 过”的概率） | 96 对 | 144 对 | 192 对 | 96 对、界值 10 pp |
|---|---:|---:|---:|---:|
| 相同，各 1% | 0.62 | 0.88 | 0.95 | 0.99 |
| 相同，各 2%（v8a given 的失败率） | **0.48** | 0.70 | 0.83 | 0.96 |
| 相同，各 3% | 0.38 | 0.56 | 0.70 | 0.93 |
| rule 低 1 pp（2% / 3%） | 0.29 | 0.42 | 0.55 | 0.86 |
| rule 低 2 pp（1% / 3%） | 0.16 | 0.26 | 0.37 | 0.79 |

界值处（真实差 −5 pp）的 I 类错误 ≤ 0.017。96 对、两组完全没有不一致时 z = 2.25 > 1.96，H1 成立；只要有一对不一致，z 就降到 1.96 附近。

**结论：在最可能的情形（两组相同、偶发失败各约 2%）下，按当前设计（96 对、界值 5 pp），功效约 0.48，达不到 0.8。** 可选的调整方案（未采用，由操作员决定）：

| 方案 | 改动 | 功效（相同、各 2%） | 估算 credit |
|---|---|---:|---:|
| A（当前） | 96 对，界值 5 pp | 0.48 | ≈ 445 |
| B | 界值放宽到 10 pp，规模不变 | 0.96 | ≈ 445 |
| C | given 与 drafted-rule 各 32 块（192 对），drafted-norule 保持 16 块（H2 仍 96 对） | 0.83 | ≈ 715 |
| D | 三组都 32 块 | 0.83 | ≈ 890 |
| E | 去掉 opus 通道（v8a 中 opus 在所有组全对），luna、qwen 各 32 块，三组：H1 128 对 | ≈ 0.6 | ≈ 425 |

方案 B 最便宜，但 10 pp 的界值与 v8a 相同，回答的是“不比 given 差 10 个百分点以上”，比 5 pp 的问题弱。

## 次要与描述性分析（不校正）

- 每组、每通道的 A、E、K、D 率；各通道的配对符号检验 p 值（探索性）。
- C：两个起草组的 C 率、逐字段与类别的错误分布、A 按 C 分层；drafted-rule 中 `draft-invalid` 的错误信息分类（R1、R2 或其他）。
- safety：每组七类的 trial 数与用例数。
- Verifier：私有 `appctl operations` 绑定每个 trial 实际使用的目录与平台版本（given、drafted-rule 为规则平台，drafted-norule 为 v8a 平台），只报告。
- 成本：每个正确结果的 credit 与时间（所有会话，含 draft-invalid 等已开始的 trial），块级 bootstrap 95% 区间（10 000 次，种子 20261118）；起草与适配两个阶段分别报告。

## 规模、credit 估算与预算

v8a 实测的每 trial 平均 credit（主机 runs，`docs/evidence/v8a-runs-2026-11-17/host-runs.tgz`）：given opus 1.17、luna 0.68、qwen 0.62；drafted（起草 + 适配）opus 2.92、luna 1.35、qwen ≈ 1.18。drafted-rule 假设比 drafted 多 10%（起草时会反复运行 Validate）。

| 模型 | given | drafted-norule | drafted-rule | 每块 | × 32 块（2 任务 × 16） |
|---|---:|---:|---:|---:|---:|
| opus | 1.17 | 2.92 | 3.21 | 7.30 | ≈ 234 |
| luna | 0.68 | 1.35 | 1.49 | 3.52 | ≈ 113 |
| qwen | 0.62 | 1.18 | 1.30 | 3.10 | ≈ 99 |
| **合计** | | | | | **≈ 445** |

**估算高于给定的上限 350 credit。** 按 R5，消耗达到上限时停止全部通道，剩余 trial 记为未测量；按估算，约有 20% 的块不能完成，H1 的有效对数降到约 75。冻结前需要决定：把上限提高到约 480（估算 + 8%），或按上表改变规模或模型组合。本文暂按上限 350 书写 R5。

时间：每块 5 次会话，4 通道并行约 5–7 小时。

## 用量上限与续跑规则

- **R1**：预检（READY）被拒的 trial 从未开始生成；该通道停止，配额恢复后用同一计划、同一 kit、按原顺序续跑。被拒时的结果目录归档到 `<run>/preflight-blocked-<日期>/`。
- **R2**：生成途中（任一会话）被上限或服务错误打断的 trial 记为未测量（provider-error），不重跑，也不从中断的阶段续跑。
- **R3**：服务区域或端点变化时，先用 discover 模式确认主机，设置 `KIRO_ALLOW_HOSTS`，重跑网络隔离控制并通过后才续跑，在附录中记录。
- **R4**：首轮与续跑相隔超过 6 小时的，报告按批次拆分描述（主分析不分层）。跨天执行适用 R1–R3。
- **R5**：通道之间并行不超过 4 个；同一块的三组在同一通道连续执行；评估占用任意空闲的数据库槽位（`scripts/run_v9.sh`）。总消耗达到 **350 credit** 仍未完成时，停止所有通道并在附录中记录；剩余 trial 记为未测量，不改变假设与检验。

## 排除规则

- 主分析不排除任何已观察 trial。
- 敏感性分析：排除任一会话有 `retrieval-attempt` 的 trial（连同其块中参与同一假设的另一组），重跑 H1、H2。v8a 的两个误报都是 `curl 127.0.0.1:8080/health`；v9 事先规定 `curl` 访问 127.0.0.1 或 localhost 不算检索（控制 `retrieval regex`）。
- 冻结后发现 Oracle 或条目正确性 Oracle 实现错误时：只能在附录中说明，并同时报告修正前后两版结果。

## 附录（只追加）

### 附录 A：冻结清单（冻结前由操作员填写，现为空）

- [ ] 规模与预算的决定（功效方案、credit 上限），已写回正文。
- [ ] 公开平台 commit `fad8adfa89edb753c55410518d531e21489f7d9b`，主机 `/opt/poc-oracle/public` 已核对。
- [ ] 私有仓库提交 SHA（6 个计划的 `harness_sha`）：`________`；树 hash：harness `________`，tasks `________`。
- [ ] 规则补丁 `harness/platform_rules.py` SHA256：`________`；规则平台 `opdef.go` hash（hold `________`，project `________`）；registry.go hash（hold `________`，project `________`）；`harness/source/common/opcheck.go.txt` SHA256：`________`。
- [ ] 参考目录摘要（规则平台与 v8a 平台相同）：hold `________`，project `________`。
- [ ] `harness/prompts.json`、`harness/entry_oracle.py`、`harness/catalogcheck.py`、`harness/stages.py` 的 SHA256：`________`。
- [ ] 分析脚本 `harness/analyze_v9.py` SHA256：`________`；`harness/analyze_v9_check.py` 本机与主机都 `all_ok`。
- [ ] 控制：主机 hold `__` 项、project `__` 项全部通过（含规则控制 2 × 6、条目正确性控制 2 × 27、私有内核回归）；本机 alpine 同样通过（辅助）；证据目录 `docs/evidence/v9-controls-<日期>/`。
- [ ] 跨主机假 kiro 流程：每任务三组各 1 个 trial 全部通过。
- [ ] 验收主机数据库：`postgres@sha256:888402a8cd6075c5dc83a31f58287f13306c318eaad016661ed12e076f3e6341`（PostgreSQL 17.2 Debian/glibc，`en_US.utf8`）。
- [ ] 网络白名单 `KIRO_ALLOW_HOSTS=cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`（顺序不变），冻结当天重跑网络隔离控制并通过（含两个私有仓库不可读）。
- [ ] 6 个计划文件的 SHA256（每个 48 个 trial，种子 20261118）：`v9-hold-claude-opus-5.5`、`v9-hold-gpt-5.6-luna`、`v9-hold-qwen3-coder-next`、`v9-project-claude-opus-5.5`、`v9-project-gpt-5.6-luna`、`v9-project-qwen3-coder-next`：`________`。
