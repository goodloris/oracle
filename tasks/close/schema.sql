-- v8b task schema (close), applied after harness/sql/base.sql.
-- Goods receipts are booked by the warehouse against approved purchases; once the accepted receipts cover the
-- purchase amount, the purchase can be closed.
ALTER TABLE "Purchase" DROP CONSTRAINT "Purchase_status_check";
ALTER TABLE "Purchase" ADD CONSTRAINT "Purchase_status_check" CHECK (status IN ('submitted','approved','rejected','pending-second','closed'));
CREATE TABLE "GoodsReceipt" (
  "tenantId" text NOT NULL,
  "receiptNo" text NOT NULL,
  "purchaseId" text NOT NULL,
  amount integer NOT NULL CHECK (amount > 0),
  status text NOT NULL CHECK (status IN ('pending','accepted','rejected')),
  "receivedBy" text NOT NULL,
  PRIMARY KEY ("tenantId","receiptNo"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "GoodsReceipt" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "GoodsReceipt" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "GoodsReceipt" FROM app_runtime;
GRANT SELECT ON "GoodsReceipt" TO app_runtime;
CREATE POLICY goods_receipt_read ON "GoodsReceipt" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','bulk-close.v8'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-close.v8'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_fromStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_fromStatus_check" CHECK ("fromStatus" IN ('submitted','rejected','pending-second','approved'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_toStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected','submitted','pending-second','closed'));
-- Replaces the base transition policy (the guarded runtime requires exactly three Purchase policies):
-- an approver may also move an approved purchase created by someone else to closed.
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second','approved'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted','pending-second','closed'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
