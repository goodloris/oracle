-- v7 disposable POC schema: the v6 world (purchase, tiered approve, reject, reopen, countersign, reassign) in one
-- file. Equivalent to the public layers 001_purchase + guard + transactional-action + phase4 + phase5 + phase6a +
-- phase6b + internal/opkernel/testdata/union.sql. Task schemas (tasks/<task>/schema.sql) are applied after it.
CREATE TABLE "Purchase" (
  id text PRIMARY KEY,
  "tenantId" text NOT NULL,
  "createdBy" text NOT NULL,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  amount integer NOT NULL CHECK (amount > 0),
  status text NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','approved','rejected','pending-second')),
  "internalNote" text NOT NULL DEFAULT 'supplier-bank-account',
  "assigneeId" text
);
CREATE INDEX purchase_tenant_idx ON "Purchase" ("tenantId", id);
CREATE UNIQUE INDEX purchase_tenant_id_unique ON "Purchase" ("tenantId", id);
ALTER TABLE "Purchase" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "Purchase" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "Purchase" FROM app_runtime;
GRANT SELECT (id,"tenantId","createdBy",title,amount,status,"assigneeId") ON "Purchase" TO app_runtime;
GRANT INSERT (id,"tenantId","createdBy",title,amount,status) ON "Purchase" TO app_runtime;
GRANT UPDATE (status,"assigneeId") ON "Purchase" TO app_runtime;
CREATE POLICY purchase_read ON "Purchase" FOR SELECT TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND current_setting('app.role', true) IN ('approver','requester'));
CREATE POLICY purchase_create ON "Purchase" FOR INSERT TO app_runtime
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND "createdBy" = current_setting('app.user', true)
 AND current_setting('app.role', true) IN ('requester') AND status = 'submitted');
-- The guarded runtime requires exactly three Purchase policies; task schemas replace this one.
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));

CREATE TABLE "TenantMember" (
  "tenantId" text NOT NULL,
  "userId" text NOT NULL,
  role text NOT NULL CHECK (role IN ('approver','requester')),
  active boolean NOT NULL,
  PRIMARY KEY ("tenantId","userId")
);
ALTER TABLE "TenantMember" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "TenantMember" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "TenantMember" FROM app_runtime;
GRANT SELECT ON "TenantMember" TO app_runtime;
CREATE POLICY tenant_member_read ON "TenantMember" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
INSERT INTO "TenantMember" ("tenantId","userId",role,active) VALUES
 ('tenant-a','alice','requester',true),('tenant-a','zoe','requester',true),('tenant-a','bob','approver',true),
 ('tenant-a','carol','approver',true),('tenant-a','hana','approver',true),('tenant-a','frank','approver',false),
 ('tenant-b','dave','requester',true),('tenant-b','erin','approver',true),('tenant-b','ivan','approver',true);
ALTER TABLE "Purchase" ADD FOREIGN KEY ("tenantId","assigneeId") REFERENCES "TenantMember" ("tenantId","userId");

CREATE TABLE "ActionRequest" (
  "tenantId" text NOT NULL,
  "actorId" text NOT NULL,
  operation text NOT NULL CONSTRAINT "ActionRequest_operation_check"
    CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6')),
  key text NOT NULL CHECK (length(key) BETWEEN 1 AND 128),
  "requestHash" text NOT NULL CHECK ("requestHash" ~ '^[0-9a-f]{64}$'),
  response jsonb NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("tenantId","actorId",operation,key)
);
CREATE TABLE "ApprovalAudit" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "approvedBy" text NOT NULL,
  operation text NOT NULL CONSTRAINT "ApprovalAudit_operation_check"
    CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6')),
  "idempotencyKey" text NOT NULL,
  "fromStatus" text NOT NULL CONSTRAINT "ApprovalAudit_fromStatus_check" CHECK ("fromStatus" IN ('submitted','rejected','pending-second')),
  "toStatus" text NOT NULL CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected','submitted','pending-second')),
  "createdAt" timestamptz NOT NULL DEFAULT now(),
  reason text CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 200),
  PRIMARY KEY ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id),
  FOREIGN KEY ("tenantId","approvedBy",operation,"idempotencyKey")
    REFERENCES "ActionRequest" ("tenantId","actorId",operation,key) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE "PurchaseEvent" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  type text NOT NULL,
  "actorId" text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT now(),
  "deliveredAt" timestamptz,
  PRIMARY KEY ("tenantId","purchaseId",type,"actorId","idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
CREATE TABLE "AssignmentHistory" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "fromAssignee" text,
  "toAssignee" text NOT NULL,
  "changedBy" text NOT NULL,
  operation text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY ("tenantId","purchaseId","changedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "ActionRequest" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "ActionRequest" FORCE ROW LEVEL SECURITY;
ALTER TABLE "ApprovalAudit" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "ApprovalAudit" FORCE ROW LEVEL SECURITY;
ALTER TABLE "PurchaseEvent" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "PurchaseEvent" FORCE ROW LEVEL SECURITY;
ALTER TABLE "AssignmentHistory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "AssignmentHistory" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "ActionRequest","ApprovalAudit","PurchaseEvent","AssignmentHistory" FROM app_runtime;
GRANT SELECT,INSERT ON "ActionRequest","ApprovalAudit","PurchaseEvent","AssignmentHistory" TO app_runtime;
CREATE POLICY action_request_read ON "ActionRequest" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY action_request_insert ON "ActionRequest" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY approval_audit_read ON "ApprovalAudit" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true)='approver');
CREATE POLICY approval_audit_insert ON "ApprovalAudit" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "approvedBy"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY purchase_event_read ON "PurchaseEvent" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY purchase_event_insert ON "PurchaseEvent" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY assignment_history_read ON "AssignmentHistory" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY assignment_history_insert ON "AssignmentHistory" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "changedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');

-- Platform guard for every writer, including the legacy per-purchase approve: the tier and the
-- second approver are enforced on the row transition, so a mistaken handler fails instead of approving.
CREATE OR REPLACE FUNCTION purchase_tier_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status = OLD.status THEN RETURN NEW; END IF;
 IF (OLD.status = 'submitted' AND NEW.status = 'approved' AND NEW.amount > 50000)
  OR (NEW.status = 'pending-second' AND (OLD.status <> 'submitted' OR NEW.amount <= 50000))
  OR (OLD.status = 'pending-second' AND (NEW.status <> 'approved' OR current_setting('app.user', true) IS NOT DISTINCT FROM
      (SELECT a."approvedBy" FROM "ApprovalAudit" a WHERE a."tenantId" = OLD."tenantId" AND a."purchaseId" = OLD.id
        AND a."toStatus" = 'pending-second' ORDER BY a."createdAt" DESC LIMIT 1))) THEN
  RAISE EXCEPTION 'two-level approval transition refused' USING ERRCODE = 'P0001';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER purchase_tier_guard BEFORE UPDATE OF status ON "Purchase" FOR EACH ROW EXECUTE FUNCTION purchase_tier_guard();
