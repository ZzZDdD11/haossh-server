"""SSH 运维 Agent 工具集。

工具函数通过 RunContext[AgentDeps].deps 获取运行时上下文（session_id 等），
deps 由路由层在 agent.run_stream(deps=...) 时注入，LLM 看不到。

依赖方向：tools → deps（无循环）
"""

import asyncio
import logging
import re
import shlex
import uuid

from pydantic_ai import RunContext, Tool
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import ToolDefinition

from haossh.agent.deps import AgentDeps
from haossh.ssh import file as sftp
from haossh.ssh import terminal

logger = logging.getLogger(__name__)


# ── 危险命令拦截层 ───────────────────────────────────────────
# 毁灭性命令：直接拒绝，不让 LLM 重试

_FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+-rf?\s+/(?:\s|$|\*)"),                    # rm -rf /  /  /*
    re.compile(r"rm\s+-rf?\s+/\*"),                              # rm -rf /*
    re.compile(r"dd\s+if=/dev/(?:zero|random|urandom)"),         # dd 覆写磁盘
    re.compile(r"mkfs\.[a-z0-9]+"),                              # mkfs 格式化
    re.compile(r":\(\)\s*\{\s*:\|:\&\s*\}\s*;:\s*\}"),          # fork bomb :(){ :|:& };:
    re.compile(r">\s*/dev/sd[a-z]"),                             # 直写块设备
    re.compile(r"chmod\s+-R\s+777\s+/\s*$"),                     # 全盘 777
]


def _check_forbidden(command: str) -> str | None:
    """命中毁灭性命令返回拒绝原因，否则返回 None。"""
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(command):
            return f"已拦截毁灭性命令（匹配规则: {pattern.pattern}）。请换用更安全的操作。"
    return None


# ── execute_command ──────────────────────────────────────────

async def execute_command(
    ctx: RunContext[AgentDeps],
    command: str,
    timeout: int = 120,
) -> str:
    """在远程服务器上执行单条 shell 命令（非交互式）。

    适用于：系统信息查询、服务管理、日志查看、网络排障等快速命令（通常 30 秒内完成）。
    不适用于需要交互输入的场景（如 top、vim、ssh 二次确认），请改用终端会话。
    长时间命令（uv sync、apt install、docker build、git clone 等可能超过 30 秒）请改用 run_background 后台执行。

    遇到 Permission denied 时，对系统管理类操作应主动加 sudo 重试。

    Args:
        command: 要执行的 shell 命令，如 "df -h"、"systemctl status nginx"、"sudo apt install -y nginx"。
        timeout: 命令超时秒数，会被会话上限约束（默认 60s）。
    """
    # 1. 危险命令拦截（毁灭性命令不交给 LLM 重试）
    forbidden = _check_forbidden(command)
    if forbidden:
        logger.warning(
            "拦截危险命令 session_id=%s command=%s",
            ctx.deps.session_id, command,
        )
        return forbidden

    # 2. sudo 权限检查
    if "sudo" in command and not ctx.deps.allow_sudo:
        return "错误：当前会话不允许执行 sudo 命令"

    # 3. 超时上限约束
    timeout = min(timeout, ctx.deps.max_command_timeout)

    # 4. 执行
    try:
        stdout, stderr, exit_status = await terminal.exec_command(
            connection_id=ctx.deps.session_id,
            command=command,
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ModelRetry(
            f"命令执行超时（{timeout}秒）。长时间命令请改用 run_background 后台执行，再用 check_task 轮询。"
        ) from None
    except Exception as e:
        logger.warning(
            "命令执行失败 session_id=%s command=%s error=%s",
            ctx.deps.session_id, command, e,
        )
        # 反馈给 LLM，让它据此重试或换方案
        raise ModelRetry(f"命令执行失败: {e}") from e

    # 5. 组装返回——让 LLM 清楚看到退出码与 stderr
    parts: list[str] = [f"[exit={exit_status}]"]
    if stdout:
        parts.append(f"[stdout]\n{stdout}")
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if not stdout and not stderr:
        parts.append("(命令执行完成，无输出)")
    return "\n".join(parts)


# ── read_file ────────────────────────────────────────────────

async def read_file(
    ctx: RunContext[AgentDeps],
    path: str,
    max_size: int = 8192,
) -> str:
    """读取远程文本文件内容。

    适用于查看配置文件、脚本等中小文件。
    大文件（如日志）建议改用 execute_command 配合 tail/grep/head 查看。

    Args:
        path: 远程文件绝对路径，如 "/etc/nginx/nginx.conf"
        max_size: 最大读取字节数，默认 8192（8KB），超出部分截断
    """
    cid = ctx.deps.session_id

    # 1. 获取文件总大小
    try:
        total = await sftp.get_size(cid, path)
    except Exception as e:
        raise ModelRetry(f"无法访问文件 {path}: {e}") from e

    # 2. 读取内容（最多 max_size 字节）
    try:
        read_size = min(max_size, total) if total > 0 else max_size
        content = await sftp.read_chunk(cid, path, 0, read_size)
    except Exception as e:
        raise ModelRetry(f"读取文件失败 {path}: {e}") from e

    # 3. 组装返回
    truncated = total > max_size
    parts = [f"[path={path} size={total} bytes]"]
    if content:
        parts.append(content)
    if truncated:
        parts.append(
            f"\n(文件共 {total} 字节，已读前 {max_size} 字节。"
            f"查看完整内容请用 execute_command 执行 tail/grep)"
        )
    elif not content:
        parts.append("(空文件)")
    return "\n".join(parts)


# ── write_file ───────────────────────────────────────────────

async def write_file(
    ctx: RunContext[AgentDeps],
    path: str,
    content: str,
) -> str:
    """写入（覆盖）远程文本文件。文件不存在则创建，存在则完全覆盖。

    适用于编辑配置文件、写入脚本等。如需修改部分内容，
    应先 read_file 读取完整内容，本地修改后整体写回。

    Args:
        path: 远程文件绝对路径
        content: 要写入的完整内容
    """
    try:
        await sftp.save_content(ctx.deps.session_id, path, content)
    except Exception as e:
        raise ModelRetry(f"写入文件失败 {path}: {e}") from e

    return f"已写入 {path}（{len(content)} 字符）"


# ── list_directory ───────────────────────────────────────────

async def list_directory(
    ctx: RunContext[AgentDeps],
    path: str = "/",
) -> str:
    """列出远程目录内容（单层，不递归）。

    Args:
        path: 远程目录绝对路径，默认 "/"
    """
    try:
        entries = await sftp.list_dir(ctx.deps.session_id, path)
    except Exception as e:
        raise ModelRetry(f"列出目录失败 {path}: {e}") from e

    if not entries:
        return f"[path={path}]\n(空目录)"

    # 目录优先，再按名字排序
    entries.sort(key=lambda e: (e.type != "directory", e.name))

    lines = [f"[path={path}] ({len(entries)} 项)"]
    for e in entries:
        type_flag = "d" if e.type == "directory" else ("l" if e.type == "link" else "-")
        lines.append(f"{type_flag} {e.permissions} {e.size:>10}  {e.name}")
    return "\n".join(lines)


# ── get_environment ──────────────────────────────────────────

async def get_environment(ctx: RunContext[AgentDeps]) -> str:
    """获取远程服务器的环境信息（一次性返回 OS、用户、当前目录、内核版本、运行时长）。

    首次操作服务器时建议先调用此工具了解环境，后续命令可据此判断发行版、权限等。
    """
    cid = ctx.deps.session_id
    try:
        # 并行执行 5 条探测命令
        os_info, user, pwd, kernel, uptime = await asyncio.gather(
            terminal.exec_command(cid, "cat /etc/os-release 2>/dev/null | head -5", timeout=10),
            terminal.exec_command(cid, "whoami", timeout=5),
            terminal.exec_command(cid, "pwd", timeout=5),
            terminal.exec_command(cid, "uname -srm", timeout=5),
            terminal.exec_command(cid, "uptime -p 2>/dev/null || uptime", timeout=5),
        )
    except Exception as e:
        raise ModelRetry(f"获取环境信息失败: {e}") from e

    parts = [
        "=== 服务器环境 ===",
        f"OS: {os_info[0].strip()}",
        f"用户: {user[0].strip()}",
        f"当前目录: {pwd[0].strip()}",
        f"内核: {kernel[0].strip()}",
        f"运行时长: {uptime[0].strip()}",
    ]
    return "\n".join(parts)


# ── run_background / check_task（后台执行 + 轮询）──────────────

# 后台任务映射：task_id → {pid, log_file, command}
# 进程级内存存储，服务重启丢失（后台任务本身也会随重启终止）
_background_tasks: dict[str, dict] = {}


async def run_background(
    ctx: RunContext[AgentDeps],
    command: str,
) -> str:
    """后台执行长时间命令（非阻塞），返回任务ID用于轮询。

    适用于可能超过 30 秒的命令：uv sync、apt install、docker build、git clone 等。
    命令在远程服务器后台运行（nohup），不受 SSH 超时或断开影响。
    执行后用 check_task 轮询状态和输出，直到任务完成。

    Args:
        command: 要后台执行的 shell 命令，如 "uv sync"、"apt install -y nginx"。
    """
    # 1. 危险命令拦截
    forbidden = _check_forbidden(command)
    if forbidden:
        return forbidden

    # 2. 生成 task_id 和日志路径
    task_id = uuid.uuid4().hex[:8]
    log_file = f"/tmp/haossh_bg_{task_id}.log"

    # 3. 后台执行：nohup 让命令不受 SSH 断开影响，& 让命令在后台运行
    #    shlex.quote 防止命令里的特殊字符破坏 shell 语法
    full_command = f"nohup bash -c {shlex.quote(command)} > {log_file} 2>&1 & echo $!"
    try:
        stdout, stderr, exit_status = await terminal.exec_command(
            connection_id=ctx.deps.session_id,
            command=full_command,
            timeout=10,
        )
    except Exception as e:
        raise ModelRetry(f"启动后台任务失败: {e}") from e

    # 4. 解析 PID（echo $! 输出在最后一行）
    pid = stdout.strip().split("\n")[-1].strip()
    if not pid.isdigit():
        raise ModelRetry(f"无法获取后台任务 PID，stdout={stdout!r}")

    # 5. 记录任务
    _background_tasks[task_id] = {
        "pid": pid,
        "log_file": log_file,
        "command": command,
    }

    logger.info("后台任务已启动 task_id=%s pid=%s command=%s", task_id, pid, command[:80])
    return (
        f"后台任务已启动\n"
        f"[task_id={task_id}] [pid={pid}]\n"
        f"命令: {command}\n"
        f"用 check_task(task_id=\"{task_id}\") 查询状态和输出。"
    )


async def check_task(
    ctx: RunContext[AgentDeps],
    task_id: str,
) -> str:
    """查询后台任务的运行状态和最近输出。

    配合 run_background 使用：启动任务后反复调用此工具轮询，直到状态为 done。
    每次返回最近 30 行输出，可观察进度。

    Args:
        task_id: run_background 返回的任务ID。
    """
    task = _background_tasks.get(task_id)
    if not task:
        return f"任务不存在: {task_id}。可能已过期或 task_id 错误。"

    pid = task["pid"]
    log_file = task["log_file"]

    # 检查进程状态 + 读日志尾部
    # ps -p 返回 0 表示进程存在（running），非 0 表示已结束（done）
    check_command = (
        f"ps -p {pid} -o pid= 2>/dev/null && echo '__RUNNING__' || echo '__DONE__';"
        f"echo '===LOG===';"
        f"tail -30 {log_file}"
    )
    try:
        stdout, stderr, exit_status = await terminal.exec_command(
            connection_id=ctx.deps.session_id,
            command=check_command,
            timeout=10,
        )
    except Exception as e:
        raise ModelRetry(f"查询任务状态失败: {e}") from e

    # 解析状态
    is_running = "__RUNNING__" in stdout
    status = "running" if is_running else "done"

    # 提取日志部分
    log_parts = stdout.split("===LOG===")
    log_output = log_parts[1].strip() if len(log_parts) > 1 else "(无输出)"

    return f"[{status}] task_id={task_id} pid={pid}\n{log_output}"


# ── prepare 钩子 ─────────────────────────────────────────────

async def require_connection(
    ctx: RunContext[AgentDeps],
    tool_def: ToolDefinition,
) -> ToolDefinition | None:
    """SSH 未连接时隐藏工具——LLM 根本看不到，避免瞎调浪费交互。

    只做内存级检查（查连接池 dict），不做网络心跳，保证纳秒级返回。
    """
    from haossh.ssh.session import ssh_sessions
    conn = ssh_sessions.get(ctx.deps.session_id)
    if conn is not None and not conn.is_closed():
        return tool_def
    return None


# ── 工具注册清单 ─────────────────────────────────────────────

# 里程碑存储已迁移到 DB（milestones 表），record_milestone 直接写 DB


async def record_milestone(
    ctx: RunContext[AgentDeps],
    event_type: str,
    content: str,
) -> str:
    """记录关键事件到里程碑系统。

    在对话过程中遇到重要节点时主动调用，帮助保持长期记忆：
    - error: 命令执行失败、用户报告问题
    - solution: 找到解决方案、成功修复
    - decision: 用户改变需求方向、调整目标
    - done: 任务完成

    里程碑独立于消息历史，不受上下文裁剪影响——每轮请求时通过动态 prompt 注入。

    Args:
        event_type: 事件类型，必须是 error/solution/decision/done 之一
        content: 事件简述，一句话说明发生了什么
    """
    from haossh.db import repo_conversation
    conv_id = ctx.deps.conversation_id
    if not conv_id:
        return "警告：无对话ID，里程碑未记录"
    await repo_conversation.append_milestone(conv_id, event_type, content)
    logger.info("里程碑已记录 conv_id=%s type=%s content=%s", conv_id[:12], event_type, content[:60])
    return f"已记录里程碑: [{event_type}] {content}"


tools: list[Tool] = [
    Tool(
        execute_command,
        prepare=require_connection,
        max_retries=2,
        timeout=300.0,
    ),
    Tool(
        read_file,
        prepare=require_connection,
        max_retries=1,
    ),
    Tool(
        write_file,
        prepare=require_connection,
        max_retries=1,
    ),
    Tool(
        list_directory,
        prepare=require_connection,
        max_retries=1,
    ),
    Tool(
        get_environment,
        prepare=require_connection,
        max_retries=1,
        timeout=30.0,
    ),
    Tool(
        run_background,
        prepare=require_connection,
        max_retries=1,
        timeout=30.0,
    ),
    Tool(
        check_task,
        prepare=require_connection,
        max_retries=2,
        timeout=30.0,
    ),
    Tool(
        record_milestone,
        max_retries=1,
    ),
]
