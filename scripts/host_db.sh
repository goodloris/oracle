#!/bin/bash
# Start/stop the lane's disposable PostgreSQL on the evaluation host (127.0.0.1:5670<lane> only).
# Uses Docker's builtin seccomp profile. On 10.30.11.112 postgres:17-alpine cannot initdb under it (kernel 3.10 +
# libseccomp 2.3.1, see docs/evidence/seccomp-2026-11-16/); the image choice for the host is pending (no unconfined).
IMAGE=${POC_PG_IMAGE:-postgres:17-alpine}
# Usage: host_db.sh up|down <lane> [host]
set -eu
ACTION=$1; LANE=$2; HOST=${3:-root@10.30.11.112}
case "$LANE" in 1|2|3|4) ;; *) echo "lane must be 1..4" >&2; exit 2 ;; esac
NAME=poc-oracle-pg-$LANE; PORT=$((56700+LANE))
if [ "$ACTION" = up ]; then
  ssh -o BatchMode=yes "$HOST" "docker run -d --rm --name $NAME -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_agent_poc \
    -p 127.0.0.1:$PORT:5432 $IMAGE >/dev/null && for i in \$(seq 60); do docker exec $NAME pg_isready -U postgres -d enterprise_agent_poc -q && \
    docker exec $NAME psql -U postgres -d enterprise_agent_poc -Atc 'select 1' >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1"
  echo "$NAME up on 127.0.0.1:$PORT"
else
  ssh -o BatchMode=yes "$HOST" "docker rm -f $NAME >/dev/null 2>&1 || true"
  echo "$NAME removed"
fi
