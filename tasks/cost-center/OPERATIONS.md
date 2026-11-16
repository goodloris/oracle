# Operation catalog (reviewed, read-only)

This is the reviewed catalog of the business operations of this service and the authoritative statement of their
semantics. It is documentation: nothing executes it. Every Action is hand-written in internal/candidate/ and must
implement its entry exactly. Changing this file needs owner review.

## What every entry means

- `name`: the versioned operation name, stored as ActionRequest.operation and as the operation of the record rows.
- `kind`: `transition` moves Purchase.status from `from` to `to`; `field-update` sets Purchase.`column` to the request
  parameter named `param` on targets whose status is `status`, and keeps the status.
- `role`: the only role that may call the operation. `subject`: `own` = targets the actor created, `others` = targets
  created by someone else. Targets must belong to the actor's tenant.
- `response`: the array field of a batch response, holding the sorted target IDs; a field update also returns its
  parameter (`{"<response>":[...],"<param>":value}`). An entry without `response` takes one target and responds
  `{"id":ID,"status":to}`.
- `inputs`: the request fields, targets (`ids`) first. `fingerprint`: the inputs bound into ActionRequest.requestHash.
  The digest domain is `name`, followed by `"\n"+value` for every fingerprint field after `ids`; the hash is
  ledger.Fingerprint(domain, ids). Inputs not listed (such as `reason`) and the Idempotency-Key are never hashed.
- `record`: the append-only table that gets, in the same transaction:
  - `ApprovalAudit`: one row per target: tenantId, purchaseId, approvedBy = the actor, operation = name, idempotencyKey = the Idempotency-Key, fromStatus = from, toStatus = the status the target moved to, reason = the request reason (NULL when the entry has no reason input).
  - `AssignmentHistory`: one row per target: tenantId, purchaseId, fromAssignee = the assigneeId before the change (NULL if none), toAssignee = the new value, changedBy = the actor, operation = name, idempotencyKey = the Idempotency-Key.
  - `CostCenterHistory`: one row per target: tenantId, purchaseId, fromCostCenter = the costCenterId before the change (NULL if none), toCostCenter = the new value, changedBy = the actor, operation = name, idempotencyKey = the Idempotency-Key.
- `event`: the PurchaseEvent type written in the same transaction, one row per target: tenantId, purchaseId,
  type = event, actorId = the actor, idempotencyKey = the Idempotency-Key. `eventExemption`: the entry writes no
  event, for the stated reason.
- `denial`: the one status every refused target or parameter value gets (missing, other tenant, wrong owner, wrong
  state, or an unacceptable parameter value), so an Action never reveals whether or why a target exists.
  For a field update of `assigneeId`, the value must be a TenantMember of the current tenant with role approver and active = true.
  For a field update of `costCenterId`, the value must be a CostCenter code of the current tenant with active = true.
- Every operation runs in one transaction with the ActionRequest row (idempotency scope: tenant, actor, operation,
  key). A repeated key with the same fingerprint replays the stored response; a different fingerprint gets `denial`.
  Any failed write, including the record, event or ActionRequest row, rolls the whole request back.

## Entries

### `bulk-approve.v3`

```json
{
  "name": "bulk-approve.v3",
  "kind": "transition",
  "role": "approver",
  "subject": "others",
  "from": "submitted",
  "to": "approved",
  "response": "approvedIds",
  "inputs": [
    "ids"
  ],
  "fingerprint": [
    "ids"
  ],
  "record": "ApprovalAudit",
  "eventExemption": "predates the PurchaseEvent outbox; the daily digest reads approvals from ApprovalAudit",
  "denial": 409
}
```

### `bulk-cost-center.v7`

```json
{
  "name": "bulk-cost-center.v7",
  "kind": "field-update",
  "role": "approver",
  "subject": "others",
  "status": "submitted",
  "column": "costCenterId",
  "param": "costCenter",
  "response": "updatedIds",
  "inputs": [
    "ids",
    "costCenter"
  ],
  "fingerprint": [
    "ids",
    "costCenter"
  ],
  "record": "CostCenterHistory",
  "event": "purchase.cost_center_changed",
  "denial": 409
}
```

### `bulk-reassign.v6`

```json
{
  "name": "bulk-reassign.v6",
  "kind": "field-update",
  "role": "approver",
  "subject": "others",
  "status": "submitted",
  "column": "assigneeId",
  "param": "assignee",
  "response": "reassignedIds",
  "inputs": [
    "ids",
    "assignee"
  ],
  "fingerprint": [
    "ids",
    "assignee"
  ],
  "record": "AssignmentHistory",
  "event": "purchase.reassigned",
  "denial": 409
}
```

### `bulk-reject.v4`

```json
{
  "name": "bulk-reject.v4",
  "kind": "transition",
  "role": "approver",
  "subject": "others",
  "from": "submitted",
  "to": "rejected",
  "response": "rejectedIds",
  "inputs": [
    "ids"
  ],
  "fingerprint": [
    "ids"
  ],
  "record": "ApprovalAudit",
  "event": "purchase.rejected",
  "denial": 409
}
```

### `reopen.v5`

```json
{
  "name": "reopen.v5",
  "kind": "transition",
  "role": "requester",
  "subject": "own",
  "from": "rejected",
  "to": "submitted",
  "inputs": [
    "ids",
    "reason"
  ],
  "fingerprint": [
    "ids"
  ],
  "record": "ApprovalAudit",
  "event": "purchase.reopened",
  "denial": 409
}
```
