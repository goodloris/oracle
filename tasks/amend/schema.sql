-- v8b task schema (amend), applied after harness/sql/base.sql.
-- A requester may correct the amounts of their own purchases while they wait for approval.
GRANT UPDATE (amount) ON "Purchase" TO app_runtime;
CREATE TABLE "AmendmentHistory" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "fromAmount" integer NOT NULL,
  "toAmount" integer NOT NULL CHECK ("toAmount" > 0),
  "changedBy" text NOT NULL,
  operation text NOT NULL,
  "idempotencyKey" text NOT NULL,
  reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY ("tenantId","purchaseId","changedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "AmendmentHistory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "AmendmentHistory" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "AmendmentHistory" FROM app_runtime;
GRANT SELECT,INSERT ON "AmendmentHistory" TO app_runtime;
CREATE POLICY amendment_history_read ON "AmendmentHistory" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY amendment_history_insert ON "AmendmentHistory" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "changedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='requester');
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','bulk-amend.v8'));
-- Replaces the base transition policy (the guarded runtime requires exactly three Purchase policies):
-- the creator may also update their own submitted purchase (its amount; status stays submitted).
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status IN ('rejected','submitted'))))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
