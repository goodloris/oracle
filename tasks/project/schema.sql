-- v9 task schema (project), applied after harness/sql/base.sql.
-- Every tenant keeps its own projects; an approver may tag a purchase with one of them.
CREATE TABLE "Project" (
  "tenantId" text NOT NULL,
  code text NOT NULL CHECK (length(code) BETWEEN 1 AND 100),
  name text NOT NULL,
  active boolean NOT NULL,
  PRIMARY KEY ("tenantId",code)
);
ALTER TABLE "Project" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "Project" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "Project" FROM app_runtime;
GRANT SELECT ON "Project" TO app_runtime;
CREATE POLICY project_read ON "Project" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
INSERT INTO "Project" ("tenantId",code,name,active) VALUES
 ('tenant-a','PR-APOLLO','Apollo rollout',true),('tenant-a','PR-BOREAS','Boreas lab',true),('tenant-a','PR-CLOSED','Closed project',false),
 ('tenant-b','PR-B1','Tenant B project',true),('tenant-b','PR-B2','Tenant B project two',true);
ALTER TABLE "Purchase" ADD COLUMN "projectId" text;
ALTER TABLE "Purchase" ADD FOREIGN KEY ("tenantId","projectId") REFERENCES "Project" ("tenantId",code);
GRANT SELECT ("projectId") ON "Purchase" TO app_runtime;
GRANT UPDATE ("projectId") ON "Purchase" TO app_runtime;
CREATE TABLE "ProjectHistory" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "fromProject" text,
  "toProject" text NOT NULL,
  "changedBy" text NOT NULL,
  operation text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY ("tenantId","purchaseId","changedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "ProjectHistory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "ProjectHistory" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "ProjectHistory" FROM app_runtime;
GRANT SELECT,INSERT ON "ProjectHistory" TO app_runtime;
CREATE POLICY project_history_read ON "ProjectHistory" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY project_history_insert ON "ProjectHistory" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "changedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check"
 CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6','bulk-reassign.v6','bulk-project.v9'));
