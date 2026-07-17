import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter

from haossh.config import settings, agent_config, init_settings, load_agent_config

# 以上变量在 lifespan 启动时被重新赋值，import 只为声明全局引用

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 & 关闭钩子"""
    # === 启动：初始化配置 ===
    global settings, agent_config

    settings = init_settings()
    agent_config = load_agent_config()

    logger.info("haossh 启动完成, port=%s, profile=%s", settings.server_port, settings.profile)

    yield  # <--- 应用运行期间停在这里

    # === 关闭 ===
    logger.info("haossh 关闭")


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


@router.get("/v1/query_ai_agent_config_list")
async def query_ai_agent_config_list():
    """查询 Agent 配置列表 —— 从 YAML 读取"""
    result = []
    for _key, table in agent_config.tables.items():
        result.append({
            "agentId": table.agent.agent_id,
            "agentName": table.agent.agent_name,
            "agentDesc": table.agent.agent_desc,
        })
    return {"code": "0000", "info": "成功", "data": result}


app.include_router(router)
