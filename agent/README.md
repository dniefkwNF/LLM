# agent — 命令行 LLM 助手练习

从**基础流式对话**逐步进阶到 **function call 工具调用**的命令行 LLM 助手练习，用于理解 Chat Completions 接口、SSE 流式输出与模型工具调用机制。

## 练习一览

| 目录 | 主题 | 主要内容 |
| --- | --- | --- |
| [Practice01](./Practice01) | 基础流式对话 | 读取 `config.ini`，调用 LLM 流式接口，打字机式逐字输出，携带全部历史保持多轮记忆 |
| [Practice02](./Practice02) | function call 工具调用 | 在基础对话之上，声明并执行 11 个本机文件系统工具 |

## Practice01

- `chat.py`：读取同目录 `config.ini` 的 `[llm]` 配置（`api_key` / `base_url` / `model`），拼接请求地址。
- 使用 `stream=True` 的流式接口，逐字打印模型回复。
- 把每一轮的 user / assistant 消息追加进 `messages`，每次请求都携带**完整历史**，实现对话记忆。
- 输入 `Ctrl+C` 退出。

## Practice02

在基础对话之上引入 **function call（工具调用）**，模型可自主调用本机文件系统工具：

- `chat.py` / `chat02.py`：进阶过程版本。
- `chat03.py`：完整版，声明并执行 11 个工具（`getDate`、`getCwd`、`listDir`、`getFileType`、`readFile`、`createFile`、`editFile`、`mkdir`、`rmtree`、`deleteFile`、`rename`）。
- 支持单轮内多次工具调用：解析 `delta.tool_calls` 分片 → 按 `function.name` 分派 → 回填 `role=tool` 消息 → 携带更新后的历史再次请求。
- 含架构图 `architecture.mmd`。

> ⚠️ 安全提示：工具包含删除目录 / 删除文件 / 覆盖写入等操作，请仅在受控目录内使用。

详见 [Practice02/README.md](./Practice02/README.md)。

## 运行方式

进入对应目录，准备 `config.ini` 后运行：

```ini
[llm]
api_key = sk-xxxxxxxx
base_url = https://api.example.com/v1
model = gpt-4o-mini
```

```bash
# 基础对话
cd Practice01 && python chat.py

# 工具调用
cd Practice02 && python chat03.py
```
