from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from haossh.agent import agent
from haossh.agent.deps import AgentDeps
from haossh.api.schemas.chat import ChatRequest

router = APIRouter()


@router.post("/chat_stream")
async def chat_stream(req: ChatRequest):
    deps = AgentDeps(
        session_id=req.session_id,
        terminal_session_id=req.terminal_session_id,
        allow_sudo=True,
    )

    async def generator():
        async with agent.run_stream(req.message, deps=deps) as result:
            async for chunk in result.stream_text():
                yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
    )
