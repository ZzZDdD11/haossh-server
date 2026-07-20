"""SSH 连接管理 API 路由。"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from haossh.api.schemas.ssh_connection import ConnectRequest, CreateConnectionRequest
from haossh.db import repo_connection
from haossh.db.models import SSHConnection
from haossh.ssh import session
from haossh.ssh.security import decrypt, encrypt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ssh", tags=["SSH Connection"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str) -> dict:
    return {"code": "1001", "info": info, "data": None}


def _to_dto(conn: SSHConnection, status: int = 0) -> dict:
    """SSHConnection 模型 → 前端 DTO（不含敏感字段）。"""
    return {
        "connectionId": conn.id,
        "connectionName": conn.name,
        "host": conn.host,
        "port": conn.port,
        "username": conn.username,
        "authType": conn.auth_type,
        "status": status,
        "encrypted": 1,  # 落库后均已加密
        "userId": conn.user_id,
        "createdAt": conn.created_at,
        "updatedAt": conn.updated_at,
    }


def _pick_plain_secret(req: CreateConnectionRequest) -> str | None:
    """根据 auth_type 取明文密码或私钥。"""
    if req.auth_type == 1:
        return req.password
    return req.private_key


# ===== CRUD =====

@router.post("/create_connection")
async def create_connection(req: CreateConnectionRequest):
    """创建 SSH 连接记录（不建立实际连接）。"""
    connection_id = req.connection_id or uuid.uuid4().hex

    if await repo_connection.get(connection_id):
        return _err(f"连接已存在: {connection_id}")

    plain_secret = _pick_plain_secret(req)
    if not plain_secret:
        return _err("缺少密码或私钥")

    conn = SSHConnection(
        id=connection_id,
        user_id=req.user_id,
        name=req.connection_name,
        host=req.host,
        port=req.port,
        username=req.username,
        auth_type=req.auth_type,
        secret_enc=encrypt(plain_secret),
        connect_timeout=req.connect_timeout,
        keepalive_interval=req.keepalive_interval,
        startup_command=req.startup_command,
        compression=req.compression,
        strict_host_key_check=req.strict_host_key_check,
    )
    await repo_connection.create(conn)
    logger.info("连接记录已创建 connection_id=%s name=%s", connection_id, req.connection_name)
    return _ok(_to_dto(conn))


@router.post("/update_connection")
async def update_connection(req: CreateConnectionRequest):
    """更新 SSH 连接记录。"""
    if not req.connection_id:
        return _err("缺少 connectionId")

    conn = await repo_connection.get(req.connection_id)
    if not conn:
        return _err(f"连接不存在: {req.connection_id}")

    conn.name = req.connection_name
    conn.host = req.host
    conn.port = req.port
    conn.username = req.username
    conn.auth_type = req.auth_type
    conn.connect_timeout = req.connect_timeout
    conn.keepalive_interval = req.keepalive_interval
    conn.startup_command = req.startup_command
    conn.compression = req.compression
    conn.strict_host_key_check = req.strict_host_key_check
    # 敏感字段：传了新的才更新
    plain_secret = _pick_plain_secret(req)
    if plain_secret:
        conn.secret_enc = encrypt(plain_secret)
    conn.updated_at = _now()

    await repo_connection.update(conn)
    return _ok(_to_dto(conn))


@router.post("/delete_connection")
async def delete_connection(connectionId: str = Query(..., alias="connectionId")):
    """删除 SSH 连接记录。"""
    await session.disconnect(connectionId)  # 先断开活跃连接
    ok = await repo_connection.delete(connectionId)
    if not ok:
        return _err(f"连接不存在: {connectionId}")
    return _ok()


@router.get("/get_connection")
async def get_connection(connectionId: str = Query(..., alias="connectionId")):
    """查询单个连接详情。"""
    conn = await repo_connection.get(connectionId)
    if not conn:
        return _err(f"连接不存在: {connectionId}")
    status = 1 if await session.is_connected(connectionId) else 0
    return _ok(_to_dto(conn, status))


@router.get("/connection_list")
async def connection_list(userId: str = Query(default="default", alias="userId")):
    """查询用户的所有连接。"""
    conns = await repo_connection.list_by_user(userId)
    result = []
    for c in conns:
        status = 1 if await session.is_connected(c.id) else 0
        result.append(_to_dto(c, status))
    return _ok(result)


# ===== 连接操作 =====

@router.post("/connect")
async def connect(req: ConnectRequest = None, connectionId: str = Query(default=None, alias="connectionId")):
    """建立 SSH 连接。支持两种模式：1）传 connectionId 从存储读取；2）直接传 host/port/user/pwd。"""
    if connectionId:
        conn = await repo_connection.get(connectionId)
        if not conn:
            return _err(f"连接不存在: {connectionId}")
        host = conn.host
        port = conn.port
        username = conn.username
        password = decrypt(conn.secret_enc)
        cid = connectionId
    elif req and req.host:
        host = req.host
        port = req.port
        username = req.username
        password = req.password
        cid = uuid.uuid4().hex
    else:
        return _err("请提供 connectionId 或连接参数")

    ok = await session.connect(
        connection_id=cid,
        host=host,
        port=port,
        username=username,
        password=password,
    )
    if ok:
        # 表单值连接成功后自动保存到 DB，下次刷新页面可从历史记录一键连接
        if not connectionId:
            existing = await repo_connection.get(cid)
            if not existing:
                conn = SSHConnection(
                    id=cid,
                    user_id="default",
                    name=f"{username}@{host}",
                    host=host,
                    port=port,
                    username=username,
                    auth_type=1,
                    secret_enc=encrypt(password),
                )
                await repo_connection.create(conn)
                logger.info("连接信息已自动保存 connection_id=%s host=%s", cid, host)
        return _ok({"connectionId": cid})
    return _err("SSH 连接失败，请检查主机地址和认证信息")


@router.post("/disconnect")
async def disconnect(connectionId: str = Query(..., alias="connectionId")):
    """断开 SSH 连接。"""
    ok = await session.disconnect(connectionId)
    if ok:
        return _ok()
    return _err("连接不存在或断开失败")


@router.get("/is_connected")
async def is_connected(connectionId: str = Query(..., alias="connectionId")):
    """检查 SSH 连接状态。"""
    alive = await session.is_connected(connectionId)
    return _ok({"connected": alive})
