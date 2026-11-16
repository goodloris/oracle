-- v8b task schema (send-back), applied after harness/sql/base.sql.
-- A second approver may return a purchase waiting for countersignature to the first approval step.
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','send-back.v8'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','send-back.v8'));
-- Platform guard (replaces the base function): leaving pending-second is allowed towards approved (countersign) or back
-- to submitted, and in both cases never by the approver whose audit row moved the purchase into pending-second.
CREATE OR REPLACE FUNCTION purchase_tier_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status = OLD.status THEN RETURN NEW; END IF;
 IF (OLD.status = 'submitted' AND NEW.status = 'approved' AND NEW.amount > 50000)
  OR (NEW.status = 'pending-second' AND (OLD.status <> 'submitted' OR NEW.amount <= 50000))
  OR (OLD.status = 'pending-second' AND (NEW.status NOT IN ('approved','submitted') OR current_setting('app.user', true) IS NOT DISTINCT FROM
      (SELECT a."approvedBy" FROM "ApprovalAudit" a WHERE a."tenantId" = OLD."tenantId" AND a."purchaseId" = OLD.id
        AND a."toStatus" = 'pending-second' ORDER BY a."createdAt" DESC LIMIT 1))) THEN
  RAISE EXCEPTION 'two-level approval transition refused' USING ERRCODE = 'P0001';
 END IF;
 RETURN NEW;
END $$;
