"""工具注册表。"""
import time

from .models import ToolSpec
from .ports import Tool
from .trace import get_logger

_log = get_logger("tools")


class ToolRegistry:
    """按名称注册与分发工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    @property
    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    @property
    def names(self) -> set[str]:
        """当前已注册的工具名集合。"""
        return set(self._tools)

    def run(self, name: str, arguments: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            _log.warning("unknown tool name=%s", name)
            return f"未知工具：{name}"
        started = time.perf_counter()
        try:
            result = tool.run(arguments)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            _log.exception("tool crash name=%s elapsed_ms=%.1f", name, elapsed)
            raise
        elapsed = (time.perf_counter() - started) * 1000
        _log.info(
            "tool=%s elapsed_ms=%.1f args_chars=%d result_chars=%d",
            name, elapsed, len(arguments or ""), len(result or ""),
        )
        return result
