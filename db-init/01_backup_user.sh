#!/bin/bash
# Runs once, when the MySQL container creates its data folder for the first time.
# Creates a read-only account for the nightly backup (docker/backup.sh), so the
# backup service never holds the root or application password.
# For an EXISTING database (already initialized), run the same SQL once by hand:
#   docker compose exec mysql mysql -uroot -p
set -euo pipefail

if [ -z "${MYSQL_BACKUP_PASSWORD:-}" ]; then
  echo "MYSQL_BACKUP_PASSWORD not set: skipping the backup user." >&2
  exit 0
fi

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS '${MYSQL_BACKUP_USER:-fmcg_backup}'@'%' IDENTIFIED BY '${MYSQL_BACKUP_PASSWORD}';
GRANT SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES, PROCESS ON *.* TO '${MYSQL_BACKUP_USER:-fmcg_backup}'@'%';
FLUSH PRIVILEGES;
SQL
echo "Backup user ${MYSQL_BACKUP_USER:-fmcg_backup} ready."
