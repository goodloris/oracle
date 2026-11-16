# v7 控制与跨主机流程证据（2026-11-16）

harness 树 `git rev-parse <sha>:harness` = `a95c50e3…`，`tasks` = `b5eb3fef…`，在 `39fb4fa` 与 `35aa42b` 之间未变。

| 文件 | 内容 |
|---|---|
| `local/<task>-controls.json` | 本机（macOS，Python 3.13，Go 1.26，postgres:17-alpine）在 `35aa42b` 上的 16 项控制 + 静态检查 + Verifier 断言，`dirty: false` |
| `host/<task>-controls.json` | 验收主机（CentOS 7，Python 3.10.9，Go 1.25.4）在 `39fb4fa` 上的同一套控制 |
| `crosshost/xh2-<task>-20261116/` | 主机 prepare → 本机 isolate_net 沙箱 + 公开 worker + 假 kiro-cli → 推回 → 主机 restore/evaluate/summarize 的完整一轮（每任务 3 个 trial）：plan、summary、每个 trial 的 receipt、network.log、preflight、outcome、result（含逐步骤状态摘要、safety、verifier），候选差异存为 `candidate.diff.txt` |

假 kiro-cli 不调用模型，credit 为固定的 0.01；这些数字只证明管线能传递 usage，不是模型证据。
