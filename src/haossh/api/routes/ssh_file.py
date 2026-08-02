"""SSH 文件操作 API 路由。"""

import logging

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from haossh.api.schemas.ssh_file import (
    CreateDirectoryRequest,
    CreateFileRequest,
    DeleteRequest,
    FileEntryOut,
    ReadChunkRequest,
    ReadContentRequest,
    RenameRequest,
    SaveContentRequest,
)
from haossh.db import repo_connection
from haossh.ssh import file as sftp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ssh/file", tags=["SSH File"])


def _ok(data: object = None) -> dict:
    return {"code": "0000", "info": "成功", "data": data}


def _err(info: str) -> dict:
    return {"code": "1001", "info": info, "data": None}


async def _require_owned_connection(connection_id: str, tenant_id: str) -> None:
    """校验 connectionId 属于当前登录租户，不属于则抛 403（越权/不存在统一处理，不泄露信息）。

    文件操作直接接触远程服务器数据，必须先做归属校验（AuthZ）才能往下走，
    不能像 ssh_connection.py 之外的路由那样只信任前端传的 connectionId。
    """
    if not await repo_connection.get_owned(connection_id, tenant_id):
        raise HTTPException(status_code=403, detail=f"连接不存在或无权访问: {connection_id}")


# ── 文件浏览 ──────────────────────────────────────────────────

@router.get("/tree")
async def file_tree(
    request: Request,
    connectionId: str = Query(...),
    path: str = Query(default="/"),
):
    """获取目录树（单层）。"""
    await _require_owned_connection(connectionId, request.state.tenant_id)
    try:
        entries = await sftp.list_dir(connectionId, path)
        result = [
            FileEntryOut(
                name=e.name,
                path=e.path,
                type=e.type,
                size=e.size,
                permissions=e.permissions,
                modifiedAt=e.modified_at,
            ).model_dump(by_alias=True)
            for e in entries
        ]
        return _ok(result)
    except ValueError as e:
        return _err(str(e))


@router.get("/content")
async def file_content(
    request: Request,
    connectionId: str = Query(...),
    path: str = Query(...),
):
    """读取文件完整内容。"""
    await _require_owned_connection(connectionId, request.state.tenant_id)
    try:
        content = await sftp.read_content(connectionId, path)
        return _ok({"content": content, "path": path})
    except ValueError as e:
        return _err(str(e))


@router.get("/content-chunk")
async def file_content_chunk(
    request: Request,
    connectionId: str = Query(...),
    path: str = Query(...),
    offset: int = Query(...),
    size: int = Query(...),
):
    """分块读取文件内容。"""
    await _require_owned_connection(connectionId, request.state.tenant_id)
    try:
        content = await sftp.read_chunk(connectionId, path, offset, size)
        return _ok({"content": content, "offset": offset, "size": size})
    except ValueError as e:
        return _err(str(e))


# ── 文件编辑 ──────────────────────────────────────────────────

@router.post("/create-file")
async def create_file(req: CreateFileRequest, request: Request):
    """创建新文件。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
    try:
        await sftp.create_file(req.connection_id, req.path, req.content)
        return _ok()
    except ValueError as e:
        return _err(str(e))


@router.post("/save-content")
async def save_content(req: SaveContentRequest, request: Request):
    """保存（覆盖）文件内容。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
    try:
        await sftp.save_content(req.connection_id, req.path, req.content)
        return _ok()
    except ValueError as e:
        return _err(str(e))


@router.post("/create-directory")
async def create_directory(req: CreateDirectoryRequest, request: Request):
    """创建目录。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
    try:
        await sftp.create_directory(req.connection_id, req.path)
        return _ok()
    except ValueError as e:
        return _err(str(e))


# ── 文件操作 ──────────────────────────────────────────────────

@router.post("/rename")
async def rename_file(req: RenameRequest, request: Request):
    """重命名/移动文件。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
    try:
        await sftp.rename_file(req.connection_id, req.old_path, req.new_path)
        return _ok()
    except ValueError as e:
        return _err(str(e))


@router.post("/delete")
async def delete_file(req: DeleteRequest, request: Request):
    """删除文件或目录。"""
    await _require_owned_connection(req.connection_id, request.state.tenant_id)
    try:
        await sftp.delete(req.connection_id, req.path)
        return _ok()
    except ValueError as e:
        return _err(str(e))


@router.post("/upload")
async def upload_file(
    request: Request,
    connectionId: str = Query(..., alias="connectionId"),
    path: str = Query(...),
    file: UploadFile = File(...),
):
    """上传文件到远程服务器。"""
    await _require_owned_connection(connectionId, request.state.tenant_id)
    try:
        data = await file.read()
        await sftp.upload(connectionId, data, path)
        return _ok({"path": path, "size": len(data)})
    except ValueError as e:
        return _err(str(e))


@router.get("/download")
async def download_file(
    request: Request,
    connectionId: str = Query(...),
    path: str = Query(...),
):
    """下载文件（返回二进制流）。"""
    await _require_owned_connection(connectionId, request.state.tenant_id)
    try:
        content = await sftp.download(connectionId, path)
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        return _err(str(e))

