import logging
from pathlib import Path

from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles

from haossh.api.routes import chat, ssh_connection, ssh_file, ssh_terminal, terminal_binding

logger = logging.getLogger(__name__)

app = FastAPI(
    title="haossh-server",
    version="0.1.0",
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
app.include_router(ssh_terminal.router, prefix="/api/v1")
app.include_router(ssh_connection.router, prefix="/api/v1")
app.include_router(ssh_file.router, prefix="/api/v1")
app.include_router(terminal_binding.router, prefix="/api/v1")

# 调试前端：访问 / 直接打开 index.html（显式 /api 路由优先匹配，不冲突）
_static_dir = Path(__file__).parent.parent / "resources" / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
