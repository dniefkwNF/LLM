"""读取 config.ini。缺失时创建一份带 mock 信息的模板，并返回 sentinel。"""
import configparser
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    context_window: int | None = None


@dataclass(frozen=True)
class SearchConfig:
    """联网搜索配置。缺失字段时各工具自行处理并返回错误文本。"""
    provider: str
    api_key: str
    base_url: str


@dataclass(frozen=True)
class AgentConfig:
    """项目级指引与文件工具边界配置（[agent] 段，全部可选）。"""
    agents_file: str = "AGENT.md"
    agents_enabled: bool = True
    agents_max_chars: int = 4000
    fs_root: str = ""


# ---------- 硬编码默认值集中在这里 ----------

# 默认的 config.ini 模板（当项目目录没有 config.ini 时自动写入）
DEFAULT_CONFIG_TEMPLATE = """\
# YAgent 配置文件。首次启动自动生成，请填入真实信息后重启。

[llm]
# 也可用环境变量 YAGENT_API_KEY 覆盖
api_key = sk-mock-please-replace
base_url = https://api.deepseek.com/v1
model = deepseek-v4-flash
# 可选：显式设置模型上下文窗口大小，不写默认 128k
# context_window = 128000

[search]
# 搜索源：auto（百度→Bing 兜底）/ baidu / bing
provider = auto
# 百度千帆 AI 搜索 API Key（bce-v3 格式，provider=baidu 时必填）
api_key = bce-v3/mock-please-replace
# 百度网页搜索接口（返回网页标题/链接/摘要）
base_url = https://qianfan.baidubce.com/v2/ai_search/web_search

[agent]
# 项目级指引文件名（同目录下也会兼容 AGENTS.md / agents.md）
agents_file = AGENT.md
# 是否启用指引注入
agents_enabled = true
# 注入内容的字符上限，超出部分截断
agents_max_chars = 4000
# 文件工具允许访问的根目录；留空则使用启动目录
fs_root =
"""

DEFAULT_SEARCH_BASE = "https://qianfan.baidubce.com/v2/ai_search/web_search"


def ensure_config_exists(path: Path) -> bool:
    """若 config.ini 不存在则创建模板，返回 True 表示刚创建。"""
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return True


def load_config(path: Path) -> tuple[LLMConfig, SearchConfig]:
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    llm_section = parser["llm"]

    context_window: int | None = None
    raw = llm_section.get("context_window", "").strip()
    if raw:
        try:
            context_window = int(raw)
        except ValueError:
            context_window = None

    llm = LLMConfig(
        api_key=os.environ.get("YAGENT_API_KEY") or llm_section.get("api_key", ""),
        base_url=llm_section.get("base_url", "").rstrip("/"),
        model=llm_section.get("model", ""),
        context_window=context_window,
    )

    search_section = parser["search"]
    search = SearchConfig(
        provider=search_section.get("provider", "auto").strip().lower(),
        api_key=search_section.get("api_key", "").strip(),
        base_url=search_section.get(
            "base_url", DEFAULT_SEARCH_BASE
        ).rstrip("/"),
    )
    return llm, search


def load_agent_config(path: Path) -> AgentConfig:
    """读取 [agent] 段；缺失或非法值一律回退到安全默认值。"""
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section("agent"):
        return AgentConfig()

    section = parser["agent"]
    raw_file = section.get("agents_file", "AGENT.md").strip() or "AGENT.md"
    raw_enabled = section.get("agents_enabled", "true").strip().lower()
    raw_max = section.get("agents_max_chars", "4000").strip()
    try:
        max_chars = max(0, int(raw_max))
    except ValueError:
        max_chars = 4000
    return AgentConfig(
        agents_file=raw_file,
        agents_enabled=raw_enabled not in {"0", "false", "no", "off"},
        agents_max_chars=max_chars,
        fs_root=section.get("fs_root", "").strip(),
    )
