#!/usr/bin/env bash
# ============================================================================
# Flutter Server Box 同步服务 — 一键安装脚本（多人版）
# ============================================================================
# 使用方法:
#   chmod +x install.sh && ./install.sh
# ============================================================================

set -e

# ── 颜色 ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
BOLD='\033[1m'

info()  { echo -e "${BLUE}${BOLD}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}${BOLD}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}${BOLD}[WARN]${NC}  $1"; }
err()   { echo -e "${RED}${BOLD}[ERR]${NC}   $1"; }
step()  { echo ""; echo -e "${CYAN}${BOLD}═══════════════════════════════════════${NC}"; echo -e "${CYAN}${BOLD}  $1${NC}"; echo -e "${CYAN}${BOLD}═══════════════════════════════════════${NC}"; }

# ── 脚本所在目录（用于定位源文件） ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 检测 root ─────────────────────────────────────────────────────────────
if [ "$EUID" -eq 0 ]; then
  warn "不建议以 root 用户运行，但继续执行"
fi

# ── 欢迎 ───────────────────────────────────────────────────────────────────
clear
cat << "EOF"
╔══════════════════════════════════════════════════════╗
║     Flutter Server Box 同步服务 — 安装向导          ║
║                                                      ║
║   FastAPI + MySQL + Docker                           ║
║   多人版同步后端 | 邀请码注册 + TOTP + AES-256-GCM   ║
╚══════════════════════════════════════════════════════╝
EOF
echo ""

# ── Step 0: 安装目录 ─────────────────────────────────────────────────────
step "0/7 — 安装目录"

INSTALL_DIR_DEFAULT="/opt/flutter-sync-server"
read -p "  安装目录 (默认 ${INSTALL_DIR_DEFAULT}): " INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-${INSTALL_DIR_DEFAULT}}
info "安装目录: ${INSTALL_DIR}"

# 如果脚本不在目标目录，复制所有源文件过去
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
  if [ -d "$INSTALL_DIR" ]; then
    warn "目录 ${INSTALL_DIR} 已存在，将保留现有文件"
  else
    mkdir -p "$INSTALL_DIR"
    # 排除 .git 和 __pycache__
    rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
    # 如果源有 .git，也复制过去
    if [ -d "${SCRIPT_DIR}/.git" ]; then
      cp -a "${SCRIPT_DIR}/.git" "${INSTALL_DIR}/"
    fi
    ok "源文件已复制到 ${INSTALL_DIR}"
  fi
else
  ok "已在安装目录中，无需复制"
fi

cd "$INSTALL_DIR"

# ── Step 1: 环境检测 ─────────────────────────────────────────────────────
step "1/7 — 检查系统依赖"

PREREQ_OK=true

# Docker
if command -v docker &>/dev/null; then
  ok "Docker 已安装 ($(docker --version))"
else
  err "Docker 未安装"
  echo "  安装命令: curl -fsSL https://get.docker.com | sh"
  PREREQ_OK=false
fi

# Docker Compose
if docker compose version &>/dev/null 2>&1; then
  ok "Docker Compose 已安装 ($(docker compose version 2>/dev/null | head -1))"
elif docker-compose --version &>/dev/null 2>&1; then
  ok "Docker Compose (legacy) 已安装"
else
  err "Docker Compose 未安装"
  PREREQ_OK=false
fi

# openssl
if command -v openssl &>/dev/null; then
  ok "openssl 已安装"
else
  warn "openssl 未安装 — 密钥将使用系统随机数生成"
fi

# curl
if command -v curl &>/dev/null; then
  ok "curl 已安装"
else
  err "curl 未安装"
  PREREQ_OK=false
fi

if [ "$PREREQ_OK" = false ]; then
  echo ""
  err "请安装缺失的依赖后重新运行"
  exit 1
fi

# ── Step 2: MySQL 配置 ──────────────────────────────────────────────────
step "2/7 — 数据库配置"

echo "  请选择数据库方式："
echo "    ${BOLD}1)${NC} 使用 1Panel 已有 MySQL（推荐）"
echo "    ${BOLD}2)${NC} 使用 Docker 启动全新 MySQL 容器"
echo "    ${BOLD}3)${NC} 使用外部远程 MySQL 数据库"
echo ""
read -p "  请输入 [1/2/3] (默认 1): " DB_MODE
DB_MODE=${DB_MODE:-1}

case $DB_MODE in
  2)
    info "Docker MySQL 模式 — 将自动创建 MySQL 容器"
    DB_HOST="mysql"
    DB_PORT=3306
    DB_NAME="sync_server"
    DB_USER="sync_user"
    DB_PASSWORD=$(openssl rand -hex 16 2>/dev/null || echo "$(date +%s%N)$RANDOM" | md5sum | head -c 32)
    MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16 2>/dev/null || echo "$(date +%s%N)$RANDOM" | md5sum | head -c 32)
    ok "密码已自动生成"
    USE_EXISTING_DB=false
    ;;
  3)
    info "远程 MySQL 模式"
    read -p "  MySQL 主机地址: " DB_HOST
    read -p "  MySQL 端口 (默认 3306): " DB_PORT
    DB_PORT=${DB_PORT:-3306}
    read -p "  数据库名 (默认 sync_server): " DB_NAME
    DB_NAME=${DB_NAME:-sync_server}
    read -p "  数据库用户名: " DB_USER
    read -s -p "  数据库密码: " DB_PASSWORD
    echo ""
    USE_EXISTING_DB=true
    ;;
  *)
    info "1Panel 本地 MySQL 模式"
    DB_HOST="127.0.0.1"
    read -p "  MySQL 端口 (默认 3306): " DB_PORT
    DB_PORT=${DB_PORT:-3306}
    read -p "  数据库名 (默认 sync_server): " DB_NAME
    DB_NAME=${DB_NAME:-sync_server}
    read -p "  数据库用户名: " DB_USER
    read -s -p "  数据库密码: " DB_PASSWORD
    echo ""
    USE_EXISTING_DB=true
    ;;
esac

echo ""
info "数据库配置汇总："
echo "    主机: ${DB_HOST}:${DB_PORT}"
echo "    库名: ${DB_NAME}"
echo "    用户: ${DB_USER}"
if [ "$DB_MODE" = "2" ]; then
  echo "    密码: ${DB_PASSWORD}"
  echo "    Root 密码: ${MYSQL_ROOT_PASSWORD}"
fi

# ── Step 3: 密钥生成 ────────────────────────────────────────────────────
step "3/7 — 密钥与安全配置"

read -p "  JWT 密钥（留空自动生成 64 位随机串）: " JWT_SECRET_KEY
if [ -z "$JWT_SECRET_KEY" ]; then
  JWT_SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || echo "$(date +%s%N)$RANDOM$(date +%s%N)" | sha256sum | head -c 64)
fi
ok "JWT 密钥已设置"

ENC_SALT=$(openssl rand -hex 16 2>/dev/null || echo "$(date +%s%N)$RANDOM" | md5sum | head -c 32)
ok "加密盐已自动生成"

# ── Step 4: SMTP 配置（可选） ──────────────────────────────────────────
step "4/7 — 邮箱配置（可选）"

echo "  如果不配置 SMTP，用户注册时不会验证邮箱，但仍可正常登录。"
echo ""
read -p "  是否配置 SMTP 邮箱？(y/n, 默认 n): " SETUP_SMTP
SETUP_SMTP=${SETUP_SMTP:-n}

SMTP_HOST=""
SMTP_PORT=""
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_FROM=""
SMTP_STARTTLS="true"
SMTP_USE_TLS="false"

if [ "$SETUP_SMTP" = "y" ] || [ "$SETUP_SMTP" = "Y" ]; then
  echo ""
  echo "  SMTP 配置（支持 QQ邮箱/163/Gmail/Outlook 等）"
  echo "  端口 465 = 隐式 SSL/TLS（旧标准）"
  echo "  端口 587 = STARTTLS（推荐）"
  read -p "  SMTP 服务器地址: " SMTP_HOST
  read -p "  SMTP 端口 (默认 587): " SMTP_PORT
  SMTP_PORT=${SMTP_PORT:-587}
  read -p "  SMTP 用户名 (完整邮箱地址): " SMTP_USER
  read -s -p "  SMTP 密码/授权码: " SMTP_PASSWORD
  echo ""
  read -p "  发件人地址 (默认同用户名): " SMTP_FROM
  SMTP_FROM=${SMTP_FROM:-$SMTP_USER}
  if [ "$SMTP_PORT" = "465" ]; then
    SMTP_USE_TLS="true"
    SMTP_STARTTLS="false"
    ok "端口 465：已启用隐式 SSL/TLS"
  else
    read -p "  启用 STARTTLS? (y/n, 默认 y): " SMTP_TLS
    SMTP_TLS=${SMTP_TLS:-y}
    if [ "$SMTP_TLS" = "n" ]; then
      SMTP_STARTTLS="false"
    fi
  fi
  ok "邮箱已配置: ${SMTP_FROM}"
else
  info "跳过邮箱配置 — 注册时不会验证邮箱"
fi

# ── Step 5: 管理员账号 ──────────────────────────────────────────────────
step "5/7 — 管理员账号"

echo "  请选择管理员创建方式："
echo "    ${BOLD}1)${NC} 使用默认管理员（用户名 admin，密码 admin123456789）"
echo "    ${BOLD}2)${NC} 自行创建管理员账号和密码"
echo ""
read -p "  请输入 [1/2] (默认 1): " ADMIN_MODE
ADMIN_MODE=${ADMIN_MODE:-1}

if [ "$ADMIN_MODE" = "2" ]; then
  read -p "  管理员用户名: " ADMIN_USERNAME
  read -s -p "  管理员密码: " ADMIN_PASSWORD
  echo ""
  read -s -p "  再次输入密码: " ADMIN_PASSWORD2
  echo ""
  if [ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD2" ]; then
    err "两次输入的密码不一致，请重新运行安装脚本"
    exit 1
  fi
  ok "自定义管理员已设置: ${ADMIN_USERNAME}"
else
  ADMIN_USERNAME="admin"
  ADMIN_PASSWORD="admin123456789"
  ok "使用默认管理员: admin / admin123456789"
  warn "请安装后尽快登录管理后台修改密码！"
fi

read -p "  服务监听端口 (默认 8765): " SERVER_PORT
SERVER_PORT=${SERVER_PORT:-8765}

# ── 生成 .env ──────────────────────────────────────────────────────────
step "生成配置文件 .env"

cat > .env << EOF
# ──────────────────────────────────────────────
# Flutter Server Box 同步服务 — 环境变量
# 由 install.sh 自动生成于 $(date '+%Y-%m-%d %H:%M:%S')
# ──────────────────────────────────────────────

# 数据库
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=${DB_NAME}

# JWT
JWT_SECRET_KEY=${JWT_SECRET_KEY}

# 加密盐
ENC_SALT=${ENC_SALT}

# 管理员
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}

# 服务端口
SERVER_PORT=${SERVER_PORT}
EOF

if [ -n "$SMTP_HOST" ]; then
  cat >> .env << EOF

# SMTP
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USER=${SMTP_USER}
SMTP_PASSWORD=${SMTP_PASSWORD}
SMTP_FROM=${SMTP_FROM}
SMTP_STARTTLS=${SMTP_STARTTLS}
SMTP_USE_TLS=${SMTP_USE_TLS}
EOF
fi

chmod 600 .env
ok ".env 文件已生成 (权限 600)"

# ── 构建并启动 ──────────────────────────────────────────────────────────
step "6/7 — 构建并启动"

echo "  请选择启动方式："
echo "    ${BOLD}1)${NC} docker compose up -d（推荐，后台运行）"
echo "    ${BOLD}2)${NC} 仅生成配置文件，稍后手动启动"
echo ""
read -p "  请输入 [1/2] (默认 1): " START_MODE
START_MODE=${START_MODE:-1}

if [ "$START_MODE" = "1" ]; then
  echo ""
  info "正在构建并启动服务..."

  if [ "$DB_MODE" = "2" ]; then
    export MYSQL_ROOT_PASSWORD
    export DB_PASSWORD
    docker compose --profile with-db up -d --build
  else
    docker compose up -d --build app
  fi

  echo ""
  echo "  等待服务就绪..."
  for i in $(seq 1 30); do
    if curl -s "http://127.0.0.1:${SERVER_PORT}/docs" >/dev/null 2>&1; then
      echo ""
      ok "服务已启动！"
      break
    fi
    sleep 2
    echo -n "."
  done
else
  info "配置文件已就绪，稍后运行: docker compose up -d --build"
fi

# ── 完成 ────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ${GREEN}${BOLD}✅  安装完成${NC}                                        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  ${BOLD}安装目录:${NC}     ${INSTALL_DIR}"
echo "  ${BOLD}服务地址:${NC}     http://127.0.0.1:${SERVER_PORT}"
echo "  ${BOLD}API 文档:${NC}     http://127.0.0.1:${SERVER_PORT}/docs"
echo "  ${BOLD}管理后台:${NC}     http://127.0.0.1:${SERVER_PORT}/admin"
echo ""

if [ "$DB_MODE" = "2" ]; then
  echo "  ┌─ MySQL ──────────────────────────────────┐"
  echo "  │  主机: 127.0.0.1:3306                     │"
  echo "  │  用户: ${DB_USER}                          │"
  echo "  │  密码: ${DB_PASSWORD}                      │"
  echo "  │  Root: ${MYSQL_ROOT_PASSWORD}              │"
  echo "  └──────────────────────────────────────────┘"
  echo ""
fi

echo "  ┌─ 管理账号 ────────────────────────────────┐"
echo "  │  用户名: ${ADMIN_USERNAME}                  │"
echo "  │  密码:   ${ADMIN_PASSWORD}                  │"
echo "  └──────────────────────────────────────────┘"
echo ""
echo "  ${YELLOW}${BOLD}⚠  后续操作：${NC}"
echo "  1. 在 1Panel 配置反向代理到 127.0.0.1:${SERVER_PORT}"
echo "  2. 配置 HTTPS（Let's Encrypt 自动证书）"
echo "  3. 登录管理后台创建邀请码"
echo "  4. 分发邀请码给用户注册"
echo ""
echo "  ${YELLOW}${BOLD}📄  关键信息已保存到 ${INSTALL_DIR}/.env（权限 600），请妥善保管！${NC}"
echo "  ${YELLOW}${BOLD}📄  重新安装请运行: ${INSTALL_DIR}/install.sh${NC}"
echo ""

read -p "  按回车退出..."
