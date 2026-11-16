-- v8a task schema (return), applied after harness/sql/base.sql.
-- A second approver may send a purchase waiting for the second approval back to its requester.
ALTER TABLE "Purchase" DROP CONSTRAINT "Purchase_status_check";
ALTER TABLE "Purchase" ADD CONSTRAINT "Purchase_status_check" CHECK (status IN ('submitted','approved','rejected','pending-second','returned'));
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','return.v8'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','return.v8'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_toStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected','submitted','pending-second','returned'));
-- Replaces the base transition policy (the guarded runtime requires exactly three Purchase policies):
-- an approver may also move a pending-second purchase of someone else to returned.
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted','pending-second','returned'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
-- The platform guard: a pending-second purchase leaves that state only by the second approval or by being
-- returned, and in both cases only by an approver other than the one whose approval put it there.
CREATE OR REPLACE FUNCTION purchase_tier_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status = OLD.status THEN RETURN NEW; END IF;
 IF (OLD.status = 'submitted' AND NEW.status = 'approved' AND NEW.amount > 50000)
  OR (NEW.status = 'pending-second' AND (OLD.status <> 'submitted' OR NEW.amount <= 50000))
  OR (OLD.status = 'pending-second' AND (NEW.status NOT IN ('approved','returned') OR current_setting('app.user', true) IS NOT DISTINCT FROM
      (SELECT a."approvedBy" FROM "ApprovalAudit" a WHERE a."tenantId" = OLD."tenantId" AND a."purchaseId" = OLD.id
        AND a."toStatus" = 'pending-second' ORDER BY a."createdAt" DESC LIMIT 1))) THEN
  RAISE EXCEPTION 'two-level approval transition refused' USING ERRCODE = 'P0001';
 END IF;
 RETURN NEW;
END $$;
