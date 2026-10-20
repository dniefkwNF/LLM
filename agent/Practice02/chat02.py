import configparser
import datetime
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


# 工具：判断是否为闰年
def is_leap_year(year):
    y = int(year)
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    return f"{y} 是闰年" if leap else f"{y} 不是闰年"


# 工具：返回指定日期是星期几
def get_week_day(year, month, day):
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    d = datetime.date(int(year), int(month), int(day))
    return f"{year}年{month}月{day}日 是 {wd[d.weekday()]}"


# 工具：返回指定月份共有多少天
def get_days_of_month(year, month):
    y, m = int(year), int(month)
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return f"{y}年{m}月 共有 {days[m - 1]} 天"


tools = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取本机当前日期时间",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断指定年份是否为闰年",
            "parameters": {
                "type": "object",
                "properties": {"year": {"type": "integer", "description": "年份"}},
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getWeekDay",
            "description": "查询指定日期是星期几",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "年"},
                    "month": {"type": "integer", "description": "月"},
                    "day": {"type": "integer", "description": "日"},
                },
                "required": ["year", "month", "day"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getDaysOfMonth",
            "description": "查询指定年月共有多少天",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "年"},
                    "month": {"type": "integer", "description": "月"},
                },
                "required": ["year", "month"],
            },
        },
    },
]

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
            args = json.loads(s["arguments"]) if s["arguments"] else {}
            if s["name"] == "getDate":
                result = get_date()
            elif s["name"] == "isLeapYear":
                result = is_leap_year(args["year"])
            elif s["name"] == "getWeekDay":
                result = get_week_day(args["year"], args["month"], args["day"])
            elif s["name"] == "getDaysOfMonth":
                result = get_days_of_month(args["year"], args["month"])
            else:
                result = "unknown tool"
            messages.append({"role": "tool", "tool_call_id": s["id"], "content": result})
