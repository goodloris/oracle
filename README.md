# enterprise_agent 私有验收（v7）

这个仓库**必须保持 private**。这里存放 v7 确认性实验中不能公开的部分：任务、起点、Oracle、参考实现、正反控制和预注册全文。公开的平台代码在 <https://github.com/XUJiahua/enterprise_agent>。

## 为什么要分开

v1–v6 的 Oracle 和参考答案都放在公开仓库里。Kiro 的服务端 `web_search` 能检索到 GitHub，客户端网络隔离挡不住这条路径。v7 把新任务的答案只放在本仓库，并且只在可信验收主机上运行；生成端拿到的只有任务说明和起点代码。

## 角色与位置

| 位置 | 内容 | 不应出现 |
|---|---|---|
| 公开仓库 | 平台（内核、操作目录、Verifier）、生成 worker、隔离工具 | v7 的任务、Oracle、参考实现 |
| 本仓库（private） | `tasks/<task>/`：任务说明、起点模板、Oracle、参考实现、控制；`prereg/`；评估脚本 | 私有 seed、认证、模型输出 |
| 验收主机 `10.30.11.112:/opt/poc-oracle` | 本仓库的同步副本、私有 seed、PostgreSQL 容器、验收结果 | — |
| 生成端（操作员 Mac） | 每个 trial 一个 kit（任务说明 + 起点 + worker），在网络受限沙箱中生成 | 本仓库 checkout（生成时 deny）、seed |

验收主机没有 GitHub 凭据，本仓库由操作员机器通过 `rsync`/`git bundle` 推送过去，不在共享主机上留 deploy key。

## 流程

1. 在验收主机上执行 `fixtures`、`plan`、`prepare`：seed 留在主机，kit 用 scp 传给生成端。
2. 生成端执行 `run_kiro_cohort_net.sh`，产出 bundle、receipt 和网络日志，再用 scp 传回主机。
3. 在验收主机上执行 `restore`、`evaluate`、`summarize`，Oracle 输出结构化的安全字段。
4. 预注册全文提交在本仓库；只把它的 SHA256 和提交时间贴到公开 issue，用来证明数据产生之前已经冻结。
