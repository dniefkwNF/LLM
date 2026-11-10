"""对话编排：流式输出、工具调用循环与上下文压缩。"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .context import (
    COMPACT_SYSTEM_PROMPT,
    ContextArchive,
    build_compact_user_prompt,
    estimate_tokens,
    estimate_tokens_text,
    format_timestamp,
)
from .conversation import Conversation
from .models import ChatEvent, EventType, Message, ToolCall
from .ports import EventSink, LLMClient
from .session_store import SessionStore
from .tools import ToolRegistry
from .trace import get_logger

if TYPE_CHECKING:
    # 避免循环 import
    from infra.mcp_client import MCPManager

# 达到窗口的 80% 时告警并阻塞普通输入
_LIMIT_RATIO = 0.80

_log = get_logger("engine")


class ChatEngine:
    """与界面无关的对话引擎。"""

    def __init__(
        self,
        client: LLMClient,
        tools: ToolRegistry,
        conversation: Conversation | None = None,
        system_prompt: str = "",
        context_window: int | None = None,
        archive: ContextArchive | None = None,
        sessions: SessionStore | None = None,
        mcp_manager: "MCPManager | None" = None,
    ) -> None:
        self._client = client
        self._tools = tools
        self._conversation = conversation or Conversation()
        self._is_default_window = context_window is None
        self._context_window = context_window or 128_000
        self._archive = archive
        self._sessions = sessions
        self._mcp_manager = mcp_manager
        self._last_tokens: int | None = None
        self._compacting = False
        self._agents_context: str | None = None
        if system_prompt:
            self._conversation.add(Message("system", content=system_prompt))

    # ---------- 项目级指引（AGENT.md / AGENTS.md） ----------

    def set_agents_context(self, text: str) -> None:
        """记录 AGENT.md 内容并注入当前对话（只注入一次）。"""
        text = (text or "").strip()
        if not text:
            return
        self._agents_context = text
        self._inject_agents_context()

    def _inject_agents_context(self) -> None:
        if not self._agents_context:
            return
        for m in self._conversation.messages:
            if m.role == "system" and m.content == self._agents_context:
                return
        index = self._conversation.prefix_count("system")
        self._conversation.insert(index, Message("system", content=self._agents_context))
        _log.info("agents_context injected chars=%d index=%d", len(self._agents_context), index)

    # ---------- 状态查询 ----------

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def is_default_window(self) -> bool:
        """窗口大小是否未显式配置（用了默认 128k）。"""
        return self._is_default_window

    @property
    def current_tokens(self) -> int:
        msgs = self._conversation.messages
        if self._last_tokens is not None:
            # 优先用真实 usage（如果 chat 调用返回了），否则估算
            # 混合策略：若 conversation 有变化（被 add 了新消息），回退到估算
            estimated = estimate_tokens(msgs)
            # 如果 last_tokens 明显偏离估算（超过 2 倍），说明是很久以前的值，刷新
            if self._last_tokens <= estimated * 1.2:
                return self._last_tokens
        return estimate_tokens(msgs)

    @property
    def usage_ratio(self) -> float:
        return self.current_tokens / self._context_window if self._context_window else 0

    @property
    def is_at_limit(self) -> bool:
        return self.usage_ratio >= _LIMIT_RATIO

    @property
    def limit_ratio(self) -> float:
        return _LIMIT_RATIO

    @property
    def is_compacting(self) -> bool:
        """压缩任务进行中（锁）。此状态下禁止 send() 和再次 compact()。"""
        return self._compacting

    # ---------- 对话主流程 ----------

    def send(self, text: str, emit: EventSink, max_tool_rounds: int = 20) -> str:
        """发送用户消息，流式回调事件，返回最终回复。

        Args:
            text: 用户输入。
            emit: 事件回调。
            max_tool_rounds: 单次对话内允许的最大工具调用轮次（防止无限循环）；
                超过后返回截断提示，避免阻塞模型。默认 20。
        """
        if self._compacting:
            raise RuntimeError("压缩进行中，禁止发送新消息。")
        _log.info("send start chars=%d messages=%d", len(text), len(self._conversation.messages))
        self._conversation.add(Message("user", content=text))
        self._last_tokens = None
        tool_rounds = 0
        while True:
            content, calls = self._round(emit)
            emit(ChatEvent(EventType.ASSISTANT_END))

            if not calls:
                self._conversation.add(Message("assistant", content=content))
                self._last_tokens = None
                _log.info("send end chars=%d tool_rounds=%d", len(content), tool_rounds)
                return content

            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                truncated = content + (
                    f"\n\n[安全保护：工具调用轮次已达上限 {max_tool_rounds}，"
                    "本轮对话已截断。请用 '/compact' 压缩上下文后重试，"
                    "或检查模型是否陷入工具调用循环。]"
                )
                self._conversation.add(Message("assistant", content=truncated))
                self._last_tokens = None
                return truncated

            self._conversation.add(
                Message("assistant", content=content, tool_calls=calls)
            )
            for call in calls:
                emit(ChatEvent(EventType.TOOL_START, tool=call.name))
                _log.info("tool_call name=%s round=%d", call.name, tool_rounds)
                result = self._tools.run(call.name, call.arguments)
                emit(ChatEvent(EventType.TOOL_END, tool=call.name, text=result))
                self._conversation.add(
                    Message("tool", content=result, tool_call_id=call.id)
                )
            self._last_tokens = None

    def _round(self, emit: EventSink) -> tuple[str, list[ToolCall]]:
        """发起一次流式请求，汇总文本与工具调用。"""
        content = ""
        pending: dict[int, ToolCall] = {}

        for delta in self._client.stream(
            self._conversation.messages, self._tools.specs
        ):
            if delta.content:
                content += delta.content
                emit(ChatEvent(EventType.ASSISTANT_DELTA, text=delta.content))
            for part in delta.tool_calls:
                call = pending.setdefault(part.index, ToolCall(id="", name=""))
                if part.id:
                    call.id = part.id
                if part.name:
                    call.name = part.name
                call.arguments += part.arguments

        return content, list(pending.values())

    # ---------- 上下文压缩 ----------

    def compact(self, emit: EventSink | None = None) -> tuple[bool, str]:
        """执行手动上下文压缩。

        返回 (是否成功, 描述文本)。失败时保留原上下文。
        """
        if self._compacting:
            return False, "压缩已在进行中，请等待完成。"
        if self._archive is None:
            return False, "未配置 ContextArchive，无法执行压缩。"

        self._compacting = True
        try:
            return self._do_compact(emit)
        finally:
            self._compacting = False

    def _do_compact(self, emit: EventSink | None = None) -> tuple[bool, str]:
        msgs = self._conversation.messages
        # 1. 归档原始上下文
        try:
            archive_id = self._archive.save(msgs)
        except Exception as exc:
            return False, f"归档保存失败，压缩中止：{exc}"

        keep_prefix = self._conversation.prefix_count("system")

        # 2. 构造压缩请求（只能调用 LLM，不能用工具）
        compact_messages = [
            Message("system", content=COMPACT_SYSTEM_PROMPT),
            Message("user", content=build_compact_user_prompt(msgs)),
        ]

        if emit:
            emit(ChatEvent(EventType.ASSISTANT_DELTA, text=f"\n[compact] 归档已保存 {archive_id}，正在生成摘要..."))

        # 3. 调用 LLM 生成摘要（非流式，禁用 tools）
        try:
            resp = self._client.chat(compact_messages, tools=(), temperature=0.3)
        except Exception as exc:
            return False, f"调用 LLM 压缩失败：{exc}（原始上下文已保留，归档 {archive_id}）"

        summary = (resp.content or "").strip()
        if not summary:
            return False, "LLM 返回空摘要，压缩中止（原始上下文已保留）。"

        # 4. 追加归档说明
        archive_note = (
            f"\n\n---\n\n此前上下文已于 {format_timestamp()} 完成压缩，"
            f"原始上下文保存在编号为 {archive_id} 的归档文件中。"
            f"如需查找历史信息，请使用 recallContext 工具。"
        )

        # 5. 原子替换：保留 system，用摘要替换历史
        new_messages = [Message("assistant", content=summary + archive_note)]
        self._conversation.replace_history(keep_prefix, new_messages)

        # 6. 更新 token 统计（非流式返回了真实值）
        if resp.total_tokens is not None:
            self._last_tokens = resp.total_tokens
        else:
            self._last_tokens = None

        if emit:
            emit(ChatEvent(EventType.ASSISTANT_DELTA, text=f"\n[compact] 完成。归档编号 {archive_id}。"))

        _log.info("compact done archive=%s kept_system=%d", archive_id, keep_prefix)
        return True, f"压缩成功，归档编号 {archive_id}。"

    # ---------- 会话管理 ----------

    def save_session(self, session_id: str | None = None) -> str:
        """保存当前会话，返回 session_id。"""
        if self._sessions is None:
            return ""
        meta = self._sessions.save(self._conversation.messages, session_id=session_id)
        _log.info("session saved id=%s messages=%d", meta.session_id, meta.message_count)
        return meta.session_id

    def list_sessions(self):
        if self._sessions is None:
            return []
        return self._sessions.list()

    def resume_session(self, session_id: str) -> bool:
        """恢复指定会话：替换当前 conversation，并补回项目指引。"""
        if self._sessions is None:
            return False
        msgs = self._sessions.load(session_id)
        if msgs is None:
            return False
        self._conversation = Conversation()
        for m in msgs:
            self._conversation.add(m)
        self._inject_agents_context()
        self._last_tokens = None
        _log.info("session resumed id=%s messages=%d", session_id, len(msgs))
        return True

    def new_session(self) -> None:
        """清空 conversation 开始新会话，保留开头连续的 system prompt。"""
        keep = self._conversation.prefix_count("system")
        prefix = self._conversation.messages[:keep]
        self._conversation = Conversation()
        for m in prefix:
            self._conversation.add(m)
        self._last_tokens = None
        _log.info("new session, kept system messages=%d", keep)

    def delete_session(self, session_id: str) -> bool:
        if self._sessions is None:
            return False
        return self._sessions.delete(session_id)

    # ---------- MCP 状态 ----------

    @property
    def mcp_status(self) -> dict:
        """返回 TUI 侧边栏显示用的 MCP 状态快照。"""
        if self._mcp_manager is None:
            return {"servers": [], "tools": [], "total_count": 0}
        snap = self._mcp_manager.status_snapshot()
        return snap

    def attach_mcp_tools(self) -> int:
        """把 MCP 工具运行时注册进 ToolRegistry，返回新注册数量。

        TUI 启动后，后台 MCP 连好时调用此方法。
        """
        if self._mcp_manager is None:
            return 0
        mcp_tools = self._mcp_manager.build_tools(reserved=set(self._tools.names))
        count = 0
        for mt in mcp_tools:
            self._tools.register(mt)
            count += 1
        return count
