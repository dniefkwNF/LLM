# LLM — LLM / MCP 工程实践合集

围绕 **大模型（LLM）** 与 **MCP（Model Context Protocol）** 的工程实践仓库，包含 3 个可独立运行的项目与 1 组命令行助手练习。

## 项目一览

| 项目 | 语言 | 说明 | 状态 |
| --- | --- | --- | --- |
| [PetHospitauMCP](./PetHospitauMCP) | Go + Python | 宠物医院 REST API（纯标准库、自研单文件嵌入式数据库）及其 MCP 服务 | ✅ 完成 |
| [1-AnythingLLMMCP](./1-AnythingLLMMCP) | Python | 将本机 AnythingLLM 暴露为 MCP 服务的极简 Server（Streamable HTTP） | ✅ 完成 |
| [1-AnythingLLMServer](./1-AnythingLLMServer) | HTML | AnythingLLM 文档上传 / 向量化嵌入的网页工具 | ✅ 完成 |
| [agent](./agent) | Python | 命令行 LLM 助手练习：基础流式对话 → function call 工具调用 | ✅ 完成 |

## 目录结构

```text
LLM/
├── PetHospitauMCP/          # Go REST API + Python MCP 服务（主力项目）
│   ├── main.go              # 服务入口
│   ├── internal/            # api / model / store（自研嵌入式单文件数据库）
│   ├── python-service/      # MCP 服务：暴露 list_pets 工具
│   └── docs/                # 实施文档
├── 1-AnythingLLMMCP/        # AnythingLLM MCP Server（单工具）
├── 1-AnythingLLMServer/     # AnythingLLM 文档上传网页
└── agent/                   # 命令行 LLM 助手练习
    ├── Practice01/          # 基础流式对话
    └── Practice02/          # function call（11 个工具）
```

## 各项目简介

### PetHospitauMCP

用 Go 标准库实现的本地宠物医院管理系统 + 面向 AI Agent 的 MCP 服务。

- **零第三方依赖**：仅用 Go 标准库，`go build` 即可。
- **自研单文件嵌入式数据库**：追加写日志 + CRC32 校验 + 内存索引 + 自动压实 + 崩溃安全（原子重写 / `fsync`）。
- **网页界面已内嵌进可执行文件**：`可执行文件 + data/pet.db` 两个文件即可迁移。
- **python-service**：基于 `mcp` SDK 的 MCP 服务，暴露 `list_pets` 工具。

详见 [PetHospitauMCP/README.md](./PetHospitauMCP/README.md)。

### 1-AnythingLLMMCP

极简 MCP Server，通过 **Streamable HTTP** 把本机 AnythingLLM 暴露给 MCP 客户端。

- 唯一工具：`ask_first_workspace(question)` —— 取 id 最小的工作区发起提问。
- 协议版本 `2026-07-28`，`mcp` Python SDK 2.0.0。
- API Key 通过环境变量 `ANYTHINGLLM_API_KEY` 提供（不硬编码）。

详见 [1-AnythingLLMMCP/README.md](./1-AnythingLLMMCP/README.md)。

### 1-AnythingLLMServer

单文件网页工具（`upload.html`），用于向 AnythingLLM 工作区上传文档并触发向量化嵌入。

详见 [1-AnythingLLMServer/README.md](./1-AnythingLLMServer/README.md)。

### agent

命令行 LLM 助手练习，从基础流式对话逐步进阶到 function call 工具调用。

详见 [agent/README.md](./agent/README.md)。

## 快速开始

各子项目独立运行，进入对应目录按其中 README 操作即可，例如：

```bash
# PetHospitauMCP（Go）
cd PetHospitauMCP && go run . -seed

# 1-AnythingLLMMCP（Python）
cd 1-AnythingLLMMCP && setx ANYTHINGLLM_API_KEY "你的Key" && py server.py

# agent（Python）
cd agent/Practice02 && python chat03.py
```

## 技术栈

- **Go**：`net/http`、标准库、自研存储引擎
- **Python**：`mcp` SDK、`httpx`、`requests`、`configparser`、`pytest`
- **前端**：原生 HTML / CSS / JavaScript

## License

[MIT](./LICENSE) © 2026 atfa
