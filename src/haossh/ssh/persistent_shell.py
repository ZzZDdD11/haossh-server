"""持久 Shell：让 execute_command 的多次调用共享同一个 shell 进程的状态。

问题背景：terminal.exec_command() 每次调用都用 conn.run() 开一个全新 channel，
不共享 shell 状态——cd 只在当次调用生效，下一次又回到登录目录。

设计：
- 每个 conversation_id 绑定一条持久 bash 进程（非 PTY，纯管道，无回显/提示符干扰）
- 命令包装：用随机 token 生成 sentinel 标记，追加到 stdout/stderr 末尾，
  读取时并发读两条流，各自读到标记为止，从标记行解析退出码 + 当前 cwd（$PWD）
- 一条 shell 内命令强制串行执行（asyncio.Lock），避免并发写入交叉
- 超时后尝试恢复（发 Ctrl+C 中断 + 探测响应），恢复失败则整条 shell 作废，
  下次调用自动重建（对模型可见：get_shell 返回 was_rebuilt=True）
- 工作区（workspace）不是声明式设置的，而是直接读 shell 真实 cwd 观测得到：
  每次 run() 后 cwd 会随 $PWD 一起返回；shell 重建时自动 cd 回上次已知的 cwd，
  避免崩溃重建后模型需要重新手动导航
"""

import asyncio
import logging
import uuid

import asyncssh

from haossh.ssh.session import get_session

logger = logging.getLogger(__name__)


class PersistentShell:
    """绑定一个 conversation 的持久 bash 进程。"""

    def __init__(self, conversation_id: str, connection_id: str, process: asyncssh.SSHClientProcess):
        self.conversation_id = conversation_id
        self.connection_id = connection_id
        self.process = process
        self.lock = asyncio.Lock()
        self.alive = True
        self.cwd: str | None = None  # 最近一次观测到的真实 cwd，重建时用来 cd 回去

    async def run(self, command: str, timeout: float) -> tuple[str, str, int, str]:
        """执行一条命令，返回 (stdout, stderr, exit_status, cwd)。

        cwd 是命令执行后 shell 的真实 $PWD——不是声明式设置的，是观测得到的事实。
        命令内的 cd / 环境变量修改会延续到下一次 run() 调用。
        """
        async with self.lock:
            token = uuid.uuid4().hex[:8]
            marker = f"__END_{token}__"
            # printf 前置 \n 保证标记独占一行，不会和命令末尾无换行的输出粘在一起
            wrapped = (
                f"{command}\n"
                f"__EC__=$?\n"
                f'printf "\\n{marker}:%s:%s\\n" "$__EC__" "$PWD"\n'
                f'printf "\\n{marker}\\n" >&2\n'
            )
            try:
                self.process.stdin.write(wrapped)
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                self.alive = False
                raise ConnectionError(f"持久 shell 已断开: {e}") from e

            try:
                (stdout, exit_status, cwd), (stderr, _, _) = await asyncio.wait_for(
                    asyncio.gather(
                        self._read_until(self.process.stdout, marker, with_meta=True),
                        self._read_until(self.process.stderr, marker, with_meta=False),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                recovered = await self._recover()
                if not recovered:
                    self.alive = False
                raise

            if cwd:
                self.cwd = cwd
            return stdout, stderr, exit_status, cwd or (self.cwd or "")

    async def _read_until(self, stream, marker: str, with_meta: bool) -> tuple[str, int, str]:
        """逐行读取，直到看到标记行为止。返回（标记前的输出内容, 退出码, cwd）。"""
        lines: list[str] = []
        exit_status = -1
        cwd = ""
        while True:
            line = await stream.readline()
            if line == "":
                # EOF：shell 进程已退出（可能被意外杀死或执行了 exit）
                self.alive = False
                raise ConnectionError("持久 shell 进程已退出（EOF）")
            if line.startswith(marker):
                if with_meta:
                    # 格式：__END_xxx__:<exit_code>:<cwd>
                    _, _, rest = line.strip().partition(":")
                    code_str, _, cwd = rest.partition(":")
                    try:
                        exit_status = int(code_str)
                    except ValueError:
                        exit_status = -1
                break
            lines.append(line)
        # 去掉 printf 人为插入的那一行前置空行
        if lines and lines[-1] == "\n":
            lines.pop()
        return "".join(lines), exit_status, cwd

    async def _recover(self) -> bool:
        """超时后尝试恢复：发 Ctrl+C 中断挂起命令，再探测 shell 是否还能响应。"""
        try:
            self.process.stdin.write("\x03")  # Ctrl+C
            token = uuid.uuid4().hex[:8]
            probe_marker = f"__PROBE_{token}__"
            self.process.stdin.write(f'\necho "{probe_marker}"\n')
            while True:
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout=3)
                if line == "":
                    return False
                if probe_marker in line:
                    logger.info("持久 shell 恢复成功 conversation_id=%s", self.conversation_id[:12])
                    return True
        except Exception as e:
            logger.warning("持久 shell 恢复失败 conversation_id=%s: %s", self.conversation_id[:12], e)
            return False

    def close(self) -> None:
        """关闭这条持久 shell 进程。"""
        try:
            self.process.stdin.write_eof()
        except Exception:
            pass
        try:
            self.process.terminate()
        except Exception:
            pass


# conversation_id -> PersistentShell
_shells: dict[str, PersistentShell] = {}


async def _create_shell(
    conversation_id: str, connection_id: str, restore_cwd: str | None = None
) -> PersistentShell:
    conn = await get_session(connection_id)
    # --noprofile --norc：跳过 .bashrc/.profile，避免用户自定义脚本输出干扰标记解析
    process = await conn.create_process("bash --noprofile --norc", encoding="utf-8")
    shell = PersistentShell(conversation_id, connection_id, process)
    _shells[conversation_id] = shell
    logger.info(
        "持久 shell 已创建 conversation_id=%s connection_id=%s",
        conversation_id[:12], connection_id[:12] if connection_id else "",
    )
    if restore_cwd:
        # 重建后自动 cd 回上次已知的工作目录，避免模型需要重新手动导航
        try:
            await shell.run(f"cd {_shell_quote(restore_cwd)}", timeout=10)
            logger.info("持久 shell 已恢复到 cwd=%s", restore_cwd)
        except Exception as e:
            logger.warning("持久 shell 恢复 cwd 失败 cwd=%s: %s", restore_cwd, e)
    return shell


def _shell_quote(path: str) -> str:
    """简单的 shell 路径转义（用于 cd 恢复，路径来自可信的历史观测值）。"""
    return "'" + path.replace("'", "'\\''") + "'"


async def get_shell(
    conversation_id: str, connection_id: str, known_workspace: str | None = None
) -> tuple[PersistentShell, bool]:
    """获取（或创建/重建）conversation 绑定的持久 shell。

    Args:
        known_workspace: 已知的历史工作区路径（从 DB 读取），仅在需要重建时用于自动 cd 恢复。

    Returns:
        (shell, was_rebuilt)：was_rebuilt=True 表示这次是因为旧 shell 失效而重新创建的
        （此时会自动尝试 cd 回 known_workspace，仍应告知模型环境已重置）。
        首次创建（之前不存在）不算 rebuilt。
    """
    shell = _shells.get(conversation_id)
    if shell is not None and shell.alive:
        return shell, False
    was_rebuilt = shell is not None  # 存在过但已失效 → 这次是重建
    restore_cwd = known_workspace if was_rebuilt else None
    shell = await _create_shell(conversation_id, connection_id, restore_cwd=restore_cwd)
    return shell, was_rebuilt


async def close_shell(conversation_id: str) -> None:
    """关闭并移除指定对话的持久 shell。"""
    shell = _shells.pop(conversation_id, None)
    if shell:
        shell.close()


async def close_shells_for_connection(connection_id: str) -> None:
    """SSH 连接断开时，清理该连接下所有绑定的持久 shell（避免占用已失效的 channel 引用）。"""
    to_remove = [cid for cid, s in _shells.items() if s.connection_id == connection_id]
    for cid in to_remove:
        await close_shell(cid)
    if to_remove:
        logger.info("已清理 %d 个持久 shell（connection_id=%s 断开）", len(to_remove), connection_id[:12])
