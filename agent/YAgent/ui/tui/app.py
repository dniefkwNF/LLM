"""Textual 界面：只负责渲染 ChatEvent，不含对话逻辑。"""
from __future__ import annotations

import sys
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Footer, Header, Input, ListItem, ListView, Static,
)

from core.engine import ChatEngine
from core.models import ChatEvent, EventType
from core.session_store import SessionMeta


def _read_clipboard() -> str:
    try:
        import tkinter as tk
        root = tk.Tk(); root.withdraw()
        try:
            text = root.clipboard_get()
            if text and sys.platform.startswith("win"):
                text = text.replace("\r\n", "\n")
            return text
        finally:
            root.destroy()
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes
            CF_UNICODETEXT = 13; GMEM_MOVEABLE = 0x0002
            OpenClipboard = ctypes.windll.user32.OpenClipboard
            GetClipboardData = ctypes.windll.user32.GetClipboardData
            CloseClipboard = ctypes.windll.user32.CloseClipboard
            GlobalLock = ctypes.windll.kernel32.GlobalLock
            GlobalUnlock = ctypes.windll.kernel32.GlobalUnlock
            GlobalFree = ctypes.windll.kernel32.GlobalFree
            if not OpenClipboard(0): return ""
            try:
                h = GetClipboardData(CF_UNICODETEXT)
                if not h: return ""
                p = GlobalLock(h)
                if not p: return ""
                try:
                    buf = ctypes.create_unicode_buffer(0xFFFF)
                    ctypes.memmove(buf, p, wintypes.DWORD(0xFFFF))
                    return (buf.value or "").replace("\r\n", "\n")
                finally:
                    GlobalUnlock(h); GlobalFree(h)
            finally:
                CloseClipboard()
        except Exception:
            pass
    return ""


# ---------- /resume 会话选择对话框 ----------

class ResumeScreen(ModalScreen[str | None]):
    """弹出历史会话列表，上下键移动，回车选中，ESC 取消。"""

    CSS = """
    ResumeScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: 24;
        border: round $accent;
        background: $surface;
    }
    #title {
        height: 1;
        text-style: bold;
        padding: 0 1;
        background: $surface-highlight;
    }
    #hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ListView {
        height: 1fr;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, sessions: list[SessionMeta]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Static("恢复会话 （↑↓ 选择，回车确认，ESC 取消）", id="title")
            with VerticalScroll(id="list_scroll"):
                with Horizontal():
                    items = [
                        ListItem(
                            Text(f"  {s.title[:40]:<40}  {s.updated_at}  ({s.message_count}条)",
                                 style="none", justify="left"),
                            id=s.session_id,
                        )
                        for s in self._sessions
                    ]
                    if not items:
                        items = [ListItem(Text("  （暂无保存的会话）", style="dim"))]
                    yield ListView(*items, id="resume_list")
            yield Static("提示：输入 /delete <session_id> 可删除，/save 保存当前会话", id="hint")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.id if event.item else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------- 补全提示面板（轻量：Input 下方） ----------

class YAgentApp(App):
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("ctrl+v", "paste", "粘贴剪贴板"),
        Binding("escape", "clear_completion", "隐藏补全面板"),
    ]

    SLASH_COMMANDS: list[tuple[str, str]] = [
        ("/help",        "显示所有命令"),
        ("/compact",     "手动压缩上下文，生成归档"),
        ("/new",         "新建会话（保留 system prompt）"),
        ("/save",        "保存当前会话到 .agents/sessions/"),
        ("/resume",      "恢复历史会话（弹出列表）"),
        ("/delete",      "删除一个历史会话（/delete <session_id>）"),
        ("/clear",       "清空聊天界面显示（不影响上下文）"),
        ("/mcp",         "刷新侧边栏的 MCP 工具状态"),
        ("/tools",       "列出当前可用的所有工具"),
        ("/context",     "查看当前上下文使用情况"),
        ("/quit",        "退出 YAgent"),
    ]

    def __init__(self, engine: ChatEngine) -> None:
        super().__init__()
        self._engine = engine
        self._busy = False
        self._busy_compact = False
        self._stream: Static | None = None
        self._stream_text = ""
        self._mcp_ready_event = None  # main.py 注入
        self._mcp_attached = False

    # ---------- 布局 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            # 左侧聊天区
            with Container(id="chat_area"):
                with VerticalScroll(id="chat"):
                    yield Static(
                        Text("YAgent 已就绪，输入消息开始对话。\n"
                             "斜杠命令：/help /compact /new /save /resume /quit",
                             style="dim"),
                        classes="hint",
                    )
                yield Static(id="ctx_status", classes="ctx-status")
                yield Static(id="completion", classes="completion")  # 补全面板
                yield Input(
                    placeholder="输入消息，回车发送（Ctrl+C 退出，Ctrl+V 粘贴）。输入 / 触发命令补全",
                    id="prompt",
                )
            # 右侧 MCP + Sessions 侧边栏
            with Container(id="sidebar"):
                yield Static("⚡ MCP 工具", classes="side-title")
                with VerticalScroll(id="mcp_panel"):
                    yield Static(id="mcp_content")
                yield Static("📁 历史会话", classes="side-title")
                with VerticalScroll(id="sessions_panel"):
                    yield Static(id="sessions_content")
        yield Footer()

    # ---------- 生命周期 ----------

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._refresh_ctx_status()
        self._refresh_sidebar()
        # 定时刷新
        self.set_interval(5.0, self._refresh_ctx_status)
        self.set_interval(10.0, self._refresh_sidebar)
        # MCP 异步加载：等后台连好后 attach 并刷新侧边栏
        if self._mcp_ready_event is not None:
            self._attach_mcp_when_ready()

    @work(thread=True)
    def _attach_mcp_when_ready(self) -> None:
        """后台等 MCP 连接完成，然后 attach 工具并刷新 TUI。"""
        try:
            if self._mcp_ready_event.wait(timeout=60):
                count = self._engine.attach_mcp_tools()
                self._mcp_attached = True

                def _done() -> None:
                    self._refresh_sidebar()
                    if count > 0:
                        self._add_block(
                            "系统",
                            f"✅ MCP 已就绪：注册 {count} 个新工具。",
                            "hint",
                        )

                self.call_from_thread(_done)
        except Exception as exc:
            def _fail() -> None:
                self._add_block("系统", f"⚠️ MCP 异步加载出错：{exc}", "error")
            self.call_from_thread(_fail)

    # ---------- 快捷键 ----------

    def action_paste(self) -> None:
        text = _read_clipboard()
        if not text:
            return
        inp = self.query_one("#prompt", Input)
        inp.value = inp.value + text
        inp.cursor_position = len(inp.value)

    def action_clear_completion(self) -> None:
        self.query_one("#completion", Static).update(Text(""))

    # ---------- 侧边栏刷新 ----------

    def _refresh_sidebar(self) -> None:
        # MCP 部分
        mcp = self._engine.mcp_status
        mcp_text = Text()
        servers = mcp.get("servers", []) if isinstance(mcp, dict) else []
        tools = mcp.get("tools", []) if isinstance(mcp, dict) else []
        if not servers:
            mcp_text.append("未配置 MCP\n", style="dim")
        else:
            for s in servers:
                name = s.get("name", "?") if isinstance(s, dict) else str(s)
                status = s.get("status", "?") if isinstance(s, dict) else "?"
                if status in ("connected", "ok", "成功"):
                    mcp_text.append(f"● {name}\n", style="green")
                elif status in ("error", "失败", "disconnected"):
                    mcp_text.append(f"✗ {name}\n", style="red")
                else:
                    mcp_text.append(f"○ {name}\n", style="yellow")
        if tools:
            for t in tools:
                tname = t if isinstance(t, str) else t.get("name", str(t))
                mcp_text.append(f"    └ {tname}\n", style="blue")
        self.query_one("#mcp_content", Static).update(mcp_text)

        # Sessions 部分
        sessions = self._engine.list_sessions()
        if not sessions:
            self.query_one("#sessions_content", Static).update(Text("（未保存会话）\n", style="dim"))
        else:
            lines = Text()
            for s in sessions[:20]:
                lines.append(
                    f"[{s.session_id[-6:]}] {s.title[:22]:<22} {s.message_count:>3}条\n",
                    style="none",
                )
            self.query_one("#sessions_content", Static).update(lines)

    # ---------- 输入提交 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._busy or self._busy_compact:
            return
        event.input.value = ""

        # 斜杠命令
        if text.startswith("/"):
            self._dispatch_slash(text)
            return

        # 超限阻塞
        if self._engine.is_at_limit:
            self._add_block(
                "系统",
                f"⚠️ 上下文已达窗口的 {int(self._engine.usage_ratio * 100)}%，"
                "请先执行 /compact 压缩后再继续。",
                "error",
            )
            self._refresh_ctx_status()
            return

        self._busy = True
        event.input.disabled = True
        self._add_block("你", text, "user")
        self._ask(text)

    # ---------- 输入改变 → 补全面板 ----------

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        completion = self.query_one("#completion", Static)
        if value.startswith("/"):
            prefix = value[1:].lower()
            matches = [(n, d) for (n, d) in self.SLASH_COMMANDS if n[1:].startswith(prefix)]
            if matches:
                lines = "\n".join(f"  {n:<14} {d}" for n, d in matches[:6])
                completion.update(Text(lines, style="dim"))
            else:
                completion.update(Text(""))
        else:
            completion.update(Text(""))

    # ---------- 斜杠命令 dispatch ----------

    def _dispatch_slash(self, text: str) -> None:
        parts = text.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        dispatch = {
            "/help": self._cmd_help,
            "/compact": self._run_compact,
            "/new": self._cmd_new,
            "/save": self._cmd_save,
            "/resume": self._cmd_resume,
            "/delete": self._cmd_delete,
            "/clear": self._cmd_clear,
            "/mcp": self._cmd_mcp,
            "/tools": self._cmd_tools,
            "/context": self._cmd_context,
            "/quit": self._cmd_quit,
        }
        handler = dispatch.get(cmd)
        if handler is None:
            self._add_block("系统", f"未知命令：{cmd}。输入 /help 查看所有命令。", "error")
            return
        handler(args)

    # ---------- 命令实现 ----------

    def _cmd_help(self, _args: list[str]) -> None:
        text = "\n".join(f"  {n:<14} {d}" for (n, d) in self.SLASH_COMMANDS)
        self._add_block("系统", f"可用命令：\n{text}", "hint")

    def _cmd_new(self, _args: list[str]) -> None:
        self._engine.save_session()  # 先自动保存当前会话
        self._engine.new_session()
        self._add_block("系统", "已新建会话（原会话已自动保存）。", "hint")
        self._refresh_ctx_status()
        self._refresh_sidebar()

    def _cmd_save(self, args: list[str]) -> None:
        sid = args[0] if args else None
        new_sid = self._engine.save_session(sid)
        if new_sid:
            self._add_block("系统", f"会话已保存：{new_sid}", "hint")
        else:
            self._add_block("系统", "保存失败（未配置 SessionStore）。", "error")
        self._refresh_sidebar()

    def _cmd_resume(self, _args: list[str]) -> None:
        sessions = self._engine.list_sessions()
        if not sessions:
            self._add_block("系统", "暂无保存的会话。", "hint")
            return
        self.push_screen(ResumeScreen(sessions), callback=self._after_resume)

    def _after_resume(self, session_id: str | None) -> None:
        if session_id is None:
            return
        ok = self._engine.resume_session(session_id)
        if ok:
            self._add_block("系统", f"已恢复会话 {session_id}。", "hint")
            self._refresh_ctx_status()
        else:
            self._add_block("系统", f"恢复失败：{session_id}", "error")

    def _cmd_delete(self, args: list[str]) -> None:
        if not args:
            self._add_block("系统", "用法：/delete <session_id>", "error")
            return
        ok = self._engine.delete_session(args[0])
        self._add_block(
            "系统",
            f"已删除会话 {args[0]}." if ok else f"删除失败（不存在）：{args[0]}",
            "hint" if ok else "error",
        )
        self._refresh_sidebar()

    def _cmd_clear(self, _args: list[str]) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        chat.clear()
        self._add_block("系统", "界面已清空（上下文未变，对话继续）。", "hint")

    def _cmd_mcp(self, _args: list[str]) -> None:
        self._refresh_sidebar()
        self._add_block("系统", f"MCP 状态：{self._engine.mcp_status}", "hint")

    def _cmd_tools(self, _args: list[str]) -> None:
        names = sorted(self._engine._tools.names)
        self._add_block("系统", f"可用工具 ({len(names)})：{', '.join(names)}", "hint")

    def _cmd_context(self, _args: list[str]) -> None:
        cur_k = self._engine.current_tokens / 1000
        win_k = self._engine.context_window / 1000
        pct = int(self._engine.usage_ratio * 100)
        msgs = self._engine._conversation.message_count
        self._add_block(
            "系统",
            f"上下文：{cur_k:.1f}k / {win_k:.0f}k = {pct}%  消息数：{msgs}",
            "hint",
        )

    def _cmd_quit(self, _args: list[str]) -> None:
        self.exit()

    # ---------- 后台任务：普通对话 ----------

    @work(thread=True)
    def _ask(self, text: str) -> None:
        try:
            self._engine.send(
                text, lambda event: self.call_from_thread(self._on_event, event)
            )
        except Exception as error:
            self.call_from_thread(self._add_block, "错误", str(error), "error")
        finally:
            self.call_from_thread(self._finish)

    def _on_event(self, event: ChatEvent) -> None:
        if event.type == EventType.ASSISTANT_DELTA:
            if self._stream is None:
                self._stream_text = ""
                self._stream = self._add_block("AI", "", "assistant")
            self._stream_text += event.text
            self._stream.update(Text(f"AI: {self._stream_text}"))
            self._scroll()
        elif event.type == EventType.ASSISTANT_END:
            self._stream = None

    def _finish(self) -> None:
        self._busy = False
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()
        self._refresh_ctx_status()

    # ---------- 后台任务：上下文压缩 ----------

    def _run_compact(self, _args: list[str] | None = None) -> None:
        self._busy_compact = True
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True
        self._add_block("系统", "正在执行上下文压缩，请稍候...", "hint")
        self._refresh_ctx_status()
        self._do_compact()

    @work(thread=True)
    def _do_compact(self) -> None:
        try:
            ok, msg = self._engine.compact(
                emit=lambda e: self.call_from_thread(self._on_event, e)
            )
        except Exception as exc:
            ok, msg = False, f"压缩异常：{exc}"

        def _done() -> None:
            self._busy_compact = False
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = False
            prompt.focus()
            if ok:
                self._add_block("系统", msg, "user")
            else:
                self._add_block("系统", f"❌ {msg}", "error")
            self._refresh_ctx_status()
            self._refresh_sidebar()

        self.call_from_thread(_done)

    # ---------- 渲染工具 ----------

    def _add_block(self, label: str, text: str, css_class: str) -> Static:
        block = Static(Text(f"{label}: {text}"), classes=f"block {css_class}")
        self.query_one("#chat", VerticalScroll).mount(block)
        self._scroll()
        return block

    def _scroll(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        self.call_after_refresh(lambda: chat.scroll_end(animate=False))

    # ---------- 上下文状态行 ----------

    def _refresh_ctx_status(self) -> None:
        status = self.query_one("#ctx_status", Static)
        cur = self._engine.current_tokens
        win = self._engine.context_window
        ratio = self._engine.usage_ratio
        pct = int(ratio * 100)
        limit_pct = int(self._engine.limit_ratio * 100)

        cur_k = cur / 1000
        win_k = win / 1000

        win_label = f"{win_k:.0f}k"
        if self._engine.is_default_window:
            win_label += "(默认)"

        parts = [f"上下文: {cur_k:.1f}k / {win_label}", f"{pct}%"]

        if ratio >= self._engine.limit_ratio:
            parts.append(f"⚠️ 已达 {limit_pct}% 阈值，执行 /compact 压缩")
            style = "bold red"
        elif ratio >= self._engine.limit_ratio * 0.9:
            parts.append("接近阈值，建议尽快 /compact")
            style = "yellow"
        else:
            style = "green"

        status.update(Text("  |  ".join(parts), style=style))
