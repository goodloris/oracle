# 主机 seccomp 调查（2026-11-16，未改动任何主机配置）

结论：在这台主机上，**无法**用“默认 profile + 少数系统调用返回 ENOSYS”的方式让 `postgres:17-alpine` 启动，
因为需要改的那几个系统调用的名字，runc 所链接的 libseccomp 2.3.1 不认识，按名字写的规则会被静默丢弃。

环境：内核 3.10.0-1160（CentOS 7），Docker 26.1.4，runc 1.1.12，**动态链接主机的 libseccomp-2.3.1-4.el7**。

| 文件 | 内容 |
|---|---|
| `docker-26.1.4-default.json` | moby v26.1.4 的默认 profile（与 builtin 相同），`defaultAction=SCMP_ACT_ERRNO`、`defaultErrnoRet=1`（EPERM） |
| `scprobe.go.txt` | 静态 Go 程序：在容器内按**编号**调用 50 个较新的系统调用（参数全零或无效 fd），打印 errno |
| `probe-unique-errno.json` | 默认 profile 前面加上一组规则：53 个候选系统调用各返回一个独有的 errno（150+i） |
| `try-pwritev2-preadv2-enosys.json` | 默认 profile，把 `pwritev2`、`preadv2` 从 allow 规则中移出，改为 `errnoRet: 38` |
| `probe-log.txt` | 以上 profile 下 initdb 的结果与系统调用探针输出 |

观察（`probe-log.txt`）：

1. 默认 profile 下 initdb 失败（`could not write to file "postmaster.pid": Operation not permitted`）。
   探针显示返回 EPERM 的是 `preadv2`(327)、`pwritev2`(328)、`pkey_mprotect/alloc/free`(329–331)，
   以及默认 profile 有意拒绝的 `fanotify_init`、`process_vm_readv`、`kcmp`、`bpf`、`userfaultfd`。
   `preadv2/pwritev2/pkey_*` 在默认 profile 里是 **allow** 的，却返回 EPERM：它们的规则没有生效。
2. `probe-unique-errno.json`：`kcmp`、`bpf`、`userfaultfd` 变成了独有 errno（名字可解析，规则生效）；
   `preadv2`、`pwritev2`、`pkey_*` 仍是 EPERM（名字不可解析，规则被丢弃）。
3. `try-pwritev2-preadv2-enosys.json`：显式的 `errnoRet: 38` 规则同样无效，initdb 仍失败。
4. 只有修改 `defaultErrnoRet` 为 38（不认识的调用落到默认动作时返回 ENOSYS）时 initdb 成功（本次未采用）。
5. `postgres:latest`（主机已有，PG 17，Debian/glibc）在 **builtin 默认 profile** 下直接启动成功。

推断：musl（alpine）的 PostgreSQL 17 写文件时用到 `pwritev2`/`preadv2`（或其一），内核 3.10 没有它们，
正确答案应是 ENOSYS（musl 会回退）；但 libseccomp 2.3.1 不认识这些名字，这些调用落到默认动作 EPERM，
于是不回退。Docker profile 只能按名字写规则，无法按编号补救。
