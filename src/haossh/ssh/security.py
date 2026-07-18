"""AES-256-GCM 密码加解密。

加密流程：明文 → AES-256-GCM → nonce + 密文 → base64 → 存数据库
解密流程：数据库 → base64 解码 → 拆分 nonce + 密文 → AES-256-GCM → 明文
"""

import base64
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# 32 字节密钥。生产环境必须通过环境变量注入，开发环境用默认值。
# HAOSSH_SECRET_KEY 应该是 base64 编码的 32 字节随机数据
_DEFAULT_KEY_BYTES = b"haossh-dev-key" + b"-" * 18  # 恰好 32 字节

def _get_key() -> bytes:
    """从环境变量读取密钥，未设置则返回默认值并警告。"""
    key_str = os.getenv("HAOSSH_SECRET_KEY")
    if key_str is None:
        logger.warning(
            "HAOSSH_SECRET_KEY 未设置，使用默认密钥。"
            "生产环境请务必设置此环境变量！"
        )
        return _DEFAULT_KEY_BYTES
    return base64.b64decode(key_str)


def encrypt_password(password: str) -> str:
    """加密明文密码，返回 base64 编码的密文（可直接存数据库）。

    Args:
        password: 明文密码

    Returns:
        base64 编码字符串，包含 nonce + 密文
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 12 字节随机数，每次加密都不同
    ciphertext = aesgcm.encrypt(nonce, password.encode("utf-8"), None)

    # 存储格式：nonce(12字节) + 密文(变长)，再 base64
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("utf-8")


def decrypt_password(encrypted: str) -> str:
    """解密密文，返回明文密码。

    Args:
        encrypted: encrypt_password 产出的 base64 字符串

    Returns:
        明文密码

    Raises:
        Exception: 密钥不匹配或数据损坏时解密失败
    """
    key = _get_key()
    aesgcm = AESGCM(key)

    combined = base64.b64decode(encrypted)
    nonce = combined[:12]       # 前 12 字节是 nonce
    ciphertext = combined[12:]  # 后面是密文

    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
