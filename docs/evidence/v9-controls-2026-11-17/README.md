# v9 控制与跨主机流程证据（2026-11-17）

| 文件 | 内容 |
|---|---|
| `opdef-event-rules.diff.txt` | 规则补丁对公开 `fad8adf` `internal/opdef/opdef.go` 的完整差异（`harness/platform_rules.py diff`），移植参考 |
| `registry-hold.go.txt` | hold 任务规则平台的 `internal/opdef/registry.go`（由 notify 生成） |
| `local/<task>-controls.json` | 本机（macOS，postgres:17-alpine，`ea-v9-dev` 55670/18270）在 `79a18b6`：静态检查、规则控制 6 项、条目正确性控制 27 项、真实 PostgreSQL 控制（hold 23、project 26，project 另含私有内核回归），`dirty: false`，全部通过 |
| `host/<task>-controls.json` | **权威结果**：验收主机 `/opt/poc-oracle/private-v9` 在 `79a18b6`，槽位 1、2，PostgreSQL 17.2 Debian/glibc（语言排序）：同一套控制全部通过，私有内核回归在 ICU 与数据库默认排序下通过 |
| `crosshost/v9-xh1-<task>-20261117/` | 主机 prepare → 本机 isolate_net 沙箱 + 公开 worker / 阶段包装 + 假 kiro-cli → 推回 → 主机 restore/evaluate/summarize，每任务三组各 1 个 trial，6/6 passed；result 中 `catalog.rule_platform` 与私有 Verifier 判定 |
| `crosshost/xh1-analysis.json` | 主机上 `harness/analyze_v9.py` 对 xh1 的输出（格式验证，非模型证据） |
| `analysis-check-{local,host}.json` | `harness/analyze_v9_check.py`：本机 16 项、主机 17 项（含 xh1 的 run 格式检查）全部通过，含功效模拟 |

参考目录摘要（私有 appctl 报告；规则平台与 v8a 平台相同）：hold `sha256:5637b3714cc8194150fcdf075dca79893f305a7b1a5b2ab92eb9fbc6913ac23d`，project `sha256:fbb70d14c736343424775ae1f71d3690697ad53893d2329bef17600e91bd0b4f`。网络日志起始行记录 4 个白名单主机；用 drafted-rule kit 在 isolate_net 下读取两个私有仓库，均为 DENIED。假 kiro-cli 每个会话 credit 固定 0.01，不是模型证据。
