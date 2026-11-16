# v8b 控制与跨主机流程证据（2026-11-16）

harness 树 `619b373e…`、tasks 树 `487841e0…`，在 `988f852`、`1bd24c2` 及记录本证据的提交之间未变（之后的提交只改 `docs/`、`prereg/`）。

| 文件 | 内容 |
|---|---|
| `host/<task>-controls.json` | **权威结果**：验收主机（CentOS 7，Python 3.10，Go 1.25.4，PostgreSQL 17.2 Debian/glibc `en_US.utf8`，语言排序），`988f852`，`dirty: false`。close 17、send-back 15、amend 18 项全部通过（含排序负控制，`collation: linguistic`）；私有内核回归在 ICU 与数据库默认排序下通过（`database_default.applicable: true`） |
| `local/<task>-controls.json` | 本机辅助（macOS，Go 1.26，`postgres:17-alpine`，musl 排序等价字节序），`ea-v8b-dev` 55660/18260：close 在 `988f852`，send-back、amend 在之后只改文档的提交上；同一套控制全部通过，另含 `vendored_worker_equals_public: true` |
| `crosshost/v8b-xh1-<task>/` | 主机 prepare（`988f852`，synthetic）→ 本机 `isolate_net.py` 沙箱 + 公开 worker + 假 kiro-cli → 推回 → 主机 restore/evaluate/summarize，每任务两组各 1 个 trial：plan、summary、每个 trial 的 receipt、network.log、preflight、outcome、result；候选差异存为 `candidate.diff.txt` |
| `crosshost/xh1-analysis.json` | 主机上 `harness/analyze_v8b.py` 对上面 3 个 run 的输出（格式验证，1000 次重抽，非模型证据） |

- 6 个假 kiro trial 全部 `passed`（close 58/58、send-back 59/59、amend 67/67 用例），safety 全为 0。
- network.log 的白名单为 `cognito-identity.us-east-1.amazonaws.com`、`oidc.us-east-1.amazonaws.com`、`management.us-east-1.kiro.dev`、`runtime.us-east-1.kiro.dev`（isolate_net 按字母序打印；脚本中的环境变量保持指定顺序，runtime 在最后）。
- 同一沙箱参数下的探针：`/Users/administrator/poc-v8b-private` 与 `/Users/administrator/poc-v7-private` 都不可读、不可列目录。
- 私有 Verifier：amend 的 manual 参考实现判 `fail`（`FINGERPRINT_INPUT_MISSING`），控制按设计文档记录该期望；其余参考实现判 `pass`。公开 Verifier 对全部 50 项控制都没有判通过，given 组候选都报了 `CATALOG_DRIFT`。

假 kiro-cli 不调用模型，credit 为固定的 0.01；这些数字只证明管线能传递 usage，不是模型证据。
