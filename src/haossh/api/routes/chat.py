from fastapi import APIRouter
from haossh.agent import agent
from haossh.api.schemas.chat import ChatRequest
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.post("/chat_stream")
async def chat_stream(req: ChatRequest):
    async def generator():
        async with agent.run_stream(req.message) as result:
            async for chunk in result.stream_text():
                yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
    )
