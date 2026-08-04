"""AES-256-GCM 加解密（密码/私钥等敏感信息）。

加密流程：明文 → AES-256-GCM → nonce + 密文 → base64 → 存数据库
解密流程：数据库 → base64 解码 → 拆分 nonce + 密文 → AES-256-GCM → 明文
"""

import base64
import logging
import os
import re

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


def encrypt(plaintext: str) -> str:
    """加密明文（密码/私钥），返回 base64 编码的密文（可直接存数据库）。

    Args:
        plaintext: 明文内容

    Returns:
        base64 编码字符串，包含 nonce + 密文
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 12 字节随机数，每次加密都不同
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    # 存储格式：nonce(12字节) + 密文(变长)，再 base64
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("utf-8")


def decrypt(encrypted: str) -> str:
    """解密密文，返回明文。

    Args:
        encrypted: encrypt 产出的 base64 字符串

    Returns:
        明文内容

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


# ── 危险命令拦截 ───────────────────────────────────────────────
# 毁灭性命令：直接拒绝执行（AI 工具层和用户 HTTP 路由共用同一套规则）

_FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+-rf?\s+/(?:\s|$|\*)"),                    # rm -rf /  /  /*
    re.compile(r"rm\s+-rf?\s+/\*"),                              # rm -rf /*
    re.compile(r"dd\s+if=/dev/(?:zero|random|urandom)"),         # dd 覆写磁盘
    re.compile(r"mkfs\.[a-z0-9]+"),                              # mkfs 格式化
    re.compile(r":\(\)\s*\{\s*:\|:\&\s*\}\s*;:\s*\}"),          # fork bomb :(){ :|:& };:
    re.compile(r">\s*/dev/sd[a-z]"),                             # 直写块设备
    re.compile(r"chmod\s+-R\s+777\s+/\s*$"),                     # 全盘 777
]


def check_forbidden(command: str) -> str | None:
    """命中毁灭性命令返回拒绝原因，否则返回 None。

    被 AI 工具层（execute_command/run_background）和用户 HTTP 路由（/ssh/terminal/exec）
    共用，确保两条路径的拦截规则一致。
    """
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(command):
            return f"已拦截毁灭性命令（匹配规则: {pattern.pattern}）。请换用更安全的操作。"
    return None
