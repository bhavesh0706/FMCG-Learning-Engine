#!/bin/sh
# ============================================================
# Restore a database backup made by docker/backup.sh.
# ============================================================
# Run from the folder with docker-compose.yml:
#
#   1. Stop the app so nothing writes meanwhile:
#        docker compose stop backend
#   2. Restore (the newest dump, or name one):
#        docker compose run --rm -e MYSQL_RESTORE_PASSWORD=<root password> --entrypoint /bin/sh backup /usr/local/bin/restore.sh
#        docker compose run --rm -e MYSQL_RESTORE_PASSWORD=<root password> --entrypoint /bin/sh backup /usr/local/bin/restore.sh /backups/db/fmcg_2026-10-15T200000Z.sql.gz
#   3. Recordings: copy them back into the recordings volume if they were lost:
#        docker compose run --rm --entrypoint /bin/sh backup -c "cp -Rn /backups/recordings/. /recordings/"
#      (the backup service mounts /recordings read-only; for a restore, remove
#       ':ro' from that volume line in docker-compose.yml first.)
#   4. Start again:  docker compose start backend
#
# Test a restore every month on a spare server: a backup that was never
# restored is not known to work.
set -eu

FILE="${1:-$(ls -1t /backups/db/fmcg_*.sql.gz 2>/dev/null | head -n 1)}"
if [ -z "${FILE}" ] || [ ! -f "${FILE}" ]; then
  echo "No backup file found (looked in /backups/db)." >&2
  exit 1
fi

echo "Restoring ${FILE} into database ${MYSQL_DATABASE} on ${MYSQL_HOST} ..."
if [ -z "${MYSQL_RESTORE_PASSWORD:-}" ]; then
  echo "Set MYSQL_RESTORE_PASSWORD (the MySQL root password) for the restore, e.g.:" >&2
  echo "  docker compose run --rm -e MYSQL_RESTORE_PASSWORD=... --entrypoint /bin/sh backup /usr/local/bin/restore.sh" >&2
  exit 1
fi
opt="$(mktemp)"; chmod 600 "${opt}"
printf '[client]\npassword="%s"\n' "${MYSQL_RESTORE_PASSWORD}" > "${opt}"
gunzip -c "${FILE}" | mysql --defaults-extra-file="${opt}" \
  --host="${MYSQL_HOST}" --user="${MYSQL_RESTORE_USER:-root}" --default-character-set=utf8mb4 "${MYSQL_DATABASE}"
rm -f "${opt}"
echo "Restore finished."
