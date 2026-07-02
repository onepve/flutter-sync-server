#!/bin/bash
"""
每日 MySQL 全库备份脚本
保留 30 天，按日期命名
"""

BACKUP_DIR="${HOME}/sync-backups"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_USER="${DB_USER:-sync_user}"
DB_PASSWORD="${DB_PASSWORD:-changeme}"
DB_NAME="${DB_NAME:-sync_server}"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR" 2>/dev/null || { echo "❌ 创建备份目录失败"; exit 1; }

DATE_TAG=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/sync-${DB_NAME}-${DATE_TAG}.sql.gz"

mysqldump \
  --host="${DB_HOST}" \
  --user="${DB_USER}" \
  --password="${DB_PASSWORD}" \
  --single-transaction \
  --routines \
  --triggers \
  "${DB_NAME}" 2>/dev/null | gzip > "${BACKUP_FILE}"

if [ $? -eq 0 ]; then
    echo "✅ 备份完成: ${BACKUP_FILE}"
    # 清理 30 天前的旧备份
    find "${BACKUP_DIR}" -name "sync-*.sql.gz" -mtime +${RETENTION_DAYS} -delete 2>/dev/null
    exit 0
else
    echo "❌ 备份失败"
    exit 1
fi
