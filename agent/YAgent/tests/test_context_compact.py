"""上下文压缩模块 pytest 单元测试。

运行方式：
    cd e:\\LLM\\Agent\\YAgent
    E:\\xiaoxueqi\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pytest

# 确保项目根在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.context import ContextArchive, estimate_tokens, generate_archive_id
from core.conversation import Conversation
from core.engine import ChatEngine
from core.models import Message, StreamDelta, ToolCall, ToolSpec
from core.ports import LLMResponse
from core.tools import ToolRegistry
from infra.config import SearchConfig, load_config


# ============================================================
# 测试辅助
# ============================================================

class _FakeLLM:
    """可控的 LLM 桩：stream() 和 chat() 都可按需替换。"""

    def __init__(self, chat_return: LLMResponse | None = None, chat_error: Exception | None = None):
        self._chat_return = chat_return
        self._chat_error = chat_error
        self.chat_calls: list[tuple[list[Message], tuple[ToolSpec, ...], float | None]] = []

    def stream(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> Iterable[StreamDelta]:
        # 默认不迭代 → send() 直接返回空内容、无 tool_calls
        return iter(())

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] = (),
            temperature: float | None = None) -> LLMResponse:
        self.chat_calls.append((list(messages), tuple(tools), temperature))
        if self._chat_error is not None:
            raise self._chat_error
        if self._chat_return is None:
            return LLMResponse(content="# 摘要\n模拟摘要", total_tokens=5000)
        return self._chat_return


def _make_engine(
    client: _FakeLLM | None = None,
    archive: ContextArchive | None = None,
    context_window: int | None = None,
    system_prompt: str = "你是助手。",
) -> ChatEngine:
    if client is None:
        client = _FakeLLM()
    reg = ToolRegistry()  # 空注册表即可，测试 compact 不需要工具
    return ChatEngine(
        client=client,
        tools=reg,
        system_prompt=system_prompt,
        context_window=context_window,
        archive=archive,
    )


def _add_convo(engine: ChatEngine, n: int = 3) -> None:
    """快速追加 n 轮 user/assistant 对话。"""
    for i in range(n):
        engine._conversation.add(Message("user", content=f"用户第{i}轮"))
        engine._conversation.add(Message("assistant", content=f"助手第{i}轮"))


@pytest.fixture
def archive(tmp_path) -> ContextArchive:
    return ContextArchive(tmp_path)


@pytest.fixture
def engine(archive) -> ChatEngine:
    return _make_engine(archive=archive)


# ============================================================
# 1. 归档 ID 唯一性
# ============================================================

class TestArchiveIdUniqueness:
    def test_consecutive_saves_no_collision(self, archive: ContextArchive):
        ids = [archive.save([Message("user", content=f"msg{i}")]) for i in range(20)]
        assert len(ids) == len(set(ids)), f"发现重复归档 ID: {ids}"
        # 全部以 _000000 序号后缀结尾
        for aid in ids:
            assert "_" in aid and len(aid.split("_")[-1]) == 6, f"缺序号后缀: {aid}"

    def test_files_not_overwritten(self, archive: ContextArchive):
        # 快速连续保存，检查文件大小各不相同
        for i in range(5):
            archive.save([Message("user", content="x" * (i + 1) * 100)])
        sizes = sorted(p.stat().st_size for p in archive.archive_dir.glob("*.jsonl"))
        assert len(sizes) == 5
        assert len(set(sizes)) >= 2, "连续归档内容不同，文件大小不应完全相同"


# ============================================================
# 2. 归档内容完整性
# ============================================================

class TestArchiveContentIntegrity:
    def test_tool_calls_and_results_preserved(self, archive: ContextArchive):
        msgs = [
            Message("system", content="SYS_PROMPT"),
            Message("user", content="帮我查天气"),
            Message(
                "assistant",
                content="好的，我来调用搜索。",
                tool_calls=[
                    ToolCall(id="call_001", name="searchweb",
                             arguments='{"query":"北京天气"}'),
                    ToolCall(id="call_002", name="getDate", arguments="{}"),
                ],
            ),
            Message("tool", content="北京今天晴，25℃", tool_call_id="call_001"),
            Message("tool", content="2024-01-01", tool_call_id="call_002"),
            Message("assistant", content="北京今天晴天，25 度。"),
        ]
        archive_id = archive.save(msgs)
        records = archive.load(archive_id)

        # meta + 6 message = 7
        assert len(records) == 7
        assert records[0]["type"] == "meta"
        for i, m in enumerate(msgs, start=1):
            rec = records[i]
            assert rec["type"] == "message"
            assert rec["role"] == m.role
            assert rec["content"] == m.content
            if m.tool_call_id:
                assert rec.get("tool_call_id") == m.tool_call_id
            if m.tool_calls:
                saved_calls = rec["tool_calls"]
                assert len(saved_calls) == len(m.tool_calls)
                for saved, orig in zip(saved_calls, m.tool_calls):
                    assert saved["id"] == orig.id
                    assert saved["function"]["name"] == orig.name
                    assert saved["function"]["arguments"] == orig.arguments

    def test_order_preserved(self, archive: ContextArchive):
        msgs = [Message("user", content=str(i)) for i in range(10)]
        aid = archive.save(msgs)
        records = archive.load(aid)
        contents = [r["content"] for r in records if r.get("type") == "message"]
        assert contents == [str(i) for i in range(10)]


# ============================================================
# 3. /compact 状态锁
# ============================================================

class TestCompactLock:
    def test_reentry_rejected(self, engine: ChatEngine):
        """压缩期间再次 compact() 被拒绝。"""
        _add_convo(engine, 2)

        # 用 Exception 桩让 chat 调用抛错 → 立即释放锁，我们验证锁在 compact 入口处生效
        # 更好的方式：直接操控内部标志
        engine._compacting = True
        ok, msg = engine.compact()
        assert ok is False
        assert "已在进行" in msg

    def test_send_rejected_while_compacting(self, engine: ChatEngine):
        """压缩期间 send() 抛 RuntimeError。"""
        _add_convo(engine, 1)
        engine._compacting = True
        with pytest.raises(RuntimeError, match="压缩进行中"):
            engine.send("新消息", emit=lambda e: None)

    def test_lock_released_after_success(self, archive: ContextArchive):
        client = _FakeLLM(LLMResponse(content="# 摘要内容", total_tokens=1000))
        eng = _make_engine(client=client, archive=archive)
        _add_convo(eng, 2)
        eng.compact()
        assert eng.is_compacting is False

    def test_lock_released_after_failure(self, archive: ContextArchive):
        """LLM 抛异常时锁仍被释放（finally）。"""
        client = _FakeLLM(chat_error=RuntimeError("LLM 崩了"))
        eng = _make_engine(client=client, archive=archive)
        _add_convo(eng, 2)
        eng.compact()  # 应该返回 (False, ...)
        assert eng.is_compacting is False

    def test_send_rejected_integrated(self, archive: ContextArchive):
        """真实 compact 期间 send 抛错（用线程模拟耗时 chat）。"""
        import threading
        import time as _t

        class _SlowLLM:
            def stream(self, messages, tools):
                return iter(())
            def chat(self, messages, tools=(), temperature=None):
                _t.sleep(0.2)  # 让另一个线程有机会进入
                return LLMResponse(content="# 慢摘要", total_tokens=500)

        eng = _make_engine(client=_SlowLLM(), archive=archive)
        _add_convo(eng, 2)

        results: list[bool] = []
        exc: list[Exception] = []

        def _run_compact():
            try:
                ok, _ = eng.compact()
                results.append(ok)
            except Exception as e:
                exc.append(e)

        def _run_send():
            try:
                eng.send("并发消息", emit=lambda e: None)
                results.append(True)
            except RuntimeError:
                results.append(False)  # False 表示被拒绝（预期）

        t1 = threading.Thread(target=_run_compact)
        t1.start()
        _t.sleep(0.05)  # 等 compact 拿到锁
        t2 = threading.Thread(target=_run_send)
        t2.start()
        t1.join()
        t2.join()

        assert exc == []
        # 至少有一次 send 因为锁被拒绝
        assert False in results


# ============================================================
# 4. 压缩失败回滚
# ============================================================

class TestCompactRollback:
    def test_context_preserved_on_llm_exception(self, archive: ContextArchive):
        client = _FakeLLM(chat_error=ConnectionError("LLM 服务不可达"))
        eng = _make_engine(client=client, archive=archive)

        orig_msgs = [Message("user", content="原始消息") for _ in range(5)]
        for m in orig_msgs:
            eng._conversation.add(m)
        before = eng._conversation.messages
        before_count = len(before)

        ok, msg = eng.compact()

        assert ok is False
        assert "LLM 服务不可达" in msg
        # 原上下文完整保留
        after = eng._conversation.messages
        assert len(after) == before_count
        for b, a in zip(before, after):
            assert b.role == a.role
            assert b.content == a.content

    def test_context_preserved_on_empty_summary(self, archive: ContextArchive):
        client = _FakeLLM(LLMResponse(content="   \n\n  "))  # 空摘要
        eng = _make_engine(client=client, archive=archive)
        eng._conversation.add(Message("user", content="不要丢"))
        before = eng._conversation.messages
        ok, _ = eng.compact()
        assert ok is False
        assert len(eng._conversation.messages) == len(before)

    def test_context_preserved_on_archive_fail(self, tmp_path):
        """归档目录不可写时，上下文也保留。"""
        # 在只读/不可写目录下建 archive
        bad_dir = tmp_path / "readonly_context"
        bad_dir.mkdir()
        try:
            # 在只读文件夹下尝试创建子目录 → 模拟失败
            bad_archive = ContextArchive(bad_dir)
            # monkey-patch save 让它直接抛
            original_save = bad_archive.save

            def _boom(_msgs):
                raise OSError("磁盘满了")

            bad_archive.save = _boom

            client = _FakeLLM()
            eng = _make_engine(client=client, archive=bad_archive)
            eng._conversation.add(Message("user", content="不能丢"))
            before = eng._conversation.messages

            ok, msg = eng.compact()
            assert ok is False
            assert "磁盘满了" in msg
            assert len(eng._conversation.messages) == len(before)
        finally:
            bad_dir.chmod(0o755)  # 收尾


# ============================================================
# 5. recallContext 检索
# ============================================================

@pytest.fixture
def multi_archive(tmp_path) -> ContextArchive:
    """三个不同归档，方便测试指定归档 vs 全局检索。"""
    a = ContextArchive(tmp_path)
    a.save([Message("user", content="项目A: FastAPI 后端"),
            Message("assistant", content="已在 8080 端口启动")])
    a.save([Message("user", content="项目B: Flask 爬虫"),
            Message("assistant", content="已用 trafilatura 提取正文")])
    a.save([Message("user", content="项目A: FastAPI 连接数据库"),
            Message("assistant", content="用 SQLAlchemy 定义模型")])
    return a


class TestRecallContext:
    def test_global_search(self, multi_archive: ContextArchive):
        r = multi_archive.recall("FastAPI")
        assert "命中" in r and "未找到" not in r
        # FastAPI 出现了至少 2 次（归档 1 和 归档 3）
        assert r.count("归档") >= 2

    def test_specified_archive_only(self, multi_archive: ContextArchive):
        aids = multi_archive.list_archives()
        aid_first = aids[0]
        # 第一个归档只有 FastAPI，没有 SQLAlchemy → 未命中
        r = multi_archive.recall("SQLAlchemy", archive_id=aid_first)
        assert "未找到" in r
        # 最后一个归档有 SQLAlchemy → 命中
        r3 = multi_archive.recall("SQLAlchemy", archive_id=aids[-1])
        assert "命中" in r3

    def test_no_match_returns_standard_message(self, multi_archive: ContextArchive):
        r = multi_archive.recall("完全不存在的关键词xyzzy")
        assert "未找到" in r
        assert "关键词" in r

    def test_and_keywords_both_present(self, multi_archive: ContextArchive):
        # "项目A FastAPI"  AND 匹配：同时出现项目A 和 FastAPI 的归档
        r = multi_archive.recall("项目A FastAPI")
        assert "命中" in r
        # 归档 1 和 3 都含这两个词，归档 2 不含项目A
        # 只含一个词的归档不会在第一批次命中（但 snippet 提取逻辑会显示）
        # 只要结果里至少出现 2 个归档就对
        assert r.count("归档") >= 2

    def test_bad_archive_id(self, multi_archive: ContextArchive):
        r = multi_archive.recall("FastAPI", archive_id="00000000-000000")
        assert "未找到" in r

    def test_empty_keyword(self, multi_archive: ContextArchive):
        r = multi_archive.recall("")
        assert "关键词" in r

    def test_too_long_result_truncated(self, multi_archive: ContextArchive):
        r = multi_archive.recall("FastAPI", max_chars=200)
        assert len(r) <= 300  # 允许小幅冗余


# ============================================================
# 6. Token 阈值拦截
# ============================================================

class TestTokenThreshold:
    def test_is_at_limit_true(self, archive: ContextArchive):
        # 极小窗口让估算直接超限
        eng = _make_engine(archive=archive, context_window=100)
        eng._conversation.add(Message("user", content="x" * 500))
        assert eng.is_at_limit is True
        assert eng.usage_ratio >= 0.8

    def test_is_at_limit_false(self, archive: ContextArchive):
        eng = _make_engine(archive=archive, context_window=128_000)
        eng._conversation.add(Message("user", content="短消息"))
        assert eng.is_at_limit is False

    def test_compact_removes_limit(self, archive: ContextArchive):
        # 先用小窗口 → 超限 → compact → 超限解除
        client = _FakeLLM(LLMResponse(content="# 短摘要", total_tokens=50))
        eng = _make_engine(client=client, archive=archive, context_window=200)
        eng._conversation.add(Message("user", content="x" * 800))
        assert eng.is_at_limit is True
        eng.compact()
        # 压缩后摘要很短，窗口相对较大 → 应解除
        assert eng.is_at_limit is False


# ============================================================
# 7. 摘要替换结果校验
# ============================================================

class TestSummaryReplacement:
    def test_system_prompt_preserved(self, archive: ContextArchive):
        client = _FakeLLM(LLMResponse(content="# 用户目标\n实现 Agent。", total_tokens=800))
        eng = _make_engine(client=client, archive=archive, system_prompt="SYS_PROMPT_ORIGINAL")
        _add_convo(eng, 3)
        orig_system_count = eng._conversation.prefix_count("system")
        eng.compact()
        after = eng._conversation.messages
        assert after[0].role == "system"
        assert after[0].content == "SYS_PROMPT_ORIGINAL"
        assert len(after) == orig_system_count + 1  # system + 摘要

    def test_summary_ends_with_archive_note(self, archive: ContextArchive):
        expected_content = "# 用户目标\n实现 Agent 的上下文压缩功能。\n\n## 已完成\n核心模块都写完了。"
        client = _FakeLLM(LLMResponse(content=expected_content, total_tokens=800))
        eng = _make_engine(client=client, archive=archive)
        _add_convo(eng, 3)
        ok, msg = eng.compact()

        assert ok is True
        aid = msg.rsplit(" ", 1)[-1].rstrip("。")

        after = eng._conversation.messages
        summary_msg = after[-1]
        assert summary_msg.role == "assistant"
        # 摘要正文保留
        assert "# 用户目标" in summary_msg.content
        # 归档说明追加
        assert aid in summary_msg.content
        assert "此前上下文已于" in summary_msg.content
        assert "recallContext" in summary_msg.content

    def test_tools_registry_unaffected(self, archive: ContextArchive):
        # 压缩不应清除 tools 注册表 — 它在 engine 外部传入
        reg = ToolRegistry()
        client = _FakeLLM(LLMResponse(content="摘要", total_tokens=100))
        eng = ChatEngine(client=client, tools=reg, archive=archive)
        _add_convo(eng, 2)
        eng.compact()
        # engine._tools 仍是同一个实例
        assert eng._tools is reg


# ============================================================
# 8. 配置项读取
# ============================================================

class TestConfigContextWindow:
    def test_explicit_value_kept(self, tmp_path):
        f = tmp_path / "config.ini"
        f.write_text(
            "[llm]\napi_key=x\nbase_url=https://x/v1\nmodel=m\ncontext_window=32768\n"
            "\n[search]\nprovider=auto\napi_key=\nbase_url=https://x\n",
            encoding="utf-8",
        )
        llm_cfg, _ = load_config(f)
        assert llm_cfg.context_window == 32768

    def test_missing_key_becomes_none(self, tmp_path):
        f = tmp_path / "config.ini"
        f.write_text(
            "[llm]\napi_key=x\nbase_url=https://x/v1\nmodel=m\n"
            "\n[search]\nprovider=auto\napi_key=\nbase_url=https://x\n",
            encoding="utf-8",
        )
        llm_cfg, _ = load_config(f)
        assert llm_cfg.context_window is None

    def test_invalid_value_becomes_none(self, tmp_path):
        f = tmp_path / "config.ini"
        f.write_text(
            "[llm]\napi_key=x\nbase_url=https://x/v1\nmodel=m\ncontext_window=abc\n"
            "\n[search]\nprovider=auto\napi_key=\nbase_url=https://x\n",
            encoding="utf-8",
        )
        llm_cfg, _ = load_config(f)
        assert llm_cfg.context_window is None

    def test_engine_defaults_to_128k(self, archive: ContextArchive):
        eng = _make_engine(archive=archive, context_window=None)
        assert eng.context_window == 128_000
        assert eng.is_default_window is True

    def test_engine_explicit_value(self, archive: ContextArchive):
        eng = _make_engine(archive=archive, context_window=32768)
        assert eng.context_window == 32768
        assert eng.is_default_window is False
