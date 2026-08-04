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


# ── 命令风险分级 ───────────────────────────────────────────────
# dangerous（rm -rf / 等）已被 check_forbidden 拦截，不会走到分级这里。
# 这里只区分 read（只读，AI 自主执行）和 mutate（变更，需用户确认）。

_MUTATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(systemctl|service)\s+\S+\s+(restart|stop|start|reload)"),   # 服务管理
    re.compile(r"\b(kill|killall|pkill)\b"),                                     # 杀进程
    re.compile(r"\b(chmod|chown)\b"),                                            # 权限修改
    re.compile(r"\brm\b"),                                                       # 删除（根目录已被 check_forbidden 拦）
    re.compile(r"\b(apt|apt-get|yum|dnf|pip|pip3|npm|yarn)\s+(install|remove|uninstall|purge)"),  # 包管理
    re.compile(r">\s*\S"),                                                       # 重定向写入（> >> 2> 都匹配）
    re.compile(r"\bcrontab\b"),                                                  # 定时任务
    re.compile(r"\biptables\b"),                                                 # 防火墙
    re.compile(r"\buser(add|del|mod)\b"),                                        # 用户管理
    re.compile(r"\b(ifconfig|ip\s+route)\b"),                                    # 网络配置
]


def classify_command_risk(command: str) -> str:
    """判断命令风险级别，返回 'read' 或 'mutate'。

    - read: 只读查询类（ls/cat/grep/ps/df 等），AI 可自主执行
    - mutate: 变更类（restart/rm/chmod/包管理/重定向写入等），需用户确认
    - dangerous（rm -rf / 等）已被 check_forbidden 拦截，不会走到这里

    误匹配代价低（多确认一次），漏匹配代价高（数据被改），宁可多拦。
    """
    for pattern in _MUTATE_PATTERNS:
        if pattern.search(command):
            return "mutate"
    return "read"


# ── Prompt 注入检测 ───────────────────────────────────────────
# 检测用户消息中的注入攻击：指令覆盖、角色伪装、越狱、指令提取

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # 直接指令覆盖（英文）
    re.compile(r"(?i)ignore\s+(previous|above|all|prior)\s+(instructions?|prompts?|rules?)"),
    re.compile(r"(?i)disregard\s+(previous|above|all|prior)\s+(instructions?|prompts?)"),
    re.compile(r"(?i)forget\s+(previous|above|all|prior)\s+(instructions?|prompts?)"),
    # 直接指令覆盖（中文）
    re.compile(r"忽略?(以上|前面|之前|上述)的?(指令|提示|规则|限制|约束)"),
    re.compile(r"无视?(以上|前面|之前|上述)的?(指令|提示|规则|限制|约束)"),
    re.compile(r"不要遵守?(你的|任何)?(指令|提示|规则|限制|约束)"),
    # 角色伪装
    re.compile(r"(?i)\b(system|assistant)\s*:"),                    # 伪装角色标记
    re.compile(r"(?i)you\s+are\s+now\s+(a|an)\s"),                  # 角色重定义
    re.compile(r"(?i)pretend\s+(you\s+are|to\s+be)\s"),
    re.compile(r"(?i)act\s+as\s+(if|a|an)\s"),
    # 越狱关键词
    re.compile(r"(?i)\bjailbreak\b"),
    re.compile(r"(?i)\bDAN\s+mode\b"),
    re.compile(r"(?i)developer\s+mode"),
    re.compile(r"(?i)unrestricted\s+mode"),
    # 指令提取/泄露
    re.compile(r"(?i)(show|reveal|print|output)\s+(me\s+)?(your|the)\s+(system|initial)\s+(prompt|instructions?)"),
    re.compile(r"(?i)what\s+(are|is)\s+your\s+(system|initial)\s+(prompt|instructions?)"),
]


def check_prompt_injection(message: str) -> str | None:
    """检测 prompt 注入。命中返回警告，否则 None。

    检测四类注入：
    - 直接指令覆盖："忽略以上指令"、"ignore previous instructions"
    - 角色伪装："system:"、"you are now a"、"act as"
    - 越狱关键词：jailbreak、DAN mode、developer mode
    - 指令提取：要求泄露 system prompt
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(message):
            return f"检测到疑似 prompt 注入（匹配规则: {pattern.pattern}）。请直接描述你的运维需求，不要尝试操纵 AI 的行为。"
    return None
