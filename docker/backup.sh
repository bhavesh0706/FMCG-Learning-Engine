#!/bin/sh
# ============================================================
# Nightly backups (OPS-01), run by the "backup" service in docker-compose.yml.
# ============================================================
#   /backups/db/fmcg_<date>.sql.gz          full MySQL dump, consistent snapshot
#                                           (--single-transaction, no table locks)
#   /backups/recordings/                    copy of every recording (new files
#                                           added each night, nothing deleted)
#
# /backups is BACKUP_DIR on the host (.env). Put it on a DIFFERENT disk, or a
# mounted cloud bucket, or copy it off the server: a backup on the same disk
# does not survive that disk failing.
#
# Settings: MYSQL_HOST, MYSQL_DATABASE, MYSQL_BACKUP_USER, MYSQL_BACKUP_PASSWORD,
#           BACKUP_KEEP_DAYS (default 14), BACKUP_HOUR (UTC hour, default 20 = 01:30 IST),
#           BACKUP_RUN_NOW=1 to also run once when the service starts.
# Restore:  docker/restore.sh (see the comments there).
set -eu

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
HOUR="${BACKUP_HOUR:-20}"
mkdir -p /backups/db /backups/recordings

backup_once() {
  stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
  target="/backups/db/fmcg_${stamp}.sql.gz"
  echo "[backup] $(date -u) dumping database ${MYSQL_DATABASE} ..."
  # Password in a private option file, not on the command line or in MYSQL_PWD (deprecated).
  opt="$(mktemp)"; chmod 600 "${opt}"
  printf '[client]\npassword="%s"\n' "${MYSQL_BACKUP_PASSWORD}" > "${opt}"
  mysqldump --defaults-extra-file="${opt}" \
    --host="${MYSQL_HOST}" --user="${MYSQL_BACKUP_USER}" \
    --single-transaction --quick --routines --triggers --no-tablespaces \
    --default-character-set=utf8mb4 "${MYSQL_DATABASE}" | gzip -9 > "${target}.part"
  rm -f "${opt}"
  # Only a complete dump gets its final name (a half-written file is never mistaken for a backup).
  mv "${target}.part" "${target}"
  echo "[backup] database dump ok: ${target} ($(du -h "${target}" | cut -f1))"

  echo "[backup] copying new recordings ..."
  # -n: never overwrite; existing copies stay as they are.
  cp -Rn /recordings/. /backups/recordings/ 2>/dev/null || true
  echo "[backup] recordings ok ($(du -sh /backups/recordings | cut -f1))"

  find /backups/db -name 'fmcg_*.sql.gz' -mtime +"${KEEP_DAYS}" -delete
  find /backups/db -name '*.part' -mmin +600 -delete
  date -u > /backups/LAST_SUCCESS
}

if [ "${BACKUP_RUN_NOW:-0}" = "1" ]; then
  backup_once
fi

while true; do
  now_h="$(date -u +%H)"
  now_m="$(date -u +%M)"
  if [ "${now_h}" = "$(printf '%02d' "${HOUR}")" ] && [ "${now_m}" = "00" ]; then
    backup_once || echo "[backup] FAILED at $(date -u)" >&2
    sleep 61
  fi
  sleep 30
done
