# v7 控制与跨主机流程证据（2026-11-16）

harness 树 `git rev-parse <sha>:harness` = `a95c50e3…`，`tasks` = `b5eb3fef…`，在 `39fb4fa` 与 `35aa42b` 之间未变。

| 文件 | 内容 |
|---|---|
| `local/<task>-controls.json` | 本机（macOS，Python 3.13，Go 1.26，postgres:17-alpine）在 `35aa42b` 上的 16 项控制 + 静态检查 + Verifier 断言，`dirty: false` |
| `host/<task>-controls.json` | 验收主机（CentOS 7，Python 3.10.9，Go 1.25.4）在 `39fb4fa` 上的同一套控制 |
| `crosshost/xh2-<task>-20261116/` | 主机 prepare → 本机 isolate_net 沙箱 + 公开 worker + 假 kiro-cli → 推回 → 主机 restore/evaluate/summarize 的完整一轮（每任务 3 个 trial）：plan、summary、每个 trial 的 receipt、network.log、preflight、outcome、result（含逐步骤状态摘要、safety、verifier），候选差异存为 `candidate.diff.txt` |

假 kiro-cli 不调用模型，credit 为固定的 0.01；这些数字只证明管线能传递 usage，不是模型证据。

## 2026-11-16 下午：重新绑定与主机数据库更换之后

| 文件 | 内容 |
|---|---|
| `local-fad8adf/` | 本机 alpine，`5dec13b`（重新绑定到公开 `fad8adf` 后）的 2 × 16 项控制 |
| `host-glibc/` | **权威结果**：验收主机，`98e1181`，PostgreSQL 17.2 Debian/glibc（`en_US.utf8`，语言排序）：withdraw 17 项、cost-center 18 项（含排序负控制）+ 私有内核回归（ICU 与数据库默认排序） |
| `local-alpine/` | 本机 alpine（musl，排序等价字节序），`98e1181` 的同一套控制，辅助证据 |
| `crosshost/xh3-*` | 在 `5dec13b` 准备、生成的 6 个假 kiro trial，在主机 glibc 数据库上用同一提交的 checkout（`/opt/poc-oracle/private-5dec13b`）完成 restore/evaluate/summarize |
| `crosshost/xh4-*` | 在 `aef208a`（最终 harness，计划含实测数据库环境）完整跑通的 6 个假 kiro trial |
| `crosshost/xh4-analysis.json` | 主机上 `harness/analyze_v7.py` 对 xh4 的输出（格式验证，非模型证据；1000 次重抽） |
