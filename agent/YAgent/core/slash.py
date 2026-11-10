"""斜杠命令注册与调度。

所有 /xxx 命令都在这里注册；TUI 层只负责解析 "/xxx args..." 字符串并 dispatch。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class SlashCommand:
    name: str          # 如 "compact"
    handler: Callable[[list[str]], str]
    usage: str         # 如 "/compact  手动压缩上下文"
    aliases: tuple[str, ...] = ()


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._cmds: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._cmds[cmd.name] = cmd
        for alias in cmd.aliases:
            self._cmds[alias] = cmd

    def get(self, name: str) -> SlashCommand | None:
        return self._cmds.get(name)

    @property
    def all(self) -> list[SlashCommand]:
        seen: set[str] = set()
        out: list[SlashCommand] = []
        for c in self._cmds.values():
            if c.name not in seen:
                out.append(c)
                seen.add(c.name)
        return out

    def complete(self, prefix: str = "") -> list[str]:
        """补全面板用的命令名列表。"""
        return sorted(n for n in self._cmds if n.startswith(prefix))

    @staticmethod
    def parse(text: str) -> tuple[str, list[str]] | None:
        """把 '/new  some title' 解析为 ('new', ['some', 'title'])。"""
        if not text.startswith("/"):
            return None
        parts = text.strip().split()
        if not parts:
            return None
        name = parts[0][1:].lower()
        args = parts[1:]
        return name, args


def build_default_registry() -> SlashCommandRegistry:
    """注册一组默认命令。handler 在 main.py / TUI 层根据当前实例填充。"""
    reg = SlashCommandRegistry()
    # 此处只注册占位 handler，真正逻辑由 TUI 层注入
    return reg
