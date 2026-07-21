import logging
from contextlib import asynccontextmanager
from pathlib import Path

# 应用日志配置：必须在其他模块 import 之前，确保所有 logger 都能输出 INFO
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles

from haossh.api.routes import chat, conversation, ssh_connection, ssh_file, ssh_terminal, terminal_binding
from haossh.db import engine, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表，关闭时释放连接池。"""
    await init_db()
    yield
    await engine.dispose()


app = FastAPI(
    title="haossh-server",
    version="0.1.0",
    lifespan=lifespan,
)

router = APIRouter(prefix="/api")


@router.get("/v1/health")
async def health():
    return {
        "code": "0000",
        "info": "成功",
        "data": {"status": "ok", "version": "0.1.0"},
    }


app.include_router(router)
app.include_router(chat.router, prefix="/api/v1")
app.include_router(conversation.router, prefix="/api/v1")
app.include_router(ssh_terminal.router, prefix="/api/v1")
app.include_router(ssh_connection.router, prefix="/api/v1")
app.include_router(ssh_file.router, prefix="/api/v1")
app.include_router(terminal_binding.router, prefix="/api/v1")

# 调试前端：访问 / 直接打开 index.html（显式 /api 路由优先匹配，不冲突）
_static_dir = Path(__file__).parent.parent / "resources" / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
