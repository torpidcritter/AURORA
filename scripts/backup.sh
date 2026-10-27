#!/usr/bin/env bash
# Back up the Aurora database (and, for local storage, the artifacts directory), verify the dump, prune old backups.
#
#   BACKUP_DATABASE_URL=postgresql://aurora:...@host:5432/aurora \
#   BACKUP_DIR=/var/backups/aurora RETENTION_DAYS=14 ARTIFACT_DIR=/data/artifacts scripts/backup.sh
#
# BACKUP_DATABASE_URL: libpq URL of a login that can read every table: the owner, or aurora_app (SELECT on all tables).
# ARTIFACT_DIR (optional): with local storage, the directory holding datasets and model files. With S3 storage, back up
#   the bucket with the provider's versioning/replication instead.
# Output: aurora-<UTC timestamp>.dump (pg_dump custom format) and artifacts-<timestamp>.tgz, both chmod 600.
# Backups contain business data and password hashes: copy them off-host, encrypted. This script does not upload.
set -euo pipefail

: "${BACKUP_DATABASE_URL:?set BACKUP_DATABASE_URL}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
URL="${BACKUP_DATABASE_URL/postgresql+psycopg:/postgresql:}"

umask 077
mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$BACKUP_DIR/aurora-$TS.dump"

pg_dump --format=custom --no-owner --no-privileges --file "$DUMP.partial" "$URL"
# A dump that pg_restore cannot list is not a backup.
TABLES="$(pg_restore --list "$DUMP.partial" | grep -c ' TABLE DATA ' || true)"
if [ "$TABLES" -lt 1 ]; then
  echo "backup verification failed: no table data in $DUMP.partial" >&2
  exit 1
fi
mv "$DUMP.partial" "$DUMP"
echo "database: $DUMP ($TABLES tables with data, $(du -h "$DUMP" | cut -f1))"

if [ -n "${ARTIFACT_DIR:-}" ]; then
  ART="$BACKUP_DIR/artifacts-$TS.tgz"
  tar -C "$ARTIFACT_DIR" -czf "$ART.partial" .
  tar -tzf "$ART.partial" >/dev/null
  mv "$ART.partial" "$ART"
  echo "artifacts: $ART ($(du -h "$ART" | cut -f1))"
fi

# Prune only files this script created, older than the retention window.
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'aurora-*.dump' -o -name 'artifacts-*.tgz' \) \
  -mtime +"$RETENTION_DAYS" -print -delete | sed 's/^/pruned: /'
