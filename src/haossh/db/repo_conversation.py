"""对话会话与消息的数据访问层（repo）。

设计要点：
- 对话 CRUD：与连接解耦，connection_id 可空
- 消息读写：用 pydantic TypeAdapter 序列化/反序列化 ModelMessage
  - 写：每条消息 model_dump_json 后存一行，seq 递增
  - 读：按 seq 排序，逐行 validate_json 还原成 list[ModelMessage]
- 删除对话时级联删除其消息（SQLite 默认不强制外键，手动删）
"""

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlalchemy import func
from sqlmodel import delete, select

from haossh.db import session_maker
from haossh.db.models import Conversation, Message, Milestone, _now_iso

# 单条 ModelMessage 的序列化器（Union 类型，TypeAdapter 能自动识别子类型）
_message_adapter = TypeAdapter(ModelMessage)


# ===== 对话 CRUD =====

async def create_conversation(conv: Conversation) -> Conversation:
    """创建对话。"""
    async with session_maker() as session:
        session.add(conv)
        await session.commit()
        await session.refresh(conv)
        return conv


async def get_conversation(conv_id: str) -> Conversation | None:
    """查单个对话。"""
    async with session_maker() as session:
        return await session.get(Conversation, conv_id)


async def list_conversations(user_id: str) -> list[Conversation]:
    """列某用户的对话，按更新时间倒序。"""
    async with session_maker() as session:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def update_conversation(conv: Conversation) -> Conversation:
    """更新对话（如改 connection_id / title）。"""
    async with session_maker() as session:
        merged = await session.merge(conv)
        await session.commit()
        await session.refresh(merged)
        return merged


async def update_conversation_status(conv_id: str, status: str, task_summary: str | None = None) -> None:
    """更新对话状态（active/completed）。"""
    async with session_maker() as session:
        conv = await session.get(Conversation, conv_id)
        if not conv:
            return
        conv.status = status
        if task_summary:
            conv.task_summary = task_summary
        conv.updated_at = _now_iso()
        await session.commit()


async def update_workspace(conv_id: str, path: str) -> None:
    """更新对话的工作区路径（从持久 shell 的真实 cwd 观测得到）。"""
    async with session_maker() as session:
        conv = await session.get(Conversation, conv_id)
        if not conv or conv.workspace_path == path:
            return  # 不存在或未变化，不写库
        conv.workspace_path = path
        conv.updated_at = _now_iso()
        await session.commit()


async def delete_conversation(conv_id: str) -> bool:
    """删除对话及其全部消息（级联）。"""
    async with session_maker() as session:
        conv = await session.get(Conversation, conv_id)
        if conv is None:
            return False
        # 先删关联消息
        await session.execute(delete(Message).where(Message.conversation_id == conv_id))
        await session.delete(conv)
        await session.commit()
        return True


# ===== 消息读写 =====

async def append_messages(conv_id: str, messages: list[ModelMessage]) -> None:
    """把新增消息追加到 messages 表。seq 从当前最大值+1 开始递增。"""
    if not messages:
        return
    async with session_maker() as session:
        # 查当前最大 seq
        stmt = select(func.max(Message.seq)).where(Message.conversation_id == conv_id)
        max_seq = (await session.execute(stmt)).scalar()
        start_seq = (max_seq or -1) + 1
        # 逐条插入
        for i, msg in enumerate(messages):
            row = Message(
                conversation_id=conv_id,
                seq=start_seq + i,
                role=msg.kind,  # "request" / "response"
                content_json=_message_adapter.dump_json(msg).decode("utf-8"),
            )
            session.add(row)
        await session.commit()


async def get_messages(conv_id: str) -> list[ModelMessage]:
    """读取对话的全部消息，按 seq 排序还原成 list[ModelMessage]。"""
    async with session_maker() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.seq)
        )
        rows = (await session.execute(stmt)).scalars().all()
    return [_message_adapter.validate_json(r.content_json) for r in rows]


# ===== 里程碑读写 =====

async def append_milestone(conv_id: str, event_type: str, content: str) -> None:
    """追加一条里程碑。"""
    async with session_maker() as session:
        milestone = Milestone(
            conversation_id=conv_id,
            event_type=event_type,
            content=content,
        )
        session.add(milestone)
        await session.commit()


async def get_milestones(conv_id: str, limit: int = 20) -> list[Milestone]:
    """获取对话的里程碑，按时间正序（最早的在前）。"""
    async with session_maker() as session:
        stmt = (
            select(Milestone)
            .where(Milestone.conversation_id == conv_id)
            .order_by(Milestone.id.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return list(reversed(rows))  # 反转为时间正序
