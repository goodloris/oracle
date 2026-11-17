-- v9 task schema (hold), applied after harness/sql/base.sql.
-- An approver may put a submitted purchase of someone else on hold; it then leaves every approval path.
ALTER TABLE "Purchase" DROP CONSTRAINT "Purchase_status_check";
ALTER TABLE "Purchase" ADD CONSTRAINT "Purchase_status_check" CHECK (status IN ('submitted','approved','rejected','pending-second','on-hold'));
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','hold.v9'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','hold.v9'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_toStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected','submitted','pending-second','on-hold'));
-- Replaces the base transition policy (the guarded runtime requires exactly three Purchase policies):
-- an approver may also move a submitted purchase of someone else to on-hold.
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted','pending-second','on-hold'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
