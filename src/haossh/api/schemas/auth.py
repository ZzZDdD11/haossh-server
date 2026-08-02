"""认证相关 DTO。"""

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """注册请求：邮箱 + 密码 + 组织名，注册即创建租户（MVP 不支持邀请成员）。"""
    email: str
    password: str = Field(..., min_length=8, description="密码，至少 8 位")
    org_name: str = Field(..., alias="orgName", description="组织/团队名称")


class LoginRequest(BaseModel):
    """登录请求。"""
    email: str
    password: str
