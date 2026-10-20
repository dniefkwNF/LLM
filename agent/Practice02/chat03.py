import configparser
import json
import os
import shutil
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


# 工具1：获取当前工作目录
def get_cwd():
    return os.getcwd()


# 工具2：列出指定目录的文件与子目录
def list_dir(path="."):
    items = []
    for name in os.listdir(path):
        kind = "目录" if os.path.isdir(os.path.join(path, name)) else "文件"
        items.append(f"{kind}\t{name}")
    return "\n".join(items) if items else "(空目录)"


# 工具3：获取指定文件的类型（扩展名）
def get_file_type(path):
    if os.path.isdir(path):
        return "目录"
    ext = os.path.splitext(path)[1]
    return ext if ext else "(无扩展名)"


# 工具4：读取纯文本文件内容
def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# 工具5：新建文件（可带初始内容）
def create_file(path, content=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已创建 {path}"


# 工具6：编辑纯文本文件内容（整体覆盖写入）
def edit_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}"


# 工具7：新建目录
def make_dir(path):
    os.makedirs(path, exist_ok=True)
    return f"已创建目录 {path}"


# 工具8：删除目录（含非空）
def remove_dir(path):
    shutil.rmtree(path)
    return f"已删除目录 {path}"


# 工具9：删除文件
def delete_file(path):
    os.remove(path)
    return f"已删除文件 {path}"


# 工具10：重命名文件/目录
def rename_path(src, dst):
    os.rename(src, dst)
    return f"已重命名 {src} -> {dst}"


def _props(**kwargs):
    return {"type": "object", "properties": kwargs}


tools = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取本机当前日期时间",
            "parameters": _props(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCwd",
            "description": "获取当前工作目录",
            "parameters": _props(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDir",
            "description": "列出指定目录下的文件与子目录",
            "parameters": {**_props(path={"type": "string", "description": "目录路径，默认当前目录"})},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的类型（扩展名），目录则返回'目录'",
            "parameters": {
                **_props(path={"type": "string", "description": "文件路径"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "读取纯文本文件内容",
            "parameters": {
                **_props(path={"type": "string", "description": "文件路径"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "新建文件，可带初始内容",
            "parameters": {
                **_props(
                    path={"type": "string", "description": "文件路径"},
                    content={"type": "string", "description": "初始内容，可空"},
                ),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": "编辑纯文本文件内容（整体覆盖写入）",
            "parameters": {
                **_props(
                    path={"type": "string", "description": "文件路径"},
                    content={"type": "string", "description": "新的完整内容"},
                ),
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "新建目录（支持多级）",
            "parameters": {
                **_props(path={"type": "string", "description": "目录路径"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rmtree",
            "description": "删除目录（允许非空）",
            "parameters": {
                **_props(path={"type": "string", "description": "目录路径"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除文件",
            "parameters": {
                **_props(path={"type": "string", "description": "文件路径"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename",
            "description": "重命名文件或目录",
            "parameters": {
                **_props(
                    src={"type": "string", "description": "原路径"},
                    dst={"type": "string", "description": "新路径"},
                ),
                "required": ["src", "dst"],
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
            name = s["name"]
            if name == "getDate":
                result = get_date()
            elif name == "getCwd":
                result = get_cwd()
            elif name == "listDir":
                result = list_dir(args.get("path", "."))
            elif name == "getFileType":
                result = get_file_type(args["path"])
            elif name == "readFile":
                result = read_file(args["path"])
            elif name == "createFile":
                result = create_file(args["path"], args.get("content", ""))
            elif name == "editFile":
                result = edit_file(args["path"], args["content"])
            elif name == "mkdir":
                result = make_dir(args["path"])
            elif name == "rmtree":
                result = remove_dir(args["path"])
            elif name == "deleteFile":
                result = delete_file(args["path"])
            elif name == "rename":
                result = rename_path(args["src"], args["dst"])
            else:
                result = "unknown tool"
            messages.append({"role": "tool", "tool_call_id": s["id"], "content": result})
