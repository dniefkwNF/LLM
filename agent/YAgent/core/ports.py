"""抽象接口：核心层只依赖这些协议。"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .models import ChatEvent, Message, StreamDelta, ToolSpec


@dataclass
class LLMResponse:
    """非流式完整响应。"""
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMClient(Protocol):
    """大模型客户端，可替换为任意后端实现。"""

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Iterable[StreamDelta]:
        ...

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float | None = None,
    ) -> LLMResponse:
        """非流式调用，返回完整响应和 token 使用量。"""
        ...


class Tool(Protocol):
    """可注册的工具。"""

    @property
    def spec(self) -> ToolSpec:
        ...

    def run(self, arguments: str) -> str:
        ...


class EventSink(Protocol):
    """界面层消费核心事件的回调。"""

    def __call__(self, event: ChatEvent) -> None:
        ...
