"""具体工具实现与默认注册。"""
import datetime as _dt
import html as _html
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path as _Path

import requests

from core.models import ToolSpec
from core.skills import Skill
from core.tools import ToolRegistry


# ---------- 路径边界与危险操作保护 ----------
# 所有文件/目录工具只允许操作 YAGENT_FS_ROOT（默认启动目录）之内的路径。

def _allowed_root() -> _Path:
    raw = os.environ.get("YAGENT_FS_ROOT", "").strip()
    return _Path(raw or os.getcwd()).expanduser().resolve()


def _resolve_in_root(raw: str, label: str = "path") -> "tuple[_Path | None, str | None]":
    """把用户路径解析为根目录内的绝对路径；越界或非法时返回错误文本。"""
    try:
        p = _Path(os.path.expandvars(raw)).expanduser()
        candidate = (p if p.is_absolute() else _allowed_root() / p).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"{label} 无法解析：{raw}（{exc}）"
    try:
        candidate.relative_to(_allowed_root())
    except ValueError:
        return None, f"[路径越界] {raw} 不在允许的根目录 {_allowed_root()} 内"
    return candidate, None


def _dangerous_target(path: _Path) -> "str | None":
    """拒绝删除/移动根目录本身或文件系统根。"""
    if path.parent == path or (path.anchor and _Path(path.anchor) == path):
        return f"[危险操作] 拒绝操作文件系统根：{path}"
    if path == _allowed_root():
        return f"[危险操作] 拒绝对工作根目录本身操作：{path}"
    return None



# ---------- GetDateTool（跨平台，用 datetime.now） ----------

class GetDateTool:
    """获取本机当前日期时间（跨平台）。"""

    spec = ToolSpec(name="getDate", description="获取本机当前日期时间")

    def run(self, arguments: str) -> str:
        now = _dt.datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S")


# ---------- IsLeapYearTool ----------

class IsLeapYearTool:
    """判断指定年份是否为闰年（整百年需被 400 整除）。"""

    spec = ToolSpec(
        name="isLeapYear",
        description="判断指定年份是否为闰年。闰年规则：能被 4 整除且不能被 100 整除，或能被 400 整除。",
        parameters={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "要判断的年份，如 2024"},
            },
            "required": ["year"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[isLeapYear] 参数解析失败：{exc}"
        year = args.get("year")
        if year is None:
            return "[isLeapYear] 缺少必填参数 year"
        try:
            y = int(year)
        except (TypeError, ValueError):
            return f"[isLeapYear] year 必须是整数，收到 {year!r}"

        leap = (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)
        return f"{y} 年 {'是' if leap else '不是'}闰年"


# ---------- 本地文件/目录操作工具（10 类） ----------

class GetCwdTool:
    """获取当前工作目录。"""

    spec = ToolSpec(name="getCwd", description="获取当前工作目录的绝对路径")

    def run(self, arguments: str) -> str:
        return os.getcwd()


class ListDirTool:
    """列出目录内容，标注文件/目录。"""

    spec = ToolSpec(
        name="listDir",
        description="列出指定目录下的文件与子目录，标注文件/目录。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认当前目录 '.'"},
            },
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[listDir] 参数解析失败：{exc}"
        path = args.get("path", ".")
        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[listDir] {err}"
        try:
            items = os.listdir(target)
        except FileNotFoundError:
            return f"[listDir] 目录不存在：{path}"
        except PermissionError:
            return f"[listDir] 无权限访问：{path}"
        except OSError as exc:
            return f"[listDir] 读取失败：{exc}"

        if not items:
            return "(空目录)"
        lines = []
        for name in sorted(items):
            full = target / name
            kind = "目录" if full.is_dir() else "文件"
            lines.append(f"{kind}\t{name}")
        return "\n".join(lines)


class GetFileTypeTool:
    """判断路径类型（文件/目录/扩展名）。"""

    spec = ToolSpec(
        name="getFileType",
        description="判断指定路径的类型：目录 / 文件（附扩展名）。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要判断的路径"},
            },
            "required": ["path"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[getFileType] 参数解析失败：{exc}"
        path = args.get("path")
        if not path:
            return "[getFileType] 缺少必填参数 path"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[getFileType] {err}"
        if not target.exists():
            return f"[getFileType] 路径不存在：{path}"
        if target.is_dir():
            return "目录"
        ext = target.suffix
        return ext if ext else "(无扩展名)"


class ReadFileTool:
    """读取文本文件内容。"""

    spec = ToolSpec(
        name="readFile",
        description="读取指定文本文件的全部内容（UTF-8）。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
            },
            "required": ["path"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[readFile] 参数解析失败：{exc}"
        path = args.get("path")
        if not path:
            return "[readFile] 缺少必填参数 path"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[readFile] {err}"
        if not target.is_file():
            return f"[readFile] 文件不存在：{path}"
        try:
            with open(target, encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            return f"[readFile] {path} 不是 UTF-8 文本文件"
        except PermissionError:
            return f"[readFile] 无权限读取：{path}"
        except OSError as exc:
            return f"[readFile] 读取失败：{exc}"


class CreateFileTool:
    """创建文件（可带初始内容）。"""

    spec = ToolSpec(
        name="createFile",
        description="新建文本文件，可带初始内容。已存在时覆盖。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "初始内容（可空）"},
            },
            "required": ["path"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[createFile] 参数解析失败：{exc}"
        path = args.get("path")
        content = args.get("content", "")
        if not path:
            return "[createFile] 缺少必填参数 path"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[createFile] {err}"
        try:
            with open(target, "w", encoding="utf-8") as f:
                if content:
                    f.write(content)
        except PermissionError:
            return f"[createFile] 无权限写入：{path}"
        except OSError as exc:
            return f"[createFile] 创建失败：{exc}"
        return f"已创建 {path}"


class EditFileTool:
    """覆盖写入文件内容。"""

    spec = ToolSpec(
        name="editFile",
        description="以新内容覆盖写入指定文本文件。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "新的完整内容"},
            },
            "required": ["path", "content"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[editFile] 参数解析失败：{exc}"
        path = args.get("path")
        content = args.get("content")
        if not path:
            return "[editFile] 缺少必填参数 path"
        if content is None:
            return "[editFile] 缺少必填参数 content"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[editFile] {err}"
        if not target.is_file():
            return f"[editFile] 文件不存在：{path}"
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return f"[editFile] 无权限写入：{path}"
        except OSError as exc:
            return f"[editFile] 写入失败：{exc}"
        return f"已写入 {path}"


class MkdirTool:
    """创建目录（支持多级父目录）。"""

    spec = ToolSpec(
        name="mkdir",
        description="新建目录，支持多级父目录自动创建。目录已存在时静默成功。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
            },
            "required": ["path"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[mkdir] 参数解析失败：{exc}"
        path = args.get("path")
        if not path:
            return "[mkdir] 缺少必填参数 path"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[mkdir] {err}"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return f"[mkdir] 无权限创建：{path}"
        except OSError as exc:
            return f"[mkdir] 创建失败：{exc}"
        return f"已创建目录 {path}"


class RmtreeTool:
    """删除目录（允许非空）。"""

    spec = ToolSpec(
        name="rmtree",
        description="删除指定目录（允许非空），目录及其所有子项都会被删除。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
            },
            "required": ["path"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[rmtree] 参数解析失败：{exc}"
        path = args.get("path")
        if not path:
            return "[rmtree] 缺少必填参数 path"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[rmtree] {err}"
        blocked = _dangerous_target(target)
        if blocked:
            return f"[rmtree] {blocked}"
        if not target.is_dir():
            return f"[rmtree] 不是有效目录：{path}"
        try:
            shutil.rmtree(target)
        except PermissionError:
            return f"[rmtree] 无权限删除：{path}"
        except OSError as exc:
            return f"[rmtree] 删除失败：{exc}"
        return f"已删除目录 {path}"


class DeleteFileTool:
    """删除单个文件。"""

    spec = ToolSpec(
        name="deleteFile",
        description="删除单个文件。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
            },
            "required": ["path"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[deleteFile] 参数解析失败：{exc}"
        path = args.get("path")
        if not path:
            return "[deleteFile] 缺少必填参数 path"

        target, err = _resolve_in_root(str(path), "path")
        if err:
            return f"[deleteFile] {err}"
        blocked = _dangerous_target(target)
        if blocked:
            return f"[deleteFile] {blocked}"
        if not target.is_file():
            return f"[deleteFile] 不是有效文件：{path}"
        try:
            target.unlink()
        except PermissionError:
            return f"[deleteFile] 无权限删除：{path}"
        except OSError as exc:
            return f"[deleteFile] 删除失败：{exc}"
        return f"已删除文件 {path}"


class RenamePathTool:
    """重命名或移动路径（文件/目录均可）。"""

    spec = ToolSpec(
        name="rename",
        description="重命名或移动文件/目录。src 和 dst 可以是不同目录（即移动操作）。",
        parameters={
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "原路径"},
                "dst": {"type": "string", "description": "新路径"},
            },
            "required": ["src", "dst"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"[rename] 参数解析失败：{exc}"
        src = args.get("src")
        dst = args.get("dst")
        if not src or not dst:
            return "[rename] 缺少必填参数 src 或 dst"

        src_path, src_err = _resolve_in_root(str(src), "src")
        if src_err:
            return f"[rename] {src_err}"
        dst_path, dst_err = _resolve_in_root(str(dst), "dst")
        if dst_err:
            return f"[rename] {dst_err}"
        for p in (src_path, dst_path):
            blocked = _dangerous_target(p)
            if blocked:
                return f"[rename] {blocked}"
        if not src_path.exists():
            return f"[rename] 源路径不存在：{src}"
        try:
            src_path.rename(dst_path)
        except FileExistsError:
            return f"[rename] 目标已存在：{dst}"
        except PermissionError:
            return f"[rename] 无权限（可能文件被占用）"
        except OSError as exc:
            return f"[rename] 失败：{exc}"
        return f"已重命名 {src} → {dst}"


# ---------- 新增：SearchWebTool（多后端 + 自动降级） ----------

@dataclass(frozen=True)
class SearchConfig:
    provider: str  # auto / baidu / bing
    api_key: str
    base_url: str

DEFAULT_BAIDU_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _strip_html(text: str) -> str:
    """去标签 + 解码 HTML 实体（&ensp; &#0183; &amp; 等）+ 折叠空白。"""
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class SearchWebTool:
    """联网搜索：百度网页搜索（网页结果）或 Bing 国内版兜底。"""

    spec = ToolSpec(
        name="searchweb",
        description="联网搜索，返回最多 5 条搜索结果（标题、链接、摘要）",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    )

    def __init__(self, cfg: SearchConfig) -> None:
        self._cfg = cfg

    def run(self, arguments: str) -> str:
        # 1. 解析参数
        try:
            args = json.loads(arguments) if arguments.strip() else {}
            query = args.get("query", "").strip()
        except Exception as exc:
            return f"[searchweb] 参数解析失败：{exc}（原始 arguments={arguments!r}）"
        if not query:
            return "[searchweb] 缺少必填参数 query"

        # 2. 按 provider 策略调用
        provider = self._cfg.provider
        if provider == "bing":
            return self._bing(query)

        if provider == "baidu":
            return self._baidu(query)

        # auto：先百度，失败自动降级 Bing
        baidu_result = self._baidu(query)
        if not baidu_result.startswith("[searchweb]"):
            return baidu_result
        bing_result = self._bing(query)
        if not bing_result.startswith("[searchweb]"):
            return f"【百度搜索调用失败，已自动降级 Bing】\n{bing_result}"
        return (
            f"【百度搜索调用失败，Bing 兜底也失败】"
            f"\n百度: {baidu_result}"
            f"\nBing: {bing_result}"
        )

    # ----- 百度网页搜索实现 -----
    def _baidu(self, query: str) -> str:
        if not self._cfg.api_key:
            return "[searchweb] 未配置百度搜索 api_key"

        try:
            resp = requests.post(
                self._cfg.base_url or DEFAULT_BAIDU_SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {self._cfg.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "messages": [{"role": "user", "content": query}],
                    "search_source": "baidu_search_v2",
                    "resource_type_filter": [{"type": "web", "top_k": 5}],
                    "query_policy": {"enable_rewrite": True},
                },
                timeout=15,
            )
        except requests.exceptions.Timeout:
            return "[searchweb] 百度搜索请求超时（15s）"
        except requests.exceptions.ConnectionError as exc:
            return f"[searchweb] 百度搜索网络连接失败：{exc}"
        except Exception as exc:
            return f"[searchweb] 百度搜索意外错误：{exc}"

        try:
            data = resp.json()
        except Exception:
            return f"[searchweb] 百度搜索 HTTP {resp.status_code}：{resp.text[:200]}"

        if resp.status_code != 200 or data.get("code"):
            code = data.get("code", resp.status_code)
            message = data.get("message", "") or ""
            return f"[searchweb] 百度搜索返回错误 {code}：{message}".strip()

        refs = data.get("references") or []
        if not refs:
            return "[searchweb] 百度搜索未返回任何结果"

        lines: list[str] = ["【百度搜索结果】"]
        for i, ref in enumerate(refs[:5], 1):
            title = (ref.get("title") or "").strip()
            url = (ref.get("url") or "").strip()
            snippet = (ref.get("snippet") or ref.get("content") or "").strip()
            date = (ref.get("date") or "").strip()

            lines.append(f"{i}. {title or '(无标题)'}")
            lines.append(f"   链接：{url}")
            if date:
                lines.append(f"   日期：{date}")
            if snippet:
                lines.append(f"   摘要：{snippet[:200]}")
        return "\n".join(lines)

    # ----- Bing 国内版实现 -----
    def _bing(self, query: str) -> str:
        try:
            r = requests.get(
                "https://cn.bing.com/search",
                params={"q": query},
                headers={"User-Agent": _UA},
                timeout=15,
            )
        except requests.exceptions.Timeout:
            return "[searchweb] Bing 请求超时（15s）"
        except requests.exceptions.ConnectionError as exc:
            return f"[searchweb] Bing 网络连接失败：{exc}"
        except Exception as exc:
            return f"[searchweb] Bing 意外错误：{exc}"

        if r.status_code != 200:
            return f"[searchweb] Bing HTTP {r.status_code}"

        blocks = re.findall(
            r'<li class="b_algo[^"]*"[^>]*>(.*?)</li>', r.text, re.DOTALL
        )
        if not blocks:
            return "[searchweb] Bing 未返回任何搜索结果"

        lines: list[str] = ["【Bing 搜索结果】"]
        hits: list[tuple[int, str]] = []  # (序号, url) 用于后续自动抓正文
        for i, block in enumerate(blocks[:5], 1):
            hm = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="(.*?)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL,
            )
            sm = re.search(
                r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>',
                block,
                re.DOTALL,
            )
            if not sm:
                sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)

            if not hm:
                continue
            url = hm.group(1)
            title = _strip_html(hm.group(2)) or "(无标题)"
            snippet = _strip_html(sm.group(1)) if sm else ""

            lines.append(f"{i}. {title}")
            lines.append(f"   链接：{url}")
            if snippet:
                lines.append(f"   摘要：{snippet[:200]}")
            hits.append((i, url))

        if len(lines) == 1:
            return "[searchweb] Bing 解析不到有效结果"

        # 自动抓前 2 条结果的正文，直接返回给 LLM，省去额外 fetchpage 轮次
        fetched: list[str] = []
        for i, url in hits[:2]:
            body = self._fetch_body(url, max_chars=800)
            if body and not body.startswith("["):
                fetched.append(f"[{i}] {url} → {body}")

        if fetched:
            lines.append("【自动抓取前 2 条正文】")
            lines.extend(fetched)
        return "\n".join(lines)

    def _fetch_body(self, url: str, max_chars: int = 2000) -> str:
        """轻量抓取指定 URL 正文（超时 8s，只取文本）。"""
        if not url.startswith(("http://", "https://")):
            return "[跳过：非 http(s) 链接]"
        try:
            resp = requests.get(
                url, headers={"User-Agent": _UA}, timeout=8, allow_redirects=True
            )
        except Exception:
            return "[抓取失败：超时或网络错误]"
        if resp.status_code != 200:
            return f"[抓取失败 HTTP {resp.status_code}]"
        try:
            text = _strip_html(resp.text)
        except Exception as exc:
            return f"[正文提取异常：{exc}]"
        return text[:max_chars]


# ---------- 新增：FetchPageTool ----------

class FetchPageTool:
    """抓取指定 URL 网页正文，提取纯文本（最多 2000 字符）。"""

    spec = ToolSpec(
        name="fetchpage",
        description="抓取指定 URL 网页正文并提取纯文本，返回前 2000 字符",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的网页 URL"},
            },
            "required": ["url"],
        },
    )

    def run(self, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
            url = args.get("url", "").strip()
        except Exception as exc:
            return f"[fetchpage] 参数解析失败：{exc}"
        if not url:
            return "[fetchpage] 缺少必填参数 url"
        if not url.startswith(("http://", "https://")):
            return "[fetchpage] url 必须以 http:// 或 https:// 开头"

        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _UA},
                timeout=15,
                allow_redirects=True,
            )
        except requests.exceptions.Timeout:
            return "[fetchpage] 请求超时（15s）"
        except requests.exceptions.ConnectionError as exc:
            return f"[fetchpage] 网络连接失败：{exc}"
        except Exception as exc:
            return f"[fetchpage] 意外错误：{exc}"

        if resp.status_code != 200:
            return f"[fetchpage] HTTP {resp.status_code}：{resp.text[:150]}"

        try:
            text = _strip_html(resp.text)
        except Exception as exc:
            return f"[fetchpage] 正文提取异常：{exc}"

        return text[:2000]


# ---------- 新增：RunCliTool ----------

_CLI_TIMEOUT = 30          # 秒
_CLI_MAX_OUTPUT = 2000     # 单个输出截断上限
_CLI_ENCODING = "utf-8"
_CLI_FALLBACK_ENCODING = "gbk"  # Windows 默认 GBK 回退


class RunCliTool:
    """执行本地命令行程序（shell=False）。"""

    spec = ToolSpec(
        name="RunCli",
        description="执行本地命令行程序，返回 stdout / stderr / 退出码。仅用于教学实验，以当前用户权限执行。",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "程序名称或路径，如 'python'、'dir'、'git'"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "参数列表（数组形式，不拼接字符串）",
                },
                "cwd": {"type": "string", "description": "可选，指定工作目录"},
            },
            "required": ["command", "args"],
        },
    )

    def run(self, arguments: str) -> str:
        # 1. 解析参数
        try:
            params = json.loads(arguments) if arguments.strip() else {}
            command = params.get("command", "").strip()
            args = params.get("args", []) or []
            cwd = params.get("cwd")
        except Exception as exc:
            return f"[RunCli] 参数解析失败：{exc}"

        if not command:
            return "[RunCli] 缺少必填参数 command"
        if not isinstance(args, list):
            return "[RunCli] args 必须是字符串数组"
        cmd_line = [command, *[str(a) for a in args]]

        # 2. 执行（shell=False 防注入）
        try:
            proc = subprocess.run(
                cmd_line,
                shell=False,
                capture_output=True,
                timeout=_CLI_TIMEOUT,
                cwd=cwd if cwd else None,
            )
        except subprocess.TimeoutExpired:
            return f"[RunCli] 命令执行超时（{_CLI_TIMEOUT}s）：{' '.join(cmd_line)}"
        except FileNotFoundError:
            return f"[RunCli] 程序不存在：{command}"
        except PermissionError as exc:
            return f"[RunCli] 权限不足：{exc}"
        except Exception as exc:
            return f"[RunCli] 执行失败：{exc}"

        # 3. 解码（Windows 回退 GBK）
        stdout = self._decode(proc.stdout)
        stderr = self._decode(proc.stderr)

        # 4. 截断
        stdout_truncated = len(stdout) > _CLI_MAX_OUTPUT
        stderr_truncated = len(stderr) > _CLI_MAX_OUTPUT
        if stdout_truncated:
            stdout = stdout[:_CLI_MAX_OUTPUT] + f"\n... (stdout 已截断，原长 {len(stdout)} 字符)"
        if stderr_truncated:
            stderr = stderr[:_CLI_MAX_OUTPUT] + f"\n... (stderr 已截断，原长 {len(stderr)} 字符)"

        # 5. 格式化返回
        out = [
            f"【RunCli】命令: {' '.join(cmd_line)}",
            f"退出码: {proc.returncode}",
            "",
            f"--- stdout ({len(proc.stdout)} 字节) ---",
            stdout if stdout else "(空)",
        ]
        if proc.stderr:
            out += ["", f"--- stderr ({len(proc.stderr)} 字节) ---", stderr]
        return "\n".join(out)

    @staticmethod
    def _decode(raw: bytes) -> str:
        if not raw:
            return ""
        for enc in (_CLI_ENCODING, _CLI_FALLBACK_ENCODING, "latin-1"):
            try:
                return raw.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace").strip()


# ---------- 新增：FetchWebTool（curl + trafilatura → Markdown） ----------

_FETCH_TIMEOUT = 20          # curl 秒
_FETCH_MAX_MD = 6000         # 最终返回给 LLM 的 Markdown 上限


class FetchWebTool:
    """调用本地 curl 抓 HTML，trafilatura 提取正文转 Markdown。"""

    spec = ToolSpec(
        name="fetchWeb",
        description=(
            "抓取指定 URL 的网页正文并转为 Markdown。"
            "自动过滤导航栏、广告、页脚等无关内容，保留标题/段落/列表/表格/链接。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标网页 URL"},
            },
            "required": ["url"],
        },
    )

    def run(self, arguments: str) -> str:
        # 1. 解析参数
        try:
            params = json.loads(arguments) if arguments.strip() else {}
            url = params.get("url", "").strip()
        except Exception as exc:
            return f"[fetchWeb] 参数解析失败：{exc}"
        if not url:
            return "[fetchWeb] 缺少必填参数 url"
        if not url.startswith(("http://", "https://")):
            return "[fetchWeb] url 必须以 http:// 或 https:// 开头"

        # 2. subprocess 调 curl（shell=False）
        curl_cmd = [
            "curl.exe",                   # 全名，避免 PowerShell 别名
            "-L",                         # 跟随重定向
            "--max-time", str(_FETCH_TIMEOUT),
            "-sS",                        # silent + show errors
            "-A", _UA,
            "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
            url,
        ]
        try:
            proc = subprocess.run(
                curl_cmd, shell=False, capture_output=True,
                timeout=_FETCH_TIMEOUT + 5,  # subprocess 超时略大于 curl 超时
            )
        except subprocess.TimeoutExpired:
            return f"[fetchWeb] 抓取超时（{_FETCH_TIMEOUT + 5}s）：{url}"
        except FileNotFoundError:
            return "[fetchWeb] 系统找不到 curl.exe，请安装 curl 或改用 Windows 10+ 自带版本"
        except Exception as exc:
            return f"[fetchWeb] 启动 curl 失败：{exc}"

        # curl 退出码检查
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip() or "(无 stderr)"
            return f"[fetchWeb] curl 失败（退出码 {proc.returncode}）：{err}"

        html_bytes = proc.stdout
        if not html_bytes:
            return f"[fetchWeb] curl 返回空内容：{url}"

        # 3. 解码 HTML（curl 默认返回服务端原始编码）
        html = self._decode_html(html_bytes)

        # 4. trafilatura → Markdown
        try:
            import trafilatura  # 局部 import，避免未安装时影响其他工具
            md = trafilatura.extract(
                html,
                output_format="markdown",
                include_links=True,
                include_tables=True,
                deduplicate=True,
                no_fallback=True,
            )
        except ImportError:
            return "[fetchWeb] 缺少依赖 trafilatura，请执行：pip install trafilatura"
        except Exception as exc:
            return f"[fetchWeb] trafilatura 解析失败：{exc}"

        if not md or not md.strip():
            # trafilatura 判定正文过少（模板页 / 登录墙 / JS SPA）
            return (
                f"[fetchWeb] 未能提取到有效正文（页面可能是模板主页、登录墙或纯 SPA）\n"
                f"原始 URL: {url}\n"
                f"HTML 大小: {len(html_bytes)} 字节"
            )

        # 5. 截断
        truncated = False
        if len(md) > _FETCH_MAX_MD:
            md = md[:_FETCH_MAX_MD]
            truncated = True

        # 6. 返回
        header = f"【fetchWeb】来源: {url}"
        if truncated:
            header += f"（Markdown 已截断，原长 > {_FETCH_MAX_MD} 字符）"
        return header + "\n\n" + md

    @staticmethod
    def _decode_html(raw: bytes) -> str:
        """根据 HTML meta charset 或 http-equiv 推断编码，依次尝试常见编码。"""
        # 先看 HTML 里有没有 <meta charset="..."> 或 Content-Type
        head = raw[:4096].decode("ascii", errors="ignore")
        m = re.search(r'<meta[^>]+charset=["\']?([A-Za-z0-9_\-]+)', head, re.IGNORECASE)
        if not m:
            m = re.search(r'<meta[^>]+content=["\'][^"\']*charset=([A-Za-z0-9_\-]+)', head, re.IGNORECASE)
        preferred = m.group(1).lower() if m else None

        candidates = []
        if preferred:
            candidates.append(preferred)
        candidates += ["utf-8", "gbk", "gb2312", "big5", "latin-1"]
        seen = set()
        uniq = []
        for c in candidates:
            if c not in seen:
                seen.add(c); uniq.append(c)

        for enc in uniq:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")


# ---------- CreateSkillTool（根据自然语言创建新 Skill） ----------

import re as _re

_VALID_SLUG = _re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _slugify(raw: str) -> str:
    """把任意字符串规整成小写字母-数字-连字符格式。

    规则：
      1. 去掉非 [a-zA-Z0-9] 的字符，替换为连字符
      2. 连续连字符合并成一个
      3. 去掉首尾连字符
      4. 全转小写
      5. 空值兜底 → 'untitled-skill'
    """
    if not raw:
        return "untitled-skill"
    s = _re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    s = _re.sub(r"-+", "-", s)
    return s or "untitled-skill"


def _extract_description(skill_context: str) -> str:
    """从完整 SKILL.md 内容里提取 description。

    策略（按优先级）：
      1. YAML frontmatter 里的 description 字段
      2. 正文第一段非空行（去掉 # 开头的 markdown 标题）
      3. 兜底 '自定义 Skill'
    """
    if not skill_context:
        return "自定义 Skill"

    text = skill_context.strip()
    # 1. frontmatter 里找
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            for line in fm.splitlines():
                m = _re.match(r'^\s*description\s*:\s*["\']?(.*?)["\']?\s*$', line)
                if m and m.group(1).strip():
                    return m.group(1).strip()

    # 2. 正文第一段
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        # 截 80 字，避免正文过长
        return stripped[:80]

    return "自定义 Skill"


class CreateSkillTool:
    """根据 LLM 产出的内容创建新 Skill。

    行为：
      - 在 <project_root>/.agents/skills/<slug>/ 下创建目录
      - 写入 SKILL.md，保证含正确的 YAML Frontmatter（name、description）
      - 若 slug 已存在 → 返回拒绝提示，禁止覆盖
      - 辅助目录（references/、scripts/）留给 LLM 用 RunCli 自行创建

    命名规范：slug 统一小写字母、数字、连字符，可接受任意输入自动规整。
    """

    spec = ToolSpec(
        name="createSkill",
        description=(
            "创建一个新的项目级 Skill，写入 .agents/skills/<name>/SKILL.md。"
            "用于根据用户的自然语言要求生成可复用的技能模板。"
            "name 参数会自动规整为小写-连字符格式。若目标已存在则拒绝覆盖。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Skill 名称，支持任意输入，会自动规整为小写字母+连字符格式。"
                        "示例：'PrD 2 Code' → 'prd-2-code'。"
                    ),
                },
                "skillContext": {
                    "type": "string",
                    "description": (
                        "写入 SKILL.md 的完整内容（Markdown）。"
                        "可以包含 YAML frontmatter（name / description），"
                        "也可以只有正文——工具会自动补齐缺失的 frontmatter。"
                    ),
                },
            },
            "required": ["name", "skillContext"],
        },
    )

    def __init__(
        self, skills: list[Skill] | None = None, project_root: _Path | None = None
    ) -> None:
        self._project_root = project_root
        # 参考 skills 列表用于推导 project_root
        if self._project_root is None and skills:
            self._project_root = skills[0].dir.parent.parent.parent
        # 记录已存在的 slug，用于快速判重
        self._existing_slugs: set[str] = set()
        if skills:
            self._existing_slugs = {s.dir.name for s in skills}

    def run(self, arguments: str) -> str:
        params = _safe_json_parse(arguments)
        if not isinstance(params, dict):
            return f"[createSkill] 参数解析失败：{params}"

        raw_name = (params.get("name") or "").strip()
        skill_context = params.get("skillContext") or ""

        if not raw_name:
            return "[createSkill] 缺少必填参数 name"
        if not skill_context.strip():
            return "[createSkill] 缺少必填参数 skillContext"

        # 1. 规整 slug
        slug = _slugify(raw_name)
        if not _VALID_SLUG.match(slug):
            # 理论上 _slugify 已经保证格式，这里是双保险
            return f"[createSkill] 名称 '{slug}' 规整后仍不符合规范"

        # 2. 禁止覆盖
        if slug in self._existing_slugs:
            return (
                f"[createSkill] Skill '{slug}' 已存在，禁止覆盖。"
                f"请换一个名称，或先用 loadSkill 读取内容、确认后再手动修改。"
            )

        if self._project_root is None:
            return "[createSkill] 无法定位项目根目录（skills 列表为空且未传 project_root）"

        skill_dir = self._project_root / ".agents" / "skills" / slug
        skill_file = skill_dir / "SKILL.md"

        if skill_file.exists():
            return (
                f"[createSkill] 目标文件已存在：{skill_file}"
                f"，禁止覆盖。请换一个名称。"
            )

        # 3. 构造写入内容（补齐 frontmatter）
        description = _extract_description(skill_context)
        final_content = _ensure_frontmatter(skill_context, slug, description)

        # 4. 落盘
        try:
            skill_dir.mkdir(parents=True, exist_ok=False)
            skill_file.write_text(final_content, encoding="utf-8")
        except FileExistsError:
            return (
                f"[createSkill] 目录已存在：{skill_dir}"
                f"，禁止覆盖。请换一个名称。"
            )
        except OSError as exc:
            return f"[createSkill] 写入失败：{exc}"

        # 5. 更新内存中的已存在集合（后续 createSkill 调用可以即时判重）
        self._existing_slugs.add(slug)

        return (
            f"【createSkill】成功创建 Skill：{slug}\n"
            f"  路径：{skill_file}\n"
            f"  description：{description}\n"
            f"  字符数：{len(final_content)}\n"
            f"补充 references/ 或 scripts/ 目录请继续调用 RunCli。"
        )


def _ensure_frontmatter(content: str, name: str, description: str) -> str:
    """确保 content 以正确的 YAML Frontmatter 开头（含 name 和 description）。

    三种情况：
      A. 已有 frontmatter，且含 name+description → 原样返回（只做格式校验）
      B. 已有 frontmatter，但缺字段 → 补齐后返回
      C. 没有 frontmatter → 在最前面插入完整 frontmatter
    """
    text = content.lstrip("\ufeff").strip("\n")

    # 情况 A / B：检测 frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_body = parts[1].strip("\n")
            rest = parts[2].lstrip("\n")

            has_name = bool(_re.search(r'^\s*name\s*:', fm_body, _re.MULTILINE))
            has_desc = bool(_re.search(r'^\s*description\s*:', fm_body, _re.MULTILINE))

            if has_name and has_desc:
                # A. 字段都齐了，原样返回（加上标准分隔符）
                return f"---\n{fm_body}\n---\n\n{rest}"

            # B. 缺字段 → 追加补齐
            extra_lines = []
            if not has_name:
                extra_lines.append(f"name: {_yaml_quote(name)}")
            if not has_desc:
                extra_lines.append(f"description: {_yaml_quote(description)}")
            new_fm = fm_body + "\n" + "\n".join(extra_lines) if fm_body else "\n".join(extra_lines)
            return f"---\n{new_fm}\n---\n\n{rest}"

    # 情况 C：没有 frontmatter → 新建
    return (
        f"---\n"
        f"name: {_yaml_quote(name)}\n"
        f"description: {_yaml_quote(description)}\n"
        f"---\n\n{text}"
    )


def _yaml_quote(value: str) -> str:
    """给 YAML 标量值加引号，避免内嵌双引号导致解析错误。

    规则：
      - 含双引号 " → 用单引号包（YAML 单引号内 ' 转成 ''）
      - 含单引号 '  → 用双引号包（双引号内 \\ 和 " 需转义）
      - 都不含 → 直接用双引号包即可
      - 含换行/tab/空值 → 一律双引号
    """
    if "\n" in value or "\t" in value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if '"' in value and "'" in value:
        # 两种引号都有 → 双引号 + 转义
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if '"' in value:
        # 只有双引号 → 用单引号包
        return "'" + value.replace("'", "''") + "'"
    if "'" in value:
        # 只有单引号 → 用双引号包
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f'"{value}"'


# ---------- LoadSkillTool（按需加载 Skill 完整正文或附加文件） ----------

_SKILL_FILE = "SKILL.md"
_MAX_FILE_SIZE = 512 * 1024  # 512KB 硬上限，防止误读大文件占满上下文


class LoadSkillTool:
    """按名字加载 Skill 的 SKILL.md，或其目录内的附加文件。

    参数：
      - name      (必填)  Skill 名称
      - filename  (可选)  待加载的附加文件：
                         * 相对 Skill 目录的路径   → 直接读取
                         * 项目根相对路径 / 绝对路径 → 若解析后落在 Skill 目录内则读取
                         * 仅文件名               → 在 Skill 目录内递归搜索

    安全：所有读取最终必须落在 Skill 目录内，否则拒绝，防止路径穿越。
    """

    spec = ToolSpec(
        name="loadSkill",
        description=(
            "加载指定项目级 Skill 的说明文档，或其引用的附加文件。"
            "当你需要按照某个 Skill 的规则执行任务时调用此工具。"
            "只传 name 时读取 SKILL.md；同时传 filename 时读取该 Skill 目录内的附加文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "要加载的 Skill 名称，必须与 system prompt 中列出的完全一致。"
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "可选。待加载的文件名或路径。"
                        "支持相对于该 Skill 目录的路径、仅文件名（将在 Skill 目录内递归搜索），"
                        "或绝对路径（必须位于 Skill 目录内）。"
                    ),
                },
            },
            "required": ["name"],
        },
    )

    def __init__(self, skills: list[Skill], project_root: _Path | None = None) -> None:
        self._index: dict[str, Skill] = {s.name: s for s in skills}
        # 推导项目根：skill.dir = <root>/.agents/skills/<name>
        if project_root is None and skills:
            self._project_root = skills[0].dir.parent.parent.parent
        else:
            self._project_root = project_root

    # ----------------------------------------------------------
    # 入口
    # ----------------------------------------------------------

    def run(self, arguments: str) -> str:
        params = _safe_json_parse(arguments)
        if not isinstance(params, dict):
            return f"[loadSkill] 参数解析失败：{params}"

        # 兼容旧参数名 skillName，优先取新的 name
        skill_name = (
            params.get("name")
            or params.get("skillName")
            or ""
        ).strip()
        filename = (params.get("filename") or "").strip() or None

        if not skill_name:
            return "[loadSkill] 缺少必填参数 name"

        skill = self._index.get(skill_name)
        if skill is None:
            available = ", ".join(self._index.keys()) or "(无可用 Skill)"
            return f"[loadSkill] 未找到 Skill：{skill_name}。可用的有：{available}"

        if filename is None:
            # 只加载 SKILL.md
            return f"【Skill: {skill.name}】\n\n{skill.read_content()}"

        # 加载附加文件（SKILL.md 不再重复加载）
        return self._load_file(skill, filename)

    # ----------------------------------------------------------
    # 文件解析 & 读取
    # ----------------------------------------------------------

    def _load_file(self, skill: Skill, filename: str) -> str:
        skill_root = skill.dir.resolve()

        # 分支：是否带路径分隔符
        if _has_separator(filename):
            return self._load_by_resolved_path(skill, filename, skill_root)
        else:
            return self._load_by_filename_search(skill, filename, skill_root)

    # --- 带分隔符：尝试多根解析 ---

    def _load_by_resolved_path(
        self, skill: Skill, filename: str, skill_root: _Path
    ) -> str:
        """依次尝试把 filename 解释为 skill_dir 相对、项目根相对、绝对路径。

        任何一种成功解析且最终仍落在 skill_root 内 → 读取。
        """
        tried: list[str] = []

        # 1) skill 目录相对
        candidate = _resolve_under(skill_root, filename)
        if candidate is not None:
            return self._safe_read(candidate, skill_root, skill.name)
        tried.append(f"skill 目录 {skill_root / filename}")

        # 2) 项目根相对
        if self._project_root is not None:
            candidate = _resolve_under(self._project_root.resolve(), filename)
            if candidate is not None:
                # 即使 project_root 下存在，也必须落在 skill_root 内
                try:
                    candidate.relative_to(skill_root)
                except ValueError:
                    # project_root 下存在但不在 skill 目录里 → 安全拒绝
                    return (
                        f"[loadSkill] 路径 {filename} 解析后落在项目根但不在 Skill 目录内，"
                        "出于安全限制已拒绝。"
                    )
                return self._safe_read(candidate, skill_root, skill.name)
            tried.append(f"项目根 {self._project_root / filename}")

        # 3) 绝对路径（最后兜底，同样必须在 skill_root 内）
        if _Path(filename).is_absolute():
            abs_cand = _Path(filename)
            if abs_cand.is_file():
                try:
                    abs_cand.resolve().relative_to(skill_root)
                    return self._safe_read(abs_cand.resolve(), skill_root, skill.name)
                except ValueError:
                    return (
                        f"[loadSkill] 绝对路径 {filename} 不在 Skill 目录 {skill_root} 内，"
                        "出于安全限制已拒绝。"
                    )
            tried.append(f"绝对路径 {filename}")

        return (
            f"[loadSkill] 未能定位文件：{filename}\n"
            f"已尝试：{' / '.join(tried)}"
        )

    # --- 仅文件名：递归搜索 ---

    def _load_by_filename_search(
        self, skill: Skill, filename: str, skill_root: _Path
    ) -> str:
        matches = [p for p in skill_root.rglob(filename) if p.is_file()]

        if not matches:
            return (
                f"[loadSkill] 在 Skill 目录 {skill_root} 内未找到名为 {filename} 的文件。"
            )

        if len(matches) == 1:
            return self._safe_read(matches[0], skill_root, skill.name)

        # 多匹配 → 返回候选列表，由 LLM 选
        candidates = "\n".join(
            f"  ({i+1}) {p.relative_to(skill_root).as_posix()}"
            for i, p in enumerate(matches)
        )
        return (
            f"[loadSkill] 在 Skill 目录内找到 {len(matches)} 个名为 {filename} 的文件，"
            f"请显式传入相对路径以消除歧义：\n{candidates}"
        )

    # --- 安全读取 ---

    def _safe_read(self, path: _Path, skill_root: _Path, skill_name: str) -> str:
        """带边界检查 + 大小上限地读取单个文件。"""
        resolved = path.resolve()

        # 再做一次 inside skill_root 校验（defense in depth）
        try:
            resolved.relative_to(skill_root)
        except ValueError:
            return (
                f"[loadSkill] 安全拒绝：{path} 不在 Skill 目录 {skill_root} 内，"
                "已阻止读取以防止路径穿越。"
            )

        if not resolved.is_file():
            return f"[loadSkill] 不是有效文件：{path}"

        size = resolved.stat().st_size
        if size > _MAX_FILE_SIZE:
            return (
                f"[loadSkill] 文件过大（{size} 字节 > {_MAX_FILE_SIZE} 上限），"
                "已跳过读取以避免占用过多 token。请考虑只读摘要或拆分文件。"
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return (
                f"[loadSkill] 文件 {path} 不是 UTF-8 可解码的文本，无法直接读取。"
            )
        except OSError as exc:
            return f"[loadSkill] 读取 {path} 失败：{exc}"

        rel = resolved.relative_to(skill_root).as_posix()
        return f"【Skill: {skill_name} / {rel}】\n\n{content}"


# ---------- 路径工具（模块级，便于测试） ----------

def _has_separator(p: str) -> bool:
    return "/" in p or "\\" in p


def _resolve_under(base: _Path, target: str) -> _Path | None:
    """把相对路径 target 解析到 base 下。若最终路径仍在 base 内且是文件，返回其绝对路径。"""
    try:
        cand = (base / target).resolve()
        cand.relative_to(base.resolve())  # 检查不跳出 base
    except (OSError, ValueError):
        return None
    if cand.is_file():
        return cand
    return None


def _safe_json_parse(raw: str) -> dict | str:
    """先正常 json.loads；失败时把所有裸反斜杠翻倍再重试（Windows 绝对路径常见）。

    之所以可以全量翻倍，是因为能走到 fallback 说明原始 JSON 已坏掉；
    正常情况下 json.loads 首轮就成功，不会进入这里。
    """
    if not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
        return f"参数不是 JSON object（而是 {type(obj).__name__}）"
    except Exception:
        pass
    # 兜底：把所有 \ 翻成 \\，让 json.loads 能正确解析 Windows 路径
    try:
        obj = json.loads(raw.replace("\\", "\\\\"))
        if isinstance(obj, dict):
            return obj
        return f"修复后参数不是 JSON object（而是 {type(obj).__name__}）"
    except Exception as exc2:
        return f"JSON 解析失败：{exc2}"


# ---------- RecallContextTool（历史归档检索） ----------

class RecallContextTool:
    """全文检索 .agents/context/ 下的历史归档。"""

    spec = ToolSpec(
        name="recallContext",
        description=(
            "检索之前保存的原始上下文归档。"
            "当压缩摘要中缺少某些历史细节时，用关键词在归档中查找原始记录。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "可选，指定要检索的归档编号（如 20261022-153000）。"
                        "不传则在所有归档中搜索。"
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "必需。关键词或片段内容，用空格分隔多个词（AND 语义）。",
                },
            },
            "required": ["text"],
        },
    )

    def __init__(self, archive) -> None:
        self._archive = archive

    def run(self, arguments: str) -> str:
        params = _safe_json_parse(arguments)
        if not isinstance(params, dict):
            return f"[recallContext] 参数解析失败：{params}"

        text = (params.get("text") or "").strip()
        archive_id = (params.get("id") or "").strip() or None
        if not text:
            return "[recallContext] 缺少必填参数 text"

        if self._archive is None:
            return "[recallContext] 未启用历史归档功能。"

        return self._archive.recall(text, archive_id=archive_id)


# ---------- 注册表 ----------

def build_registry(
    search_cfg: SearchConfig,
    skills: list[Skill] | None = None,
    project_root: _Path | None = None,
    mcp_manager=None,
    archive=None,
) -> ToolRegistry:
    """新增工具时只需在此注册。

    MCP 工具在原生工具之后注册：原生工具名优先占用，
    MCP 工具遇到重名会自动追加编号后缀。
    """
    registry = ToolRegistry()
    # 时间类
    registry.register(GetDateTool())
    registry.register(IsLeapYearTool())
    # 本地文件/目录操作（10 类）
    registry.register(GetCwdTool())
    registry.register(ListDirTool())
    registry.register(GetFileTypeTool())
    registry.register(ReadFileTool())
    registry.register(CreateFileTool())
    registry.register(EditFileTool())
    registry.register(MkdirTool())
    registry.register(RmtreeTool())
    registry.register(DeleteFileTool())
    registry.register(RenamePathTool())
    # 网络/网页
    registry.register(SearchWebTool(search_cfg))
    registry.register(FetchPageTool())
    registry.register(RunCliTool())
    registry.register(FetchWebTool())
    # 技能生态
    registry.register(CreateSkillTool(skills, project_root))
    if skills:
        registry.register(LoadSkillTool(skills, project_root))
    if archive is not None:
        registry.register(RecallContextTool(archive))
    if mcp_manager is not None:
        for tool in mcp_manager.build_tools(reserved=registry.names):
            registry.register(tool)
    return registry
