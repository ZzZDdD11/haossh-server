"""持久 Shell 验证脚本（非 pytest，直接跑）。

用 asyncssh 在本地起一个临时 SSH server（无需系统 sshd/管理员权限），
验证 PersistentShell 的核心行为：cd 跨调用持久化、并发命令排队、超时恢复。

运行: uv run python tests/test_persistent_shell.py
"""

import asyncio
import sys
from pathlib import Path

import asyncssh

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from haossh.ssh import persistent_shell  # noqa: E402
from haossh.ssh.session import ssh_sessions  # noqa: E402

TEST_PORT = 8765
TEST_USER = "testuser"
TEST_PASSWORD = "testpass"
TEST_CONN_ID = "test-conn-001"
TEST_CONV_ID = "test-conv-001"


class _AuthorizedSSHServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        pass

    def begin_auth(self, username):
        return True  # 需要密码验证

    def password_auth_supported(self):
        return True

    async def validate_password(self, username, password):
        return username == TEST_USER and password == TEST_PASSWORD


async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
    """把 SSH 会话请求代理到本机真实 bash 子进程（让测试用真实 shell 行为）。"""
    command = process.command or "bash --noprofile --norc"
    proc = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def pump_stdin():
        try:
            async for data in process.stdin:
                proc.stdin.write(data.encode())
                await proc.stdin.drain()
        except Exception:
            pass
        finally:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()

    async def pump_out(reader, writer):
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                writer.write(chunk.decode(errors="replace"))
        except Exception:
            pass

    # pump_stdin 会一直等客户端输入，不能用它来判断"命令结束"；
    # 用 proc.wait() 判断本地 bash 进程退出，退出后再收尾其余 pump 任务
    stdin_task = asyncio.create_task(pump_stdin())
    stdout_task = asyncio.create_task(pump_out(proc.stdout, process.stdout))
    stderr_task = asyncio.create_task(pump_out(proc.stderr, process.stderr))

    exit_code = await proc.wait()
    await asyncio.gather(stdout_task, stderr_task)  # 确保剩余输出已转发完
    stdin_task.cancel()
    process.stdout.write_eof()
    process.stderr.write_eof()
    process.exit(exit_code)


async def start_test_server():
    """起一个本地测试用 SSH server，密码认证，会话代理到真实本机 bash。"""
    return await asyncssh.create_server(
        _AuthorizedSSHServer,
        host="127.0.0.1",
        port=TEST_PORT,
        server_host_keys=[asyncssh.generate_private_key("ssh-rsa")],
        process_factory=_handle_client,
    )


async def connect_test_client():
    conn = await asyncssh.connect(
        host="127.0.0.1", port=TEST_PORT,
        username=TEST_USER, password=TEST_PASSWORD,
        known_hosts=None,
    )
    ssh_sessions[TEST_CONN_ID] = conn
    return conn


async def test_cd_persists_across_calls():
    """核心验证：cd 后再执行 pwd，应该看到 cd 后的目录（而非退回登录目录）。"""
    print("=" * 60)
    print("测试1: cd 跨调用持久化")
    print("=" * 60)

    shell, was_rebuilt = await persistent_shell.get_shell(TEST_CONV_ID, TEST_CONN_ID)
    assert was_rebuilt is False, "首次创建不应算 rebuilt"

    stdout, stderr, exit_status, cwd = await shell.run("cd /tmp && pwd", timeout=10)
    print(f"  cd /tmp && pwd -> stdout={stdout!r} exit={exit_status} cwd={cwd!r}")
    assert exit_status == 0
    assert "/tmp" in stdout
    assert cwd == "/tmp" or cwd.rstrip("/") == "/tmp", f"观测到的 cwd 应为 /tmp，实际={cwd!r}"

    # 关键验证：不带 cd，第二次调用的 pwd 应该仍然是 /tmp（说明 shell 状态延续了）
    stdout2, stderr2, exit_status2, cwd2 = await shell.run("pwd", timeout=10)
    print(f"  pwd (第二次调用) -> stdout={stdout2!r} exit={exit_status2} cwd={cwd2!r}")
    assert exit_status2 == 0
    assert "/tmp" in stdout2, f"期望 /tmp，实际={stdout2!r}（说明 cd 状态没有持久化！）"

    print("  [PASS] cd 状态跨调用持久化成功\n")


async def test_stderr_separated():
    """验证 stdout/stderr 分离正确。"""
    print("=" * 60)
    print("测试2: stdout/stderr 分离")
    print("=" * 60)

    shell, _ = await persistent_shell.get_shell(TEST_CONV_ID, TEST_CONN_ID)
    stdout, stderr, exit_status, cwd = await shell.run(
        "echo 'to-stdout' && echo 'to-stderr' >&2", timeout=10
    )
    print(f"  stdout={stdout!r} stderr={stderr!r} exit={exit_status}")
    assert "to-stdout" in stdout
    assert "to-stderr" in stderr
    assert "to-stderr" not in stdout
    print("  [PASS] stdout/stderr 正确分离\n")


async def test_exit_code():
    """验证退出码正确解析。"""
    print("=" * 60)
    print("测试3: 退出码解析")
    print("=" * 60)

    shell, _ = await persistent_shell.get_shell(TEST_CONV_ID, TEST_CONN_ID)
    _, _, exit_status, _ = await shell.run("exit_code_test() { return 7; }; exit_code_test", timeout=10)
    print(f"  自定义函数 return 7 -> exit={exit_status}")
    assert exit_status == 7
    print("  [PASS] 退出码正确解析\n")


async def test_sequential_calls_no_interleaving():
    """验证连续多次调用不会输出交叉（锁生效）。"""
    print("=" * 60)
    print("测试4: 并发调用串行化（无交叉）")
    print("=" * 60)

    shell, _ = await persistent_shell.get_shell(TEST_CONV_ID, TEST_CONN_ID)

    async def run_one(n):
        stdout, _, _, _ = await shell.run(f"echo 'line-{n}'", timeout=10)
        return stdout.strip()

    results = await asyncio.gather(*[run_one(i) for i in range(5)])
    print(f"  并发5次调用结果: {results}")
    for i, r in enumerate(results):
        assert r == f"line-{i}", f"输出交叉！期望 line-{i}，实际={r!r}"
    print("  [PASS] 无输出交叉\n")


async def test_rebuild_after_kill():
    """验证 shell 进程被杀死后，下次调用能自动重建并标记 was_rebuilt=True。"""
    print("=" * 60)
    print("测试5: shell 崩溃后自动重建")
    print("=" * 60)

    shell, _ = await persistent_shell.get_shell(TEST_CONV_ID, TEST_CONN_ID)
    # 模拟 shell 进程被杀死：直接执行 exit，让底层 bash 进程退出
    try:
        await shell.run("exit 0", timeout=5)
    except ConnectionError:
        pass  # exit 后读取会遇到 EOF，预期行为

    # 下次调用应该自动重建，并且 was_rebuilt=True
    new_shell, was_rebuilt = await persistent_shell.get_shell(TEST_CONV_ID, TEST_CONN_ID)
    print(f"  was_rebuilt={was_rebuilt}")
    assert was_rebuilt is True, "shell 失效后重建应标记 was_rebuilt=True"

    stdout, _, exit_status, _ = await new_shell.run("pwd", timeout=10)
    print(f"  重建后 pwd -> {stdout!r} exit={exit_status}")
    assert exit_status == 0
    print("  [PASS] 崩溃重建 + 标记正确\n")


async def test_workspace_restore_after_rebuild():
    """核心验证：崩溃重建时，若传入 known_workspace，shell 应自动 cd 回该目录。"""
    print("=" * 60)
    print("测试6: 工作区观测 + 崩溃重建后自动恢复 cwd")
    print("=" * 60)

    conv_id = "test-conv-workspace"
    shell, _ = await persistent_shell.get_shell(conv_id, TEST_CONN_ID)

    # 1. cd 到一个目录，观测 cwd
    _, _, _, cwd = await shell.run("cd /var && pwd", timeout=10)
    print(f"  cd /var 后观测 cwd={cwd!r}")
    assert cwd == "/var"

    # 2. 模拟崩溃
    try:
        await shell.run("exit 0", timeout=5)
    except ConnectionError:
        pass

    # 3. 重建时传入 known_workspace=/var，应该自动 cd 回去
    new_shell, was_rebuilt = await persistent_shell.get_shell(
        conv_id, TEST_CONN_ID, known_workspace="/var"
    )
    assert was_rebuilt is True

    stdout, _, exit_status, cwd_after = await new_shell.run("pwd", timeout=10)
    print(f"  重建后 pwd -> {stdout!r} cwd={cwd_after!r}")
    assert "/var" in stdout, f"重建后应自动恢复到 /var，实际={stdout!r}"

    await persistent_shell.close_shell(conv_id)
    print("  [PASS] 工作区观测 + 崩溃后自动恢复成功\n")


async def main():
    server = await start_test_server()
    print(f"测试 SSH server 已启动 127.0.0.1:{TEST_PORT}\n")
    try:
        await connect_test_client()
        await test_cd_persists_across_calls()
        await test_stderr_separated()
        await test_exit_code()
        await test_sequential_calls_no_interleaving()
        await test_rebuild_after_kill()
        await test_workspace_restore_after_rebuild()
        print("=" * 60)
        print("全部通过 ✅")
        print("=" * 60)
    finally:
        await persistent_shell.close_shell(TEST_CONV_ID)
        conn = ssh_sessions.pop(TEST_CONN_ID, None)
        if conn:
            conn.close()
        server.close()


if __name__ == "__main__":
    asyncio.run(main())
