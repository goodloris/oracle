# v8a 控制与跨主机流程证据（2026-11-16）

| 文件 | 内容 |
|---|---|
| `local/<task>-controls.json` | 本机（macOS，Python 3.13，Go 1.26，postgres:17-alpine，`ea-v8a-dev` 55650/18250）在 `8617676` 上：静态检查 13–14 项、条目正确性控制 27 项、真实 PostgreSQL 控制 24 项（delivery-site 另含私有内核回归），`dirty: false`，全部通过。alpine 排序等价字节序，排序负控制按字节序期望 |
| `host/<task>-controls.json` | **权威结果**：验收主机 `/opt/poc-oracle/private-v8a` 在 `8617676` 上，槽位 4（56704/19704），PostgreSQL 17.2 Debian/glibc（`en_US.utf8`，语言排序）：同一套控制全部通过，私有内核回归在 ICU 与数据库默认排序下通过 |
| `crosshost/v8a-xh1-<task>-20261116/` | 主机 `d98fe38` prepare → 本机 isolate_net 沙箱 + 公开 worker / 阶段包装 + 假 kiro-cli → 推回 → 主机 restore/evaluate/summarize，每任务四组各 1 个 trial，8/8 passed；每个 trial 的 receipt、outcome、network.log、preflight、result（含 C、逐阶段记录、trial 目录摘要、私有 Verifier），候选差异存为 `candidate.diff.txt` |
| `crosshost/xh1-analysis.json` | 主机上 `harness/analyze_v8a.py` 对 xh1 的输出（格式验证，非模型证据；1000 次重抽） |

参考目录摘要（私有 appctl 报告，与计划和每个 trial 的 `catalog.digest` 一致）：return `sha256:9e2131d8e2800c04122028003b44ec0775cf56f75e17eec139e74cea5777ca5c`，delivery-site `sha256:3988e28f6bc66c0a8c54f65d369d577c6c0b9f8ca5339748b6a295b1fe34a2fe`。

xh1 的 network.log 起始行记录白名单为 4 个主机（cognito-identity、oidc、management、runtime）。沙箱 deny 检查：用 drafted kit 在 isolate_net 下读取两个私有仓库，均为 DENIED。假 kiro-cli 不调用模型，每个会话 credit 固定 0.01；分阶段 trial 的 credit 为各阶段之和（drafted 0.02，drafted-review 0.03），证明逐阶段 usage 能合计到同一个 trial，不是模型证据。

## 两阶段 H1 的分析控制（`68b3287`）

`analysis-check-local.json`（本机 Python 3.13）与 `analysis-check-host.json`（主机 Python 3.10.9）：`harness/analyze_v8a_check.py` 27 项全部通过。边界 α₁ = 0.0015253（c₁ = 2.9626）、α₂ = 0.0245（c₂ = 1.9686），闭式、积分与 40 万次蒙特卡洛一致；合成数据覆盖 stop-noninferior、stop-futility、continue → noninferior、continue → not-noninferior、continue → pending-stage2，以及边界值两侧、停止后出现第二阶段数据（拒绝）、未测量块的分母、次要假设只用第一阶段。附带的 run 格式检查用 xh1 的两个跨主机 run（只有第一阶段，`final_decision = pending-stage2`）。本机另用假 kiro 跑通了第二阶段计划（`--stage 2`，given + drafted-review）的 prepare、生成、验收和两阶段合并分析。
