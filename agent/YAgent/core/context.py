"""上下文压缩、归档与历史检索。"""
from __future__ import annotations

import itertools
import json
import re
from datetime import datetime
from pathlib import Path

from .models import Message

# 粗略 token 估算：英文约 4 字符/token，中文约 1.5 字符/token
# 混合场景取 3.0 作为折中，足够监控告警使用
_CHARS_PER_TOKEN = 3.0


def estimate_tokens(messages: list[Message]) -> int:
    """粗略估算消息序列的 token 数。"""
    total_chars = 0
    for m in messages:
        payload = m.to_payload()
        total_chars += len(json.dumps(payload, ensure_ascii=False))
    return max(1, int(total_chars / _CHARS_PER_TOKEN))


def estimate_tokens_text(text: str) -> int:
    """估算单段文本的 token 数。"""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


# 进程内原子计数器，给极快的连续 save 额外加一道唯一性兜底
_id_counter = itertools.count()


def generate_archive_id() -> str:
    """生成归档编号，例如 20261022-153000.123_000001。

    毫秒后缀 + 进程内计数器 → 同一进程内全局唯一。
    """
    now = datetime.now()
    serial = next(_id_counter)
    return now.strftime("%Y%m%d-%H%M%S") + f".{now.microsecond // 1000:03d}_{serial:06d}"


def format_timestamp(dt: datetime | None = None) -> str:
    """人类可读时间戳，例如 2026-10-22 15:30:00。"""
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


class ContextArchive:
    """管理 .agents/context/ 下的历史归档。"""

    def __init__(self, project_root: Path) -> None:
        self._dir = project_root / ".agents" / "context"
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def archive_dir(self) -> Path:
        return self._dir

    def save(self, messages: list[Message]) -> str:
        """将消息序列保存为 jsonl 归档，返回归档编号。"""
        archive_id = generate_archive_id()
        path = self._dir / f"{archive_id}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            meta = {"type": "meta", "archive_id": archive_id, "saved_at": format_timestamp()}
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for m in messages:
                record = {"type": "message", **m.to_payload()}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return archive_id

    def list_archives(self) -> list[str]:
        """列出所有归档编号，按时间升序。"""
        files = sorted(self._dir.glob("*.jsonl"))
        return [p.stem for p in files]

    def load(self, archive_id: str) -> list[dict]:
        """读取指定归档的所有记录。"""
        path = self._dir / f"{archive_id}.jsonl"
        if not path.is_file():
            return []
        records: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def recall(self, text: str, archive_id: str | None = None, max_chars: int = 3000) -> str:
        """全文关键词检索，返回匹配片段。

        按关键词出现次数排序，优先返回高频命中消息。
        总输出长度不超过 max_chars。
        """
        if archive_id:
            archives = [archive_id] if (self._dir / f"{archive_id}.jsonl").is_file() else []
        else:
            archives = self.list_archives()

        if not archives:
            return "[recallContext] 未找到任何历史归档。"

        # 解析关键词：用空格分词，支持多关键词 AND 语义
        keywords = [k.lower() for k in text.split() if k.strip()]
        if not keywords:
            return "[recallContext] 请提供至少一个关键词。"

        hits: list[tuple[int, str, str]] = []  # (score, archive_id, snippet)

        for aid in archives:
            records = self.load(aid)
            for i, rec in enumerate(records):
                if rec.get("type") != "message":
                    continue
                content = json.dumps(rec, ensure_ascii=False).lower()
                score = sum(1 for kw in keywords if kw in content)
                if score == 0:
                    continue
                raw = json.dumps(rec, ensure_ascii=False)
                snippet = self._extract_snippet(raw, keywords[0])
                hits.append((score, aid, snippet))

        if not hits:
            return (
                f"[recallContext] 在 {len(archives)} 个归档中未找到包含关键词 "
                f"{'/'.join(keywords)} 的记录。"
            )

        hits.sort(key=lambda h: (-h[0], h[1]))

        lines: list[str] = [
            f"【recallContext】关键词：{'/'.join(keywords)}，"
            f"命中 {len(hits)} 条，共搜索 {len(archives)} 个归档。"
        ]
        used = len(lines[0]) + 50
        shown = 0
        for score, aid, snippet in hits:
            block = f"\n--- 归档 {aid} (命中{score}关键词) ---\n{snippet}\n"
            if used + len(block) > max_chars:
                lines.append(f"\n... 结果已截断，剩余 {len(hits) - shown} 条未显示。")
                break
            lines.append(block)
            used += len(block)
            shown += 1

        return "".join(lines)

    @staticmethod
    def _extract_snippet(raw: str, kw: str, window: int = 200) -> str:
        """围绕第一个关键词提取上下文片段。"""
        idx = raw.lower().find(kw.lower())
        if idx < 0:
            return raw[:window]
        start = max(0, idx - window // 2)
        end = min(len(raw), idx + window)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(raw) else ""
        return f"{prefix}{raw[start:end]}{suffix}"


# ---------- 上下文压缩专用 Prompt ----------

COMPACT_SYSTEM_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。"""


def build_compact_user_prompt(messages: list[Message]) -> str:
    """把消息序列序列化为压缩请求的 user prompt。"""
    lines = ["以下是当前会话的完整历史，请生成摘要：\n"]
    for m in messages:
        role_label = {
            "system": "[System]",
            "user": "[User]",
            "assistant": "[Assistant]",
            "tool": "[Tool]",
        }.get(m.role, f"[{m.role}]")
        lines.append(f"--- {role_label} ---")
        if m.content:
            lines.append(m.content)
        if m.tool_calls:
            for call in m.tool_calls:
                lines.append(f"(tool_call) {call.name}({call.arguments})")
        if m.tool_call_id:
            lines.append(f"(tool_call_id) {m.tool_call_id}")
        lines.append("")
    return "\n".join(lines)
