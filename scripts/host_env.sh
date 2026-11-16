# Sourced on the evaluation host: toolchain and lane-scoped database/ports, all under /opt/poc-oracle.
# Usage: . scripts/host_env.sh <lane 1..4>
LANE=${1:?lane 1..4}
case "$LANE" in 1|2|3|4) ;; *) echo "lane must be 1..4" >&2; return 1 ;; esac
export POC_GO=/opt/poc-oracle/go/bin/go GOTOOLCHAIN=local GOPATH=/opt/poc-oracle/gopath \
  GOMODCACHE=/opt/poc-oracle/gomod GOCACHE=/opt/poc-oracle/gocache GOPROXY=https://proxy.golang.org,direct
export PATH=/opt/poc-oracle/go/bin:$PATH
export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:$((56700+LANE))/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:$((56700+LANE))/enterprise_agent_poc
export POC_HTTP_PORT=$((19700+LANE)) POC_PG_CONTAINER=poc-oracle-pg-$LANE
