import configparser
import json
import subprocess

import requests

# 读取配置
cfg = configparser.ConfigParser()
cfg.read("config.ini", encoding="utf-8")
api_key = cfg["llm"]["api_key"]
base_url = cfg["llm"]["base_url"].rstrip("/")
model = cfg["llm"]["model"]

url = f"{base_url}/chat/completions"
headers = {"Authorization": f"Bearer {api_key}"}


# 工具：调用本机 date/time 命令获取当前日期时间
def get_date():
    d = subprocess.run("date /t", shell=True, capture_output=True, text=True).stdout.strip()
    t = subprocess.run("time /t", shell=True, capture_output=True, text=True).stdout.strip()
    return f"{d} {t}"


tools = [{
    "type": "function",
    "function": {
        "name": "getDate",
        "description": "获取本机当前日期时间",
        "parameters": {"type": "object", "properties": {}},
    },
}]

messages = []  # 全部历史记录

while True:
    try:
        user_input = input("\n我: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not user_input:
        continue

    messages.append({"role": "user", "content": user_input})

    # 循环请求：有 tool 调用就执行并再次请求，直到拿到最终回复
    while True:
        resp = requests.post(
            url,
            headers=headers,
            json={"model": model, "messages": messages, "tools": tools, "stream": True},
            stream=True,
        )

        content = ""
        tool_calls = {}
        started = False
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            delta = json.loads(data)["choices"][0]["delta"]
            if delta.get("content"):
                if not started:
                    print("AI: ", end="", flush=True)  # 逐字打字机输出
                    started = True
                content += delta["content"]
                print(delta["content"], end="", flush=True)
            for tc in delta.get("tool_calls") or []:
                slot = tool_calls.setdefault(tc["index"], {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        if started:
            print()

        # 没有工具调用，本轮结束
        if not tool_calls:
            messages.append({"role": "assistant", "content": content})
            break

        # 有工具调用：记录 assistant 消息，执行工具，回填结果后再请求
        calls = [{"id": s["id"], "type": "function",
                  "function": {"name": s["name"], "arguments": s["arguments"]}}
                 for s in tool_calls.values()]
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for s in tool_calls.values():
            result = get_date() if s["name"] == "getDate" else "unknown tool"
            messages.append({"role": "tool", "tool_call_id": s["id"], "content": result})
