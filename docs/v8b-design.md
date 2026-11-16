# v8b 设计：更多、更开放的任务上，平台给定语义的收益能否复现

本文档只在私有仓库中。预注册草稿见 [prereg/preregistration-v8b.md](../prereg/preregistration-v8b.md)（未冻结）。v7 的设计和证据仍在 `main` 分支（`docs/v7-design.md`、`docs/evidence/`）。

## 目的

v7 的 A 从 55%（手写）提高到 99%（目录 + 内核），但只测了两个由我们设计、形状相近的任务。v8b 换成 3 个形状不同、需求写得更开放的新任务，只比较两组：

| 组 | 导出 | 新操作的语义从哪里来 | 谁执行 |
|---|---|---|---|
| `go-manual`（manual） | v6 世界的手写代码 | 从分散的代码、SCHEMA.sql、notify、ledger、reconcile 推断 | 作者 |
| `go-action-opdef`（given） | 受保护的 `internal/opdef`（含本任务条目）+ `internal/opkernel` + 适配 | 目录给出 | 内核 |

不做 doc 组：v7 已经证实文档组的中间位置（H2、H3 成立），v8b 只回答 H1 能否复现，省下约三分之一的 credit。

## 三个任务

形状与 v1–v7 不同（v7 是单目标状态迁移 withdraw 和带参数的字段修改 cost-center），也避开了 v8a 正在做的方向（v8a 的任务不在本仓库可见，我们按用户给出的三个方向各选一个，且不复用任何 v7 操作名或表）。

| | T1 `bulk-close.v8` | T2 `send-back.v8` | T3 `bulk-amend.v8` |
|---|---|---|---|
| 方向 | 跨实体规则：目标是否合法取决于另一张表 | 带审批链和有效期的状态迁移 | 全部或全不的批量，校验涉及多个字段，每个目标有自己的值 |
| 接口 | `POST /purchases/bulk-close` `{"ids"}` | `POST /purchases/{id}/send-back` `{"reason"}` | `POST /purchases/bulk-amend` `{"items":[{"id","amount"}],"reason"}` |
| 角色/对象 | approver，他人创建 | approver，他人创建 | requester，本人创建 |
| 业务规则 | approved → closed；accepted 的 GoodsReceipt 金额之和 ≥ 采购金额（pending/rejected 不计） | pending-second → submitted；最近一次一审的审批人本人不能退回；只能在最近一次一审后 72 小时内 | 只改 amount；涨幅 ≤ 10%；不能跨越 50000 二级审批线（两个方向） |
| 新表/列 | `GoodsReceipt`（只读，fixture 数据）；状态 `closed` | 平台守卫允许 pending-second → submitted（且不能是一审人） | `AmendmentHistory`；`UPDATE (amount)` 权限 |
| 隐含约定 | **audit**（approved→closed，reason NULL）、**request-hash**（`Fingerprint(op, ids)`）、**outbox** `purchase.closed`、**uniform-denial**（缺失/他租户/本人/未审批/未验收齐全同一状态） | **audit**（pending-second→submitted，含 reason）、**request-hash**（reason 不进指纹）、**outbox** `purchase.sent_back`、**uniform-denial**（含一审人本人、超过 72 小时） | **history**（AmendmentHistory：from/to、reason，并发成一条链）、**request-hash**（`Fingerprint(op, ["id=amount",…])`，reason 不进指纹）、**outbox** `purchase.amended`、**uniform-denial**（金额不合规与目标不合格同一状态） |
| 用例 | 显式 49 + 隐含 9 = 58 | 显式 50 + 隐含 9 = 59 | 显式 55 + 隐含 12 = 67 |

每个任务保留 v7 的四条约定，K、D 的定义与 v7 相同，便于对照。各任务里最难推断的一条：T1 是“未验收齐全”也要按缺失处理；T2 是审批链里的“最近一次一审”和 reason 不进指纹；T3 是逐项指纹格式和“金额违规按目标不合格处理”（reassign 先例：参数不合格与目标不合格同一拒绝）。

线索（`cohort.json` 的 `sources`，静态控制 `clues_present` 与 `every_convention_has_clues_in_both_cells` 校验每条约定在两组各至少 2 条）：

| 约定 | manual 组的线索 |
|---|---|
| audit | 先例写 ApprovalAudit（reject/reopen/countersign）；`reconcile.Drift`（状态必须等于最近审计行的 toStatus）；SCHEMA 中 fromStatus/toStatus 的取值 |
| history | SCHEMA 的 `AmendmentHistory`；`reconcile.AmountDrift`；reassign 写 AssignmentHistory；“ApprovalAudit 只记录状态迁移” |
| request-hash | 先例的 `ledger.Fingerprint(op, ids)`；`ledger.subject` 的注释（单目标 reason 不进指纹；逐项响应按 `id=amount` 摘要）；`ledger.Verify` |
| outbox | `notify.audience` 中本任务事件的受众分支；先例写 PurchaseEvent；“审批早于 outbox” 的豁免说明 |
| uniform-denial | “Actions never reveal existence”；countersign 的单一拒绝；reassign “参数不合格与目标不合格同一拒绝” |

任务说明不含约定词（禁词表同 v7，另加“同一个 4xx”“留痕”）。

## 开放需求的写法

v7 的需求是一段技术规格，直接点名适配器（`protocol.Item`、`protocol.Param`）和接入方式。v8b 改为三段：

1. **背景**：用业务语言说明谁、为什么需要这个操作；
2. **业务规则**：只写可验收的规则（谁能做、对什么对象、什么条件、全部或全不、做完后状态）；
3. **验收要点**：响应、幂等、ActionRequest、失败回滚、并发、认证这些 Oracle 要检查的行为。

不点名适配器、不指定实现（提示词只说“通过 internal/protocol 的共享适配器提供新路径”），不给 SQL、不给数据结构。例（T1 节选）：

> 背景：采购单审批通过（approved）后，仓库按到货登记验收单（表 GoodsReceipt，每次到货一行……）。财务希望审批人能在货物验收齐全后，把采购单关闭归档，一次可以处理一批。
>
> 业务规则：……采购单只有在验收齐全时才能关闭：它所有 accepted 的验收单金额之和不低于采购金额。pending 和 rejected 的验收单不计入。一批要么全部关闭，要么全部不关闭……

全文在 `tasks/<task>/cohort.json` 的 `requirements`。

## 目录与内核的私有叠加

公开平台不改。私有叠加（`harness/source/go-action-opdef/{opdef,opkernel}.go.txt` + `tasks/<task>/catalog.go.txt`）对公开 `fad8adf` 的改动：

| 文件 | 改动 | 为什么 |
|---|---|---|
| `opdef.go` | `Kind` 增加 `Amend`；`Record` 增加 `AmendmentHistory`（去掉 v7 的 `CostCenterHistory`）；`Operation` 增加 `Requires`（前置条件名，当前只有 `FullyReceived`）、`WithinHours`、`MaxIncreasePercent`、`KeepSideOf`；输入名 `Amounts`；`Validate` 相应扩展 | T1 的跨表前置条件、T2 的时间窗、T3 的逐项值和多字段规则都不是公开目录能表达的 |
| `opkernel.go` | `Request.Amounts`；transition 按 `Requires` 追加 SQL 谓词（只在条目需要时拼入，因为别的任务没有 GoodsReceipt 表）和“最近一次进入 From 的审计行在 N 小时内”；新的 `amend`（锁定、按 id 配对旧值、涨幅和分界线检查、逐项更新、写 AmendmentHistory）；Amend 的指纹把值并入摘要项 `id=value` | 同上 |
| `catalog.go` ×3 | 公开 5 个条目逐字保留 + 本任务条目 | 绑定 PR #27 |

不新增导出类型，Verifier 的内核类型分类表不受影响。目录摘要（私有 `appctl` 自己报告）：

| 目录 | 摘要 |
|---|---|
| close | `sha256:ca1c5beb1cf17d053ec83d7d6f6756b2d559d8136f9d00ad4f0652f895827c73` |
| send-back | `sha256:934b6ac6317579823b616c543d156409d283239dbfe9ecef49e42409163dfe38` |
| amend | `sha256:53a34a71f35e89e4f61fd0af12791a808396d3c6839986d1a5fd5bf3deac3439` |

共享导出（两组都有）也有两处改动：`protocol.Amend`（逐项请求的适配器，requester）和 `ledger.subject`（逐项响应按 `id=amount` 摘要，使 `ledger.Verify` 能核对 T3 的行）。

私有内核回归 `harness/kernel_regression.py`：在 ICU en-US 和数据库默认排序下，字段修改（assigneeId）和 amend（amount）都按 id 配对旧值；两个按位置配对的负控制必须失败。

## Oracle

沿用 v7 的独立 Oracle 框架（逐步骤状态摘要、差异、七类 safety 分类、18 例回归），只替换 fixture 和任务用例：

- fixture 前缀 `v8-<seed hash>-`；字节序与语言排序不同的 ID：`Zed`/`Cz`/`Az`（大写在字节序中排在小写前，语言排序相反），用于排序和旧值配对。
- T2 的时间窗用 fixture 中审计行的 `createdAt`（相对 Oracle 启动时刻 70 h、74 h、100 h/99 h/1 h），离 72 小时边界各 2 小时。
- `GoodsReceipt` 计入 Oracle 的静态表：成功请求改动它算 `wrong_commit`。
- 只有 alice、bob、carol、dave、erin 是演示身份，用例中的操作者只用这五个人。

## 控制

每个任务的控制（`tasks/<task>/controls.json`）断言结局、失败分组集合、非零 safety 类别集合三者完全相等，外加 Verifier 断言（公开 `appctl` 不得 pass；given 组候选必须报 `CATALOG_DRIFT`；参考实现的私有 Verifier 判定见下）。

| 种类 | close | send-back | amend |
|---|---|---|---|
| 正确参考（两组） | 2 | 2 | 2 |
| 约定负控制（manual） | audit、request-hash、outbox、uniform-denial | 同左 | history、request-hash、outbox、uniform-denial |
| 显式负控制 | manual 响应字段名错；given 适配把所有拒绝改成 403 | manual 响应状态写错；given 适配返回 From 状态 | manual 响应字段名错；given 适配把 409 改成 422 |
| 崩溃 | goroutine panic → candidate-error | 同左 | 同左 |
| safety 人造候选 | unauthorized_write（不查验收单）、partial_batch（缺失目标跳过，其余提交）、side_effect_atomicity（事件提交后另起事务）、double_apply（不加锁 + sleep + 不查影响行数）、wrong_commit（顺手清空 assigneeId）、service_error（缺失 503）、undecidable（Unicode 目标挂起 12 秒） | 同左，除 partial_batch（单目标，分类器只对 ≥ 2 个目标的拒绝步骤计 partial_batch） | 同左；partial_batch 为“遇到第一个违规项前的项先提交” |
| 排序负控制 | 响应按数据库顺序 | — | 旧值按数据库顺序配对；响应按数据库顺序 |
| 合计 | 17 | 15 | 18 |

amend 的 manual 参考实现私有 Verifier 判 `fail`（`FINGERPRINT_INPUT_MISSING`），控制记录这一期望：公开 Verifier 的指纹规则只认“参数并入摘要域（name+"\n"+value）”，逐项把值并入摘要项不在它的模型内。这是 Verifier 的局限，不影响 A/E/K/D；预注册中 Verifier 对照表对 amend/manual 单独标注。

静态检查：线索齐全且两组各 ≥ 2 条、任务说明无禁词、两组提示词只差 capability 行、kit 只含服务源码、导出中没有其他任务的操作名、vendored worker 与公开逐字节相同（设置 `PUBLIC_WORKER` 时）。

交接控制：kit 中的 worker.py 是公开 `generation_worker.py`，生成端在 `isolate_net.py` 沙箱中用 `harness/fake_kiro.py` 作 `--synthetic-test-driver`，走完推回、restore、evaluate、summarize（每任务两组各 1 个 trial）。

## 跨主机流程（与 v8a 隔离）

| 项 | v8b | v8a |
|---|---|---|
| 本机 checkout | `/Users/administrator/poc-v8b-private`（分支 `v8b`） | `/Users/administrator/poc-v7-private`（不动） |
| 主机 checkout | `/opt/poc-oracle/private-v8b`（`scripts/sync_to_host.sh` 默认值，拒绝写 `/opt/poc-oracle/private`） | `/opt/poc-oracle/private` |
| 主机 runs | `/opt/poc-oracle/runs/v8b-*`（prepare/evaluate 拒绝其他前缀） | 其他 |
| 数据库/HTTP 端口 | 56701–56704 / 19701–19704，与 v8a 共用、顺序使用 | 同 |
| 容器 | `poc-oracle-pg-<lane>`，标签 `poc.owner=v8b`；`host_db.sh down` 只删 v8b 的容器 | — |
| 生成端 deny | 两个私有 checkout、`~/.paseo`、公开 checkout、`~/.kiro`、`~/.codex`、其他 kit/结果 | — |
| 本机开发数据库 | `ea-v8b-dev`，127.0.0.1:55660，HTTP 18260 | — |

模型端点白名单（`scripts/generate.sh` 默认值，顺序固定，runtime 在最后）：`cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`。前两个是认证刷新主机，v7 有 1 个 trial 因缺少它们报 `SignInRequiredError`。

驱动 `scripts/run_v8b.sh`：通道开始时用 `mkdir` 原子地占用任意空闲槽位（1..4），修正 v7 按固定顺序分配导致只跑 3 个通道的问题。

## 与用户给定设计的偏离及理由

1. **每个任务 4 条隐含约定，不是 1 条。** K、D 沿用 v7 定义才能对照；每个任务有一条形状特有、最难推断的约定（见上）。
2. **send-back 没有 partial_batch 控制。** 单目标操作不可能部分提交；其余 6 类都有。
3. **amend 的 manual 参考实现私有 Verifier 不通过。** 见“控制”。不扩展 Verifier，因为 v8b 不评估 Verifier，改它会让 v7 的样本外结论失去可比性。
4. **需求不点名适配器。** 这是“更开放”的一部分；提示词仍说明新路径要经过 `internal/protocol` 的共享适配器。T3 的适配器 `protocol.Amend` 是共享导出的一部分，两组都能看到。
5. **v8b 分支删除了 v7 的任务目录、`opdoc.py`、`analyze_v7*.py`。** 它们在 `main` 上完整保留；v8b 的计划只冻结 v8b 用到的文件。

## 已知局限

- 任务、约定、目录条目都由我们设计；given 组中 K、D 按构造成立。
- 内核为三个任务各加了一个能力（前置条件、时间窗、逐项修改），这些能力是先写好再测的。真实平台需要平台方先正确实现它们。
- T2 依赖 Oracle 主机与数据库时钟一致（同一主机），边界两侧各留 2 小时。
- 双写类人造候选靠 `pg_sleep(0.3)` 扩大竞争窗口。
