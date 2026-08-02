import logging

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from haossh.auth.security import COOKIE_NAME, decode_token

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {"/api/v1/health", "/api/v1/auth/register", "/api/v1/auth/login"}


class JWTAuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):

        # 白名单：健康检查、注册/登录本身、以及所有静态资源（不以 /api 开头）不需要登录
        if request.url.path in _PUBLIC_PATHS or not request.url.path.startswith("/api"):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        if token is None:
            return JSONResponse({"code": "1001", "info": "未登录", "data": None}, status_code=401)
        else:
            try:
                user_info = decode_token(token)
                request.state.user_info = user_info
                request.state.user_id = user_info.get("sub")
                request.state.tenant_id = user_info.get("tenant_id")
                request.state.role = user_info.get("role")
                return await call_next(request)
            except jwt.InvalidTokenError:
                return JSONResponse({"code": "1001", "info": "登录态过期", "data": None}, status_code=401)


