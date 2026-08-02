"""认证相关 API 路由：注册 / 登录 / 登出 / 当前用户。"""

import logging
import uuid

import jwt
from fastapi import APIRouter, Request, Response

from haossh.api.schemas.auth import LoginRequest, RegisterRequest
from haossh.auth.security import (
    COOKIE_NAME,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from haossh.config import settings
from haossh.db import repo_user
from haossh.db.models import Tenant, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register")
async def register(req: RegisterRequest, response: Response):
    """注册：创建租户 + 用户（owner），成功后直接写入登录态 Cookie。

    邮箱统一转小写存取，避免 Foo@bar.com / foo@bar.com 被当成两个账号。
    """
    email = req.email.strip().lower()

    if await repo_user.get_user_by_email(email):
        return _err("该邮箱已被注册")

    tenant = Tenant(id=uuid.uuid4().hex, name=req.org_name)
    user = User(
        id=uuid.uuid4().hex,
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(req.password),
        role="owner",
    )
    await repo_user.create_tenant_and_user(tenant, user)

    token = create_token(user_id=user.id, tenant_id=tenant.id, role=user.role)
    _set_login_cookie(response, token)

    logger.info("新租户注册成功 tenant_id=%s user_id=%s", tenant.id[:12], user.id[:12])
    return _ok(_user_dto(tenant, user))


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    """登录：校验密码后写入登录态 Cookie。

    不区分"邮箱不存在"和"密码错误"两种失败原因，统一返回"邮箱或密码错误"，
    避免把"这个邮箱是否已注册"这个信息泄露给未认证的请求方。
    """
    email = req.email.strip().lower()

    user = await repo_user.get_user_by_email(email)
    if not user or not verify_password(req.password, user.password_hash):
        return _err("邮箱或密码错误")

    tenant = await repo_user.get_tenant_by_id(user.tenant_id)
    if not tenant:
        # 正常情况下不会发生（create_tenant_and_user 保证原子性），防御性处理
        return _err("账号数据异常，请联系管理员")

    token = create_token(user_id=user.id, tenant_id=tenant.id, role=user.role)
    _set_login_cookie(response, token)

    logger.info("用户登录成功 user_id=%s tenant_id=%s", user.id[:12], tenant.id[:12])
    return _ok(_user_dto(tenant, user))


@router.post("/logout")
async def logout(response: Response):
    """登出：清除登录态 Cookie。

    JWT 是无状态的，服务端不存任何会话记录，"登出"能做的只是让浏览器不再
    发这个 Cookie——如果 token 在此之前已被复制走，理论上它在过期前依然有效，
    这是无状态方案的固有取舍，MVP 阶段接受。
    """
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return _ok()


@router.get("/me")
async def me(request: Request):
    """查询当前登录状态，供前端刷新页面时判断是否已登录、取回身份信息。

    中间件（下一阶段）接入后，可以改为直接读 request.state；
    现在中间件还没接入，这里先自己解 Cookie 校验一次，逻辑独立自洽。
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return _err("未登录")

    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError:
        return _err("登录状态已失效，请重新登录")

    user = await repo_user.get_user_by_id(payload["sub"])
    tenant = await repo_user.get_tenant_by_id(payload["tenant_id"]) if user else None
    if not user or not tenant:
        return _err("登录状态已失效，请重新登录")

    return _ok(_user_dto(tenant, user))


def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str) -> dict:
    return {"code": "1001", "info": info, "data": None}


def _user_dto(tenant: Tenant, user: User) -> dict:
    """组装返回给前端的登录态信息（绝不包含 password_hash）。"""
    return {
        "userId": user.id,
        "tenantId": tenant.id,
        "tenantName": tenant.name,
        "email": user.email,
        "role": user.role,
    }


def _set_login_cookie(response: Response, token: str) -> None:
    """把 JWT 写入 httpOnly Cookie。

    secure=False：本地开发走 http，无 TLS。生产部署上 HTTPS 后必须改成 True，
    否则浏览器不会把该 Cookie 通过明文 http 发出去（这是一个已知的部署前待办项）。
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * settings.jwt_expire_days,
    )

