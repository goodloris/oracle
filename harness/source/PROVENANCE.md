# Provenance of the vendored sources

The kits must be buildable on the evaluation host, which has no copy of the public repository, so the
export templates are vendored here. Nothing under this directory is imported by the Oracle.

| Template | Source | Change |
|---|---|---|
| `common/go.mod.txt`, `go.sum.txt`, `server.go.txt`, `demo_identity.go.txt` | public `71a213d` (`go.mod`, `go.sum`, `internal/app/*.go`) | none |
| `common/main.go.txt` | public `71a213d` `benchmark/phase3/source/main.go.txt` | none |
| `common/protocol.go.txt` | public `71a213d` `benchmark/phase6b/source/protocol.go.txt` | adds the generic `Param` adapter (`{"ids":[...],"<name>":"..."}`); `Assign` now delegates to it with the same behaviour |
| `common/ledger.go.txt` | public `71a213d` `benchmark/phase6b/source/ledger.go.txt` | `subject` binds any single string parameter (not only `assignee`) into the digest domain |
| `go-manual/approval.go.txt`, `reopen.go.txt` | public `benchmark/phase6b/source/manual-*.go.txt` | approval registers bulk-reassign |
| `go-manual/reject.go.txt` | public `benchmark/phase5b/source/manual-reject.go.txt` | none |
| `go-manual/reassign.go.txt` | new (v7 start state: v6b reassign done by hand, correctly) | — |
| `go-action-opdef/opdef.go.txt` | worktree `operation-semantics` (`poc/operation-semantics`, uncommitted, sha256 `da9bc050…`) `internal/opdef/opdef.go` | adds `Record` `CostCenterHistory`; a field update may use either history record |
| `go-action-opdef/opkernel.go.txt` | same worktree, `internal/opkernel/opkernel.go` (sha256 `8655075e…`) | the per-column `valid` map becomes `columns` (validity query + history table/columns), adding `costCenterId` |
| `go-action-opdef/approval.go.txt` | new adapter over `opkernel.Executor.Run` | — |
| `tasks/<task>/catalog.go.txt` | public catalog (`736429c1…`) minus countersign and the approve tier (the v7 world is v6b), plus the task's entry | — |

When the operation-semantics PR is merged, re-bind: copy its `opdef.go` / `opkernel.go`, re-apply the two
changes above (or drop them if upstream gained an equivalent), regenerate `tasks/*/OPERATIONS.md` with
`harness/opdoc.py`, and re-run both control suites before any plan is registered.
