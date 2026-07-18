"""SSH 连接管理 API 路由。"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from haossh.api.schemas.ssh_connection import ConnectRequest, CreateConnectionRequest
from haossh.ssh import session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ssh", tags=["SSH Connection"])

# ===== 内存存储（Phase 3 迁移到数据库） =====
_connections: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str) -> dict:
    return {"code": "1001", "info": info, "data": None}


def _to_connection_dto(conn: dict) -> dict:
    """内存 dict → 前端 DTO 格式。"""
    return {
        "connectionId": conn["connectionId"],
        "connectionName": conn["connectionName"],
        "host": conn["host"],
        "port": conn["port"],
        "username": conn["username"],
        "authType": conn["authType"],
        "status": conn.get("status", 0),
        "encrypted": conn.get("encrypted", 0),
        "userId": conn["userId"],
        "createdAt": conn["createdAt"],
        "updatedAt": conn["updatedAt"],
    }


# ===== CRUD =====

@router.post("/create_connection")
async def create_connection(req: CreateConnectionRequest):
    """创建 SSH 连接记录（不建立实际连接）。"""
    connection_id = req.connection_id or uuid.uuid4().hex

    if connection_id in _connections:
        return _err(f"连接已存在: {connection_id}")

    now = _now()
    conn = {
        "connectionId": connection_id,
        "connectionName": req.connection_name,
        "host": req.host,
        "port": req.port,
        "username": req.username,
        "authType": req.auth_type,
        "password": req.password or "",
        "privateKey": req.private_key or "",
        "status": 0,
        "encrypted": 0,
        "userId": req.user_id,
        "createdAt": now,
        "updatedAt": now,
    }
    _connections[connection_id] = conn
    logger.info("连接记录已创建 connection_id=%s name=%s", connection_id, req.connection_name)
    return _ok(_to_connection_dto(conn))


@router.post("/update_connection")
async def update_connection(req: CreateConnectionRequest):
    """更新 SSH 连接记录。"""
    if not req.connection_id:
        return _err("缺少 connectionId")

    conn = _connections.get(req.connection_id)
    if not conn:
        return _err(f"连接不存在: {req.connection_id}")

    conn["connectionName"] = req.connection_name
    conn["host"] = req.host
    conn["port"] = req.port
    conn["username"] = req.username
    conn["authType"] = req.auth_type
    if req.password:
        conn["password"] = req.password
    if req.private_key:
        conn["privateKey"] = req.private_key
    conn["updatedAt"] = _now()

    return _ok(_to_connection_dto(conn))


@router.post("/delete_connection")
async def delete_connection(connectionId: str = Query(..., alias="connectionId")):
    """删除 SSH 连接记录。"""
    conn = _connections.pop(connectionId, None)
    if not conn:
        return _err(f"连接不存在: {connectionId}")
    # 如果已建立 SSH 连接，也断开
    await session.disconnect(connectionId)
    return _ok()


@router.get("/get_connection")
async def get_connection(connectionId: str = Query(..., alias="connectionId")):
    """查询单个连接详情。"""
    conn = _connections.get(connectionId)
    if not conn:
        return _err(f"连接不存在: {connectionId}")
    # 同步 SSH 连接状态
    conn["status"] = 1 if await session.is_connected(connectionId) else 0
    return _ok(_to_connection_dto(conn))


@router.get("/connection_list")
async def connection_list(userId: str = Query(default="default", alias="userId")):
    """查询用户的所有连接。"""
    result = [
        _to_connection_dto(c)
        for c in _connections.values()
        if c["userId"] == userId
    ]
    return _ok(result)


# ===== 连接操作 =====

@router.post("/connect")
async def connect(req: ConnectRequest = None, connectionId: str = Query(default=None, alias="connectionId")):
    """建立 SSH 连接。支持两种模式：1）传 connectionId 从存储读取；2）直接传 host/port/user/pwd。"""
    if connectionId:
        conn = _connections.get(connectionId)
        if not conn:
            return _err(f"连接不存在: {connectionId}")
        host = conn["host"]
        port = conn["port"]
        username = conn["username"]
        password = conn["password"]
    elif req and req.host:
        host = req.host
        port = req.port
        username = req.username
        password = req.password
        connection_id = uuid.uuid4().hex
    else:
        return _err("请提供 connectionId 或连接参数")

    ok = await session.connect(
        connection_id=connectionId or uuid.uuid4().hex,
        host=host,
        port=port,
        username=username,
        password=password,
    )
    if ok:
        if connectionId and connectionId in _connections:
            _connections[connectionId]["status"] = 1
        return _ok({"connectionId": connectionId})
    if connectionId and connectionId in _connections:
        _connections[connectionId]["status"] = 3  # 失败
    return _err("SSH 连接失败，请检查主机地址和认证信息")


@router.post("/disconnect")
async def disconnect(connectionId: str = Query(..., alias="connectionId")):
    """断开 SSH 连接。"""
    ok = await session.disconnect(connectionId)
    if ok:
        if connectionId in _connections:
            _connections[connectionId]["status"] = 0
        return _ok()
    return _err("连接不存在或断开失败")


@router.get("/is_connected")
async def is_connected(connectionId: str = Query(..., alias="connectionId")):
    """检查 SSH 连接状态。"""
    alive = await session.is_connected(connectionId)
    return _ok({"connected": alive})
