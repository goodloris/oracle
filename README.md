# enterprise_agent 私有验收（v8b 分支）

这个仓库**必须保持 private**。分支 `v8b` 存放 v8b 实验（3 个新任务，manual 对 given 两组）中不能公开的部分：任务、起点、Oracle、参考实现、正反控制和预注册全文。公开的平台代码在 <https://github.com/XUJiahua/enterprise_agent>。

## 为什么要分开

v1–v6 的 Oracle 和参考答案都放在公开仓库里。Kiro 的服务端 `web_search` 能检索到 GitHub，客户端网络隔离挡不住这条路径。v7 把新任务的答案只放在本仓库，并且只在可信验收主机上运行；生成端拿到的只有任务说明和起点代码。

v7 的任务、证据和冻结预注册在 `main` 分支。v8b 的设计见 `docs/v8b-design.md`，预注册草稿见 `prereg/preregistration-v8b.md`（未冻结）。

## 角色与位置

| 位置 | 内容 | 不应出现 |
|---|---|---|
| 公开仓库 | 平台（内核、操作目录、Verifier）、生成 worker、隔离工具 | v7 的任务、Oracle、参考实现 |
| 本仓库（private） | `tasks/<task>/`：任务说明、起点模板、Oracle、参考实现、控制；`prereg/`；评估脚本 | 私有 seed、认证、模型输出 |
| 验收主机 `10.30.11.112:/opt/poc-oracle` | `private-v8b/`（本分支的同步副本；`private/` 属于 v8a，不动）、`runs/v8b-*`（私有 seed、验收结果）、PostgreSQL 容器（标签 `poc.owner=v8b`） | — |
| 生成端（操作员 Mac） | 每个 trial 一个 kit（任务说明 + 起点 + worker），在网络受限沙箱中生成 | 两个私有 checkout（生成时 deny）、seed |

验收主机没有 GitHub 凭据，本仓库由操作员机器通过 `rsync`/`git bundle` 推送过去，不在共享主机上留 deploy key。

## 布局

| 路径 | 内容 |
|---|---|
| `tasks/{close,send-back,amend}/` | 任务说明与约定（`cohort.json`）、任务 schema、notify/reconcile、私有目录 `catalog.go.txt`、参考实现与控制清单 |
| `harness/` | 控制器 `ctl.py`、独立 Oracle `oracle.py`、`dbrpc/`、控制 `controls.py`、Verifier 构建 `verifier.py`、内核回归 `kernel_regression.py`、分析 `analyze_v8b.py`、导出模板 `source/` |
| `benchmark/phase3/generation_worker.py` | 公开 worker 的逐字节副本（kit 的 worker.py） |
| `scripts/` | 主机同步、主机数据库、prepare、拉取 kit、沙箱生成、推回、验收、主机控制 |
| `prereg/preregistration-v8b.md` | v8b 预注册草稿（未冻结）；`preregistration-v7.md` 为 v7 冻结版 |
| `docs/v8b-design.md` | v8b 设计说明；`docs/evidence/v8b-*` 为控制与跨主机证据（v7 证据仍在此目录） |

## 流程

1. `scripts/sync_to_host.sh`（默认目标 `/opt/poc-oracle/private-v8b`、分支 `v8b`），`scripts/host_db.sh up <lane>`（先确认 v8a 没有占用 56701–56704）。
2. `scripts/host_prepare.sh v8b-<run> <task> <model> <reps>`：主机上执行 `fixtures`、`plan`、`prepare`，seed 留在主机。
3. `scripts/pull_kits.sh <run> <dir>`（目录必须在本仓库之外）→ `scripts/generate.sh <dir>/<run>` → `scripts/push_results.sh <run> <dir>`；或用 `scripts/run_v8b.sh` 驱动全部通道。
4. `scripts/host_evaluate.sh <run> <lane>`：主机上执行 `restore`、`evaluate`、`summarize`；`scripts/host_db.sh down <lane>`。
5. 分析：`python3 harness/analyze_v8b.py --run <host run dir> ...`。
6. 预注册全文提交在本仓库；只把它的 SHA256 和提交时间贴到公开 issue。
