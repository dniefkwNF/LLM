"""对话历史。"""
from .models import Message


class Conversation:
    """维护发送给模型的消息序列。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add(self, message: Message) -> None:
        self._messages.append(message)

    def insert(self, index: int, message: Message) -> None:
        """在指定位置插入消息（用于恢复会话后补回项目指引）。"""
        self._messages.insert(index, message)

    def clear(self) -> None:
        self._messages.clear()

    def replace_history(self, keep_prefix: int, new_messages: list[Message]) -> None:
        """保留前 keep_prefix 条（通常是 system prompt），其余替换为 new_messages。"""
        self._messages = self._messages[:keep_prefix] + new_messages

    def prefix_count(self, role: str = "system") -> int:
        """统计开头连续是某角色的消息数。"""
        count = 0
        for m in self._messages:
            if m.role == role:
                count += 1
            else:
                break
        return count
