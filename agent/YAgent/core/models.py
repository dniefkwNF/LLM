"""领域数据模型。"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """工具声明，可转换为 OpenAI tools 参数。"""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCall:
    """模型请求执行的一次工具调用。"""

    id: str
    name: str
    arguments: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass
class Message:
    """一条对话消息。"""

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [call.to_payload() for call in self.tool_calls]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass
class ToolCallDelta:
    """流式返回中的工具调用片段。"""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamDelta:
    """流式返回中的一个增量。"""

    content: str = ""
    tool_calls: list[ToolCallDelta] = field(default_factory=list)


class EventType(str, Enum):
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_END = "assistant_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"


@dataclass(frozen=True)
class ChatEvent:
    """核心层向界面层输出的统一事件。"""

    type: EventType
    text: str = ""
    tool: str = ""
