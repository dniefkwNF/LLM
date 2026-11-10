"""会话持久化：把整段对话序列化成 json 并落盘，支持 list/load/delete。

归档（context/*.jsonl）保存的是原始消息，供 recallContext 用；
会话（sessions/*.json）保存的是 Conversation + 元数据，供 /resume 用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from .models import Message


@dataclass
class SessionMeta:
    """会话元数据。"""
    session_id: str
    title: str                # 从首条 user 消息截短而来
    created_at: str            # 2026-11-04 15:30:00
    updated_at: str
    message_count: int
    last_user: str


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SessionStore:
    """管理 sessions/*.json。"""

    def __init__(self, project_root: Path) -> None:
        self._dir = project_root / ".agents" / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---------- 保存 ----------

    def save(self, messages: list[Message], session_id: str | None = None) -> SessionMeta:
        """保存当前会话，返回 meta。"""
        sid = session_id or _new_id()
        title = "未命名会话"
        last_user = ""
        for m in messages:
            if m.role == "user":
                last_user = m.content
                title = m.content.strip().split("\n")[0][:40] or title
        meta = SessionMeta(
            session_id=sid,
            title=title,
            created_at=_now_str(),
            updated_at=_now_str(),
            message_count=len(messages),
            last_user=last_user[:60],
        )
        payload = {
            "meta": asdict(meta),
            "messages": [m.to_payload() for m in messages],
        }
        (self._dir / f"{sid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return meta

    # ---------- 查询 ----------

    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for p in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                md = data.get("meta") or {}
                metas.append(SessionMeta(
                    session_id=md.get("session_id", p.stem),
                    title=md.get("title", "(无标题)"),
                    created_at=md.get("created_at", ""),
                    updated_at=md.get("updated_at", ""),
                    message_count=int(md.get("message_count", 0)),
                    last_user=md.get("last_user", ""),
                ))
            except Exception:
                continue
        return metas

    def load(self, session_id: str) -> list[Message] | None:
        path = self._dir / f"{session_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        result: list[Message] = []
        for item in data.get("messages", []):
            result.append(Message(
                role=item.get("role", ""),
                content=item.get("content", ""),
                tool_calls=[ToolCall(**tc) for tc in (item.get("tool_calls") or [])],
                tool_call_id=item.get("tool_call_id", ""),
            ))
        return result

    def delete(self, session_id: str) -> bool:
        path = self._dir / f"{session_id}.json"
        if path.is_file():
            path.unlink()
            return True
        return False
