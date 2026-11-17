#!/bin/bash
# Start/stop the lane's disposable PostgreSQL on the evaluation host (127.0.0.1:5670<lane> only).
# Uses Docker's builtin seccomp profile. postgres:17-alpine cannot initdb under it on 10.30.11.112 (kernel 3.10 +
# libseccomp 2.3.1, docs/evidence/seccomp-2026-11-16/), so the host uses the pinned Debian/glibc PostgreSQL 17.2
# image (option B); it must match POC_PG_IMAGE in scripts/host_env.sh.
# The container carries the label poc.owner=v9; `up` fails if the lane's container already exists (another
# experiment may hold the slot) and `down` removes only a container this experiment started.
IMAGE=${POC_PG_IMAGE:-postgres@sha256:888402a8cd6075c5dc83a31f58287f13306c318eaad016661ed12e076f3e6341}
# Usage: host_db.sh up|down <lane> [host]
set -eu
ACTION=$1; LANE=$2; HOST=${3:-root@10.30.11.112}
case "$LANE" in 1|2|3|4) ;; *) echo "lane must be 1..4" >&2; exit 2 ;; esac
NAME=poc-oracle-pg-$LANE; PORT=$((56700+LANE))
if [ "$ACTION" = up ]; then
  ssh -o BatchMode=yes "$HOST" "docker run -d --rm --name $NAME --label poc.owner=v9 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_agent_poc \
    -p 127.0.0.1:$PORT:5432 $IMAGE >/dev/null && for i in \$(seq 60); do docker exec $NAME pg_isready -U postgres -d enterprise_agent_poc -q && \
    docker exec $NAME psql -U postgres -d enterprise_agent_poc -Atc 'select 1' >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1"
  echo "$NAME up on 127.0.0.1:$PORT"
else
  ssh -o BatchMode=yes "$HOST" "owner=\$(docker inspect -f '{{index .Config.Labels \"poc.owner\"}}' $NAME 2>/dev/null || true)
    if [ \"\$owner\" = v9 ]; then docker rm -f $NAME >/dev/null; echo '$NAME removed'; elif [ -n \"\$owner\" ]; then echo '$NAME belongs to another experiment: left running' >&2; else echo '$NAME not running'; fi"
fi
