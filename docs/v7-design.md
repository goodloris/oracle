# v7 设计：平台给定的权威语义能否提高完整验收成功率

本文档只在私有仓库中。预注册草稿见 [prereg/preregistration-v7.md](../prereg/preregistration-v7.md)。

## 目的

v6 的 A 两组持平，因为作者声明的事件（D）在 action 组只有 0.39。PR #27（公开 `cbe1336`）把业务语义放进受保护的操作目录 `internal/opdef`，内核 `internal/opkernel` 只按目录名执行。v7 检验这能否提高 **A**，并用第三组 `go-manual-doc` 把“知道正确事实”和“内核替你执行”两种来源分开。同时报告每个正确结果的 credit 和时间。

## 仓库布局

| 路径 | 内容 |
|---|---|
| `harness/ctl.py` | fixtures / plan / prepare / export / restore / evaluate / summarize；plan、manifest、job、receipt 格式与公开 phase3 worker 兼容 |
| `harness/oracle.py` | 独立 Oracle（不导入候选、目录或内核） |
| `harness/dbrpc/` | 验收端 Go 小程序：建库、重置、快照、故障注入（只接受 `*_poc` 库） |
| `harness/controls.py` | 静态检查 + 真实 PostgreSQL 控制 + Verifier 控制 |
| `harness/verifier.py` | 从公开平台 + 私有目录叠加构建 `appctl operations` |
| `harness/opdoc.py` | 由目录编译生成 `tasks/<task>/OPERATIONS.md`（`--check` 校验等价） |
| `harness/fake_kiro.py` | 交接控制用的假 kiro-cli（不调用模型） |
| `harness/source/` | 导出模板（`.go.txt`），来源见 `PROVENANCE.md` |
| `harness/sql/` | bootstrap 与 v6 世界的合并 schema |
| `tasks/<task>/` | 任务说明与约定（`cohort.json`）、任务 schema、notify/reconcile、私有目录 `catalog.go.txt`、目录文档、参考实现、控制清单 |
| `benchmark/phase3/generation_worker.py` | 公开 worker 的逐字节副本（kit 中的 worker.py） |
| `scripts/` | 主机同步、主机数据库、准备、拉取、生成、推回、验收、主机控制 |

## 起点世界

两个任务共用 v6 的完整世界：分级批量审批（>50000 进入 pending-second）、批量驳回、重新提交、会签、批量改派，以及 PR #27 的公开 5 个目录条目。schema 是公开各层（001 + guard + transactional-action + phase4/5/6a/6b + opkernel 的 union fixup）合并成的一个文件，加上任务 schema。

- `go-manual` / `go-manual-doc`：五个操作都手写（会签取自公开 phase6a 控制中的正确参考，改派是按 v6b 约定手写的正确实现）。
- `go-action-opdef`：五个操作都由 `opkernel.Executor.Run` 执行，`internal/candidate/approval.go` 只做 HTTP 适配。

## 任务与约定

| | T1 withdraw.v7 | T2 bulk-cost-center.v7 |
|---|---|---|
| 类型 | 状态迁移，单目标 | 字段修改，批量，带参数 |
| 接口 | `POST /purchases/{id}/withdraw` `{"reason"}`，经 `protocol.Item` | `POST /purchases/bulk-cost-center` `{"ids","costCenter"}`，经新的通用适配 `protocol.Param` |
| 权限/对象 | requester，本人创建 | approver，他人创建 |
| 状态 | submitted → withdrawn | 保持 submitted，改 `costCenterId`，值须为本租户 active 的 CostCenter |
| 显式分组 | functional 3、authorization 3、eligibility 6、input 6、idempotency 3、concurrency 2、failure-rollback 1、lifecycle 4，regression 18 | functional 4、authorization 3、eligibility 5、value 3、input 7、idempotency 4、concurrency 2、failure-rollback 1、lifecycle 2，regression 18 |
| 隐含约定 | **audit**：一行 ApprovalAudit（submitted→withdrawn，reason）；**request-hash**：`Fingerprint("withdraw.v7",[id])`，reason 不进指纹；**outbox**：`purchase.withdrawn`；**uniform-denial**：缺失、他租户、非本人、非 submitted 同一状态码 | **history**：每个目标一行 CostCenterHistory（from/to、并发成一条链）；**request-hash**：`Fingerprint("bulk-cost-center.v7\n"+cc, ids)`；**outbox**：`purchase.cost_center_changed`；**uniform-denial**：未知、他租户、未激活的成本中心同一状态码 |
| 用例数 | 显式 46 + 隐含 8 = 54 | 显式 49 + 隐含 11 = 60 |

`regression` 覆盖既有 API：审批（含分级）、会签（含首审人被拒）、驳回、重新提交、改派、列表租户隔离、他租户 404、创建、匿名 401，并逐表核对新增行。

线索：每条约定在 go-manual 中至少有 2–4 条分散线索（先例代码、SCHEMA.sql、`notify` 的受众分支、`ledger` 的指纹规则、`reconcile` 的漂移检查），清单在 `cohort.json` 的 `sources`。任务说明不含任何约定词（禁词表同 v6，另加“catalog/目录”）。

## 三组的差异

| | go-manual | go-manual-doc | go-action-opdef |
|---|---|---|---|
| 导出 | v6 世界的手写代码 | 同左 + `docs/OPERATIONS.md` | `internal/opdef`（私有目录）+ `internal/opkernel` + 适配 |
| 新操作的事实 | 推断 | 文档给出（与目录逐字段相同） | 目录给出 |
| 语义的执行 | 作者 | 作者 | 内核 |
| 作者可选择事件/豁免 | 是（写什么就是什么） | 是 | 否（只能按名字引用条目） |
| 提示词 | 基础 | 基础 + 文档说明一句 | 基础 + 目录/内核说明 |

**manual-doc 的文档只给事实、不给执行。** 文档的 JSON 由 `opdoc.py` 把导出的 `opdef.go` + 私有 `catalog.go` 编译后打印 `opdef.All()` 得到；说明部分写的是内核执行这些字段的方式（记录行的列、事件行的列、指纹公式、拒绝语义、事务性），与 go-action-opdef 中 `opdef.go`/`opkernel.go` 可见的信息等价。

## 私有目录与 Verifier 绑定

公开目录不得出现 v7 条目。做法：

1. `tasks/<task>/catalog.go.txt` = 公开 `catalog.go`（5 个条目，逐字保留）+ 本任务条目。go-action-opdef 的导出用它覆盖公开版本；go-manual-doc 只得到由它生成的文档。
2. **T2 需要改两处平台代码。** 公开 `opdef.Validate` 只允许字段修改记录到 AssignmentHistory，公开 `opkernel` 只管理 `assigneeId` 列（PR #27 文档“新增业务操作的路径”第 2 步也写明新列需要改内核）。私有叠加因此包括：`opdef.go` 增加 `Record` 常量 `CostCenterHistory`；`opkernel.go` 把 `valid` 映射换成 `columns`（值校验 + 历史表/列），加入 `costCenterId`。两处都不新增导出类型，Verifier 的内核类型分类表不受影响。T1 只用到目录条目，但两个任务使用同一份平台叠加。
3. `harness/verifier.py` 用 `git archive` 取公开 `cbe1336`（核对被替换的三个公开文件的 hash，防止上游漂移），叠加上述三个文件后构建 `appctl`。`plan` 把公开 commit、叠加文件 hash 和**私有目录摘要**（由私有 `appctl` 自己报告）写入计划；`load_plan` 校验绑定未变，`evaluate` 核对 `appctl` 报告的摘要与计划一致。

| 目录 | 摘要 |
|---|---|
| 公开（PR #27，`opdef_test` 钉住） | `sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055` |
| v7 withdraw（私有） | `sha256:7a6ebca3d84695e38e6ef90733a64227c9a2cf2b0d8cb61a894efc098b87e262` |
| v7 cost-center（私有） | `sha256:ab1f5e79cd6f8bd01a004012f7d832ae329193356da687c12f7490ef64efae5c` |

主机上的公开平台代码由 `scripts/sync_public_to_host.sh` 以 git bundle 放到 `/opt/poc-oracle/public`（只读取本机公开 checkout，不改动它）。

`evaluate` 在 Oracle 之后对候选运行私有 `appctl operations`，结果写入 `result.json` 的 `verifier` 字段，**不影响 A/E/K/D**，供预注册中的样本外评估使用。

## Oracle

独立编写：只依据任务说明和 `cohort.json` 中的约定规则，自带指纹公式副本；不导入候选、目录或内核。数据库访问通过 `harness/dbrpc`（pgx），HTTP 用标准库。

每个用例由若干**步骤**组成，每一步（包括并发步骤）都记录：

- `expect`：apply / refuse / replay / fault / concurrent；`op`：本步属于新操作还是既有 API；`targets`；`fault`（注入故障的表）；
- `responses`：每个请求的状态码、`Idempotency-Replayed`、响应体 hash；超时或连接失败记为 `status: null` 和错误类型；
- `state_before` / `state_after`：逐表 `{rows, hash}`（行集合去掉 createdAt/deliveredAt 后的规范 JSON 排序再 hash）；
- `diff`：逐表新增与删除的行（各最多 20 行）及计数；
- `safety`：本步的安全分类。

显式用例只断言任务说明写明的效果（Purchase 行、ActionRequest 的范围与 response、状态码与头）；隐含用例只看各自的表，并且只要求请求成功（不再重复显式断言），所以显式负控制和约定负控制互不连带。

### safety 字段

每个 trial 的 `safety` = 七类 × `{count, cases}`，count 是出现该类的用例数。分类只看记录的事实：

| 类别 | 判定（逐步骤） |
|---|---|
| `unauthorized_write` | 期望拒绝（refuse）的步骤改变了数据库任意表，且不属于下一类。**包括“返回 4xx 但改了状态”** |
| `partial_batch` | 期望拒绝的批量步骤（目标 ≥ 2）中，被改动的 Purchase 恰好是目标的非空真子集 |
| `side_effect_atomicity` | 故障注入步骤中 Purchase 被改动，并且（a）响应不是 2xx，或（b）响应 2xx、注入表没有新行，而本 trial 中该操作的成功请求平时会写这张表。**只是“从不写这张表”的候选不算**（那是约定失败；这修正了 v6 重扫把 110 个漏写事件的候选计为原子性破坏的问题） |
| `double_apply` | 并发步骤中，首次成功（2xx 且非重放）数超过契约允许的数目、**且** ActionRequest 新增行也超过；或用例自己的状态谓词成立：同一目标出现多于一行记录（withdraw），或两次都成功但历史行不能排成一条从初值到终值的链（cost-center 丢失更新） |
| `wrong_commit` | 期望成功的步骤改动了数据库，但 Purchase 结果与期望不符、响应不是 2xx、或改动了静态表（TenantMember/CostCenter）；重放步骤写了数据 |
| `service_error` | 非故障步骤中出现 5xx |
| `undecidable` | 有请求没有得到响应（超时、连接被拒/重置） |

### 结局与分数

`score` 要求用例清单与 `cohort.json` 的分组计数完全一致，否则记为基础设施问题。`result.json` 中：`explicit_passed`（E）、`conventions_passed`（K）、`outbox_passed`（D）、`passed`（A）、`conventions`（逐条）、`safety`、`verifier`、`generation`（含 credit 和秒数）。`summarize` 逐组汇总 A/E/K/D、各类 safety 的 trial 数、结局分布、credit/秒数总量与每个正确结果的成本。

## 控制

每个任务 16 项真实 PostgreSQL 控制（`tasks/<task>/controls.json`），每项断言**结局、失败分组集合、非零 safety 类别集合三者完全相等**，外加 Verifier 断言：

| 种类 | withdraw | cost-center |
|---|---|---|
| 正确参考（三组各一，必须全过；私有 Verifier 必须 pass） | 3 | 3 |
| 约定负控制（go-manual，只让目标约定失败） | audit、request-hash、outbox、uniform-denial | history、request-hash、outbox、uniform-denial |
| 显式负控制（每组一个） | manual 响应状态写错；doc 存错 ActionRequest.response；opdef 适配照抄 reopen 的状态 | manual 响应字段名错；doc 重放头写成 false；opdef 参数名照抄 assignee |
| 崩溃控制 | 首次 withdraw 时 goroutine panic → candidate-error | — |
| safety 人造候选 | unauthorized_write（**拒绝但先提交了状态**）、double_apply（**并发双写**：不加锁 + 不检查影响行数）、side_effect_atomicity（事件在提交后另起事务）、service_error（缺失目标返回 503）、undecidable（Unicode 目标挂起 12 秒） | partial_batch、wrong_commit（顺手清空 assigneeId）、double_apply（不加锁读旧值 → 丢失更新）、side_effect_atomicity（历史在提交后写）、unauthorized_write（接受未激活成本中心）、service_error（未知成本中心返回 503） |
| 公开 Verifier（每项控制） | 公开 `appctl` 不得 pass；go-action-opdef 候选必须报 `CATALOG_DRIFT` | 同左 |

静态检查（不需要数据库）：线索齐全、任务说明无禁词、三组提示词只差 capability 行、doc 组 = manual 组 + 文档、文档与目录等价、kit 只含服务源码、导出中没有另一个任务的操作名、vendored worker 与公开逐字节相同（本机设置 `PUBLIC_WORKER` 时）。

交接控制：kit 中的 worker.py 是公开 `generation_worker.py`，生成端在 `isolate_net.py` 沙箱中用 `harness/fake_kiro.py` 作 `--synthetic-test-driver`（READY 预检、ACP 事件、credit 用量都按 worker 的解析格式给出），再走完整的推回、restore、evaluate、summarize。

## 跨主机流程

```
本机                                   验收主机 root@10.30.11.112:/opt/poc-oracle
scripts/sync_to_host.sh  ──bundle──▶   private/（本仓库）
scripts/sync_public_to_host.sh ─────▶  public/（公开 cbe1336，只用于构建 Verifier）
scripts/host_db.sh up <lane>     ───▶  poc-oracle-pg-<lane>，127.0.0.1:5670<lane>
scripts/host_prepare.sh <run> …  ───▶  runs/<run>/{private/fixtures.json, plan.json, kits/}
scripts/pull_kits.sh <run> <dir> ◀───  kits、order.txt、plan.json（不含 seed）
scripts/generate.sh <dir>/<run>        isolate_net.py 沙箱，deny 本仓库等
scripts/push_results.sh <run> <dir> ─▶ runs/<run>/results/
scripts/host_evaluate.sh <run> <lane>  restore → evaluate（端口 1970<lane>）→ summary.json
```

主机约束：Go 用 `/opt/poc-oracle/go/bin/go`（`GOTOOLCHAIN=local`，GOPATH/GOMODCACHE/GOCACHE 都在 `/opt/poc-oracle` 下），Python 3.10，容器统一 `poc-oracle-*` 并只绑定 127.0.0.1。主机内核是 3.10（CentOS 7），默认 seccomp 配置下 `postgres:17-alpine` 的 initdb 报 EPERM，因此我们的容器加 `--security-opt seccomp=unconfined`（只作用于该容器）。

## 与用户给定设计的偏离及理由

1. **T2 需要私有的平台代码叠加**，不只是 `catalog.go`。理由见上文“私有目录与 Verifier 绑定”第 2 条；公开内核按设计不能承载新的字段修改列。
2. **起点世界包含会签和分级审批。** 绑定 PR #27 后目录必须包含公开 5 个条目；如果起点代码不实现分级和会签，manual-doc 的文档会与手写代码冲突（违反“线索不冲突”）。
3. **公开 Verifier 对 manual 组候选报 `OPERATION_NOT_IN_CATALOG`/`EVENT_NOT_IN_CATALOG`（fail），不是 `CATALOG_DRIFT`。** manual 组导出没有 `internal/opdef`，没有可比对的目录文件；控制要求的是“不能判为通过”，32 项控制中公开 Verifier 都没有判通过，go-action-opdef 候选都报了 `CATALOG_DRIFT`。
4. **新增通用适配 `protocol.Param`**（`Assign` 改为委托它，行为不变），供 T2 注册；v6 为 T3 新增 `protocol.Assign` 的做法相同。

## 已知局限

- 隐含约定和任务由我们设计。go-action-opdef 中 K、D 按构造成立，A 的差异主要来自适配代码与显式契约。
- `opkernel` 的 `eligible` 按 `ORDER BY p.id`（数据库排序规则）取旧值，再按 Go 的字节序与目标配对。`postgres:17-alpine` 的 en_US.utf8 在 musl 下等价于字节序，所以本实验不受影响；在 glibc 排序规则下历史行的 from 值可能错配。这是上游问题，已在汇报中提出。
- Oracle 的并发用例依赖时序；双写类人造候选靠 `pg_sleep(0.3)` 扩大窗口，本机各重复 3 次结果一致。
- `undecidable` 只在请求超时（8 秒）或连接失败时出现；候选崩溃时剩余用例都会是 undecidable，但结局已记为 candidate-error。
