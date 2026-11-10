"""组合根：装配依赖并启动 TUI。"""
import os
import sys
import tkinter as tk
import threading
from pathlib import Path

from core.context import ContextArchive
from core.engine import ChatEngine
from core.session_store import SessionStore
from core.skills import discover_skills
from core.system_prompt import build_skills_system_prompt
from core.trace import setup_logging
from infra.config import AgentConfig, ensure_config_exists, load_agent_config, load_config
from infra.llm import OpenAICompatClient
from infra.mcp_client import MCPManager
from infra.tools import build_registry
from ui.tui.app import YAgentApp

_mcp_manager: MCPManager | None = None
_mcp_ready_event: threading.Event | None = None


_MCP_CONFIG_NAMES = (".mcp.json", "mcp.json")


def _find_mcp_config(project_root: Path) -> Path | None:
    """向上递归查找 .mcp.json / mcp.json（最多 3 层）。

    方便嵌套项目（如 e:\\LLM\\Agent\\YAgent）自动复用上层的 MCP 配置。
    """
    cur = project_root
    for _ in range(3):
        for name in _MCP_CONFIG_NAMES:
            candidate = cur / name
            if candidate.is_file():
                return candidate
        parent = cur.parent
        if parent == cur:
            break  # 到根了
        cur = parent
    return None


def _start_mcp_async(project_root: Path) -> tuple[MCPManager, threading.Event]:
    """启动 MCP Manager（非阻塞：立刻返回，后台连）。"""
    config_path = _find_mcp_config(project_root)
    manager = MCPManager(config_path or (project_root / _MCP_CONFIG_NAMES[0]))
    if config_path is None:
        print(f"[mcp] 未找到 MCP 配置，跳过 MCP 工具加载")
        return manager, threading.Event()  # 立刻 set()

    print(f"[mcp] 读取配置：{config_path}，异步启动中...")
    ready = manager.start(block=False)
    return manager, ready


def _load_project_skills(root: Path):
    skills = discover_skills(root)
    if skills:
        print(f"[skills] 从 {root} 及其一级子目录加载了 {len(skills)} 个项目级 Skill")
    else:
        print(f"[skills] 未发现项目级 Skill")
    return skills


def find_config() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        external = exe_dir / "config.ini"
        if external.exists():
            return external
        bundle_dir = Path(getattr(sys, "_MEIPASS", exe_dir))
        return bundle_dir / "config.ini"
    return Path(__file__).resolve().with_name("config.ini")


def _inject_agents_md(project_root: Path, engine: ChatEngine, cfg: AgentConfig) -> None:
    """读取项目级指引（AGENT.md，兼容 AGENTS.md/agents.md），截断后注入。

    - 文件名、启用开关、长度上限、文件工具根目录均由 config.ini [agent] 控制；
    - 只注入一次；内容超长按 agents_max_chars 截断；
    - 文件缺失不报错，仅打印提示。
    """
    if not cfg.agents_enabled:
        print("[agents] 已在 config.ini 中禁用（agents_enabled=false）")
        return

    names: list[str] = []
    for name in (cfg.agents_file, "AGENT.md", "AGENTS.md", "agents.md"):
        if name and name not in names:
            names.append(name)

    candidates = [project_root / n for n in names]
    candidates += [project_root.parent / n for n in names[:2]]

    for p in candidates:
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[agents] 读取失败 {p}：{exc}")
            continue
        if not content:
            continue

        original_len = len(content)
        if cfg.agents_max_chars and original_len > cfg.agents_max_chars:
            content = content[: cfg.agents_max_chars] + (
                f"\n\n...[已截断：原文 {original_len} 字符，上限 {cfg.agents_max_chars}]"
            )
        engine.set_agents_context(f"# 项目指引（{p.name}）\n\n{content}")
        print(f"[agents] 已注入 {p}（{original_len} 字符，上限 {cfg.agents_max_chars}）")
        return

    print(f"[agents] 未找到项目指引（查找：{', '.join(p.name for p in candidates)}）")


def build_engine(project_root: Path, skip_config_check: bool = False) -> ChatEngine:
    global _mcp_manager, _mcp_ready_event

    setup_logging(project_root)
    config_path = find_config()
    if not skip_config_check and ensure_config_exists(config_path):
        _show_config_dialog(config_path)

    llm_cfg, search_cfg = load_config(config_path)
    agent_cfg = load_agent_config(config_path)
    if agent_cfg.fs_root:
        os.environ["YAGENT_FS_ROOT"] = str(Path(agent_cfg.fs_root).expanduser())
    skills = _load_project_skills(project_root)
    system_prompt = build_skills_system_prompt(skills)
    if system_prompt:
        print(f"[system-prompt] 注入 {len(system_prompt)} 字符")

    archive = ContextArchive(project_root)
    sessions = SessionStore(project_root)
    print(f"[context] 归档目录：{archive.archive_dir}  |  会话目录：{sessions._dir}")

    # 异步启动 MCP — start(block=False) 立刻返回，后台连
    _mcp_manager, _mcp_ready_event = _start_mcp_async(project_root)

    engine = ChatEngine(
        OpenAICompatClient(llm_cfg),
        build_registry(
            search_cfg, skills, project_root=project_root,
            mcp_manager=_mcp_manager, archive=archive,
        ),
        system_prompt=system_prompt,
        context_window=llm_cfg.context_window,
        archive=archive,
        sessions=sessions,
        mcp_manager=_mcp_manager,
    )

    _inject_agents_md(project_root, engine, agent_cfg)
    return engine


def _show_config_dialog(config_path: Path) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        tk.messagebox.showinfo(
            "YAgent 首次启动",
            f"已为你生成一份 mock config.ini：\n\n{config_path}\n\n"
            "请编辑填入真实的 API Key 和模型信息，然后重新启动 YAgent。",
        )
        root.destroy()
    except Exception:
        pass


def main() -> None:
    project_root = Path.cwd()
    engine = build_engine(project_root)
    try:
        app = YAgentApp(engine)
        # 告诉 TUI 有一个后台 ready_event 等 MCP 连好
        app._mcp_ready_event = _mcp_ready_event
        app.run()
    finally:
        if _mcp_manager is not None:
            _mcp_manager.stop()


if __name__ == "__main__":
    main()
