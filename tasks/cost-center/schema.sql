-- v7 task schema (cost-center), applied after harness/sql/base.sql.
-- Every tenant keeps its own cost centers; a purchase may be charged to one of them.
CREATE TABLE "CostCenter" (
  "tenantId" text NOT NULL,
  code text NOT NULL CHECK (length(code) BETWEEN 1 AND 100),
  name text NOT NULL,
  active boolean NOT NULL,
  PRIMARY KEY ("tenantId",code)
);
ALTER TABLE "CostCenter" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "CostCenter" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "CostCenter" FROM app_runtime;
GRANT SELECT ON "CostCenter" TO app_runtime;
CREATE POLICY cost_center_read ON "CostCenter" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
INSERT INTO "CostCenter" ("tenantId",code,name,active) VALUES
 ('tenant-a','CC-OPS','Operations',true),('tenant-a','CC-RND','Research',true),('tenant-a','CC-OLD','Retired',false),
 ('tenant-b','CC-B1','Tenant B',true),('tenant-b','CC-B2','Tenant B two',true);
ALTER TABLE "Purchase" ADD COLUMN "costCenterId" text;
ALTER TABLE "Purchase" ADD FOREIGN KEY ("tenantId","costCenterId") REFERENCES "CostCenter" ("tenantId",code);
GRANT SELECT ("costCenterId") ON "Purchase" TO app_runtime;
GRANT UPDATE ("costCenterId") ON "Purchase" TO app_runtime;
CREATE TABLE "CostCenterHistory" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "fromCostCenter" text,
  "toCostCenter" text NOT NULL,
  "changedBy" text NOT NULL,
  operation text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY ("tenantId","purchaseId","changedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "CostCenterHistory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "CostCenterHistory" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "CostCenterHistory" FROM app_runtime;
GRANT SELECT,INSERT ON "CostCenterHistory" TO app_runtime;
CREATE POLICY cost_center_history_read ON "CostCenterHistory" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY cost_center_history_insert ON "CostCenterHistory" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "changedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','bulk-cost-center.v7'));
