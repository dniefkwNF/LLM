"""OpenAI 兼容接口的流式/非流式客户端。"""
import json
from collections.abc import Iterable, Sequence

import requests

from core.models import Message, StreamDelta, ToolCallDelta, ToolSpec
from core.ports import LLMResponse

from .config import LLMConfig


class OpenAICompatClient:
    """通过 SSE 读取 chat/completions 流。"""

    def __init__(self, config: LLMConfig) -> None:
        self._url = f"{config.base_url}/chat/completions"
        self._headers = {"Authorization": f"Bearer {config.api_key}"}
        self._model = config.model

    # ---------- 流式 ----------

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Iterable[StreamDelta]:
        payload = {
            "model": self._model,
            "messages": [message.to_payload() for message in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [tool.to_payload() for tool in tools]

        with requests.post(
            self._url, headers=self._headers, json=payload, stream=True
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8")
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    break

                delta = json.loads(data)["choices"][0]["delta"]
                tool_calls = [
                    ToolCallDelta(
                        index=item["index"],
                        id=item.get("id") or "",
                        name=(item.get("function") or {}).get("name") or "",
                        arguments=(item.get("function") or {}).get("arguments") or "",
                    )
                    for item in delta.get("tool_calls") or []
                ]
                if delta.get("content") or tool_calls:
                    yield StreamDelta(
                        content=delta.get("content") or "", tool_calls=tool_calls
                    )

    # ---------- 非流式（压缩摘要用） ----------

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        temperature: float | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": self._model,
            "messages": [m.to_payload() for m in messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = [t.to_payload() for t in tools]
        if temperature is not None:
            payload["temperature"] = temperature

        resp = requests.post(
            self._url, headers=self._headers, json=payload, timeout=300
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""

        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
