"""MCP Client：读取 mcp.json，连接 MCP Server 并把其工具动态注册到 YAgent。

设计要点：

* **同步/异步桥接**：MCP Python SDK 是全异步的，而 YAgent 的工具调用链是同步的。
  这里在后台线程里跑一个独立 asyncio 事件循环，每个 Server 由**一个常驻协程**
  独占管理（连接、初始化、list_tools、call_tool 都在同一个 Task 内完成），
  避免 anyio cancel scope 跨 Task 退出的问题。主线程通过
  ``concurrent.futures.Future`` 拿结果。
* **故障隔离**：单个 Server 连接失败只标记该 Server 不可用，其余 Server 与
  原生工具不受影响；单次调用异常也只会以文本形式返回给模型。
"""
from __future__ import annotations

import asyncio
import concurrent.futures as cf
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.models import ToolSpec
from core.ports import Tool

try:  # pragma: no cover - 依赖缺失时给出可读提示
    from mcp.client.client import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client

    _MCP_AVAILABLE = True
    _MCP_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    Client = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False
    _MCP_IMPORT_ERROR = str(exc)


# ---------- 配置 ----------

@dataclass
class MCPServerConfig:
    """mcp.json 中的一个 Server 条目。"""

    name: str
    transport: str = "stdio"          # stdio | http
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.name}({self.transport})"


def load_mcp_config(path: Path) -> list[MCPServerConfig]:
    """解析 mcp.json，返回 Server 列表；文件缺失或格式错误时返回空列表。

    兼容三种写法：
      * ``{"mcpServers": {...}}``（Claude/Trae 风格，推荐）
      * ``{"servers": {...}}``
      * ``{"servers": [ {...}, ... ]}``（数组形式，元素需自带 name）
    """
    if not path.is_file():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[mcp] 解析 {path.name} 失败：{exc}")
        return []

    block: Any = None
    if isinstance(raw, dict):
        block = raw.get("mcpServers") or raw.get("servers") or raw.get("mcp")

    servers: list[MCPServerConfig] = []
    if isinstance(block, dict):
        items = list(block.items())
    elif isinstance(block, list):
        items = [(str(entry.get("name") or f"server{i}"), entry) for i, entry in enumerate(block)]
    else:
        print(f"[mcp] {path.name} 缺少 mcpServers 配置块")
        return []

    for name, entry in items:
        if not isinstance(entry, dict):
            print(f"[mcp] 跳过 {name}：配置项不是对象")
            continue

        url = str(entry.get("url") or entry.get("serverUrl") or "").strip()
        command = str(entry.get("command") or "").strip()
        transport = str(entry.get("type") or entry.get("transport") or "").strip().lower()

        if not transport:
            transport = "http" if url else "stdio"

        if transport in ("http", "streamable-http", "streamable_http"):
            transport = "http"
            if not url:
                print(f"[mcp] 跳过 {name}：HTTP 传输缺少 url")
                continue
        else:
            transport = "stdio"
            if not command:
                print(f"[mcp] 跳过 {name}：stdio 传输缺少 command")
                continue

        servers.append(
            MCPServerConfig(
                name=str(name),
                transport=transport,
                command=command,
                args=[str(a) for a in entry.get("args") or []],
                env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                cwd=entry.get("cwd"),
                url=url,
                headers={str(k): str(v) for k, v in (entry.get("headers") or {}).items()},
            )
        )

    return servers


# ---------- 工具名规整 ----------

_INVALID_NAME = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_NAME_LEN = 64


def _sanitize_tool_name(raw: str) -> str:
    """把 ``server__tool`` 规整成 OpenAI function name 允许的字符集。"""
    cleaned = _INVALID_NAME.sub("_", raw).strip("_")
    if not cleaned:
        cleaned = "mcp_tool"
    return cleaned[:_MAX_NAME_LEN]


# ---------- 结果格式化 ----------

def format_call_result(result: Any) -> str:
    """把 MCP ``CallToolResult`` 转成给模型看的文本。"""
    chunks: list[str] = []

    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)
            continue
        kind = getattr(item, "type", "")
        if kind in ("image", "audio"):
            data = getattr(item, "data", "") or ""
            mime = getattr(item, "mimeType", "") or getattr(item, "mime_type", "") or "?"
            chunks.append(f"[{kind}: {mime}, {len(data)} base64 字符]")
        elif kind == "resource":
            chunks.append(f"[resource: {getattr(item, 'resource', item)}]")
        else:
            chunks.append(str(item))

    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    # 只有没有文本内容时才输出结构化数据，避免与文本重复
    if structured is not None and not chunks:
        try:
            chunks.append(json.dumps(structured, ensure_ascii=False))
        except TypeError:
            chunks.append(str(structured))

    body = "\n".join(part for part in chunks if part)
    if not body:
        body = "(空结果)"

    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    return f"[MCP 工具返回错误] {body}" if is_error else body


# ---------- 会话管理 ----------

@dataclass
class _Request:
    tool: str
    arguments: dict[str, Any]
    future: cf.Future


class MCPManager:
    """管理所有 MCP Server 的连接、工具发现与调用。"""

    def __init__(
        self,
        config_path: Path,
        *,
        connect_timeout: float = 30.0,
        call_timeout: float = 120.0,
    ) -> None:
        self._config_path = config_path
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout

        self._servers: list[MCPServerConfig] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()

        self._queues: dict[str, asyncio.Queue] = {}
        self._ready: dict[str, threading.Event] = {}
        self._tasks: list[asyncio.Task] = []

        self._tools: dict[str, list[Any]] = {}       # server -> mcp Tool 列表
        self._errors: dict[str, str] = {}            # server -> 连接失败原因
        self._mapping: dict[str, tuple[str, str]] = {}  # 暴露名 -> (server, 原始工具名)

    # ---- 生命周期 ----

    def start(self, *, block: bool = True) -> threading.Event:
        """解析配置、启动后台事件循环并连接所有 Server。

        Args:
            block: True = 阻塞至发现完成（老行为）；False = 立刻返回，
                   调用方拿到返回的 ready Event，等它 set() 后再 build_tools。

        Returns:
            threading.Event — 所有 Server 连接/失败都完成的信号。
        """
        all_ready = threading.Event()
        self._servers = load_mcp_config(self._config_path)
        if not self._servers:
            print(f"[mcp] 未在 {self._config_path} 找到可用的 MCP Server 配置")
            all_ready.set()
            return all_ready

        if not _MCP_AVAILABLE:
            print(f"[mcp] MCP SDK 不可用（{_MCP_IMPORT_ERROR}），已跳过 MCP 工具")
            all_ready.set()
            return all_ready

        self._thread = threading.Thread(target=self._run_loop, name="mcp-loop", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=10) or self._loop is None:
            print("[mcp] 后台事件循环启动失败，已跳过 MCP 工具")
            all_ready.set()
            return all_ready

        for cfg in self._servers:
            self._queues[cfg.name] = asyncio.Queue()
            self._ready[cfg.name] = threading.Event()
            asyncio.run_coroutine_threadsafe(self._serve(cfg), self._loop)

        def _wait_all() -> None:
            for cfg in self._servers:
                if not self._ready[cfg.name].wait(self._connect_timeout):
                    self._errors[cfg.name] = f"连接超时（{self._connect_timeout:.0f}s）"
                    print(f"[mcp] {cfg.label} 连接超时，已跳过")
            all_ready.set()

        if block:
            _wait_all()
        else:
            threading.Thread(target=_wait_all, name="mcp-wait", daemon=True).start()

        return all_ready

    def stop(self) -> None:
        """关闭所有连接并停止后台线程。"""
        if self._loop is None:
            return

        async def _shutdown() -> None:
            for queue in self._queues.values():
                queue.put_nowait(None)
            await asyncio.sleep(0.1)
            for task in self._tasks:
                task.cancel()
            await asyncio.sleep(0.05)

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=5)
        except Exception:
            pass

        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)

        self._loop = None
        self._thread = None
        print("[mcp] 已关闭全部 MCP 连接")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    # ---- 每个 Server 的常驻协程 ----

    def _build_transport(self, cfg: MCPServerConfig):
        if cfg.transport == "stdio":
            env = dict(os.environ)
            env.update(cfg.env)
            params = StdioServerParameters(
                command=cfg.command,
                args=list(cfg.args),
                env=env,
                cwd=cfg.cwd,
            )
            return stdio_client(params)
        return streamable_http_client(cfg.url)

    async def _serve(self, cfg: MCPServerConfig) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.append(task)

        queue = self._queues[cfg.name]
        try:
            transport = self._build_transport(cfg)
            async with Client(transport, read_timeout_seconds=self._call_timeout) as client:
                listed = await client.list_tools()
                self._tools[cfg.name] = list(getattr(listed, "tools", []) or [])
                print(
                    f"[mcp] {cfg.label} 已连接，发现 {len(self._tools[cfg.name])} 个工具"
                )
                self._ready[cfg.name].set()
                await self._pump(cfg, client, queue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._errors[cfg.name] = str(exc)
            print(f"[mcp] {cfg.label} 连接失败：{exc}")
        finally:
            self._ready[cfg.name].set()

    async def _pump(self, cfg: MCPServerConfig, client: Any, queue: asyncio.Queue) -> None:
        while True:
            req = await queue.get()
            if req is None:
                return
            try:
                result = await client.call_tool(req.tool, req.arguments)
            except Exception as exc:
                if not req.future.done():
                    req.future.set_result(exc)
                continue
            if not req.future.done():
                req.future.set_result(result)

    # ---- 对外接口 ----

    @property
    def available(self) -> bool:
        return bool(self._tools)

    def status(self) -> str:
        if not self._servers:
            return "未配置"
        parts = []
        for cfg in self._servers:
            if cfg.name in self._errors:
                parts.append(f"{cfg.name}=失败")
            elif cfg.name in self._tools:
                parts.append(f"{cfg.name}={len(self._tools[cfg.name])}个工具")
            else:
                parts.append(f"{cfg.name}=未知")
        return ", ".join(parts)

    def status_snapshot(self) -> dict:
        """返回 TUI 侧边栏渲染用的结构化快照。

        {
            "servers": [{"name": "pet-hospital", "status": "connected", "tool_count": 7}],
            "tools": ["pet_hospital__list_pets", ...],
            "total_count": 7,
        }
        """
        servers_out: list[dict] = []
        all_tools: list[str] = []
        for cfg in self._servers:
            if cfg.name in self._errors:
                st = "error"
                count = 0
            elif cfg.name in self._tools:
                st = "connected"
                count = len(self._tools[cfg.name])
            else:
                st = "connecting"
                count = 0
            servers_out.append({"name": cfg.name, "status": st, "tool_count": count})
        for name in self._mapping:
            all_tools.append(name)
        return {
            "servers": servers_out,
            "tools": sorted(all_tools),
            "total_count": len(all_tools),
        }

    def build_tools(self, reserved: set[str] | None = None) -> list["MCPTool"]:
        """把已发现的 MCP 工具包装成 YAgent 工具，并处理重名。"""
        used: set[str] = set(reserved or ())
        tools: list[MCPTool] = []

        for cfg in self._servers:
            for mcp_tool in self._tools.get(cfg.name, []):
                base = _sanitize_tool_name(f"{cfg.name}__{mcp_tool.name}")
                name = base
                suffix = 2
                while name in used:
                    tail = f"_{suffix}"
                    name = base[: _MAX_NAME_LEN - len(tail)] + tail
                    suffix += 1
                used.add(name)

                schema = getattr(mcp_tool, "inputSchema", None) or getattr(
                    mcp_tool, "input_schema", None
                )
                if not isinstance(schema, dict) or "type" not in schema:
                    schema = {"type": "object", "properties": {}}

                spec = ToolSpec(
                    name=name,
                    description=(
                        f"[MCP:{cfg.name}] "
                        f"{getattr(mcp_tool, 'description', '') or mcp_tool.name}"
                    ),
                    parameters=schema,
                )
                self._mapping[name] = (cfg.name, mcp_tool.name)
                tools.append(MCPTool(self, name, cfg.name, mcp_tool.name, spec))

        if tools:
            print(f"[mcp] 注册 {len(tools)} 个 MCP 工具：{[t.spec.name for t in tools]}")
        return tools

    def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any], timeout: float | None = None
    ) -> str:
        """同步调用指定 Server 的工具，任何异常都转成文本返回。"""
        if self._loop is None:
            return f"[MCP:{server}] 客户端未启动"
        if self._errors.get(server):
            return f"[MCP:{server}] 服务不可用：{self._errors[server]}"
        queue = self._queues.get(server)
        if queue is None:
            return f"[MCP:{server}] 未知服务"

        future: cf.Future = cf.Future()
        try:
            self._loop.call_soon_threadsafe(queue.put_nowait, _Request(tool, arguments, future))
        except RuntimeError:
            return f"[MCP:{server}] 事件循环已关闭"

        try:
            outcome = future.result(timeout=timeout or self._call_timeout)
        except cf.TimeoutError:
            return f"[MCP:{server}] 调用超时：{tool}"
        except Exception as exc:
            return f"[MCP:{server}] 调用失败：{exc}"

        if isinstance(outcome, Exception):
            return f"[MCP:{server}] 调用失败：{outcome}"
        return format_call_result(outcome)


# ---------- 工具适配器 ----------

class MCPTool(Tool):
    """把单个 MCP 工具适配成 YAgent 的 ``Tool``。"""

    def __init__(
        self,
        manager: MCPManager,
        exposed_name: str,
        server: str,
        tool_name: str,
        spec: ToolSpec,
    ) -> None:
        self._manager = manager
        self._spec = spec
        self._server = server
        self._tool_name = tool_name

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def run(self, arguments: str) -> str:
        try:
            parsed = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[MCP:{self._server}] 参数解析失败：{exc}"
        if not isinstance(parsed, dict):
            return f"[MCP:{self._server}] 参数必须是 JSON 对象"
        return self._manager.call_tool(self._server, self._tool_name, parsed)
