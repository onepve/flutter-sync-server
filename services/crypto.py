"""
AES-256-GCM 加密服务

密钥从用户密码通过 Argon2id 派生，服务端零信任：
- 服务端只存密文
- 只有知道密码的用户本人能解密
- 管理员也无法查看用户数据
"""

import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


class CryptoService:
    """AES-256-GCM 加密/解密"""

    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        """使用 Argon2id 从密码派生 32 字节 AES 密钥"""
        kdf = Argon2id(
            salt=salt,
            length=32,
            iterations=4,          # 迭代次数
            lanes=4,               # 并行度
            memory_cost=64 * 1024, # 64 MB
        )
        return kdf.derive(password.encode("utf-8"))

    @staticmethod
    def encrypt(plaintext: str, password: str) -> str:
        """
        加密明文 → base64 密文

        输出格式: base64(salt + nonce + ciphertext)
        """
        salt = os.urandom(16)
        nonce = os.urandom(12)  # 96-bit nonce
        key = CryptoService._derive_key(password, salt)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        # 打包: salt(16) + nonce(12) + ciphertext
        payload = salt + nonce + ciphertext
        return base64.b64encode(payload).decode("ascii")

    @staticmethod
    def decrypt(encrypted_b64: str, password: str) -> str:
        """
        解密 base64 密文 → 明文

        输入格式: base64(salt + nonce + ciphertext)
        """
        payload = base64.b64decode(encrypted_b64)
        salt = payload[:16]
        nonce = payload[16:28]
        ciphertext = payload[28:]

        key = CryptoService._derive_key(password, salt)
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
