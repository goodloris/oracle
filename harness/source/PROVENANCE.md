# Provenance of the vendored sources

The kits must be buildable on the evaluation host, which has no copy of the public repository, so the
export templates are vendored here. Nothing under this directory is imported by the Oracle.

| Template | Source | Change |
|---|---|---|
| `common/go.mod.txt`, `go.sum.txt`, `server.go.txt`, `demo_identity.go.txt` | public `71a213d` (`go.mod`, `go.sum`, `internal/app/*.go`) | none |
| `common/main.go.txt` | public `71a213d` `benchmark/phase3/source/main.go.txt` | none |
| `common/protocol.go.txt` | public `71a213d` `benchmark/phase6b/source/protocol.go.txt` | v7: adds the generic `Param` adapter; v8b: adds the itemised `Amend` adapter (`{"items":[{"id","amount"}],"reason"}`, requester) |
| `common/ledger.go.txt` | public `71a213d` `benchmark/phase6b/source/ledger.go.txt` | v7: `subject` binds any single string parameter into the digest domain; v8b: an itemised response digests each item as `id=amount` |
| `go-manual/approval.go.txt`, `reopen.go.txt` | public `benchmark/phase6b/source/manual-*.go.txt` | approval registers bulk-reassign |
| `go-manual/reject.go.txt` | public `benchmark/phase5b/source/manual-reject.go.txt` | none |
| `go-manual/countersign.go.txt` | public phase6a control (correct reference) | none |
| `go-manual/reassign.go.txt` | new (v7 start state: v6b reassign done by hand, correctly) | — |
| `go-action-opdef/opdef.go.txt` | public `fad8adf` `internal/opdef/opdef.go` (sha256 `da9bc050…`) | v8b: `Kind` `Amend`, `Record` `AmendmentHistory`, transition fields `Requires` (`FullyReceived`) and `WithinHours`, amend fields `MaxIncreasePercent`/`KeepSideOf`, input `Amounts`; no new exported type (the Verifier's kernel type table is unchanged) |
| `go-action-opdef/opkernel.go.txt` | public `fad8adf` `internal/opkernel/opkernel.go` (sha256 `55228e1b…`, includes the pair-old-values-by-id fix) | v8b: `Request.Amounts`; transition preconditions (`GoodsReceipt` coverage) and the time window on the latest audit row into `From`; the `amend` kind (per-target values, increase limit, same side of a line, `AmendmentHistory`, items digested as `id=value`); the v7 `costCenterId` column is gone. Regression `harness/kernel/opkernel_v8b_test.go.txt` |
| `go-action-opdef/approval.go.txt` | new adapter over `opkernel.Executor.Run` | — |
| `tasks/<task>/catalog.go.txt` | public catalog (`736429c1…`), its 5 entries verbatim, plus the task's entry | — |

When the operation-semantics PR is merged, re-bind: copy its `opdef.go` / `opkernel.go`, re-apply the two
changes above (or drop them if upstream gained an equivalent), and re-run the three control suites before any plan is registered.
