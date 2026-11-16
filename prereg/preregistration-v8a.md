# v8a 确认性实验预注册（已冻结 2026-11-17）

> **状态：已冻结（2026-11-17）。** 原说明： 冻结由操作员执行：填写附录 A 的冻结清单，提交本文件的最终版本，把该提交中本文件的 SHA256（`sha256sum prereg/preregistration-v8a.md`）和提交时间贴到公开 issue。此后正文不再修改，只能在附录中追加。

## 背景与问题

v7（公开仓库 `docs/v7-confirmatory-results.md`）中，平台方预先写好目录条目、内核按条目执行，完整验收成功率 A 从手写组的 55% 提高到 99%。但这些条目是我们写的，平台方写条目的成本和出错的可能都不在 v7 的度量范围内（v7 结果的局限 1）。

v8a 测：**由 Agent 根据业务需求起草目录条目时，v7 的收益还能保留多少？在起草和实现之间加一次固定的自动 AI 审查后，又能挽回多少？**

## 设计

### 任务（2 个，不公开）

任务说明、起点、参考条目、Oracle 只在私有仓库和验收主机上。两个任务的类型与 v1–v7 的任务都不同，形状保持简单，业务对象与并行的 v8b 不重叠。

| 任务 | 类型 | 接口 | 隐含约定（Oracle 的 implicit 组） |
|---|---|---|---|
| T1 `return.v8` | 状态迁移，单目标 | `POST /purchases/{id}/return` `{"reason"}`；approver；他人创建、`pending-second` → `returned`；批到 `pending-second` 的那位审批人不能退回 | audit（ApprovalAudit 行含 reason）、request-hash（reason 不进指纹）、outbox（`purchase.returned`）、uniform-denial（含“第一级审批人”） |
| T2 `bulk-delivery-site.v8` | 字段修改，批量，带参数 | `POST /purchases/bulk-delivery-site` `{"ids","site"}`；requester；本人创建、`submitted`，改 `deliverySiteId`，值须为本租户 active 的 DeliverySite | history（DeliverySiteHistory 行与并发链）、request-hash（site 进指纹）、outbox（`purchase.delivery_site_changed`）、uniform-denial |

需求用业务语言写，不出现目录字段名和目录常量（禁词表见 `tasks/<task>/cohort.json`，控制 `task_text_has_no_forbidden_words`）。HTTP 契约本身的词（`status`、响应字段名、请求字段名 `site`）不在禁词之列。

**参考条目（ground truth）**，即 `tasks/<task>/catalog.go.txt` 在公开 5 个条目之后追加的一项：

```go
{Name: "return.v8", Kind: Transition, Role: "approver", Subject: Others,
 From: "pending-second", To: "returned", Separation: DistinctFromPreviousApprover,
 Inputs: []string{IDs, Reason}, Fingerprint: []string{IDs}, Record: ApprovalAudit,
 Event: "purchase.returned", Denial: UniformDenial}

{Name: "bulk-delivery-site.v8", Kind: FieldUpdate, Role: "requester", Subject: Own,
 Status: "submitted", Column: "deliverySiteId", Param: "site",
 Response: "changedIds", Inputs: []string{IDs, "site"}, Fingerprint: []string{IDs, "site"}, Record: DeliverySiteHistory,
 Event: "purchase.delivery_site_changed", Denial: UniformDenial}
```

每个字段都由需求、既有条目或起点代码唯一确定（逐字段依据见 `docs/v8a-design.md`“唯一性”一节）。起点沿用 v7 的完整世界：公开 5 个目录条目，分级批量审批、会签、批量驳回、重新提交、批量改派都已实现。T2 的平台版本带一处私有叠加：`opdef` 增加 `Record` 常量 `DeliverySiteHistory`，`opkernel` 的受管列增加 `deliverySiteId`（与 v7 的 `costCenterId` 叠加做法相同）。T1 使用公开 `fad8adf` 的 `opdef`/`opkernel` 原样。

### 四组（cell）

| 组 | 起点 | 会话 | Agent 可以改的路径 |
|---|---|---|---|
| `manual` | v7 go-manual（手写） | 1 次：写实现 | `internal/candidate/*.go` |
| `given` | v7 go-action-opdef，目录已含参考条目 | 1 次：写适配 | `internal/candidate/*.go` |
| `drafted` | 同 given，但目录只有公开 5 个条目 | ① 起草：同一模型；② 适配：同一模型，新会话 | ① 只有 `internal/opdef/catalog.go`；② 只有 `internal/candidate/*.go`，目录冻结 |
| `drafted-review` | 同 drafted | ① 起草；② 审查：固定为 claude-opus-5.5；③ 适配：同一模型，新会话 | ①② 只有 `internal/opdef/catalog.go`；③ 只有 `internal/candidate/*.go` |

- 起草或审查结束后，目录必须能编译并通过 `opdef.Validate()`，否则 trial 以 **`draft-invalid`** 结束（已观察结局，A=0，不重试）。检查由生成端的 `harness/catalogcheck.py` 执行，验收主机用同一程序复核，两边不一致时记为 `infrastructure-blocked`。
- 审查者能看到需求、既有目录、平台代码、起点代码和草稿条目（上一次提交），可以修改 `catalog.go`；看不到 Oracle 和参考条目。
- 适配阶段的提示词与 `given` 组的提示词**逐字相同**（控制 `adapter_stage_prompt_equals_given_prompt`）。`drafted` 与 `given` 的唯一差别是目录条目的来源。
- 所有提示词集中在 `harness/prompts.json`，由计划按 hash 冻结。manual 与 given 只差 capability 一行（控制）。
- 每个阶段一次提交；每个阶段的原始 ACP 事件（`results/<trial>/<stage>/events.jsonl`）、usage 和耗时分别记录，并合计到同一个 trial 的 `generation`（credit 与秒数为各阶段之和，审查计入 drafted-review）。

### 模型与样本

- 模型：claude-opus-5.5（强）、gpt-5.6-luna（中）、qwen3-coder-next（弱）；engine kiro，effort medium；**每个会话**预算 600 秒。审查者固定为 claude-opus-5.5、effort medium。
- **两阶段成组序贯设计**（只服务于 H1）：
  - **第一阶段**：每个任务 × 模型 8 块（块号 1–8），每块 4 组，组内顺序由计划种子 20261116 随机。共 **2 × 3 × 8 × 4 = 192 个 trial**，6 个通道；其中 48 个 drafted-review trial 各含一次审查会话。
  - **第二阶段**：只有第一阶段的停止规则给出 `continue` 时才执行。只跑 given 和 drafted-review 两组，每个任务 × 模型再 8 块（块号 9–16），组内顺序种子 20261117。共 **2 × 3 × 8 × 2 = 96 个 trial**，另含 48 次审查会话。
  - 两个阶段的计划（12 个）和 kit 在冻结前一起生成、一起冻结（附录 A）。第二阶段计划的块号（9–16）、组内顺序种子和私有 seed 都与第一阶段不重叠；它的 kit 由 prepare 准备好，执行与否只由停止规则决定。
- 每个 trial 只做一次生成，不重试、不修复、不替换。

### 隔离与跨主机流程

- 验收主机 `10.30.11.112`：私有仓库 checkout 在 `/opt/poc-oracle/private-v8a`，运行目录 `/opt/poc-oracle/runs/v8a-*`；`fixtures`、`plan`、`prepare` 在主机上执行，私有 seed 只留在主机。
- 生成端（操作员 Mac）在 `isolate_net.py` 沙箱中运行：单会话组用公开 phase3 worker 原样；分阶段组用私有 `harness/stages.py`，它导入 kit 中逐字节相同的公开 worker，按阶段调用其 `invoke()`。deny 列表包含两个私有仓库（`/Users/administrator/poc-v7-private`、`/Users/administrator/poc-v8b-private`）、`~/.paseo`、公开 checkout、`~/.kiro`、`~/.codex`、本 run 的其他 kit 和结果。
- **kit 中的计划是按组裁剪的视图**（`ctl.view`）：去掉其他组的导出 hash（given 组的 `catalog.go` hash）、`tasks/` 与 `harness/` 的文件 hash（参考目录、Oracle）和 Verifier 绑定（参考目录摘要）。否则起草会话可以离线用 hash 验证猜测。控制 `kit_plan_views_hold_nothing_derived_from_the_entry`。
- 网络白名单（顺序固定，模型 runtime 主机在最后）：`cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`。v7 中 1 个 trial 因认证刷新被拦（`SignInRequiredError`），v8 把认证刷新主机加入白名单。
- 审计规则沿用 v6 附录 A/C/F（`retrieval-attempt` 扫描每个会话的 ACP 事件；遥测主机不计为 `network-denied`）。

## 结局变量

- **A**（主要）：完整验收通过（`outcome == passed`）。
- **C**（描述性）：drafted 与 drafted-review 中，**冻结的条目**（drafted：起草后；drafted-review：审查后）与参考条目逐字段一致。drafted-review 另报告 **C_draft**（审查前）。判定由 `harness/entry_oracle.py` 对编译后的目录（`opdef.All()`）进行，等价规则预先写定：
  - E1 只比较编译结果：结构体字面量字段顺序、常量标识符、命名常量与其字面值（`IDs` 与 `"ids"`、`UniformDenial` 与 `409`）、格式和注释都视为等价；
  - E2 按需求给出的操作名定位条目；没有同名条目时，唯一的非公开条目被视为本任务条目（name 字段记错）；位置无关；
  - E3 省略的字段等于零值（`Separation: NoSeparation`、`Tier: nil`、`ConfirmAmount: false`、`Response: ""`）；
  - E4 `Inputs`、`Fingerprint` 是有序列表，按列表比较（分别是请求/处理函数参数顺序和摘要顺序）；
  - E5 `Event` 与 `EventExemption` 合成一个逻辑字段 event，豁免永远不等于事件；
  - E6 其余条目必须与公开 5 个条目的编译结果完全相同，否则 catalog 字段记错；多出的条目也记错。
  
  逐字段的错误分布按 19 个逻辑字段（name、kind、role、subject、from、to、tier、status、column、param、response、inputs、fingerprint、record、event、separation、confirmAmount、denial、catalog）和 10 个类别（event、fingerprint、record、separation、authority=role/subject、state=from/to/status/tier、shape=kind/column/param/response/inputs/confirmAmount、name、denial、catalog）报告。没有冻结条目的已观察 trial（在起草会话中超预算或越界）记 C=0，单列计数。
- **E**、**K**、**D**、**safety**：沿用 v7 的结构化定义（七类：`unauthorized_write`、`partial_batch`、`side_effect_atomicity`、`double_apply`、`wrong_commit`、`service_error`、`undecidable`）。
- **S**：所有会话的生成秒数之和；**credits**：所有会话的 `usage.credits` 之和（含审查）。

`draft-invalid`、`scope-violation`、`budget-exceeded`（任一会话）、`candidate-error` 是已观察结局，A=E=K=D=0。`runtime-blocked`、`provider-error`、`protocol-error`、`infrastructure-blocked` 为未测量。

## 假设与检验

所有检验把 6 个通道的块合并，按块配对：同一 (任务, 模型, 块序号) 中两组的 trial 构成一对。一个块对某个假设有效，当且仅当该假设涉及的两组在这一块中都是已观察结局。**每个假设各自报告有效块数（分母，最多 48）**。

### H1（主）：两阶段成组序贯非劣效检验

- **比较**：A 上 drafted-review **不劣于** given，界值 δ = 10 个百分点。H0：p(drafted-review) − p(given) ≤ −0.10。
- **统计量**：Tango（1998）配对比例差的 score 统计量 z(δ0)。b =“只有 drafted-review 通过”的有效块数，c =“只有 given 通过”的有效块数，n = 有效块数；z 在 δ0 = −0.10 处计算（`tango_z`）。
- **α 消耗**：O'Brien-Fleming 型 Lan-DeMets 消耗函数，单侧总 α = 0.025：

  α(t) = 2 − 2Φ(z₁₋α/₂ / √t)，t₁ = 0.5，t₂ = 1.0（按计划的 48 对与 96 对；实际有效块数不同时不重算边界）。

  - c₁ = Φ⁻¹(1 − α(0.5))；
  - c₂ 由 P(Z₁ < c₁, Z₂ > c₂) = α(1) − α(0.5) 解出，Corr(Z₁, Z₂) = √(t₁/t₂) = √0.5，二维正态概率用 Simpson 积分（`analyze_v8a.boundaries()`）。

  | | 累积 α | 临界值 | 名义单侧 α |
  |---|---:|---:|---:|
  | 第一阶段（48 对） | 0.0015253 | c₁ = 2.9626 | **α₁ = 0.001525** |
  | 第二阶段（96 对） | 0.0250000 | c₂ = 1.9686 | **α₂ = 0.024500** |

  控制（`harness/analyze_v8a_check.py`）：闭式计算与积分一致；40 万次蒙特卡洛重算 P(Z₁ > c₁) 与 P(Z₁ > c₁ 或 Z₂ > c₂)，都落在 4 个标准误之内。
- **第一阶段的停止规则**（只依据 H1 的统计量，不看任何次要指标）：
  - (a) z(−0.10) > c₁，即在 α₁ 下证明非劣效 → **停止，H1 成立**（`stage1_decision = stop-noninferior`）；
  - (b) z(−0.10) < −c₁，即 drafted-review 减 given 的单侧 (1 − α₁) score 置信上界低于 −10 个百分点 → **无效停止，H1 不成立**，结论报告为“显著更差”（`stop-futility`）；
  - (c) 其他情况一律进入第二阶段（`continue`）。
- **第二阶段**：合并两阶段的全部 H1 有效块（最多 96 对），z(−0.10) > c₂ 时 H1 成立，否则不成立（`final_decision` 为 `noninferior` 或 `not-noninferior`）。无效停止是非约束性的，不影响 c₂。
- **输出**：`stage1_decision`、`final_decision`（`noninferior`、`not-noninferior`、`worse`；第二阶段未跑完时为 `pending-stage2`）、两次检验的 z、p、差值估计与 score 置信上下界。第一阶段停止后不得再执行第二阶段（分析脚本会拒绝这种数据）。

### 次要假设

| 假设 | 比较 | 检验 | α 与校正 |
|---|---|---|---|
| H2 | A：drafted 高于 manual | 精确配对符号检验（McNemar 精确），单侧 | H2、H3 组成次要族，Holm 校正，族 α = 0.05 |
| H3 | A：drafted-review 高于 drafted | 同上，单侧 | 同上 |

H2、H3 **只用第一阶段的块**（最多 48 对）。

实现：`harness/analyze_v8a.py`（本文全部结局、假设、成本指标的唯一实现）；分析控制 `harness/analyze_v8a_check.py`（Tango 统计量性质、边界与 α 消耗、四种决策路径的合成数据、界值处的 I 类错误）。

**运行特性**（模拟 4000 次，Tango 统计量，`analyze_v8a_check.py`）：

| 真实情形（b、c 为每对的不一致概率） | H1 成立的概率 | 第一阶段停止（非劣效 / 无效） |
|---|---:|---|
| 界值处（差 −10 个百分点） | ≤ 0.026 | 0 / 0 |
| 两组相同，不一致各 2% | ≈ 0.96 | 0.4% / 0 |
| 两组相同，不一致各 5% | ≈ 0.76 | 3% / 0 |
| drafted-review 低 3 个百分点 | ≈ 0.64 | 0 / 0 |
| drafted-review 低 20 个百分点 | 0 | 0 / ≈ 4% |

第一阶段的早停边界很严：48 对、两组没有任何不一致时 z = 2.31 < c₁，所以除非 drafted-review 明显更好或明显更差，几乎都会进入第二阶段。

解读规则（事先约定）：

- H1 成立：可以表述为“在本设计下，Agent 起草 + 一次固定 AI 审查得到的目录条目，在完整验收上不比平台给定的条目差超过 10 个百分点”。
- 无效停止：表述为“drafted-review 显著比 given 差 10 个百分点以上”。第二阶段后不成立：如实报告差值估计和置信下界，只能说“没有足够证据说明不劣于”，不能说“劣于”。都不追加子集分析。
- H2 成立：没有平台给定的条目时，“起草 + 内核执行”仍比手写好。H3 成立：审查在起草之外显著提高了 A。
- C 与成本只作描述，不做检验；C 的错误分布用于说明“起草错在哪里”，不用于修正 A。
- 任务、约定和参考条目由我们设计；结论不能推广为通用平台的收益。v8b 的 send-back 与 T1 形态接近（见下文），两个实验的结论分开解读。

## 次要与描述性分析（不校正）

以下全部**只用第一阶段的块**；第二阶段的块只进入 H1。

- 每组、每通道的 A、E、K、D 率；各通道的配对符号检验 p 值（探索性）。
- C：drafted 与 drafted-review 的 C 率；drafted-review 的 C_draft；审查的效果分为 fixed（C_draft=0 → C=1）、broke（1 → 0）、kept_correct、kept_wrong，以及审查是否改动了目录；按字段和类别的错误分布；A 按 C 分层。
- safety：每组七类的 trial 数与用例数；逐个列出前四类不为 0 的 trial。
- Verifier：私有 `appctl operations` 绑定**每个 trial 实际使用的目录**（manual 与 given 为参考目录，分阶段组为冻结的草稿），摘要按 trial 写入 `result.json`。它只检查代码是否符合目录，不检查目录本身是否正确（控制中，事件或职责分离写错的草稿配上正确适配，私有 Verifier 判为 pass），因此只报告，不作为 C 或 A 的一部分。
- 生成耗时：块内差的 Wilcoxon 检验不做（v7 已做），只报告每组总量。

## 成本指标

对每个“组 × 通道”和每组合计：

- **每个正确结果的 credit** = Σ credits（该组所有开始生成的 trial，所有会话之和，含审查；含 draft-invalid、budget-exceeded、scope-violation、candidate-error，以及 usage 已知的 provider-error）÷ 该组 A=1 的 trial 数。
- **每个正确结果的时间** = Σ S（同一集合）÷ A=1 的 trial 数。
- 分阶段组另报告起草、审查、适配三个阶段各自的 credit 和秒数总量。
- 区间：以块为单位做 10 000 次 bootstrap（种子 20261116），报告 95% 百分位区间；A=1 为 0 的格子记为“无正确结果”。
- usage 缺失的 trial 单独计数；某组缺失超过 5% 时，该组 credit 指标标为不完整。

## 规模、credit 估算与预算

依据 v7 实测的每 trial 平均 credit（主机 runs，`docs/evidence/v7-runs-2026-11-16/host-runs.tgz`）：

| 模型 | v7 手写（go-manual） | v7 给定（go-action-opdef） |
|---|---:|---:|
| opus | 2.42 | 1.23 |
| luna | 1.00 | 0.62 |
| qwen | 0.88 | 0.54 |

估算假设：manual ≈ v7 手写；given ≈ v7 给定；起草会话 ≈ 一次给定组会话（保守，起草只写一项条目但要读同样多的代码）；适配会话 ≈ 给定组；审查会话 ≈ opus 的给定组会话（1.23）。每个模型 16 块（2 任务 × 8）：

| 模型 | manual | given | drafted | drafted-review | 每块 | × 16 块 |
|---|---:|---:|---:|---:|---:|---:|
| opus | 2.42 | 1.23 | 2.46 | 3.69 | 9.80 | ≈ 157 |
| luna | 1.00 | 0.62 | 1.24 | 2.47 | 5.33 | ≈ 85 |
| qwen | 0.88 | 0.54 | 1.08 | 2.31 | 4.81 | ≈ 77 |
| **合计** | | | | | | **≈ 319** |

其中 48 次审查约 59 credit。READY 预检每个 trial 一次，消耗可忽略。

第二阶段（given + drafted-review，每个模型 16 块）：opus 4.92 × 16 ≈ 79，luna 3.09 × 16 ≈ 49，qwen 2.85 × 16 ≈ 46，合计 ≈ 174（其中审查约 59）。

| | 估算 | 预算 |
|---|---:|---:|
| 第一阶段 | ≈ 319 | ≈ 320；驱动的 credit 守卫设为 **360**（总上限减去为第二阶段保留的 200） |
| 第二阶段 | ≈ 174 | ≈ 200 |
| **合计上限** | | **560** |

时间：按 v7 每会话耗时（opus ≈ 60–80 s，luna ≈ 50–80 s，qwen ≈ 115–225 s），第一阶段每块 7 次会话，4 通道并行约 4–6 小时；第二阶段每块 4 次会话，约 2–3 小时。

## 用量上限与续跑规则

- **R1**：预检（READY）被拒的 trial 从未开始生成。`generate.sh` 遇到预检被拒即停止该通道；配额恢复后用同一计划、同一 kit、按原顺序续跑，即为首次生成。被拒时的结果目录归档到 `<run>/preflight-blocked-<日期>/`，不删除。
- **R1b**：第二阶段只在第一阶段全部 trial 验收完毕、`analyze_v8a.py` 输出 `stage1_decision = continue` 之后开始；决策结果（含分析输出的 SHA256）先写进附录，再执行第二阶段。第二阶段因额度原因跨天执行时，适用 R1–R4（同一计划、同一 kit、按原顺序续跑）。
- **R2**：生成途中（任一会话）被上限或服务错误打断的 trial 记为未测量（provider-error），**不重跑，也不从中断的阶段续跑**；其所在块只对涉及该组的假设失效。
- **R3**：服务区域或端点变化时，先用 discover 模式确认主机，设置 `KIRO_ALLOW_HOSTS`，重跑网络隔离控制并全部通过后才续跑，在附录中记录。
- **R4**：首轮与续跑相隔超过 6 小时的，报告按批次拆分描述（主分析不分层）。
- **R5**：通道之间并行不超过 4 个；同一块的四组在同一通道连续执行。驱动 `scripts/run_v8a.sh` 在评估时占用任意空闲的数据库槽位（1–4）。第一阶段总消耗（所有会话，含审查）达到 **360 credit**、或两阶段合计达到 **560 credit** 仍未完成时，停止所有通道并在附录中记录；剩余 trial 记为未测量，不改变假设、边界与检验（边界按计划的信息分数，不重算）。
- **R6**：v8a 与 v8b 的正式运行不同时进行。

## 排除规则

- 主分析不排除任何已观察 trial。
- 敏感性分析：排除任一会话有 `retrieval-attempt` 的 trial（连同其块中参与同一假设的另一组），重跑 H1–H3（H1 用同样的两阶段边界；是否执行第二阶段只由主分析决定）。
- 冻结后发现 Oracle 或条目正确性 Oracle 实现错误时：只能在附录中说明，并同时报告修正前后两版结果；不得只报告修正版。

## 与 v8b 的关系

v8b 有一个 send-back 任务：二审人在 72 小时内把 pending-second 的单**退回到 submitted**，并有职责分离要求。它与 T1 `return.v8` 形态接近，区别是：T1 的目标状态是新的 `returned`（之后不能再被任何既有操作处理），没有有效期，返回对象是 `{"id","status":"returned"}`。两个实验的任务、Oracle、结论分开，不合并分析。

## 附录（只追加）

### 附录 A：冻结清单（冻结前由操作员填写，现为空）

- [x] 公开平台 commit：`fad8adfa89edb753c55410518d531e21489f7d9b`（公开目录摘要 `sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055`），主机 `/opt/poc-oracle/public` 已核对。
- [x] 私有仓库提交 SHA（12 个计划的 `harness_sha`）：`9deabda1b54f692852222078094d91562dca7ff0`；树 hash：harness `eb55af6f3b39143e55a5d7a0685dc628d0e42f71`，tasks `2204b0a9d017b5b49cc41065f02875857186c697`。
- [x] 平台版本：T1 `opdef.go` `da9bc050…`、`opkernel.go` `55228e1b…`（公开原样）；T2 叠加 `opdef.go` `93c49f89d52c7a0085829a9f04458a637056001d133ad9b28fc5faa3867b08ba`、`opkernel.go` `ad38304d9d0e10049b1c580f482e0e0ba95f92082af8c5558fc86da243d77612`。
- [x] 参考目录摘要（与 12 个计划的 `verifier.reference_catalog_digest` 一致）：return `sha256:9e2131d8e2800c04122028003b44ec0775cf56f75e17eec139e74cea5777ca5c`，delivery-site `sha256:3988e28f6bc66c0a8c54f65d369d577c6c0b9f8ca5339748b6a295b1fe34a2fe`。
- [x] 提示词文件 `harness/prompts.json` SHA256：`5ca9adc34a00a6260346847f082cbf55bead75fb60da57ce36e1e85f2ba3ffd8`；审查者 claude-opus-5.5、effort medium。
- [x] 条目正确性 Oracle `harness/entry_oracle.py` SHA256：`fc83a01d7d69a9090d02065e82a10d6596e7cf0f2cd2c0622dba32e904eeb006`；`harness/catalogcheck.py` SHA256：`02c56216ee91c41bf82b3a6b611ed9d090007d5b007a55707fa9c33ed739af75`；阶段包装 `harness/stages.py` SHA256：`ea8e3844ce4b7f87683635d41a7a1ad49327b4cc6a602434994cb7a0ce250d1e`。
- [x] 分析脚本 `harness/analyze_v8a.py` SHA256：`e0d5a9cc27e171a62f79bb12f8d428097f223b02bb97070f1c8a475ba12b240d`；`harness/analyze_v8a_check.py` 通过。
- [x] 控制：验收主机 return 24 项、delivery-site 24 项全部通过（含条目正确性控制 2 × 27、排序负控制、私有内核回归）；本机 alpine 同样通过（辅助）；证据目录 `docs/evidence/v8a-controls-2026-11-16/`。
- [x] 跨主机假 kiro 流程：每任务四组各 1 个 trial 全部通过（证据目录同上）。
- [x] 验收主机数据库：`postgres@sha256:888402a8cd6075c5dc83a31f58287f13306c318eaad016661ed12e076f3e6341`（PostgreSQL 17.2 Debian/glibc，`en_US.utf8`，语言排序），每个计划的 `database` 字段与实测一致。
- [x] 网络白名单 `KIRO_ALLOW_HOSTS=cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`（顺序不变），冻结当天重跑网络隔离控制并通过（含两个私有仓库不可读）：2026-11-17 02:10 / 23/23 通过。
- [x] 第一阶段 6 个计划文件的 SHA256（每个 32 个 trial，块 1–8，种子 20261116）：`v8a-return-claude-opus-5.5`、`v8a-return-gpt-5.6-luna`、`v8a-return-qwen3-coder-next`、`v8a-delivery-site-claude-opus-5.5`、`v8a-delivery-site-gpt-5.6-luna`、`v8a-delivery-site-qwen3-coder-next`：
  - `v8a-return-claude-opus-5.5`：`21287c8a19b6e7fec229e81b5f6aedb61e7722bfb90bad31d37493e314ff0f33`
  - `v8a-return-gpt-5.6-luna`：`7534b01e4554259b56f4746687aed1a3e8241d6573235b710d8118094ca8e356`
  - `v8a-return-qwen3-coder-next`：`1de80c510062f5d72b5676c963a6a0819bb223babdddb9b75b815b0c957d5b93`
  - `v8a-delivery-site-claude-opus-5.5`：`346a7753c6f824a0e4b60eb08964d786caf72a12cfa8e5d2f197cf64e2b02a50`
  - `v8a-delivery-site-gpt-5.6-luna`：`549d32b103308da33d8ae11b69ae18ea03ee89325edd7539cbb45b910d5fb7f0`
  - `v8a-delivery-site-qwen3-coder-next`：`ef739887cf6bf66a2337b8fb8ac021c56eeaa18d15e730f0a2a8949adc18307f`
- [x] 第二阶段 6 个计划文件的 SHA256（每个 16 个 trial，given + drafted-review，块 9–16，种子 20261117，`STAGE=2 FIRST_INSTANCE=9 scripts/host_prepare.sh v8a-s2-<task>-<model> <task> <model> 8`），与第一阶段同一 `harness_sha`：kit 已 prepare，未执行。
  - `v8a-s2-return-claude-opus-5.5`：`52ccbc5a765bdfbfd076433631b6a5e2a9cab28b5da31e9aedf55dde5565e471`
  - `v8a-s2-return-gpt-5.6-luna`：`d1da7f0c3b9af3b26edc3a55f91fdcb588dfbe270c36352c0d161d7ee06d22ca`
  - `v8a-s2-return-qwen3-coder-next`：`6f06493977a655a2fdaf3c778dc619add3e9ef98f54711aec1cfe52ea0a22477`
  - `v8a-s2-delivery-site-claude-opus-5.5`：`76e7a8a512a8949ae5eefad3497b6c2266dfbd03b013d04d170daaed6548f9ba`
  - `v8a-s2-delivery-site-gpt-5.6-luna`：`b592ed996a287ebdc07cefb4b6fa366d652f291c020528a863d54ac6eac286a5`
  - `v8a-s2-delivery-site-qwen3-coder-next`：`58181c43daaf24207d6c4638a4f33823c711a25fd4a5461def01664ebf2bf21d`
- [x] 两阶段边界（`python3 -c "import sys; sys.path.insert(0,'harness'); import analyze_v8a as a; print(a.boundaries())"`）：α₁ = 0.0015253，c₁ = 2.9626，α₂ = 0.0245，c₂ = 1.9686；`harness/analyze_v8a_check.py` 本机与主机都 `all_ok`。
- [x] 预算：第一阶段守卫 360 credit，两阶段合计上限 560 credit。

### 附录 B（2026-11-17 02:48，第一阶段运行中；尚未查看任何验收结果，各通道都还在生成，没有一个 trial 完成验收）：预算调整

截至目前，已生成 56 个 trial，实测每块（4 组）的 credit 为：opus 约 12、luna 约 6.3、qwen 约 5.2。drafted-review 组比冻结时的估算贵很多，opus 每次约 5.4。据此推算第一阶段约 376 credit，超过了冻结的守卫值 360；如果维持 360，最后一批 trial 会被预算守卫截断，导致 H1 的有效对数下降，而这只是出于预算原因，与实验无关。

调整如下：
- 第一阶段守卫由 360 改为 **420**；
- 两阶段合计上限由 560 改为 **650**。第二阶段按实测单价推算约 227 credit。

该调整只涉及预算，不改动任何计划、假设、检验、边界或停止规则，也没有基于任何结局数据做出。操作上的做法是：终止运行中驱动的守卫子进程，换成一个等价的守卫，阈值改为 420，其余逻辑不变。

### 附录 C（2026-11-17 05:45）：第一阶段决策与第二阶段启动（R1b）

第一阶段 6 个通道全部完成，共 192 个 trial，0 个未测量，消耗 392.2 credit，在附录 B 的守卫 420 以内。冻结的 `analyze_v8a.py`（SHA256 `e0d5a9cc…240d`）给出：

- H1：48 个有效块；只有 drafted-review 通过的 1 块，只有 given 通过的 4 块；Tango z=0.747，c₁=2.963；`stage1_decision = continue`。

按停止规则，−c₁ < z < c₁ 时进入第二阶段。第二阶段按冻结的 6 个 `v8a-s2-*` 计划执行，计划、kit、种子都不变。两阶段合计上限仍为附录 B 的 650 credit，第二阶段可用约 258 credit。
