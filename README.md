# CBox 同步服务端 — 完整技术规格书

> **目的**：本文档描述了 CBox 同步服务端（FastAPI + MySQL）的完整技术实现。  
> 另一个开发者拿到这份文档 + 源码，就能 1:1 复刻部署、理解每一行代码的设计意图、修改任何功能。  
> 最后更新：2026-07-02 | 源码版本：基于 `flutter-sync-server` 仓库当前状态

---

## 目录

- [§1 项目概述](#1-项目概述)
- [§2 技术选型与依赖](#2-技术选型与依赖)
- [§3 项目文件结构](#3-项目文件结构)
- [§4 数据库设计](#4-数据库设计)
- [§5 环境变量](#5-环境变量)
- [§6 API 接口完整参考](#6-api-接口完整参考)
  - [6.1 认证模块 /api/auth](#61-认证模块-apiauth)
  - [6.2 数据同步模块 /api/sync](#62-数据同步模块-apisync)
  - [6.3 邀请码模块 /api/invite](#63-邀请码模块-apiinvite)
  - [6.4 管理后台模块 /api/admin](#64-管理后台模块-apiadmin)
  - [6.5 公开端点](#65-公开端点)
- [§7 业务逻辑流程详解](#7-业务逻辑流程详解)
- [§8 安全架构](#8-安全架构)
- [§9 邮件服务](#9-邮件服务)
- [§10 前端静态页面](#10-前端静态页面)
- [§11 部署指南](#11-部署指南)
- [§12 运维操作](#12-运维操作)
- [§13 附录：关键代码片段](#13-附录关键代码片段)

---

## §1 项目概述

### 1.1 这是什么

CBox 是一个跨平台 SSH 服务器管理客户端（Flutter App）。本仓库是其**云端同步服务端**，负责：

- 用户注册、登录、身份认证
- 端到端加密同步数据的存储与版本管理（服务端**零信任**，不解密任何数据）
- 邀请码机制控制用户准入
- 管理员后台：用户管理、审计日志、系统配置
- 邮件通知：验证码、Recovery Key、密码重置、数据备份

### 1.2 关键设计原则

| 原则 | 实现方式 |
|------|----------|
| **零信任** | 同步数据在客户端 AES-256-GCM 加密，服务端只存密文；密钥从用户密码 Argon2id 派生 |
| **最小依赖** | 只依赖 MySQL + FastAPI + 标准密码学库，不引入 Redis/消息队列等 |
| **自托管友好** | 所有外部服务均可选（SMTP 未配则跳过邮件流程，自动降级） |
| **防滥用** | 多层限流（slowapi）+ 登录锁定 + 验证码防刷冷却 |
| **可审计** | 所有敏感操作写入 audit_logs 表，username 写死防止用户删除后无法追溯 |

### 1.3 运行环境

```
Python 3.12+
MySQL 8.0+（utf8mb4）
Docker（推荐）或裸机 uvicorn
```

### 1.4 域名与端口

```
服务域名：sync.onepve.com
服务端口：8765（network_mode: host，绑定宿主机）
邮箱发件人：no_reply@info.onepve.com
SMTP：阿里云邮件推送 smtpdm.aliyun.com:465（隐式 SSL）
```

---

## §2 技术选型与依赖

### 2.1 requirements.txt

```
fastapi==0.115.6          # Web 框架
uvicorn[standard]==0.34.0 # ASGI 服务器
sqlalchemy==2.0.36        # ORM
pymysql==1.1.1            # MySQL 驱动
cryptography==44.0.0      # AES-GCM / Argon2id（客户端侧，服务端维护依赖）
pyotp==2.9.0              # TOTP 双因素认证
qrcode==7.4.2             # TOTP 二维码生成
python-jose[cryptography]==3.3.0  # JWT 签发与验证
passlib[bcrypt]==1.7.4    # bcrypt 密码哈希
bcrypt==4.0.1             # bcrypt 底层
python-multipart==0.0.19  # 文件上传解析（头像）
pydantic[email]==2.10.3   # 请求/响应模型验证
pydantic-settings==2.7.0  # 环境变量管理
httpx==0.28.1             # HTTP 客户端（测试用）
aiosmtplib==3.0.2         # 异步 SMTP 发送
slowapi==0.1.9            # 速率限制
```

### 2.2 为什么选这些

| 问题 | 方案 | 理由 |
|------|------|------|
| 密码存储 | bcrypt | 内置盐值，慢哈希，抗彩虹表 |
| 双因素认证 | TOTP (pyotp) | 标准 RFC 6238，兼容 Google Authenticator / Authy |
| JWT | HS256 (python-jose) | 对称签名，服务端签发验证，24 小时过期 |
| 速率限制 | slowapi | 内存计数，单进程够用 |
| 邮件 | aiosmtplib | 异步非阻塞，不拖慢 API 响应 |
| 数据库 | MySQL 8.0 + PyMySQL | 纯 Python 驱动，无 C 扩展编译问题 |

---

## §3 项目文件结构

```
flutter-sync-server/
├── main.py                    # FastAPI 入口：lifespan 启动、路由注册、静态文件挂载
├── config.py                  # 全局配置类 Settings（从 .env 读取）
├── database.py                # SQLAlchemy 引擎、会话工厂、get_db() 依赖
├── requirements.txt           # Python 依赖
├── Dockerfile                 # Docker 镜像
├── docker-compose.yml         # 容器编排
├── .env.example               # 环境变量模板
│
├── routers/                   # API 路由（4 个模块）
│   ├── __init__.py            # 导出四个 router
│   ├── auth.py                # 认证：注册/登录/TOTP/Recovery Key/密码重置/注销/头像
│   ├── sync.py                # 数据同步：上传/下载/diff/状态/删除/导出到邮箱
│   ├── invite.py              # 邀请码：CRUD/批量/锁定/重置/删除/用户自创/公开列表/CSV
│   └── admin.py               # 管理后台：统计/用户管理/审计日志/邮件日志/系统配置/邀请码日志
│
├── models/                    # SQLAlchemy 数据模型（7 个）
│   ├── user.py                # 用户
│   ├── encrypted_data.py      # 加密同步数据
│   ├── invite_code.py         # 邀请码
│   ├── audit_log.py           # 审计日志
│   ├── email_log.py           # 邮件发送日志
│   ├── password_reset.py      # 密码重置令牌
│   └── system_config.py       # 系统配置（KV 表）
│
├── schemas/                   # Pydantic 请求/响应模型（3 个模块）
│   ├── auth_schema.py         # 认证相关
│   ├── sync_schema.py         # 同步相关
│   └── admin_schema.py        # 管理后台相关
│
├── services/                  # 业务逻辑服务（4 个）
│   ├── security.py            # JWT、密码哈希、登录锁定、用户认证依赖
│   ├── email.py               # SMTP 邮件发送、邮件日志
│   ├── totp.py                # TOTP 密钥生成、二维码、验证
│   └── crypto.py              # AES-256-GCM 加密（供客户端参考，服务端不调用）
│
└── static/                    # 前端静态页面（7 个 HTML）
    ├── index.html             # 首页
    ├── admin.html             # 管理后台（7 标签页 SPA，含管理员个人资料入口）
    ├── profile.html           # 个人资料（带头像裁剪）
    ├── invite.html            # 邀请码管理
    ├── public-invites.html    # 公开邀请码浏览
    ├── user-invites.html      # 我的邀请码
    └── doc.html               # 文档页面
```

---

## §4 数据库设计

### 4.1 建库

```sql
CREATE DATABASE sync_server CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sync_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON sync_server.* TO 'sync_user'@'%';
FLUSH PRIVILEGES;
```

### 4.2 表结构

#### 4.2.1 users — 用户表

```sql
CREATE TABLE users (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    uuid            VARCHAR(36) NOT NULL UNIQUE,       -- UUIDv4
    username        VARCHAR(64) NOT NULL UNIQUE,       -- 用户名，3-64 字符
    email           VARCHAR(255) NOT NULL UNIQUE,       -- 邮箱
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,    -- 邮箱已验证
    password_hash   VARCHAR(255) NOT NULL,             -- bcrypt 哈希
    nickname        VARCHAR(64) DEFAULT NULL,           -- 昵称（可选，≤64 字符）
    avatar_url      VARCHAR(512) DEFAULT NULL,          -- 头像访问路径
    avatar_data     MEDIUMBLOB DEFAULT NULL,            -- 头像二进制（≤5MB）
    avatar_mime     VARCHAR(32) DEFAULT NULL,           -- 头像 MIME 类型
    avatar_updated_at DATETIME DEFAULT NULL,            -- 头像最后更新时间
    totp_secret     VARCHAR(64) DEFAULT NULL,           -- TOTP 密钥（Base32）
    totp_enabled    BOOLEAN NOT NULL DEFAULT FALSE,     -- TOTP 已启用
    recovery_key_hash VARCHAR(255) DEFAULT NULL,       -- Recovery Key（bcrypt）
    login_attempts  INT NOT NULL DEFAULT 0,             -- 连续登录失败次数
    locked_until    DATETIME DEFAULT NULL,              -- 锁定解除时间
    display_order   INT DEFAULT NULL,                   -- 后台排序（手动编辑）
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,      -- 账号启用
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,     -- 管理员标识
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login_at   DATETIME DEFAULT NULL,
    last_login_ip   VARCHAR(45) DEFAULT NULL,

    INDEX idx_users_username (username),
    INDEX idx_users_email (email),
    INDEX idx_users_uuid (uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

#### 4.2.2 encrypted_data — 加密同步数据表

```sql
CREATE TABLE encrypted_data (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT NOT NULL,
    data_type       VARCHAR(64) NOT NULL,              -- 数据类型: servers|keys|settings 等
    device_id       VARCHAR(128) NOT NULL,             -- 上传设备标识
    ciphertext      LONGBLOB NOT NULL,                 -- AES-256-GCM 密文
    version         INT NOT NULL DEFAULT 1,            -- 单调递增版本号
    plaintext_size  INT NOT NULL DEFAULT 0,            -- 明文大小（字节）
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_encrypted_user (user_id),
    UNIQUE KEY uk_user_type (user_id, data_type),     -- 每个用户的每种类型只有一条
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

#### 4.2.3 invite_codes — 邀请码表

```sql
CREATE TABLE invite_codes (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(64) NOT NULL UNIQUE,       -- 32 位 hex 随机码
    expires_at      DATETIME DEFAULT NULL,             -- 过期时间（NULL=永久）
    max_uses        INT NOT NULL DEFAULT 1,            -- 最大使用次数（-1=无限）
    used_count      INT NOT NULL DEFAULT 0,            -- 已使用次数
    created_by      INT DEFAULT NULL,                  -- 创建者 user_id
    is_public       BOOLEAN NOT NULL DEFAULT FALSE,    -- 是否公开
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,     -- 是否启用
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_invite_code (code),
    INDEX idx_invite_creator (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

#### 4.2.4 audit_logs — 审计日志表

```sql
CREATE TABLE audit_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT DEFAULT NULL,                  -- 操作用户 ID（可为 NULL）
    username        VARCHAR(64) DEFAULT NULL,          -- 操作用户名（写死，删除后仍可追踪）
    action          VARCHAR(64) NOT NULL,              -- 操作类型
    detail          TEXT DEFAULT NULL,                 -- 操作详情
    ip_address      VARCHAR(45) DEFAULT NULL,          -- 来源 IP
    path            VARCHAR(512) DEFAULT NULL,         -- 请求路径
    success         BOOLEAN NOT NULL DEFAULT TRUE,     -- 是否成功
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_audit_action (action),
    INDEX idx_audit_user (user_id),
    INDEX idx_audit_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

#### 4.2.5 email_logs — 邮件日志表

```sql
CREATE TABLE email_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    to_email        VARCHAR(255) NOT NULL,             -- 收件人
    subject         VARCHAR(255) NOT NULL,             -- 主题
    body            TEXT NOT NULL,                     -- HTML 正文
    success         BOOLEAN NOT NULL DEFAULT TRUE,     -- 发送成功
    error_message   TEXT DEFAULT NULL,                 -- 失败原因
    user_id         INT DEFAULT NULL,                  -- 触发的用户 ID（可为 NULL）
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_emaillog_email (to_email),
    INDEX idx_emaillog_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

#### 4.2.6 password_reset_tokens — 密码重置令牌表

```sql
CREATE TABLE password_reset_tokens (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT NOT NULL,
    token           VARCHAR(128) NOT NULL UNIQUE,      -- 8 位数字验证码
    expires_at      DATETIME NOT NULL,                 -- 过期时间（1 小时）
    used            BOOLEAN NOT NULL DEFAULT FALSE,     -- 是否已使用
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_reset_token (token),
    INDEX idx_reset_user (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

#### 4.2.7 system_config — 系统配置表（KV）

```sql
CREATE TABLE system_config (
    `key`   VARCHAR(64) PRIMARY KEY,
    `value` TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 默认值（代码中 DEFAULT_SYSTEM_CONFIG）：
-- key="require_invite_for_registration"  value="true"
-- key="allow_user_create_invite"         value="false"
-- key="max_invites_per_user"             value="5"
```

### 4.3 表关系图

```
users ──1:N── encrypted_data      (user_id FK, CASCADE DELETE)
users ──1:N── password_reset_tokens (user_id FK, CASCADE DELETE)
users ──1:N── invite_codes         (created_by, 无 FK 约束)
users ──1:N── audit_logs           (user_id, 无 FK 约束，用户删除后保留)
users ──1:N── email_logs           (user_id, 无 FK 约束)
system_config                      (独立 KV 表，无关联)
```

---

## §5 环境变量

### 5.1 .env 文件全部变量

```ini
# ========== 数据库 ==========
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=sync_user
DB_PASSWORD=changeme
DB_NAME=sync_server

# ========== JWT ==========
JWT_SECRET_KEY=change-this-to-a-random-secret     # 至少 32 字符随机字符串
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440                           # Token 有效期（分钟），默认 24h

# ========== 加密（服务端盐值，密钥仍从客户端派生）==========
ENC_SALT=sync-server-salt-change-me

# ========== TOTP ==========
TOTP_ISSUER=FlutterServerBox                      # 显示在 TOTP App 中的发行方名称

# ========== SMTP（可选，不配则跳过所有邮件）==========
SMTP_HOST=smtpdm.aliyun.com
SMTP_PORT=465
SMTP_USER=your_aliyun_smtp_user
SMTP_PASSWORD=your_aliyun_smtp_password
SMTP_FROM=no_reply@info.onepve.com
SMTP_STARTTLS=true
SMTP_USE_TLS=true                                 # 端口 465 隐式 SSL

# ========== 安全限制 ==========
LOGIN_MAX_ATTEMPTS=5                              # 锁定前允许失败次数
LOGIN_LOCK_MINUTES=15                             # 锁定分钟数
IP_BAN_THRESHOLD=10
IP_BAN_HOURS=1
RATE_LIMIT_GLOBAL=100/minute
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_SYNC=30/minute

# ========== 管理员账号（首次启动从 .env 读取创建）==========
ADMIN_USERNAME=admin
ADMIN_PASSWORD=Admin@123

# ========== 服务 ==========
SERVER_HOST=0.0.0.0
SERVER_PORT=8765
```

### 5.2 变量说明

| 变量 | 默认值 | 必填 | 说明 |
|------|--------|:---:|------|
| DB_HOST | 127.0.0.1 | ✅ | 数据库地址 |
| DB_PORT | 3306 | ✅ | 数据库端口 |
| DB_USER | sync_user | ✅ | 数据库用户 |
| DB_PASSWORD | changeme | ✅ | 数据库密码 |
| DB_NAME | sync_server | ✅ | 数据库名 |
| JWT_SECRET_KEY | — | ✅ | JWT 签名密钥，生产环境必须改 |
| JWT_EXPIRE_MINUTES | 1440 | ❌ | Token 有效期（分钟） |
| SMTP_HOST | — | ❌ | SMTP 服务器地址，不配则跳过所有邮件 |
| SMTP_PORT | — | ❌ | SMTP 端口 |
| SMTP_USER | — | ❌ | SMTP 用户名 |
| SMTP_PASSWORD | — | ❌ | SMTP 密码 |
| SMTP_FROM | — | ❌ | 发件人地址 |
| SMTP_USE_TLS | false | ❌ | 隐式 SSL（端口 465） |
| SMTP_STARTTLS | true | ❌ | STARTTLS（端口 587） |
| LOGIN_MAX_ATTEMPTS | 5 | ❌ | 登录失败锁定阈值 |
| LOGIN_LOCK_MINUTES | 15 | ❌ | 锁定时长（分钟） |
| ADMIN_USERNAME | admin | ❌ | 管理员用户名 |
| ADMIN_PASSWORD | — | ✅ | 管理员密码 |
| TOTP_ISSUER | FlutterServerBox | ❌ | TOTP 显示名称 |

---

## §6 API 接口完整参考

> **认证方式**：除标注"无需认证"外，所有接口需在 Header 中携带 `Authorization: Bearer <jwt_token>`  
> **管理员接口**：标注 `🔒` 的接口需要 `is_admin=true` 的 JWT Token  
> **限流**：全局 100/min，登录 5/min，同步 30/min  
> **响应格式**：成功返回 JSON，失败返回 `{"detail": "错误信息"}` + HTTP 状态码

---

### 6.1 认证模块 `/api/auth`

#### 6.1.1 注册 `POST /api/auth/register`

```
认证：无需
限流：全局 100/min

请求体：
{
    "username":     "john_doe",          // 必填，3-64 字符，仅允许 a-z A-Z 0-9 _
    "email":        "john@example.com",  // 必填，合法邮箱
    "password":     "mypassword123",     // 必填，8-128 字符
    "nickname":     "John",              // 可选，最多 64 字符
    "invite_code":  "abc123..."          // 可选，系统要求时必填（见下方分支逻辑）
}
```

**分支 1 — 系统要求邀请码 + 未提供**：
```
400 { "detail": "当前系统要求邀请码才能注册" }
```

**分支 2 — 邀请码无效/过期/用完**：
```
400 { "detail": "邀请码无效" }
400 { "detail": "邀请码已过期" }
400 { "detail": "邀请码已被用完" }
```

**分支 3 — 用户名或邮箱重复**：
```
400 { "detail": "用户名已被占用" }
400 { "detail": "邮箱已被注册" }
```

**分支 4 — SMTP 已配置，注册成功**：
```
200 {
    "id":             1,
    "uuid":           "550e8400-e29b-41d4-a716-446655440000",
    "username":       "john_doe",
    "email":          "john@example.com",
    "message":        "注册成功，请验证邮箱",
    "recovery_key":   "a1b2c3d4e5f6..."   // 32 位 hex，⚠️ 仅此一次可见！
}
// 同时发送两封邮件：邮箱验证码（8 位数字）+ Recovery Key
```

**分支 5 — SMTP 未配置，注册成功**：
```
200 { ...同上... }
// recovery_key 返回 "SMTP 未配置，Recovery Key 未生成"
// email_verified 自动设为 true
```

**分支 6 — SMTP 已配置但发信失败**：
```
200 { ... } // 注册仍然成功，email_verified 自动设为 true（跳过验证）
```

**服务端内部操作**：
```
1. 创建 User 记录
2. 生成 UUIDv4 作为 user.uuid
3. password_hash = bcrypt(password)
4. recovery_key = secrets.token_hex(16) → recovery_key_hash = bcrypt(recovery_key)
5. 如果有邀请码 → invite.used_count += 1
6. SMTP 已配 → 生成 8 位数字验证码存内存（10 分钟有效）
            → 发送验证码邮件 + Recovery Key 邮件
            → email_verified = False
7. SMTP 未配 → email_verified = True
8. 写入 audit_logs: action="register", success=true
```

---

#### 6.1.2 邮箱验证 `POST /api/auth/verify-email`

```
认证：无需
限流：全局 100/min

请求体：
{
    "user_id":  1,
    "code":     "12345678"       // 8 位数字
}
```

**成功**：
```
200 { "message": "邮箱验证成功" }
// 服务端自动发送解密密钥邮件（UUID）
```

**失败**：
```
400 { "detail": "用户不存在" }
400 { "detail": "邮箱已验证" }           // 已通过，幂等返回
400 { "detail": "未请求验证码或验证码已过期" }
400 { "detail": "验证码与用户不匹配" }
400 { "detail": "验证码已过期，请重新发送" }
400 { "detail": "验证码错误" }
```

**服务端内部操作**：
```
1. 查 user → 不存在 404
2. 已 verified → 幂等返回
3. 从内存字典 _verification_codes[email] 取出缓存
4. 校验 user_id 匹配 + 未过期（10 分钟）+ code 匹配
5. user.email_verified = True
6. 删除验证码缓存
7. SMTP 已配 → send_decryption_key(email, user.uuid)
8. 审计日志: action="verify_email"
```

---

#### 6.1.3 重新发送验证码 `POST /api/auth/resend-verification`

```
认证：JWT Bearer Token
限流：60 秒冷却（同邮箱）

请求体：无（从 Token 解析用户）

成功：
200 { "message": "验证码已重新发送到您的邮箱" }

失败：
200 { "message": "邮箱已验证，无需重新发送" }
200 { "message": "邮件服务未配置，请联系管理员" }
429 { "detail": "请 60 秒后再试" }        // 防刷冷却
200 { "message": "邮件发送失败，请稍后重试" }
```

---

#### 6.1.4 登录 `POST /api/auth/login`

```
认证：无需
限流：5/min（登录专用限流）

请求体：
{
    "username":   "john_doe",       // 用户名或邮箱均可
    "password":   "mypassword123",
    "totp_code":  null              // 可选，TOTP 用户第二轮提交时带
}
```

**分支 1 — 用户名/密码错误**：
```
401 { "detail": "用户名或密码错误" }
// 服务端: login_attempts += 1，达到 5 次 → locked_until = now + 15min
```

**分支 2 — 账户被管理员禁用**：
```
403 { "detail": "账户已被禁用" }
```

**分支 3 — 登录锁定中**：
```
429 { "detail": "账户已被锁定，请在 X 分钟后重试" }
```

**分支 4 — TOTP 已启用但未带 totp_code**：
```
200 {
    "access_token":   "",
    "user_id":        1,
    "uuid":           "...",
    "username":       "john_doe",
    "totp_required":  true
}
// 前端收到 totp_required=true 后弹出 TOTP 输入框
// 用户输入 6 位码后，携带 totp_code 再次请求本接口
```

**分支 5 — TOTP 验证码错误**：
```
401 { "detail": "TOTP 验证码错误" }
```

**分支 6 — 登录成功**：
```
200 {
    "access_token":   "eyJhbGciOi...",    // JWT Token
    "token_type":     "bearer",
    "user_id":        1,
    "uuid":           "...",
    "username":       "john_doe",
    "nickname":       "John",
    "avatar_url":     "/api/auth/profile/avatar/image?t=1719000000",
    "totp_required":  false
}
```

**服务端内部操作**：
```
1. 查找用户（匹配 username 或 email）
2. check_login_lock() → 检查 is_active 和 locked_until
3. bcrypt.verify(password, password_hash)
   └─ 失败 → record_login_failure() → login_attempts += 1
             → >=5 则 locked_until = now + 15min
             → 审计日志 action="login_failed"
4. 密码通过 → 检查 totp_enabled
   ├─ 未启 → 跳过
   └─ 已启 → 检查 req.totp_code
             ├─ 未带 → 返回 totp_required=true（不签发 token）
             └─ 带了 → pyotp.verify()
                      └─ 失败 → 同密码失败的锁定逻辑
5. 成功 → reset_login_lock()（清零 attempts/locked_until）
        → 更新 last_login_at, last_login_ip
        → 签发 JWT（payload: {sub, username, admin, exp, iat}）
        → 审计日志 action="login"
```

---

#### 6.1.5 Recovery Key 登录 `POST /api/auth/recovery-login`

```
认证：无需
限流：5/min

请求体：
{
    "username":     "john_doe",
    "recovery_key": "a1b2c3d4e5f6..."
}
```

**成功**：同正常登录响应（包含 access_token）
**失败**：
```
401 { "detail": "用户名或 Recovery Key 错误" }
```

**服务端内部**：
```
1. 查用户 → 不存在或 recovery_key_hash 为空 → 401
2. check_login_lock()
3. bcrypt.verify(recovery_key, recovery_key_hash)
   └─ 失败 → record_login_failure()
   └─ 成功 → reset_login_lock() → 签发 JWT
```

---

#### 6.1.6 获取 TOTP 设置信息 `POST /api/auth/totp/setup`

```
认证：JWT

请求体：无

200 {
    "secret":     "JBSWY3DPEHPK3PXP",     // Base32 TOTP 密钥
    "qrcode_b64": "iVBORw0KGgo..."        // base64 PNG 二维码
}
```

**服务端内部**：
```
1. pyotp.random_base32() 生成密钥
2. pyotp.TOTP.provisioning_uri() 生成 otpauth:// URI
3. qrcode.make(uri) 生成二维码 → base64 编码
4. secret 暂存到 user.totp_secret（未启用状态）
```

---

#### 6.1.7 验证并启用 TOTP `POST /api/auth/totp/verify`

```
认证：JWT

请求体：
{
    "code":  "123456"       // TOTP App 显示的 6 位码
}

200 {
    "message":                    "TOTP 已启用",
    "recovery_key":               "a1b2c3d4...",    // ⚠️ 仅此一次！
    "recovery_key_sent_to_email": true
}

400 { "detail": "请先调用 /totp/setup" }
400 { "detail": "验证码错误" }
```

**服务端内部**：
```
1. 检查 user.totp_secret 是否存在
2. pyotp.TOTP.verify(code, valid_window=1) → 允许 ±30s 偏差
3. 通过 → user.totp_enabled = True
4. 生成新 Recovery Key（32 位 hex）
5. recovery_key_hash = bcrypt(recovery_key)
6. SMTP 已配 → send_recovery_key()
```

---

#### 6.1.8 关闭 TOTP `POST /api/auth/totp/disable`

```
认证：JWT

请求体：
{
    "code":  "123456"       // TOTP 码 或 Recovery Key
}

200 { "message": "TOTP 已关闭" }
200 { "message": "TOTP 未启用" }        // 幂等
400 { "detail": "验证码错误" }
```

**服务端内部**：
```
1. 未启用 → 直接返回
2. pyotp.verify(totp_secret, code) → 通过则清空 totp_secret + totp_enabled=False
```

---

#### 6.1.9 查看 TOTP 状态 `GET /api/auth/totp/status`

```
认证：JWT

200 { "enabled": true }
```

---

#### 6.1.10 获取个人资料 `GET /api/auth/profile`

```
认证：JWT

200 {
    "id":             1,
    "uuid":           "550e8400-...",
    "username":       "john_doe",
    "nickname":       "John",
    "email":          "john@example.com",
    "avatar_url":     "/api/auth/profile/avatar/image?t=1719000000",
    "email_verified": true,
    "totp_enabled":   true,
    "is_active":      true,
    "created_at":     "2026-01-01T00:00:00"
}
```

---

#### 6.1.11 修改邮箱 `PUT /api/auth/profile`

```
认证：JWT

请求体：new_email (查询参数)
或 JSON: { "new_email": "new@example.com" }   ← 注意：源码中使用查询参数方式

200 { "message": "更新成功" }
400 { "detail": "邮箱已被使用" }
```

---

#### 6.1.12 修改用户名 `PUT /api/auth/profile/username`

```
认证：JWT

请求体：new_username (查询参数)

200 { "message": "用户名已更新" }
400 { "detail": "用户名长度需在 3-64 位之间" }
400 { "detail": "用户名已被占用" }
```

---

#### 6.1.13 修改昵称 `PUT /api/auth/profile/nickname`

```
认证：JWT

请求体：
{
    "new_nickname": "Johnny"
}

200 { "message": "昵称已更新" }
400 { "detail": "昵称不能超过 64 个字符" }
// 空字符串 → nickname = null
```

---

#### 6.1.14 修改密码 `PUT /api/auth/profile/password`

```
认证：JWT

请求体：
{
    "old_password": "oldpass123",
    "new_password": "newpass456"
}

200 { "message": "密码已更新" }
400 { "detail": "旧密码错误" }
400 { "detail": "密码长度需在 8-128 位之间" }
```

---

#### 6.1.15 上传头像 `POST /api/auth/profile/avatar`

```
认证：JWT
请求格式：multipart/form-data
字段名：file

200 { "avatar_url": "/api/auth/profile/avatar/image?t=1719000000" }

400 { "detail": "仅支持 JPG/PNG/WebP/GIF 格式的图片" }
400 { "detail": "图片大小不能超过 5MB" }
```

**服务端内部**：
```
1. 校验 content_type ∈ {image/jpeg, image/png, image/webp, image/gif}
2. 读取文件字节，校验 ≤ 5MB
3. 直接存入数据库（不写文件系统）：
   - avatar_data = 文件二进制
   - avatar_mime = content_type
   - avatar_updated_at = now
   - avatar_url = "/api/auth/profile/avatar/image?t={timestamp}"
```

---

#### 6.1.16 获取头像 `GET /api/auth/profile/avatar/image`

```
认证：JWT
返回：原始图片二进制（Cache-Control: no-cache）

404 { "detail": "未设置头像" }
```

---

#### 6.1.17 忘记密码 `POST /api/auth/forgot-password`

```
认证：无需

请求体：
{
    "email": "john@example.com"
}

// SMTP 已配置 → 发邮件
200 {
    "message": "重置链接已发送到您的邮箱，请在一小时内使用"
}

// SMTP 未配置 → 直接返回令牌（自托管模式）
200 {
    "message": "自托管模式：请使用以下重置令牌",
    "token":   "12345678"
}

// 邮箱未注册（防泄露，统一返回）
200 {
    "message": "如果该邮箱已注册，重置链接已发送"
}

// SMTP 发送失败
200 {
    "message": "邮件发送失败，请稍后重试"
}
```

**服务端内部**：
```
1. 查用户 → 不存在：仍返回成功（不暴露邮箱是否存在）
2. 生成 8 位数字令牌
3. 写入 password_reset_tokens（expires_at = now + 1h）
4. SMTP 已配 → 发送重置码邮件
5. SMTP 未配 → 在响应中直接返回令牌
```

---

#### 6.1.18 重置密码 `POST /api/auth/reset-password`

```
认证：无需

请求体：
{
    "token":        "12345678",       // 8 位数字
    "new_password": "newpass123"      // 8-128 字符
}

200 { "message": "密码已重置成功，请使用新密码登录" }

400 { "detail": "重置令牌无效" }
400 { "detail": "重置令牌已过期，请重新申请" }
404 { "detail": "用户不存在" }
```

**服务端内部**：
```
1. 查 password_reset_tokens → token 匹配 + used=FALSE → 不存在/过期 → 400
2. 查 user
3. password_hash = bcrypt(new_password)
4. 清除 TOTP（防止用户被锁）: totp_enabled=FALSE, totp_secret=NULL
5. 清除登录锁定: login_attempts=0, locked_until=NULL
6. 令牌标记已使用: used=TRUE
7. 审计日志: action="reset_password"
```

---

#### 6.1.19 发送删除验证码 `POST /api/auth/send-delete-code`

```
认证：JWT
查询参数：purpose（可选，如 "sync_data"）

200 { "message": "删除验证码已发送到您的邮箱" }
200 { "message": "删除操作已就绪" }          // SMTP 未配，跳过验证
429 { "detail": "请 30 秒后再试" }
200 { "message": "邮件发送失败，请稍后重试" }
```

---

#### 6.1.20 验证删除码 `POST /api/auth/verify-delete-code`

```
认证：JWT

请求体：
{
    "code": "123456"        // TOTP 6 位码 或 邮件 8 位数字码
}

200 { "message": "验证通过" }
400 { "detail": "TOTP 验证码错误" }
400 { "detail": "未请求验证码或验证码已过期" }
400 { "detail": "验证码已过期，请重新发送" }
400 { "detail": "验证码错误" }
```

**分支逻辑**：
```
if user.totp_enabled:
    → TOTP 验证（6 位码，±30s 窗口）
else:
    → 邮件验证码（8 位数字，5 分钟有效期）
```

---

#### 6.1.21 注销账号 `POST /api/auth/delete-account`

```
认证：JWT

请求体：
{
    "password":        "mypassword123",
    "export_to_email": true
}

200 {
    "message":     "账号已注销，数据已从服务器永久删除。",
    "data_backup": "备份已发送到您的邮箱"
}

401 { "detail": "密码错误" }
```

**服务端内部**：
```
1. bcrypt.verify(password, password_hash) → 失败 401
2. export_to_email=true:
   ├─ 查询所有 encrypted_data → 合并为 JSON
   └─ SMTP 已配 → send_account_backup() 发送备份邮件
3. 依次删除：
   ├─ DELETE encrypted_data WHERE user_id=X
   ├─ DELETE password_reset_tokens WHERE user_id=X
   ├─ DELETE audit_logs WHERE user_id=X
   └─ DELETE users WHERE id=X
4. 提交事务
```

---

#### 6.1.22 公开配置 `GET /api/auth/config`

```
认证：无需

200 {
    "require_invite_for_registration": true,
    "allow_user_create_invite":        false,
    "max_invites_per_user":            5
}
```

---

### 6.2 数据同步模块 `/api/sync`

> ⚠️ 所有同步接口需要 JWT 认证。数据均为客户端加密后的密文，服务端不解密。

#### 6.2.1 上传数据 `POST /api/sync/upload`

```
认证：JWT

请求体：
{
    "data_type":      "servers",
    "device_id":      "android-abc123",
    "ciphertext":     "base64encoded...",      // AES-256-GCM 密文
    "plaintext_size": 12345,
    "client_version": 2                        // 客户端的当前版本号
}

// 新建 →
200 { "version": 1, "message": "同步成功" }

// 更新 →
200 { "version": 3, "message": "同步成功" }

// 冲突（服务端版本 > 客户端版本）→
409 { "detail": "服务端版本(5)高于客户端(3)，请先下载" }
```

**分支逻辑**：
```
查找 (user_id, data_type) 的现有记录：

存在 → 更新模式：
  if server_version > client_version:
    → 409 冲突（拒绝覆盖更新版本）
  else:
    → 更新 ciphertext, device_id, plaintext_size
    → version += 1
    → 审计日志: action="sync_upload", detail="更新 {type} v{N}"

不存在 → 新建模式：
  → INSERT 新记录，version=1
  → 审计日志: action="sync_upload", detail="新建 {type} v1"
```

---

#### 6.2.2 下载数据 `GET /api/sync/download/{data_type}`

```
认证：JWT
路径参数：data_type（如 "servers"）

200 {
    "data_type":      "servers",
    "ciphertext":     "base64encoded...",
    "version":        3,
    "plaintext_size": 12345,
    "updated_at":     "2026-01-15T12:00:00"
}

404 { "detail": "未找到数据" }
```

---

#### 6.2.3 差异对比 `POST /api/sync/diff`

```
认证：JWT

请求体：
{
    "local_versions": {
        "servers":  3,
        "keys":     1,
        "settings": 0
    }
}

200 {
    "items": [
        { "data_type": "servers",  "server_version": 5, "client_version": 3, "needs_download": true  },
        { "data_type": "keys",     "server_version": 1, "client_version": 1, "needs_download": false },
        { "data_type": "settings", "server_version": 0, "client_version": 0, "needs_download": false }
    ]
}
```

**逻辑**：客户端上报本地各类型版本 → 服务端逐项对比 → 返回需要下载的类型列表。无记录的类型 version=0。

---

#### 6.2.4 同步状态 `GET /api/sync/status`

```
认证：JWT

200 {
    "devices": [
        {
            "device_id":  "android-abc123",
            "data_type":  "servers",
            "version":    3,
            "updated_at": "2026-01-15T12:00:00"
        },
        ...
    ]
}
```

---

#### 6.2.5 删除数据 `DELETE /api/sync/{data_type}`

```
认证：JWT

200 { "message": "数据已删除" }
404 { "detail": "未找到数据" }
```

---

#### 6.2.6 导出数据到邮箱 `POST /api/sync/export-to-email`

```
认证：JWT

// SMTP 已配置 →
200 { "message": "导出数据已发送到您的邮箱" }

// SMTP 未配置 → 直接在响应中返回
200 { "message": "导出数据如下", "data": "{...JSON...}" }

404 { "detail": "没有可导出的数据" }
200 { "message": "邮件发送失败，请稍后重试" }
```

---

### 6.3 邀请码模块 `/api/invite`

> 标注 🔒 的接口需要管理员权限。

#### 6.3.1 创建单个邀请码 `POST /api/invite/create` 🔒

```
请求体：
{
    "max_uses":        5,           // -1=无限，>=1=限制次数
    "expires_in_days": 30,          // null=永久
    "expires_at":      null,        // ISO 格式自定义时间（如 "2026-12-31T23:59:59"）
    "is_public":       false
}

200 {
    "id":         1,
    "code":       "a1b2c3...",      // 32 位 hex
    "max_uses":   5,
    "used_count": 0,
    "is_active":  true,
    "is_public":  false,
    "is_expired": false,
    "expires_at": "2026-02-14T00:00:00",
    "created_at": "2026-01-15T00:00:00"
}
```

---

#### 6.3.2 批量创建 `POST /api/invite/batch` 🔒

```
请求体：
{
    "count":           10,           // 1-500
    "max_uses":        1,
    "expires_in_days": 30,
    "is_public":       true
}

200 {
    "codes": [ {同上}, {同上}, ... ],
    "total": 10
}
```

---

#### 6.3.3 邀请码列表 `GET /api/invite/list` 🔒

```
查询参数：
  page=1          页码（≥1）
  page_size=50    每页条数（1-500）
  search=abc      按邀请码模糊搜索
  filter_status=active|expired|disabled|exhausted

200 {
    "codes": [ ... ],
    "total": 42
}
```

---

#### 6.3.4 锁定/解锁 `PATCH /api/invite/lock` 🔒

```
请求体：
{
    "code": "a1b2c3...",
    "lock": true                    // true=锁定, false=解锁
}

200 { "message": "邀请码 a1b2c3... 已锁定" }
200 { "message": "邀请码 a1b2c3... 已解锁" }
404 { "detail": "邀请码不存在" }
```

---

#### 6.3.5 重置使用次数 `POST /api/invite/reset` 🔒

```
请求体：
{
    "code": "a1b2c3..."
}

200 { "message": "邀请码 a1b2c3... 已复用（使用次数归零）" }
// used_count = 0, is_active = true
```

---

#### 6.3.6 删除邀请码 `DELETE /api/invite/delete` 🔒

```
请求体：
{
    "code": "a1b2c3..."
}

200 { "message": "邀请码 a1b2c3... 已删除" }
404 { "detail": "邀请码不存在" }
```

---

#### 6.3.7 导出 CSV `GET /api/invite/export` 🔒

```
返回：CSV 文件下载
内容：邀请码,最大使用次数,已使用,状态,公开,过期时间,创建时间
```

---

#### 6.3.8 用户自创邀请码 `POST /api/invite/user-create`

```
认证：JWT（普通用户）

请求体：
{
    "max_uses":        1,           // 1-100
    "expires_in_days": 30           // 1-365
}

200 { ...邀请码对象... }

403 { "detail": "当前系统未开放普通用户创建邀请码" }
400 { "detail": "已达到最大创建数量（5 个），请先删除旧的邀请码" }
```

---

#### 6.3.9 查看自己的邀请码 `GET /api/invite/user-list`

```
认证：JWT

200 { "codes": [...], "total": 3 }
```

---

#### 6.3.10 删除自己的邀请码 `DELETE /api/invite/user-delete`

```
认证：JWT
查询参数：invite_id

200 { "message": "邀请码已删除" }
404 { "detail": "邀请码不存在或不属于当前用户" }
```

---

#### 6.3.11 公开邀请码列表 `GET /api/invite/public`

```
认证：无需

200 {
    "total": 5,
    "codes": [
        {
            "id":         1,
            "code":       "abc123...",
            "max_uses":   10,
            "used_count": 3,
            "expires_at": "2026-06-01T00:00:00"
        },
        ...
    ]
}
// 只返回：is_public=true AND is_active=true AND 未过期 AND 未用完
// 最多 50 条
```

---

### 6.4 管理后台模块 `/api/admin`

> 全部需要 🔒 管理员权限。

#### 6.4.1 统计面板 `GET /api/admin/stats`

```
200 {
    "total_users":        150,
    "active_today":       23,
    "total_data_items":   320,
    "total_invite_codes": 50,
    "used_invite_codes":  35
}
```

---

#### 6.4.2 用户列表 `GET /api/admin/users`

```
查询参数：
  page=1             页码（≥1）
  page_size=20       每页条数（1-200）
  search=john        搜索（用户名/邮箱/UUID 模糊 或 ID 精确）
  sort_by=id         排序字段: id|username|email|display_order|created_at|last_login_at
  sort_order=asc     asc|desc
  filter_active=all   all|active|locked
  filter_totp=all     all|enabled|disabled
  filter_admin=all    all|admin|user

200 {
    "users": [
        {
            "id":             1,
            "uuid":           "...",
            "username":       "john_doe",
            "email":          "john@example.com",
            "nickname":       "John",
            "email_verified": true,
            "totp_enabled":   true,
            "is_active":      true,
            "is_admin":       false,
            "display_order":  1,
            "created_at":     "2026-01-01T00:00:00",
            "last_login_at":  "2026-01-15T12:00:00",
            "login_attempts": 0,
            "avatar_url":     "/api/auth/profile/avatar/image?t=..."
        },
        ...
    ],
    "total": 150
}
```

---

#### 6.4.3 锁定/解锁用户 `POST /api/admin/user/lock`

```
请求体：
{
    "user_id": 5,
    "lock":    true
}

200 { "message": "用户 john_doe 已锁定" }
200 { "message": "用户 john_doe 已解锁" }
400 { "detail": "不能锁定管理员" }
404 { "detail": "用户不存在" }
```

---

#### 6.4.4 删除用户 `POST /api/admin/user/delete`

```
请求体：
{
    "user_id":        5,
    "export_to_email": true
}

200 {
    "message":    "用户 john_doe 已永久删除",
    "email_sent": true
}

400 { "detail": "不能删除管理员" }
400 { "detail": "不能删除自己" }
404 { "detail": "用户不存在" }
```

**服务端内部**：
```
1. 校验：不能删除管理员、不能删除自己
2. export_to_email=true → 导出加密数据 JSON → 发送备份邮件
3. 删除：encrypted_data + password_reset_tokens + users（物理删除）
4. 审计日志写入 username（写死，删除后仍可追踪）
```

---

#### 6.4.5 清空用户 TOTP `POST /api/admin/user/totp-clear`

```
请求体：
{
    "user_id": 5
}

200 { "message": "用户 john_doe 的 TOTP 已清空" }
404 { "detail": "用户不存在" }
```

---

#### 6.4.6 管理员协助绑定 TOTP（两步）

**第一步** `POST /api/admin/user/totp-bind-setup`：
```
请求体：{ "user_id": 5 }
200 {
    "secret":     "JBSWY3DPEHPK3PXP",
    "qrcode_b64": "iVBORw0KGgo...",
    "message":    "请让用户扫描二维码或输入密钥，验证后确认绑定"
}
```

**第二步** `POST /api/admin/user/totp-bind-confirm`：
```
请求体：
{
    "user_id": 5,
    "code":    "123456"
}
200 { "message": "用户 john_doe 的 TOTP 已绑定成功" }
400 { "detail": "验证码错误，请重试" }
```

---

#### 6.4.7 管理员修改用户资料 `PUT /api/admin/user/profile`

```
认证：🔒

请求体：
{
    "user_id": 5,
    "email":   "newemail@example.com"
}

200 { "message": "用户资料已更新" }

400 { "detail": "用户不存在" }
400 { "detail": "邮箱已被使用" }
```

**服务端内部**：
```
1. 查用户 → 不存在 404
2. 新邮箱查重（排除当前用户）
3. 更新 email → email_verified = False（需重新验证）
4. 审计日志：action="admin_update_profile"
```

---

#### 6.4.8 用户排序 `PUT /api/admin/user/order`

```
请求体：
{
    "items": [
        { "id": 1, "display_order": 1 },
        { "id": 3, "display_order": 2 },
        { "id": 5, "display_order": 3 }
    ]
}

200 { "message": "排序已更新" }
```

---

#### 6.4.9 审计日志列表 `GET /api/admin/logs`

```
查询参数：
  page=1              页码
  page_size=50        每页条数（1-500）
  search=login        搜索（action/detail/ip/username 模糊）
  action=login        按操作类型过滤
  user_id=5           按用户过滤
  date_from=2026-01-01T00:00:00
  date_to=2026-01-31T23:59:59

200 {
    "logs": [
        {
            "id":         1001,
            "user_id":    5,
            "username":   "john_doe",
            "action":     "login",
            "detail":     "登录成功",
            "ip_address": "192.168.1.1",
            "path":       "/api/auth/login",
            "success":    true,
            "created_at": "2026-01-15T12:00:00"
        },
        ...
    ],
    "total": 5000
}
```

---

#### 6.4.10 清空审计日志 `DELETE /api/admin/logs/clear`

```
200 { "message": "已清空 5000 条审计日志" }
```

---

#### 6.4.11 邮件日志列表 `GET /api/admin/email-logs`

```
查询参数：
  page=1
  page_size=30       每页条数（1-100）
  search=john        搜索（收件人或主题）

200 {
    "logs": [
        {
            "id":            1,
            "to_email":      "john@example.com",
            "subject":       "CBox 同步服务 - 邮箱验证",
            "body":          "<html>...完整 HTML...</html>",
            "success":       true,
            "error_message": null,
            "created_at":    "2026-01-15T12:00:00"
        },
        ...
    ],
    "total": 200
}
```

---

#### 6.4.12 清空邮件日志 `DELETE /api/admin/email-logs/clear`

```
200 { "message": "已清空 200 条邮件日志" }
```

---

#### 6.4.13 获取系统配置 `GET /api/admin/config`

```
认证：🔒

200 {
    "require_invite_for_registration": true,
    "allow_user_create_invite":        false,
    "max_invites_per_user":            5
}
```

---

#### 6.4.14 更新系统配置 `PUT /api/admin/config`

```
认证：🔒

请求体（所有字段可选，只更新传入的）：
{
    "require_invite_for_registration": true,
    "allow_user_create_invite":        false,
    "max_invites_per_user":            5
}

200 { 同 GET 响应 }
```

---

#### 6.4.15 邀请码使用日志 `GET /api/admin/invite-usage-logs`

```
认证：🔒

查询参数：
  page=1
  page_size=30       每页条数
  search=abc         搜索（邀请码或使用者用户名）

200 {
    "logs": [
        {
            "id":               1,
            "invite_code":      "a1b2c3...",
            "used_by_user_id":  5,
            "used_by_username": "john_doe",
            "used_by_email":    "john@example.com",
            "created_by":       1,
            "created_at":       "2026-01-15T12:00:00"
        },
        ...
    ],
    "total": 100
}
```

---

### 6.5 公开端点

| 方法 | 路径 | 认证 | 说明 |
|------|------|:---:|------|
| GET | `/` | 无需 | 首页 (index.html) |
| GET | `/index` | 无需 | 首页 |
| GET | `/api` | 无需 | `{"status":"ok","service":"flutter-server-box-sync"}` |
| GET | `/health` | 无需 | `{"status":"healthy"}` |
| GET | `/profile` | 无需 | 资料页 (profile.html) |
| GET | `/admin` | 无需 | 管理页 (admin.html)，数据通过 JS + JWT 加载 |
| GET | `/invite` | 无需 | 邀请码页 |
| GET | `/doc` | 无需 | 文档页 |
| GET | `/public-invites` | 无需 | 公开邀请码页 |
| GET | `/my-invites` | 无需 | 我的邀请码页 |

---

## §7 业务逻辑流程详解

### 7.1 用户完整生命周期

```
                    ┌─────────────┐
                    │  访问注册页  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
              ┌─────│ 系统要求邀请码? │─────┐
              │ 是  └─────────────┘  否 │
              │                         │
     ┌────────▼────────┐                │
     │ 输入邀请码       │                │
     │ → 查 invite_codes│               │
     │ → 验证有效/未过期 │               │
     │ → 验证未用完     │               │
     └────────┬────────┘                │
              │                         │
              └─────────┬───────────────┘
                        │
               ┌────────▼────────┐
               │ 填写注册表单     │
               │ username 3-64   │
               │ email 合法       │
               │ password 8-128   │
               │ nickname 可选    │
               └────────┬────────┘
                        │
               ┌────────▼────────┐
               │ 查重             │
               │ username 重复?   │──── 400 "用户名已被占用"
               │ email 重复?      │──── 400 "邮箱已被注册"
               └────────┬────────┘
                        │ 通过
               ┌────────▼────────┐
               │ 创建 User        │
               │ uuid = UUID4     │
               │ pwd = bcrypt()   │
               │ recovery_key=hex │
               │ invite.used += 1 │
               └────────┬────────┘
                        │
                  SMTP 已配?
                 ┌──────┴──────┐
                 │ 是          │ 否
                 ▼             ▼
        ┌────────────┐  ┌──────────────┐
        │ 生成 8 位   │  │ 跳过验证      │
        │ 验证码      │  │ verified=true │
        │ 发邮件      │  └──────────────┘
        │ verified=F  │
        └─────┬──────┘
              │
     ┌────────▼────────┐
     │ 用户收验证码邮件 │
     │ → 调用 verify   │
     │ → code 匹配?     │──── 400 重试
     │ → 10 分钟有效?   │──── 400 过期，重新发送
     └────────┬────────┘
              │ 通过
     ┌────────▼────────┐
     │ verified = True  │
     │ 发送解密密钥邮件 │
     │ → UUID 发给用户  │
     └────────┬────────┘
              │
     ┌────────▼────────┐
     │   用户可登录     │
     └────────┬────────┘
              │
     ┌────────▼────────────────────┐
     │ 可选：启用 TOTP              │
     │ /totp/setup → 获取密钥+QR    │
     │ /totp/verify → 验证并启用    │
     │ → 生成新 Recovery Key        │
     └────────┬────────────────────┘
              │
     ┌────────▼────────────────────┐
     │ 正常使用：                    │
     │ - 同步数据 (/sync/*)         │
     │ - 修改资料 (/profile/*)      │
     │ - 上传头像                    │
     │ - 创建邀请码（如果开放）       │
     └────────┬────────────────────┘
              │
        ┌─────┴─────┐
        │ 忘记密码   │        │ 注销账号    │
        ▼            │        ▼            │
  ┌──────────┐       │  ┌──────────┐       │
  │/forgot   │       │  │/delete   │       │
  │ → 邮件码  │       │  │ → 验证密码│       │
  │ → /reset │       │  │ → 导出数据│       │
  │ → 新密码  │       │  │ → 发备份  │       │
  │ → 清TOTP │       │  │ → 物理删除│       │
  └──────────┘       │  └──────────┘       │
                     └─────────────────────┘
```

### 7.2 登录流程（含 TOTP 分支）

```
客户端发起 POST /api/auth/login
  │
  ├─ 查找用户（username 或 email）
  │   └─ 不存在 → 401 "用户名或密码错误"
  │
  ├─ check_login_lock()
  │   ├─ is_active=false → 403 "账户已被禁用"
  │   └─ locked_until > now → 429 "账户已被锁定 X 分钟"
  │
  ├─ bcrypt.verify(password, hash)
  │   └─ 失败 →
  │       ├─ login_attempts += 1
  │       ├─ ≥5 → locked_until = now+15min
  │       ├─ 审计日志: login_failed
  │       └─ 401 "用户名或密码错误"
  │
  ├─ 密码通过，检查 totp_enabled
  │   │
  │   ├─ false → 跳过 TOTP，进入签发 ⬇
  │   │
  │   └─ true → 检查请求中的 totp_code
  │       ├─ 未带 →
  │       │   返回 { access_token:"", totp_required:true }
  │       │   客户端弹出 TOTP 输入框
  │       │   用户输入 6 位码，再次 POST /login（带 totp_code）
  │       │
  │       └─ 带了 → pyotp.verify(totp_secret, code)
  │           ├─ 失败 → 同密码失败（计入 login_attempts）
  │           └─ 成功 → 进入签发 ⬇
  │
  └─ 签发 JWT ──────────────────────┐
      ├─ reset_login_lock()          │
      │   ├─ login_attempts = 0      │
      │   └─ locked_until = null     │
      ├─ last_login_at = now         │
      ├─ last_login_ip = client_ip   │
      ├─ JWT payload:                │
      │   { sub:user_id,             │
      │     username,                │
      │     admin:bool,              │
      │     exp:now+24h,             │
      │     iat:now }                │
      ├─ 审计日志: login             │
      └─ 返回 access_token + 用户信息 │
```

### 7.3 数据同步冲突检测

```
客户端想上传 servers 数据（client_version=2）

服务端：
  1. 查 encrypted_data WHERE user_id=X AND data_type="servers"
  
  情况 A — 无记录：
    → INSERT version=1 → 返回 {version:1}
  
  情况 B — 有记录，server_version=2：
    → server_version(2) > client_version(2)? → 否（等于）
    → 允许更新 → version=3 → 返回 {version:3}
  
  情况 C — 有记录，server_version=5：
    → server_version(5) > client_version(2)? → 是
    → 409 "服务端版本(5)高于客户端(2)，请先下载"
    → 客户端应先下载最新版，合并后重新上传

> **客户端新装优化（v1.0.1502+）**：重装/新安装客户端时，_doDbMigrate() 会写入默认配置并产生时间戳，导致空客户端的 `lastModTime` 远大于云端旧数据的时间戳，触发了情况 C → 客户端误走「上传」分支覆盖云端数据。现已修复：`SyncEngine.syncAll()` 在 diff 之前先检测本地 Server/Key/Snippet 是否全为空，全空则跳过 diff 直接 download，从根本上避免了此问题。详见客户端规格书 §6.5。
```

### 7.4 邀请码状态机

```
邀请码生命周期：

  创建 ──→ [有效] ──┬── 过期（expires_at < now）──→ [过期]
                     │
                     ├── 用完（used_count >= max_uses）──→ [用完]
                     │
                     ├── 锁定（is_active=false）──→ [禁用]
                     │                                │
                     │                         解锁 ←──┘
                     │
                     ├── 重置（used_count=0）──→ [有效]（复用）
                     │
                     └── 删除 ──→ [不存在]

注册时校验顺序：
  1. 邀请码存在？ → 否 → 400 "邀请码无效"
  2. is_active=true？ → 否 → 同上
  3. is_expired? → 是 → 400 "邀请码已过期"
  4. is_exhausted? → 是 → 400 "邀请码已被用完"
  5. 全部通过 → 允许注册 → used_count += 1
```

### 7.5 密码重置流程

```
用户忘记密码：

  POST /api/auth/forgot-password { email }
    │
    ├─ 查用户 → 不存在：仍返回 200 "如果该邮箱已注册，链接已发送"
    │           （防用户枚举攻击）
    │
    ├─ 用户存在：
    │   ├─ 生成 8 位数字 token
    │   ├─ INSERT password_reset_tokens（expires_at=now+1h）
    │   └─ SMTP 已配 → 发邮件（含 token）
    │       SMTP 未配 → 直接在响应中返回 token
    │
    └─ 用户收到 token：

  POST /api/auth/reset-password { token, new_password }
    │
    ├─ 查 password_reset_tokens
    │   ├─ token 不存在或 used=true → 400 "重置令牌无效"
    │   └─ expires_at < now → 400 "重置令牌已过期"
    │
    ├─ 更新密码：password_hash = bcrypt(new_password)
    ├─ 清除 TOTP：totp_enabled=false, totp_secret=null
    ├─ 清除锁定：login_attempts=0, locked_until=null
    ├─ 令牌标记已使用：used=true
    └─ 审计日志：reset_password
```

---

## §8 安全架构

### 8.1 认证层次

```
Layer 1 — 无认证：
  - 注册、登录、忘记密码、公开配置
  - 静态页面、健康检查

Layer 2 — JWT 认证（get_current_user）：
  - 个人资料、头像、TOTP 设置
  - 数据同步所有接口
  - 用户自创邀请码

Layer 3 — 管理员认证（require_admin）：
  - 管理后台所有接口
  - 邀请码管理（CRUD）
```

### 8.2 JWT Token 流程

```
签发：
  Header: { "alg": "HS256", "typ": "JWT" }
  Payload: {
    "sub":      1,                    // user_id
    "username": "john_doe",
    "admin":    false,
    "exp":      1719100800,          // 24 小时后
    "iat":      1719014400           // 签发时间
  }
  签名：HMAC-SHA256(payload, JWT_SECRET_KEY)

验证（每次请求）：
  1. 提取 Authorization: Bearer <token>
  2. jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
  3. 自动验证签名和过期时间
  4. 提取 user_id → 查数据库验证用户存在且 is_active=true
```

### 8.3 密码安全

```
存储：bcrypt(plaintext) → $2b$12$...（60 字符）
      盐值内置在哈希中，相同密码不同用户哈希不同
验证：bcrypt.verify(plaintext, stored_hash)

Recovery Key：同样使用 bcrypt 哈希存储
```

### 8.4 端到端加密（零信任模型）

```
┌─ 客户端 ─────────────────────┐
│ 用户密码                      │
│   → Argon2id 派生 32 字节密钥 │
│   → AES-256-GCM 加密数据      │
│   → 密文上传到服务端          │
└──────────┬───────────────────┘
           │ 密文（base64）
           ▼
┌─ 服务端 ─────────────────────┐
│ 存储密文到 encrypted_data     │
│ ❌ 不知道密码                  │
│ ❌ 无法解密                    │
│ ❌ 管理员也看不到明文           │
└──────────────────────────────┘

密钥派生参数：
  Argon2id(
    salt=16 字节随机,
    length=32 字节,
    iterations=4,
    lanes=4,
    memory_cost=64 MB
  )

加密格式：base64( salt(16) + nonce(12) + ciphertext )
```

### 8.5 速率限制

```
全局：100 请求/分钟（所有端点）
登录：5 次/分钟（仅 /api/auth/login）
同步：30 次/分钟（仅 /api/sync/*）

实现：slowapi（内存计数器，基于客户端 IP）

验证码防刷（应用层）：
  验证码重发：60 秒冷却
  删除验证码：30 秒冷却
```

### 8.6 审计追踪

```
每条审计日志记录：
  - username（写死到日志表中，用户删除后仍可追踪）
  - action（register, login, sync_upload, admin_user_delete...）
  - detail（"更新 servers v3"）
  - ip_address
  - path（/api/auth/login）
  - success（true/false）
  - created_at

操作类型汇总：
  认证：register, verify_email, login, login_failed, recovery_login, recovery_login_failed
  TOTP：totp_setup, totp_disable
  密码：forgot_password, reset_password
  同步：sync_upload, sync_download, sync_delete
  管理：admin_user_lock, admin_user_unlock, admin_user_delete,
        admin_totp_clear, admin_totp_bind, admin_user_reorder,
        admin_logs_clear, admin_email_logs_clear
```

---

## §9 邮件服务

### 9.1 发送模式

```
EmailService.__init__():
  enabled = all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM])

模式 1 — SMTP 全部配置：
  → enabled=True → 所有邮件正常发送

模式 2 — 任一 SMTP 配置缺失：
  → enabled=False → 静默降级
  → 注册自动跳过验证（email_verified=True）
  → 密码重置令牌在 API 响应中直接返回
  → 删除操作跳过验证码
```

### 9.2 邮件类型

| 方法 | 触发时机 | 主题 | 内容 |
|------|----------|------|------|
| `send_verification_code` | 注册、重发验证码 | "邮箱验证" | 8 位数字验证码 |
| `send_recovery_key` | 注册、TOTP 启用 | "恢复密钥" | 32 位 hex Recovery Key |
| `send_reset_code` | 忘记密码 | "密码重置" | 8 位数字验证码 |
| `send_decryption_key` | 邮箱验证成功 | "数据解密密钥" | UUID + 解密教程 |
| `send_account_backup` | 注销、管理员删除 | "账号注销与数据备份" | UUID + 加密数据 JSON + 解密步骤 |

### 9.3 连接方式

```
SMTP_USE_TLS=true:
  → 隐式 SSL（端口 465）
  → aiosmtplib.send(use_tls=True)

SMTP_USE_TLS=false:
  → STARTTLS（端口 587）
  → aiosmtplib.send(start_tls=SMTP_STARTTLS)
```

### 9.4 邮件日志

每条发送（成功或失败）写入 `email_logs` 表，包括完整 HTML 正文。管理员可在后台「邮件日志」标签页查看和清空。

---

## §10 前端静态页面

### 10.1 页面列表

| 路由 | HTML 文件 | 功能 | 需认证 |
|------|-----------|------|:---:|
| `/` `/index` | index.html | 首页 | ❌ |
| `/profile` | profile.html | 个人资料（修改密码、上传头像裁剪） | JWT |
| `/admin` | admin.html | 管理后台 SPA（6 标签页） | JWT+Admin |
| `/invite` | invite.html | 邀请码管理 | JWT+Admin |
| `/public-invites` | public-invites.html | 公开邀请码浏览 | ❌ |
| `/my-invites` | user-invites.html | 我的邀请码 | JWT |
| `/doc` | doc.html | 文档 | ❌ |

### 10.2 管理后台 7 标签页

| 标签 | 调用的 API | 功能 |
|------|-----------|------|
| 用户列表 | `GET /api/admin/users` | 搜索/排序/过滤/锁定/删除/TOTP 协助/修改邮箱 |
| 管理员邀请码 | `GET /api/invite/list` | 创建/批量/锁定/删除/重置/公开开关 |
| 普通用户邀请码 | `GET /api/invite/list` + filter | 同上（按角色筛选） |
| 系统设置 | `GET/PUT /api/admin/config` | 注册邀请码开关、用户自创邀请码、最大数量 |
| 审计日志 | `GET /api/admin/logs` | 搜索/过滤/清空 |
| 邮件日志 | `GET /api/admin/email-logs` | 查看/清空 |
| 邀请码日志 | `GET /api/admin/invite-usage-logs` | 查看邀请码使用记录，按邀请码或用户名搜索 |

### 10.3 管理员个人资料入口

admin.html 顶栏右侧有管理员头像下拉菜单，包含 4 个小弹窗：

| 弹窗 | 调用的 API | 功能 |
|------|-----------|------|
| 修改头像 | `POST /api/auth/profile/avatar` | 选择图片文件上传（≤5MB） |
| 修改昵称 | `PUT /api/auth/profile/nickname` | 直接修改管理员自己的昵称 |
| 修改邮箱 | `PUT /api/auth/profile?new_email=...` | 密码确认后修改邮箱 |
| 修改密码 | `PUT /api/auth/profile/password` | 三字段（旧密码/新密码/确认） |

### 10.4 皮肤切换（主题选择器）

所有页面（admin.html、profile.html、index.html 等）共享 `theme.css` 样式表，使用 Glassmorphism 设计语言。

**三模式主题**：
| 模式 | 说明 |
|------|------|
| ☀ 白天 | 浅色 Glassmorphism，半透明白底 |
| 🌙 黑夜 | 深色模式，深蓝黑底配毛玻璃效果 |
| 🖥 自动 | 跟随操作系统设置自动切换 |

**设计特征**：
- 毛玻璃卡片（`backdrop-filter: blur(20px)`）
- 顶部渐变边框装饰（`card::before` 渐变条）
- 按钮渐变背景（primary 按钮有蓝紫渐变）
- 拨动开关（toggle switch）用于二态设置
- 响应式设计，手机端适配（表格横向滚动、隐藏次要列、紧凑按钮）

主题选择使用 `<select>` 下拉框，切换通过 `setAttribute('data-theme', mode)` 改变 CSS 变量，偏好存储在 `localStorage`。

### 10.5 静态文件部署

```
Docker volume 挂载（只读）：
  /opt/docker-data/flutter-sync-server/static:/app/static:ro

修改页面后：
  cp new-admin.html /opt/docker-data/flutter-sync-server/static/admin.html
  → 立即生效，无需重启容器（volume 实时同步）

FastAPI 挂载：
  app.mount("/static", StaticFiles(directory="./static"), name="static")
  CSS/JS 等静态资源通过 /static/xxx 访问

HTML 页面：通过 FileResponse 直接返回
  @app.get("/admin") → FileResponse("./static/admin.html")
```

---

## §11 部署指南

### 11.1 前置条件

```
- Linux 服务器（Ubuntu 22.04+ / Debian 12+）
- Docker 24+ & Docker Compose v2
- MySQL 8.0+ 已安装并运行（或使用 docker compose profile=with-db 自动部署）
- 域名 DNS 已解析到服务器 IP
```

### 11.2 部署步骤

```bash
# 1. 创建数据目录
mkdir -p /opt/docker-data/flutter-sync-server/static

# 2. 克隆仓库
git clone https://github.com/onepve/flutter-sync-server.git /opt/flutter-sync-server
cd /opt/flutter-sync-server

# 3. 创建 .env 文件
cp .env.example .env
vim .env   # 填写数据库密码、JWT 密钥、管理员密码、SMTP 配置

# 4. 创建数据库
mysql -u root -p
CREATE DATABASE sync_server CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sync_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON sync_server.* TO 'sync_user'@'%';
FLUSH PRIVILEGES;

# 5. 复制静态文件到挂载目录
cp -r static/* /opt/docker-data/flutter-sync-server/static/

# 6. 构建并启动
docker compose build app
docker compose up -d

# 7. 验证
curl http://localhost:8765/api
# → {"status":"ok","service":"flutter-server-box-sync"}

curl http://localhost:8765/health
# → {"status":"healthy"}
```

### 11.3 使用宿主机 MySQL（推荐）

```bash
# docker-compose.yml 默认 network_mode: host
# app 容器直接连接到宿主机的 127.0.0.1:3306
# .env 中设置：
DB_HOST=127.0.0.1
DB_PORT=3306

docker compose up -d app    # 不启动 mysql profile
```

### 11.4 使用 Docker 内置 MySQL

```bash
# 自动启动 MySQL 容器
docker compose --profile with-db up -d

# MySQL 数据持久化在 Docker volume mysql_data
```

### 11.5 Nginx 反向代理

```nginx
server {
    listen 443 ssl http2;
    server_name sync.onepve.com;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10m;    # 允许头像上传 ≤5MB
    }
}
```

### 11.6 首次启动后的管理员创建

`.env` 中的 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 目前仅在配置中定义。首次管理员需要通过以下方式创建：

```bash
# 方式 1：直接修改数据库
docker exec -it sync-mysql mysql -u sync_user -p sync_server
INSERT INTO users (uuid, username, email, password_hash, email_verified, is_admin, is_active)
VALUES (UUID(), 'admin', 'admin@onepve.com',
        '$2b$12$...预生成的 bcrypt 哈希...',
        TRUE, TRUE, TRUE);

# 方式 2：通过 API 注册后手动提升
# 先正常注册一个用户 → 然后手动设 is_admin=1
UPDATE users SET is_admin=1 WHERE username='admin';
```

---

## §12 运维操作

### 12.1 常用命令

```bash
# 查看日志
docker logs -f sync-app

# 重启服务
docker compose restart app

# 重建镜像（源码修改后）
docker compose build app && docker compose up -d

# 更新静态页面（无需重建）
cp new-admin.html /opt/docker-data/flutter-sync-server/static/admin.html
# → 立即生效

# 进入容器
docker exec -it sync-app bash

# 备份数据库
mysqldump -u sync_user -p sync_server > backup_$(date +%Y%m%d).sql

# 恢复数据库
mysql -u sync_user -p sync_server < backup_20260702.sql
```

### 12.2 数据库迁移

服务端启动时自动执行列级迁移（`lifespan` 函数），无需手动运行：

```
检测 users 表缺少的列：
  avatar_data, avatar_mime, avatar_updated_at, nickname, display_order

检测 invite_codes 表缺少的列：
  is_public

检测逻辑：
  SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
  → 与预期对比 → 缺则 ALTER TABLE ADD COLUMN

清理：
  旧版文件系统头像引用自动清空（avatar_url LIKE '/static/avatars/%' 且 avatar_data 为空）
```

### 12.3 监控要点

```
1. 磁盘空间：MySQL 数据和 Docker 卷
2. email_logs 表增长（可定期清空）
3. audit_logs 表增长（可定期清空）
4. Docker 容器内存使用
5. /health 端点可达性
```

---

## §13 附录：关键代码片段

### 13.1 用户认证依赖链

```python
# services/security.py

# 第一层：JWT 解码 → 查用户
async def get_current_user(credentials, db):
    if credentials is None:
        raise 401 "未提供认证 Token"
    payload = jwt.decode(token, JWT_SECRET_KEY)
    user_id = payload["sub"]
    user = db.query(User).get(user_id)
    if user is None or not user.is_active:
        raise 401
    return user

# 第二层：检查管理员
async def require_admin(current_user = Depends(get_current_user)):
    if not current_user.is_admin:
        raise 403 "需要管理员权限"
    return current_user

# 使用方式：
# 普通认证 → Depends(get_current_user)
# 管理员认证 → Depends(require_admin)
# 路由级 → dependencies=[Depends(require_admin)]
```

### 13.2 数据库会话管理

```python
# database.py

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=10,        # 连接池大小
    max_overflow=20,     # 额外溢出连接
    pool_pre_ping=True,  # 使用前 ping 检测连接有效性
)

def get_db():
    """FastAPI 依赖 — 每个请求独立会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()        # 请求结束后自动归还连接
```

### 13.3 启动自动迁移

```python
# main.py lifespan 函数（每次启动执行）

1. init_db() → Base.metadata.create_all() — 建表
2. 检查 INFORMATION_SCHEMA.COLUMNS — 检测缺失列
3. 按需 ALTER TABLE ADD COLUMN — 无痛升级
4. 清理旧文件头像引用 — 数据完整性维护
```

### 13.4 验证码内存存储

```python
# 当前使用内存字典，生产环境建议迁移到 Redis

_verification_codes: dict = {}      # email → {code, expires_at, user_id}
_delete_verification_codes: dict = {}
_verification_code_cooldowns: dict = {}  # email → last_sent_timestamp
_delete_code_cooldowns: dict = {}
```

---

> **文档结束** — 此规格书覆盖了 CBox 同步服务端的全部设计、实现细节和运维操作。  
> 如有功能新增或修改，请同步更新本文档。
