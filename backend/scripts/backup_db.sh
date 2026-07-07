#!/usr/bin/env bash
#
# Automated Postgres logical backup for SiteTracker (audit R3).
#
# Produces a timestamped, compressed custom-format ``pg_dump``, verifies the
# artefact is actually readable (``pg_restore --list``), records a SHA-256
# checksum, prunes old local dumps by age, and — when configured — mirrors the
# dump to off-provider object storage. It is read-only against the database
# (pg_dump never writes), so it is safe to run against production.
#
# This is the AUTOMATED complement to the manual, Windows/PowerShell rehearsal
# in docs/operations/staging-backup-restore.md. The disaster-recovery runbook
# (docs/operations/disaster-recovery.md) is the source of truth for cadence,
# retention, offsite storage, RPO/RTO, and the restore procedure.
#
# ── Secret discipline ────────────────────────────────────────────────────────
# The connection comes from DATABASE_URL in the environment ONLY. The password
# is parsed out and passed to pg_dump via PGPASSWORD (env), so it never appears
# on a command line / process listing, in a log, or in the dump filename. Do
# NOT pass a password as an argument. Never echo $DATABASE_URL.
#
# ── Usage ────────────────────────────────────────────────────────────────────
#   DATABASE_URL='postgresql://user:pass@host:5432/db' \
#   BACKUP_DIR=/var/backups/sitetracker \
#     backend/scripts/backup_db.sh
#
# ── Optional environment ─────────────────────────────────────────────────────
#   BACKUP_DIR             local output dir             (default: ./backups)
#   BACKUP_RETENTION_DAYS  prune local dumps older than N days (default: 14; 0 = keep all)
#   BACKUP_LABEL           tag embedded in the filename (default: manual)
#   BACKUP_S3_URI          e.g. s3://bucket/prefix — if set, the dump + its
#                          .sha256 are uploaded with `aws s3 cp` (requires the
#                          AWS CLI and credentials already in the environment).
#                          This is the OFF-PROVIDER offsite copy. S3-compatible
#                          endpoints (Backblaze B2, Cloudflare R2, MinIO) work
#                          via the standard AWS_ENDPOINT_URL / AWS_* env vars.
#
# Exit codes: 0 success; non-zero on any failure (so a scheduler/CI surfaces it).

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_LABEL="${BACKUP_LABEL:-manual}"

log() { printf '%s %s\n' "[$(date -u +%Y-%m-%dT%H:%M:%SZ)]" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

command -v pg_dump    >/dev/null 2>&1 || die "pg_dump not found on PATH (install postgresql-client)."
command -v pg_restore >/dev/null 2>&1 || die "pg_restore not found on PATH (install postgresql-client)."
command -v python3    >/dev/null 2>&1 || die "python3 not found on PATH (used to parse DATABASE_URL safely)."
command -v sha256sum  >/dev/null 2>&1 || die "sha256sum not found on PATH."

[ -n "${DATABASE_URL:-}" ] || die "DATABASE_URL is not set. Refusing to run without an explicit target."

# Parse DATABASE_URL with Python's urlparse so URL-encoded passwords / special
# characters are handled correctly, and the async driver suffix is stripped.
# The parsed values are exported as libpq PG* env vars — the password stays out
# of argv entirely.
eval "$(python3 - "$DATABASE_URL" <<'PY'
import sys, urllib.parse as u
raw = sys.argv[1].replace("+asyncpg", "").replace("+psycopg2", "")
p = u.urlparse(raw)
if not p.hostname or not (p.path or "").lstrip("/"):
    sys.stderr.write("DATABASE_URL missing host or database name\n")
    sys.exit(2)
def sh(v: str) -> str:
    return "'" + (v or "").replace("'", "'\\''") + "'"
print("PGHOST=" + sh(p.hostname))
print("PGPORT=" + sh(str(p.port or 5432)))
print("PGUSER=" + sh(u.unquote(p.username or "")))
print("PGPASSWORD=" + sh(u.unquote(p.password or "")))
print("PGDATABASE=" + sh(u.unquote((p.path or "/").lstrip("/"))))
PY
)"
export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
# Filename carries only the label + timestamp (+ PID for uniqueness if two runs
# land in the same second) — never the host/creds.
OUT="${BACKUP_DIR%/}/sitetracker_${BACKUP_LABEL}_${TS}_$$.dump"

# Until the dump is verified good, treat any exit as a failure and remove the
# partial/empty file so a failed run never leaves behind something that looks
# like a valid backup. Flipped to 1 once verification + checksum succeed, so a
# later offsite/retention failure keeps the good local dump.
BACKUP_OK=0
cleanup() { [ "$BACKUP_OK" = "1" ] || rm -f "$OUT" "${OUT}.sha256"; }
trap cleanup EXIT

log "Dumping database '${PGDATABASE}' on ${PGHOST}:${PGPORT} -> ${OUT}"
# Read-only logical dump. --no-owner/--no-acl keep it portable to any restore
# target (see the flag rationale in staging-backup-restore.md).
pg_dump --format=custom --no-owner --no-acl --file="$OUT" \
  || die "pg_dump failed."

[ -s "$OUT" ] || die "Dump file is empty: $OUT"

log "Verifying the dump is readable (pg_restore --list)"
pg_restore --list "$OUT" >/dev/null \
  || die "Dump verification failed — pg_restore could not read $OUT. Treating as a FAILED backup."

sha256sum "$OUT" | awk '{print $1}' > "${OUT}.sha256"
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
# The local dump is now verified-good; a later offsite/retention failure must
# NOT delete it (the cleanup trap keys off this flag).
BACKUP_OK=1
log "Backup OK: ${OUT} (${SIZE} bytes), sha256=$(cat "${OUT}.sha256")"

# Offsite mirror (off-provider copy). Optional — only if configured.
if [ -n "${BACKUP_S3_URI:-}" ]; then
  command -v aws >/dev/null 2>&1 || die "BACKUP_S3_URI set but the AWS CLI is not installed."
  DEST="${BACKUP_S3_URI%/}/$(basename "$OUT")"
  log "Uploading offsite -> ${DEST}"
  aws s3 cp "$OUT" "$DEST"            || die "Offsite upload failed for $OUT"
  aws s3 cp "${OUT}.sha256" "${DEST}.sha256" || die "Offsite upload failed for the checksum"
  log "Offsite upload complete."
else
  log "BACKUP_S3_URI not set — skipping offsite upload (local dump only)."
fi

# Local retention: prune dumps + checksums older than N days.
if [ "${BACKUP_RETENTION_DAYS}" -gt 0 ] 2>/dev/null; then
  log "Pruning local dumps older than ${BACKUP_RETENTION_DAYS} days in ${BACKUP_DIR}"
  find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'sitetracker_*.dump' -o -name 'sitetracker_*.dump.sha256' \) \
    -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete || true
fi

log "Done."
