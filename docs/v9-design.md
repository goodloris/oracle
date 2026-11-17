# v9 设计：平台事件规则下，Agent 起草的条目能否达到给定条目的水平

本文档只在私有仓库中。预注册草稿见 [prereg/preregistration-v9.md](../prereg/preregistration-v9.md)。v8a 的设计见 [v8a-design.md](v8a-design.md)，本文只写 v9 新增或改变的部分。

## 平台事件规则（私有叠加，待移植到公开平台）

`harness/platform_rules.py` 是规则的唯一定义：`apply(opdef.go)` 做 4 处逐字唯一的修改，`registry(notify.go)` 生成注册表。规则平台的文件在导出时生成，不另存副本；静态控制核对导出与这两个函数的输出一致。对公开 `fad8adf` `opdef.go` 的完整差异见 `docs/evidence/v9-controls-2026-11-17/opdef-event-rules.diff.txt`。

1. `Validate()` 对每个条目在 `o.validate()` 之后调用 `o.eventRule()`：
   - **R2**：`EventExemption` 非空而操作不在 `exemptOperations` 中 → `event exemption is reserved for the operations on the protected exemption list`；
   - **R1**：操作不在豁免名单中，而 `Event` 不在 `consumedEvents` 中 → `event "<e>" has no registered consumer in internal/notify`（空事件也落在这里，但既有规则“Event 与 EventExemption 恰好一个”会先拒绝）；
   - 事件重复由既有检查拒绝。
2. `internal/opdef/registry.go`：`consumedEvents` 由 `internal/notify/notify.go` 中 `audience()` 的 `case` 分支生成；`exemptOperations = []Name{"bulk-approve.v3"}`。平台维护、受保护：起草会话只能改 `catalog.go`，改了 registry.go 就是越界。
3. `registry.go` 加入 `//go:embed` 与 `SourceFiles()`，所以 `appctl operations` 要求 checkout 中的 registry.go 与 Verifier 构建时的平台逐字节相同（否则 `CATALOG_DRIFT`）。

规则作用于全部条目。公开 5 个条目都满足：bulk-approve 在豁免名单中，其余 4 个事件都有消费者。为此起点 notify 增加了 `purchase.countersigned → creator` 的分支（v8a 起点没有；它是新消费者，不是新线索）。

**后果（如实说明）**：注册表只有 5 个事件，其中 4 个已被公开条目占用，所以在规则平台上，新条目唯一能通过 Validate 的事件就是本任务的事件（控制 `registry_has_exactly_one_unused_event`）。也就是说，R1 + 唯一性 + 起草时可运行 Validate，使 event 字段基本被确定性地给出。这正是被测试的处理条件；它能否推广到注册表里有多个未用事件的情形，v9 不回答。

### 起草时运行 Validate

kit 中有受保护的命令 `cmd/opcheck`（模板 `harness/source/common/opcheck.go.txt`），`go run ./cmd/opcheck` 调用 `opdef.Validate()` 并打印结果。两个起草组的提示词逐字相同，都写明可以运行它。理由：这代表平台在 CI 里给出即时反馈，属于被测试的处理条件；norule 组拿到同一反馈通道（只是没有 R1、R2），所以 H2 的差别只来自规则。没有用 `internal/opdef` 下的测试文件，是因为 Verifier 要求目录包只含平台列出的源文件。

## 任务

| | T1 `hold.v9` | T2 `bulk-project.v9` |
|---|---|---|
| 类型 | 状态迁移，单目标，带 reason | 字段修改，批量，带参数 |
| 接口 | `POST /purchases/{id}/hold`，`protocol.Item`（verb hold，role approver） | `POST /purchases/bulk-project`，`protocol.Param`（参数名 project） |
| 权限/对象 | approver，他人创建 | approver，他人创建 |
| 状态 | submitted → on-hold（新状态；之后不能被审批、驳回、重新提交、改派） | 保持 submitted，改 `projectId`，值须为本租户 active 的 Project |
| 显式分组 | functional 3、authorization 3、eligibility 7、input 6、idempotency 3、concurrency 2、failure-rollback 1、lifecycle 4，regression 18 | functional 4、authorization 3、eligibility 5、value 3、input 7、idempotency 4、concurrency 2、failure-rollback 1、lifecycle 2，regression 18 |
| 隐含约定 | audit 2、request-hash 2、outbox 2、uniform-denial 2 | history 4、request-hash 2、outbox 3、uniform-denial 2 |
| 用例数 | 47 + 8 = 55 | 49 + 11 = 60 |
| 数据库层 | status 与 ApprovalAudit 约束增加 on-hold；approver 的 UPDATE 策略允许写入 on-hold | 新表 Project、ProjectHistory；`projectId` 列与受管列叠加 |

Oracle 用例由 v7 的 withdraw / cost-center 用例改写（角色、对象、状态、表名），safety 定义与 v7/v8a 相同。T1 的 notify 消费者为 `purchase.held → creator`，T2 为 `purchase.project_changed → assignee`。每个字段的唯一确定依据与 v8a 的“唯一性”表相同（T1 对应 v8a 中去掉职责分离的状态迁移，T2 对应 cost-center 形状的受管列）。

## 三组

| 组 | 导出差别 |
|---|---|
| given | 规则平台 opdef.go + registry.go + opkernel.go + `cmd/opcheck`，目录含参考条目 |
| drafted-rule | 与 given 只差 `catalog.go`（公开原样 `736429c1…`） |
| drafted-norule | 与 drafted-rule 只差 opdef.go（不带规则）且没有 registry.go |

控制：`drafted_rule_export_is_given_without_the_entry`、`drafted_norule_export_is_rule_export_without_the_rules`、`registry_is_generated_from_notify`、`public_catalog_validates_on_both_platforms`、`reference_catalog_validates_on_both_platforms`（两个平台上参考目录摘要相同）。

计划只有单阶段，种子 20261118；kit 视图、分阶段 receipt（v81）、主机复核、按 trial 绑定目录的私有 Verifier 都与 v8a 相同。私有 Verifier 按 trial 的组选择平台版本（given、drafted-rule 规则平台，drafted-norule v8a 平台）。

## 控制

规则控制（`tasks/<task>/entry_controls.json` 的 `rule_controls`，每任务 6 项，只需 Go），断言两个平台上 Validate 的结果：

| 控制 | 规则平台 | v8a 平台 |
|---|---|---|
| 参考条目 | 通过 | 通过 |
| 缺 event（既无 Event 也无豁免） | 拒绝 | 拒绝 |
| 事件不在注册表（例如 `purchase.on_hold`） | **拒绝**（R1） | 通过 |
| 事件与其他条目重复 | 拒绝 | 拒绝 |
| 自行声明豁免 | **拒绝**（R2） | 通过 |
| 公开目录（bulk-approve 的豁免） | 通过 | 通过 |

条目正确性控制（每任务 27 项）、静态检查与 v8a 相同或按上节调整。真实 PostgreSQL 控制（hold 23 项、project 26 项）：

- 三组参考实现：通过，私有 Verifier pass，摘要与 trial 目录一致；
- 手写参考（在 given 组中为新操作手写处理函数，不走内核）：通过；它是约定负控制、safety 人造候选、排序负控制的基础（v9 没有手写组，这些控制仍然检验 Oracle 本身）；
- 草稿错误：漏 event（改为豁免）与事件不在注册表——norule 组下游 implicit-outbox 失败，rule 组 `draft-invalid`；Validate 不通过（两组）→ `draft-invalid`；规则之外的错误（T2 指纹缺 project → 幂等组失败；record 写错 → 内核拒绝启动）在 rule 组仍由完整 Oracle 识别；
- 约定负控制、显式负控制、崩溃、safety 人造候选、排序负控制、T2 私有内核回归：与 v8a 相同（T1 的 unauthorized_write 人造候选改为“拒绝但先提交一行 outbox”，因为 approver 的 RLS 策略让 v7 用的状态改写不可见）。

## 与给定设计的偏离及理由

1. **规则作用于全部条目**，而不只是“新增”条目：平台无法在 Validate 中区分新旧条目，而公开条目本来就满足规则（豁免名单 + 消费者）。为此 notify 增加 countersigned 消费者。
2. **Validate 命令是 `cmd/opcheck`**，不是 `internal/opdef` 下的测试（见上）。
3. **H1 的功效不足 0.8、估算消耗高于 350**：见预注册，给出方案，未自行改变规模。
4. **T1 没有排序负控制**：v8a T1 的排序负控制改的是手写改派，v9 没有手写组。T2 保留两项。

## 已知局限

- 注册表中只有一个未用事件，规则使 event 字段几乎确定；v9 测的是“确定性规则 + 即时反馈”的组合效果，不能分开。
- 两个任务都是简单形状；opus 在 v8a 全对，结论主要取决于 luna 和 qwen。
- 本机控制用 postgres:17-alpine（字节序排序），主机控制（glibc 语言排序）是权威结果。
