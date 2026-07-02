"""
邮件发送服务

支持 SMTP with STARTTLS，用于发送邮箱验证码和 Recovery Key。
如果未配置 SMTP，注册时自动跳过邮箱验证（静默模式）。
"""

import secrets
import logging
from typing import Optional

from config import settings
from database import SessionLocal
from models.email_log import EmailLog

logger = logging.getLogger(__name__)


def _log_email(to_email: str, subject: str, body: str, success: bool, error_message: str | None = None):
    """记录邮件发送日志到数据库"""
    try:
        db = SessionLocal()
        log = EmailLog(
            to_email=to_email,
            subject=subject,
            body=body,
            success=success,
            error_message=error_message,
        )
        db.add(log)
        db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"邮件日志写入失败: {e}")


class EmailService:
    """发送邮件"""

    def __init__(self):
        self._enabled = all([
            settings.SMTP_HOST,
            settings.SMTP_USER,
            settings.SMTP_PASSWORD,
            settings.SMTP_FROM,
        ])

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def generate_verification_code(length: int = 8) -> str:
        """生成纯数字验证码"""
        return "".join(secrets.choice("0123456789") for _ in range(length))

    async def send_verification_code(self, to_email: str, code: str) -> bool:
        """发送邮箱验证码"""
        if not self._enabled:
            logger.warning(f"SMTP 未配置，跳过发送验证码到 {to_email}")
            return False

        subject = "CBox 同步服务 - 邮箱验证"
        body = f"""
        <html>
        <body>
        <h3>CBox 同步服务</h3>
        <p>您的邮箱验证码为：</p>
        <h2 style="color: #4CAF50;">{code}</h2>
        <p>此验证码 10 分钟内有效。</p>
        <p>如非本人操作，请忽略此邮件。</p>
        </body>
        </html>
        """
        return await self._send(to_email, subject, body)

    async def send_recovery_key(self, to_email: str, recovery_key: str) -> bool:
        """发送 Recovery Key"""
        if not self._enabled:
            logger.warning(f"SMTP 未配置，跳过发送 Recovery Key 到 {to_email}")
            return False

        subject = "CBox 同步服务 - 恢复密钥"
        body = f"""
        <html>
        <body>
        <h3>CBox 同步服务</h3>
        <p>您的账户恢复密钥为：</p>
        <h2 style="color: #f44336; letter-spacing: 4px;">{recovery_key}</h2>
        <p><strong>请妥善保管此密钥！</strong></p>
        <p>如果您丢失了 TOTP 设备，可以使用此密钥恢复账户访问。</p>
        <p>如非本人操作，请立即修改密码。</p>
        </body>
        </html>
        """
        return await self._send(to_email, subject, body)

    async def send_reset_code(self, to_email: str, token: str) -> bool:
        """发送密码重置令牌"""
        if not self._enabled:
            logger.warning(f"SMTP 未配置，跳过发送重置码到 {to_email}")
            return False

        subject = "CBox 同步服务 - 密码重置"
        body = f"""
        <html>
        <body>
        <h3>CBox 同步服务</h3>
        <p>您请求了密码重置，验证码为：</p>
        <h2 style="color: #2196F3; letter-spacing: 4px; font-family: monospace;">{token}</h2>
        <p>此验证码一小时内有效。</p>
        <p>如非本人操作，请忽略此邮件，并立即登录修改密码。</p>
        </body>
        </html>
        """
        return await self._send(to_email, subject, body)

    async def send_decryption_key(self, to_email: str, uuid_key: str) -> bool:
        """邮箱验证成功后发送解密密钥（UUID）到邮箱"""
        if not self._enabled:
            logger.warning(f"SMTP 未配置，跳过发送解密密钥到 {to_email}")
            return False

        subject = "CBox 同步服务 - 数据解密密钥"
        body = f"""
        <html>
        <body>
        <h3>CBox 同步服务</h3>
        <p>您的邮箱已验证成功！</p>
        <p>以下是您的数据解密密钥，请务必妥善保存：</p>
        <h2 style="color: #f44336; letter-spacing: 4px; font-family: monospace; background: #fff3f3; padding: 12px; border-radius: 8px;">{uuid_key}</h2>
        <div style="background: #fffde7; border: 1px solid #ffe082; border-radius: 8px; padding: 12px; margin-top: 16px;">
            <p style="margin: 0;"><strong>⚠️ 重要提示：</strong></p>
            <ul>
                <li><strong>此密钥只在此邮件中显示，请立即保存到安全位置。</strong></li>
                <li>此密钥用于加密/解密您的云端同步数据，<strong>服务端无法解密您的数据</strong>。</li>
                <li>如果您更换设备，需要使用此密钥恢复同步数据。</li>
                <li>如丢失此密钥，即使您还保留密码，云端数据也将无法解密。</li>
                <li>注销账号时，此密钥会与您的导出数据一起再次发送到邮箱。</li>
            </ul>
        </div>
        <p style="margin-top: 16px;">如果您在客户端中开启同步，系统会自动使用此密钥进行端到端加密，您无需手动输入。</p>
        <p>如非本人操作，请忽略此邮件。</p>
        </body>
        </html>
        """
        return await self._send(to_email, subject, body)

    async def send_account_backup(self, to_email: str, uuid_key: str, export_data: str) -> bool:
        """注销账号时发送备份：包含解密密钥和导出的加密数据"""
        if not self._enabled:
            logger.warning(f"SMTP 未配置，跳过发送备份到 {to_email}")
            return False

        subject = "CBox 同步服务 - 账号注销与数据备份"
        body = f"""
        <html>
        <body>
        <h3>CBox 同步服务 - 账号已注销</h3>
        <p>您的账号已成功注销。以下是您的数据备份，请妥善保存。</p>

        <h4>📋 解密密钥（UUID）</h4>
        <div style="background: #fff3f3; border: 1px solid #f44336; border-radius: 8px; padding: 12px; font-family: monospace; font-size: 18px; letter-spacing: 4px; text-align: center;">
            {uuid_key}
        </div>

        <h4>🔐 加密的同步数据</h4>
        <div style="background: #f5f5f5; border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px; word-break: break-all; max-height: 300px; overflow-y: auto;">
            {export_data}
        </div>

        <h4>📖 如何解密数据</h4>
        <div style="background: #e3f2fd; border: 1px solid #90caf9; border-radius: 8px; padding: 12px;">
            <p><strong>手动解密步骤（使用 CBox 客户端）：</strong></p>
            <ol>
                <li>重新安装 CBox 客户端</li>
                <li>在「云同步」页面中点击「从备份恢复」</li>
                <li>将上方加密数据粘贴到输入框</li>
                <li>输入您的解密密钥（UUID）：<code>{uuid_key}</code></li>
                <li>系统会自动解密并恢复您的服务器配置到本地</li>
            </ol>
            <p><strong>程序化解密（高级用户）：</strong></p>
            <p>加密方式为 <strong>AES-256-GCM</strong>，密钥派生方式为 <strong>PBKDF2-HMAC-SHA256</strong>（10000 次迭代）。</p>
            <p>您可以使用标准密码学库（如 OpenSSL、Python Cryptography）自行解密：</p>
            <pre style="background: #333; color: #fff; padding: 8px; border-radius: 4px; overflow-x: auto;">
echo "{export_data}" | openssl enc -d -aes-256-gcm -pbkdf2 -iter 10000 -k "{uuid_key}" -base64 -A</pre>
        </div>

        <p style="margin-top: 16px;"><strong>注意：</strong>账号注销后，所有云端数据已从服务器永久删除。请确保您已妥善保存以上备份。</p>
        </body>
        </html>
        """
        return await self._send(to_email, subject, body)

    async def _send(self, to_email: str, subject: str, html_body: str) -> bool:
        """底层 SMTP 发送"""
        import aiosmtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            # 构建 SMTP 参数
            smtp_kwargs = {
                "hostname": settings.SMTP_HOST,
                "port": settings.SMTP_PORT,
                "username": settings.SMTP_USER,
                "password": settings.SMTP_PASSWORD,
                "timeout": 15,
            }
            if settings.SMTP_USE_TLS:
                # 隐式 SSL/TLS（端口 465）
                smtp_kwargs["use_tls"] = True
            else:
                # STARTTLS（端口 587）
                smtp_kwargs["start_tls"] = settings.SMTP_STARTTLS
            await aiosmtplib.send(msg, **smtp_kwargs)
            _log_email(to_email, subject, html_body, success=True)
            return True
        except Exception as e:
            logger.error(f"SMTP 发送失败: {e}")
            _log_email(to_email, subject, html_body, success=False, error_message=str(e))
            return False
