"""SFTP 文件操作。

通过 SFTP 协议操作远程服务器的文件系统。
每个操作函数接收 connection_id，从连接池获取连接后开启 SFTP 客户端。
"""

import logging
import os
from dataclasses import dataclass

import asyncssh

from haossh.ssh.session import ssh_sessions

logger = logging.getLogger(__name__)


@dataclass
class FileEntry:
    """文件/目录条目，供路由层序列化为 JSON。"""

    name: str
    path: str
    type: str  # "file" | "directory" | "link"
    size: int
    permissions: str
    modified_at: float | None = None


async def _get_sftp(connection_id: str) -> asyncssh.SFTPClient:
    """获取 SFTP 客户端。"""
    conn = ssh_sessions.get(connection_id)
    if conn is None:
        raise ValueError(f"SSH 连接不存在: {connection_id}")
    return await conn.start_sftp_client()


def _mode_to_type(mode: int) -> str:
    """将 stat 模式转为文件类型字符串。"""
    import stat
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "link"
    return "file"


def _mode_to_permissions(mode: int) -> str:
    """将 stat 模式转为 rwx 权限字符串。"""
    import stat
    result = "-"
    result += "r" if mode & stat.S_IRUSR else "-"
    result += "w" if mode & stat.S_IWUSR else "-"
    result += "x" if mode & stat.S_IXUSR else "-"
    result += "r" if mode & stat.S_IRGRP else "-"
    result += "w" if mode & stat.S_IWGRP else "-"
    result += "x" if mode & stat.S_IXGRP else "-"
    result += "r" if mode & stat.S_IROTH else "-"
    result += "w" if mode & stat.S_IWOTH else "-"
    result += "x" if mode & stat.S_IXOTH else "-"
    return result


# ── 文件浏览 ──────────────────────────────────────────────────

async def list_dir(connection_id: str, path: str) -> list[FileEntry]:
    """列出目录内容（单层，不递归）。"""
    sftp = await _get_sftp(connection_id)
    entries: list[FileEntry] = []

    async for item in sftp.scandir(path):
        ftype = _mode_to_type(item.attrs.permissions)
        entries.append(FileEntry(
            name=item.filename,
            path=os.path.join(path, item.filename),
            type=ftype,
            size=item.attrs.size or 0,
            permissions=_mode_to_permissions(item.attrs.permissions),
            modified_at=item.attrs.mtime,
        ))

    logger.debug("列出目录 connection_id=%s path=%s count=%d", connection_id, path, len(entries))
    return entries


async def read_content(connection_id: str, path: str) -> str:
    """读取文件全部内容（文本）。"""
    sftp = await _get_sftp(connection_id)

    async with sftp.open(path, "r") as f:  # pyright: ignore
        content = await f.read()

    logger.debug("读取文件 connection_id=%s path=%s size=%d", connection_id, path, len(content))
    return content


async def read_chunk(connection_id: str, path: str, offset: int, size: int) -> str:
    """分块读取文件内容。

    Args:
        connection_id: SSH 连接标识
        path: 文件路径
        offset: 起始字节位置
        size: 读取字节数
    """
    sftp = await _get_sftp(connection_id)

    async with sftp.open(path, "r") as f:  # pyright: ignore
        await f.seek(offset)
        content = await f.read(size)

    return content


async def file_exists(connection_id: str, path: str) -> bool:
    """检查文件或目录是否存在。"""
    sftp = await _get_sftp(connection_id)
    return await sftp.exists(path)


# ── 文件编辑 ──────────────────────────────────────────────────

async def create_file(connection_id: str, path: str, content: str = "") -> None:
    """创建新文件（写入内容）。"""
    sftp = await _get_sftp(connection_id)

    async with sftp.open(path, "w") as f:  # pyright: ignore
        await f.write(content)

    logger.info("文件已创建 connection_id=%s path=%s", connection_id, path)


async def save_content(connection_id: str, path: str, content: str) -> None:
    """保存（覆盖）文件内容。等同于 create_file，但语义上用于已存在的文件。"""
    await create_file(connection_id, path, content)
    logger.info("文件内容已保存 connection_id=%s path=%s", connection_id, path)


async def create_directory(connection_id: str, path: str) -> None:
    """创建目录（递归创建父目录）。"""
    sftp = await _get_sftp(connection_id)
    await sftp.makedirs(path)
    logger.info("目录已创建 connection_id=%s path=%s", connection_id, path)


# ── 文件操作 ──────────────────────────────────────────────────

async def rename_file(connection_id: str, old_path: str, new_path: str) -> None:
    """重命名/移动文件或目录。"""
    sftp = await _get_sftp(connection_id)
    await sftp.rename(old_path, new_path)
    logger.info("文件已重命名 connection_id=%s %s -> %s", connection_id, old_path, new_path)


async def delete(connection_id: str, path: str) -> None:
    """删除文件或目录（目录递归删除）。"""
    sftp = await _get_sftp(connection_id)
    is_dir = await sftp.isdir(path)
    if is_dir:
        await sftp.rmtree(path)
    else:
        await sftp.remove(path)
    logger.info("已删除 connection_id=%s path=%s", connection_id, path)


async def upload(connection_id: str, local_data: bytes, remote_path: str) -> None:
    """上传文件内容到远程服务器。

    Args:
        connection_id: SSH 连接标识
        local_data: 文件的二进制内容
        remote_path: 远程目标路径
    """
    sftp = await _get_sftp(connection_id)

    async with sftp.open(remote_path, "wb") as f:  # pyright: ignore
        await f.write(local_data)

    logger.info("文件已上传 connection_id=%s path=%s size=%d", connection_id, remote_path, len(local_data))


async def download(connection_id: str, remote_path: str) -> bytes:
    """下载远程文件内容。

    Returns:
        文件的二进制内容
    """
    sftp = await _get_sftp(connection_id)

    async with sftp.open(remote_path, "rb") as f:  # pyright: ignore
        content = await f.read()

    logger.info("文件已下载 connection_id=%s path=%s size=%d", connection_id, remote_path, len(content))
    return content
