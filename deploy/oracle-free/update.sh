#!/usr/bin/env bash
# Update a single-server Aurora to the latest main branch (docs/FREE_HOSTING.md). Migrations run automatically
# through the `migrate` service before the API, worker and beat start. Take a backup first if the release notes say so.
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(sudo docker compose -f docker-compose.yml -f deploy/oracle-free/compose.https.yml)

git pull --ff-only
"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d
sudo docker image prune -f >/dev/null
"${COMPOSE[@]}" ps
