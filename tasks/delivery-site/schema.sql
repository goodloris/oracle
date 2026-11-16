-- v8a task schema (delivery-site), applied after harness/sql/base.sql.
-- Every tenant keeps its own delivery sites; a requester chooses where each of their purchases is delivered.
CREATE TABLE "DeliverySite" (
  "tenantId" text NOT NULL,
  code text NOT NULL CHECK (length(code) BETWEEN 1 AND 100),
  name text NOT NULL,
  active boolean NOT NULL,
  PRIMARY KEY ("tenantId",code)
);
ALTER TABLE "DeliverySite" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "DeliverySite" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "DeliverySite" FROM app_runtime;
GRANT SELECT ON "DeliverySite" TO app_runtime;
CREATE POLICY delivery_site_read ON "DeliverySite" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
INSERT INTO "DeliverySite" ("tenantId",code,name,active) VALUES
 ('tenant-a','DS-HQ','Head office',true),('tenant-a','DS-LAB','Lab building',true),('tenant-a','DS-OLD','Closed depot',false),
 ('tenant-b','DS-B1','Tenant B dock',true),('tenant-b','DS-B2','Tenant B annex',true);
ALTER TABLE "Purchase" ADD COLUMN "deliverySiteId" text;
ALTER TABLE "Purchase" ADD FOREIGN KEY ("tenantId","deliverySiteId") REFERENCES "DeliverySite" ("tenantId",code);
GRANT SELECT ("deliverySiteId") ON "Purchase" TO app_runtime;
GRANT UPDATE ("deliverySiteId") ON "Purchase" TO app_runtime;
CREATE TABLE "DeliverySiteHistory" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "fromSite" text,
  "toSite" text NOT NULL,
  "changedBy" text NOT NULL,
  operation text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY ("tenantId","purchaseId","changedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "DeliverySiteHistory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "DeliverySiteHistory" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "DeliverySiteHistory" FROM app_runtime;
GRANT SELECT,INSERT ON "DeliverySiteHistory" TO app_runtime;
CREATE POLICY delivery_site_history_read ON "DeliverySiteHistory" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY delivery_site_history_insert ON "DeliverySiteHistory" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "changedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='requester');
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','bulk-delivery-site.v8'));
-- Replaces the base transition policy (the guarded runtime requires exactly three Purchase policies):
-- a requester may also update their own submitted purchases, which stay submitted.
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status IN ('rejected','submitted'))))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
