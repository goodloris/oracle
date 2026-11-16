#!/bin/bash
# Start/stop the lane's disposable PostgreSQL on the evaluation host (127.0.0.1:5670<lane> only).
# seccomp=unconfined: on this host's 3.10 kernel the default profile makes postgres:17-alpine initdb fail
# with EPERM (2026-11-16); it applies to this container only.
# Usage: host_db.sh up|down <lane> [host]
set -eu
ACTION=$1; LANE=$2; HOST=${3:-root@10.30.11.112}
case "$LANE" in 1|2|3|4) ;; *) echo "lane must be 1..4" >&2; exit 2 ;; esac
NAME=poc-oracle-pg-$LANE; PORT=$((56700+LANE))
if [ "$ACTION" = up ]; then
  ssh -o BatchMode=yes "$HOST" "docker run -d --rm --name $NAME --security-opt seccomp=unconfined -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_agent_poc \
    -p 127.0.0.1:$PORT:5432 postgres:17-alpine >/dev/null && for i in \$(seq 60); do docker exec $NAME pg_isready -U postgres -d enterprise_agent_poc -q && \
    docker exec $NAME psql -U postgres -d enterprise_agent_poc -Atc 'select 1' >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1"
  echo "$NAME up on 127.0.0.1:$PORT"
else
  ssh -o BatchMode=yes "$HOST" "docker rm -f $NAME >/dev/null 2>&1 || true"
  echo "$NAME removed"
fi
