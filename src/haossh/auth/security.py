"""密码哈希与 JWT 签发/校验。

密码哈希（不可逆）：
- 用 argon2 对密码做单向哈希，数据库里永远不存明文，也无法从哈希反推明文
- 登录校验时不是"解密比较"，而是把用户重新输入的密码再哈希一次，比较两次结果是否一致

JWT（无状态认证）：
- 签发时把身份信息（user_id/tenant_id/role）编码进 token 并用密钥签名
- 校验时只验证签名和过期时间，不查数据库——这是"无状态"的核心，
  代价是账号被禁用/删除后，已发出的 token 在过期前仍然有效（MVP 阶段接受这个取舍）
"""

import logging
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from haossh.config import settings

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

_JWT_ALGORITHM = "HS256"
_DEFAULT_JWT_SECRET = "haossh-dev-jwt-secret-change-me"  # 仅本地开发用

# 登录态 Cookie 名，注册/登录时写入，中间件读取校验，登出时清除——三处必须用同一个名字
COOKIE_NAME = "haossh_token"


def _get_jwt_secret() -> str:
    """从环境变量读取 JWT 签名密钥，未设置则警告后用默认值（仅限本地开发）。"""
    if not settings.jwt_secret:
        logger.warning(
            "HAOSSH_JWT_SECRET 未设置，使用默认密钥。生产环境请务必设置此环境变量！"
        )
        return _DEFAULT_JWT_SECRET
    return settings.jwt_secret


def hash_password(plain: str) -> str:
    """把明文密码哈希成不可逆字符串，供注册时存库。"""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码是否匹配存库的哈希，供登录时调用。

    不是解密比较，而是对 plain 重新哈希后与 hashed 比对（argon2 内置盐处理）。
    密码错误或哈希格式异常都返回 False，不向上抛异常（登录接口按"账号或密码错误"统一处理）。
    """
    try:
        return _hasher.verify(hashed, plain)
    except VerificationError:
        return False


def create_token(user_id: str, tenant_id: str, role: str) -> str:
    """签发 JWT：编码身份信息并签名，settings.jwt_expire_days 天后过期。"""
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """校验 JWT 签名和有效期，返回其中编码的身份信息（sub/tenant_id/role）。

    Raises:
        jwt.InvalidTokenError: 签名无效或已过期，中间件捕获后应返回 401
    """
    return jwt.decode(token, _get_jwt_secret(), algorithms=[_JWT_ALGORITHM])
