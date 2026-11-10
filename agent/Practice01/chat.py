import configparser
import json
import sys

import requests

# 读取配置
cfg = configparser.ConfigParser()
cfg.read("config.ini", encoding="utf-8")
api_key = cfg["llm"]["api_key"]
base_url = cfg["llm"]["base_url"].rstrip("/")
model = cfg["llm"]["model"]

url = f"{base_url}/chat/completions"
headers = {"Authorization": f"Bearer {api_key}"}

messages = []  # 全部历史记录

while True:
    try:
        user_input = input("\n我: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not user_input:
        continue

    messages.append({"role": "user", "content": user_input})
    print("AI: ", end="", flush=True)

    # 流式请求，携带全部历史
    resp = requests.post(
        url,
        headers=headers,
        json={"model": model, "messages": messages, "stream": True},
        stream=True,
    )

    answer = ""
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        delta = json.loads(data)["choices"][0]["delta"].get("content", "")
        if delta:
            answer += delta
            print(delta, end="", flush=True)  # 逐字打字机输出

    messages.append({"role": "assistant", "content": answer})
    print()
