# v8a 设计：Agent 起草的目录条目能保留多少收益

本文档只在私有仓库中。预注册草稿见 [prereg/preregistration-v8a.md](../prereg/preregistration-v8a.md)。v7 的设计见 [v7-design.md](v7-design.md)，本文只写 v8a 新增或改变的部分。

## 仓库布局（v8a 分支）

| 路径 | 内容 |
|---|---|
| `harness/ctl.py` | v8a 控制器：fixtures / plan / prepare / export / restore / evaluate / summarize，四组，分阶段 receipt，按组裁剪的 kit 计划视图 |
| `harness/prompts.json` | 全部提示词（manual、given、起草、审查；适配 = given），审查者模型 |
| `harness/stages.py` | 生成端阶段包装：导入 kit 中的公开 worker，逐阶段调用 `invoke()`，逐阶段检查范围、提交、目录检查 |
| `harness/catalogcheck.py` | 编译 `internal/opdef` 并报告 `Validate()`、`All()`、`Digest()`；生成端与主机共用 |
| `harness/entry_oracle.py` | 条目正确性 Oracle（结局 C），等价规则 E1–E6 |
| `harness/oracle.py` | 完整 Oracle（v7 框架；T1 `return`、T2 `delivery-site` 的用例） |
| `harness/controls.py` | 静态检查、条目正确性控制、真实 PostgreSQL 控制（含分阶段组）、Verifier 断言 |
| `harness/verifier.py` | 私有 Verifier：公开 `fad8adf` + 任务平台版本 + **trial 的目录** |
| `harness/analyze_v8a.py`、`analyze_v8a_check.py` | 预注册分析与统计自检 |
| `harness/kernel_regression.py`、`harness/kernel/opkernel_v8a_test.go.txt` | T2 叠加内核的按 id 配对回归（ICU 与数据库默认排序）+ 按位置配对的负控制 |
| `harness/source/platform/` | 公开 `fad8adf` 的 `catalog.go`、`opdef.go`、`opkernel.go` 原样（T1 平台；drafted 组的起点目录） |
| `tasks/return/`、`tasks/delivery-site/` | `cohort.json`、`schema.sql`、`notify`/`reconcile`、参考目录 `catalog.go.txt`、`ref/`、`controls.json`、`entry_controls.json`；T2 另有 `platform/` 叠加 |
| `scripts/` | 同 v7，`run_v8a.sh`（任意空闲槽位）、`sync_to_host.sh`（支持分支）、`host_db.sh`（容器带 `poc.owner=v8a` 标签） |

v7 的任务目录和 v7 分析脚本仍在树中，只用于追溯；v8a 计划只冻结 `harness/`、`tasks/<v8a 任务>/` 和 `benchmark/`。

## 任务

| | T1 `return.v8` | T2 `bulk-delivery-site.v8` |
|---|---|---|
| 类型 | 状态迁移，单目标，带 reason | 字段修改，批量，带参数 |
| 接口 | `POST /purchases/{id}/return` `{"reason"}`，`protocol.Item` | `POST /purchases/bulk-delivery-site` `{"ids","site"}`，新的通用适配 `protocol.ParamAs`（`Param` 改为委托它，行为不变） |
| 权限/对象 | approver，他人创建，且不是批到 pending-second 的那位审批人 | requester，本人创建 |
| 状态 | pending-second → returned（新状态，之后不能被会签、审批、驳回、重新提交、改派） | 保持 submitted，改 `deliverySiteId`，值须为本租户 active 的 DeliverySite |
| 显式分组 | functional 3、authorization 3、eligibility 7、input 6、idempotency 3、concurrency 2、failure-rollback 1、lifecycle 5，regression 18 | functional 4、authorization 3、eligibility 5、value 3、input 7、idempotency 4、concurrency 2、failure-rollback 1、lifecycle 2，regression 18 |
| 隐含约定 | audit 2、request-hash 2、outbox 2、uniform-denial 2 | history 4、request-hash 2、outbox 3、uniform-denial 2 |
| 用例数 | 48 + 8 = 56 | 49 + 11 = 60 |
| 数据库层 | `purchase_tier_guard` 扩展：pending-second 只能进入 approved 或 returned，且两者都要求不是第一级审批人 | 新表 DeliverySite、DeliverySiteHistory；requester 可更新本人 submitted 的采购单 |

与 v8b 的 send-back 的差别：send-back 退回到 submitted，有 72 小时有效期；T1 进入新状态 returned，没有有效期，返回 `{"id","status":"returned"}`。两个实验分开解读。

### 唯一性：每个字段从哪里确定

drafted 组看到的是：需求（TASK.md 与提示词）、公开 5 个条目、`opdef.go`（字段语义与校验）、`opkernel.go`、`notify.go`、`reconcile.go`、`ledger.go`、`SCHEMA.sql`、适配代码。

| 字段 | T1 | 依据 | T2 | 依据 |
|---|---|---|---|---|
| name | `return.v8` | 需求写明 operation | `bulk-delivery-site.v8` | 需求写明 operation |
| kind | transition | 需求：status 变化 | field-update | 需求：status 不变，改一列 |
| role | approver | 需求 | requester | 需求 |
| subject | others | 需求：他人创建 | own | 需求：本人创建 |
| from / to | pending-second / returned | 需求 | — | Validate：field-update 不得有 from/to |
| status | — | Validate：transition 不得有 status | submitted | 需求 |
| column | — | 同上 | deliverySiteId | 需求；`opkernel` 受管列；SCHEMA.sql |
| param | — | 同上 | site | 需求的请求字段名；Validate 要求 param 是 input |
| response | ""（单目标） | 需求的响应 `{"id","status"}`；单目标条目约定（reopen、countersign） | changedIds | 需求的响应字段名 |
| inputs | ids, reason | 需求的请求体；Validate 要求 ids 在首位 | ids, site | 同上 |
| fingerprint | ids | Validate 禁止 reason 进指纹；`ledger.go` “reason … is not part of the digest” | ids, site | 需求：同 key 不同 site 返回 409；`ledger.go` “operation+\n+value”；bulk-reassign 先例 |
| record | ApprovalAudit | Validate：transition 只能用 ApprovalAudit | DeliverySiteHistory | `opdef.go` 常量注释“deliverySiteId changes”；`opkernel` 受管列的 record；SCHEMA.sql；`reconcile.go` 的 `DeliverySiteDrift` |
| event | purchase.returned | `opdef.go`“Event is required by default”；`notify.go` 已有 `case "purchase.returned"` | purchase.delivery_site_changed | 同上，`notify.go` `case "purchase.delivery_site_changed"` |
| separation | distinct-from-previous-approver | 需求：批到 pending-second 的那位审批人不能退回；`opdef.go` 常量注释；SCHEMA.sql 守卫 | — | Validate：separation 需要 subject others |
| tier / confirmAmount | — | 单目标、无 amount 输入 | — | field-update 不得有 tier；无 amount |
| denial | 409 | Validate 只允许 UniformDenial | 409 | 同左 |

## 四组的流程

```
manual / given        kit: candidate + worker.py (公开)           worker.py → 1 次会话 → 提交 → receipt v3
drafted               kit: + stages.py + catalogcheck.py          stages.py → draft(只改 catalog.go) → 提交 → Validate
                                                                            → adapter(只改 internal/candidate) → 提交 → receipt v81
drafted-review                                                    draft → 提交 → Validate → review(opus, 只改 catalog.go) → 提交 → Validate
                                                                            → adapter → 提交 → receipt v81
```

- 每个会话用新的 `KIRO_HOME`（公开 worker 的 `invoke()` 在 `<output>/<stage>/kiro-home` 下创建），没有会话记忆可以继承。
- receipt v81 列出每个阶段的提交、`generation`（原始 usage、秒数、模型）和目录检查；`generation` 合计各阶段的 credit 与秒数。只要有一个阶段提交，就打 bundle，主机可以对已冻结的条目计算 C。
- 主机 `restore`/`evaluate` 校验提交链：每个阶段恰好一个提交，父提交是上一阶段，改动只在该阶段允许的路径内。
- 起草或审查后目录无效 → `draft-invalid`；主机用同一 `catalogcheck` 复核。审查会话未完成时，drafted-review 没有冻结条目，C=0。

## 结局 C（条目正确性 Oracle）

`entry_oracle.compare` 对编译后的目录（`catalogcheck` 输出的 `opdef.All()`）比较参考条目，等价规则 E1–E6 写在模块文档和预注册中。控制（`tasks/<task>/entry_controls.json`，每任务 27 项，只需 Go）：

- 正确：参考条目；字段顺序打乱；全部用字面值；换常量名；条目放到第一位并显式写零值 —— 都必须判为正确。
- 字段错误：19 个逻辑字段每个至少一个变体（event 两个：改成豁免、改名），**每个变体只能让对应字段错**；同时断言该变体能否通过 Validate（有些错误 Validate 会拦下，这些在实验中表现为 draft-invalid）。
- 缺失条目（公开目录原样）：除 catalog 外全部字段错；不能编译：全部字段错。

## 完整 Oracle 与控制

完整 Oracle 沿用 v7 框架与 safety 定义（逐步骤状态码 + 数据库前后摘要）。每任务 24 项真实 PostgreSQL 控制，每项断言结局、失败分组集合、非零 safety 类别集合，分阶段组还断言 C、C_draft 和错误字段：

| 种类 | return | delivery-site |
|---|---|---|
| 正确参考（必须全过，私有 Verifier 必须 pass 且绑定 trial 目录摘要） | manual、given、drafted、drafted-review（审查不改）、drafted-review（草稿缺事件，审查修正） | manual、given、drafted、drafted-review（审查不改）、drafted-review（草稿指纹缺 site，审查修正） |
| 约定负控制（manual） | audit、request-hash、outbox、uniform-denial | history、request-hash、outbox、uniform-denial |
| 显式负控制 | manual 响应状态写错；manual 漏掉职责分离（守卫拒绝 → 503）；given 与 drafted 适配返回 From | manual 响应字段名错；given 参数名照抄 assignee |
| **草稿错误 → 完整 Oracle 能识别** | 漏事件（改为豁免）→ implicit-outbox；漏职责分离 → eligibility + uniform-denial + service_error；Validate 不通过 → draft-invalid；审查改坏 → draft-invalid | 指纹缺 site → idempotency + request-hash；record 写成 AssignmentHistory → 内核拒绝启动 → candidate-error；漏事件 → implicit-outbox；Validate 不通过 → draft-invalid；审查改坏 → draft-invalid |
| 崩溃 | 首次请求时 goroutine panic → candidate-error | — |
| safety 人造候选 | unauthorized_write、double_apply、side_effect_atomicity、service_error、undecidable | partial_batch、wrong_commit、double_apply、side_effect_atomicity、unauthorized_write、service_error |
| 排序负控制 | 手写改派按数据库顺序配对（回归） | 历史按数据库顺序配对；响应按数据库顺序 |
| 公开 Verifier | 不得 pass；内核组必须报 `CATALOG_DRIFT` | 同左 |

静态检查：线索齐全（manual、given、drafted 三种导出各自的线索清单）、需求无禁词、manual 与 given 提示词只差 capability 行、适配阶段提示词 = given 提示词、drafted 导出 = given 导出去掉条目（`catalog.go` 为公开原样 `736429c1…`）、参考目录 = 公开目录 + 一项条目、参考目录通过 Validate、T1 平台与公开 `fad8adf` 逐字节相同、kit 只含服务源码、导出不含另一任务的操作名、drafted 导出不含条目、**kit 计划视图不含任何由参考条目派生的值**（事件名、参考目录 hash、参考摘要、given 导出的 catalog hash）、vendored worker 与公开逐字节相同。

## Verifier 绑定

计划冻结“规则与平台版本”：公开 commit、公开目录摘要、任务平台文件的 hash、被替换的上游文件 hash，以及参考目录摘要（只在主机计划中，kit 视图中去掉）。每个 trial 的 `result.json` 记录 `catalog.sha256` 与 `catalog.digest`（trial 实际使用的目录），私有 `appctl` 用这份目录构建，报告的摘要必须与之相同，否则在 `verifier.error` 中标出。分阶段组的 `--base` 是冻结目录的那次提交，所以目录本身不算越界改动。

## 跨主机流程

与 v7 相同，差别：

- 主机 checkout 在 `/opt/poc-oracle/private-v8a`（v8b 用自己的目录），`sync_to_host.sh [host] [dir] [branch]` 默认同步当前分支；主机 checkout 有未提交改动时拒绝。
- 运行目录必须以 `v8a-` 开头（`host_prepare.sh`、`host_evaluate.sh`、`run_v8a.sh` 都检查），控制目录为 `runs/v8a-controls-<task>-<时间>`。
- `host_db.sh` 给容器加 `poc.owner=v8a` 标签：`up` 在槽位被占用时失败，`down` 只删除本实验的容器。开发阶段只用槽位 4（56704 / 19704），v8b 用 1–3。
- `run_v8a.sh`：生成不占数据库；评估开始时用 `mkdir` 原子地占用任意空闲槽位，结束时释放。v7 的驱动按 `i % 4 + 1` 的固定顺序分配槽位，前一个通道占着该槽位时，即使别的槽位空闲也要等。
- `generate.sh`：默认白名单 `cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev`；deny 两个私有仓库；分阶段 kit 用 isolate_net 的命令覆盖运行 `stages.py`，超时 2400 秒（最多 3 个 600 秒的会话）。

## 与给定设计的偏离及理由

1. **T2 的平台版本带私有叠加**（`DeliverySiteHistory` 记录与 `deliverySiteId` 受管列），与 v7 T2 相同的理由：公开内核只管理 `assigneeId`。drafted 组因此能从平台代码中看到这一列已就绪，这是真实场景（平台先支持列，业务方再加条目）；字段 record 仍可能写错（Validate 允许两种 history 记录，内核启动时才拒绝）。
2. **起草阶段的 Validate 检查在生成端执行，主机复核**。只在主机检查会让无效草稿也消耗适配会话的 credit。
3. **kit 中的计划改为按组裁剪的视图**。v7 的 kit 计划含 `tasks/` 文件 hash 和全部组的导出 hash；对 v7 无害（条目是给定的），但对 v8a 的起草会话是一个可以离线验证猜测的通道。
4. **分阶段 kit 用 isolate_net 的命令覆盖**（公开工具标注为“controls only”）启动私有 `stages.py`；`stages.py` 导入 kit 中逐字节相同的公开 worker，不复制其逻辑。单会话组的执行路径与 v7 相同。
5. **主机 checkout 与槽位隔离**（`private-v8a`、容器标签），因为 v8b 同时在同一主机开发。
6. **H1 的检验**：用户给定“非劣效、界值 10 个百分点”。配对二元结局采用 Tango score 检验，单侧 α = 0.025（非劣效的常规水平）。8 块/通道下功效有限，已在预注册中写明，冻结前由操作员确认块数与 α。

## 已知局限

- 任务、约定、参考条目由我们设计；“唯一确定”是我们对线索的论证，不是形式证明。条目正确性 Oracle 只比较参考条目，与参考条目不同但同样满足需求的条目（如果存在）会被记为错误；逐字段错误分布用于事后审视这种情况。
- 审查者固定为 opus：drafted-review 对 opus 通道是“自审”（同一模型，不同会话），对 luna、qwen 是“强模型审弱模型”。
- 私有 Verifier 绑定 trial 的目录，只能发现代码与目录不一致，不能发现目录本身的错误。
- 本机控制用 postgres:17-alpine（musl，排序等价字节序），主机控制（glibc，语言排序）是权威结果。
