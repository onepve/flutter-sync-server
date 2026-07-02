"""
TOTP 双因素认证服务

基于 pyotp，兼容 Google Authenticator / Authy 等标准 TOTP App。
"""

import pyotp
import qrcode
import io
import base64


class TOTPService:
    """TOTP 管理"""

    @staticmethod
    def generate_secret() -> str:
        """生成 TOTP 密钥"""
        return pyotp.random_base32()

    @staticmethod
    def get_provisioning_uri(secret: str, username: str, issuer: str) -> str:
        """生成标准的 otpauth:// URI"""
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=username, issuer_name=issuer)

    @staticmethod
    def generate_qrcode_base64(uri: str) -> str:
        """生成 TOTP 二维码（base64 编码的 PNG）"""
        qr = qrcode.make(uri)
        buf = io.BytesIO()
        qr.save(buf)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def verify_code(secret: str, code: str) -> bool:
        """验证 TOTP 6 位码（默认允许 1 步时间偏差）"""
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)
