# anythingllm-mcp

一个极简的 MCP (Model Context Protocol) Server，用 Python 开发，通过 **Streamable HTTP** 传输协议把本机 AnythingLLM 暴露给 MCP 客户端（如 Trae）。

- **MCP 协议版本**：`2026-07-28`（由 `mcp` Python SDK 2.0.0 实现，`LATEST_PROTOCOL_VERSION = "2026-07-28"`）
- **传输协议**：Streamable HTTP（非 stdio）
- **依赖**：`mcp>=2.0.0`、`httpx>=0.27`（已通过 `py` 启动器安装在系统 Python 3.13 中）
- **唯一工具**：`ask_first_workspace(question)` — 取 AnythingLLM 中 id 最小的工作区，向其发起 `query` 模式提问，返回 AI 文本回答 + 来源数

---

## 1. 项目结构

```
anythingllm-mcp/
├── server.py          # MCP 服务 + 单个工具实现
├── requirements.txt    # mcp, httpx
├── .mcp.json           # 项目级 MCP 配置（指向本地 HTTP 端点）
└── README.md
```

## 2. 前置条件

1. 本机已运行 AnythingLLM（默认 `http://localhost:3001`）。
2. AnythingLLM 已生成 API Key，并通过环境变量 `ANYTHINGLLM_API_KEY` 提供（不再硬编码在代码中）。
3. Python 3.10+，通过 `py` 启动器可调用；已安装 `mcp` 与 `httpx`：
   ```powershell
   py -m pip install -r requirements.txt
   ```

> 配置可通过环境变量覆盖（无需改代码）：
> - `ANYTHINGLLM_BASE_URL`（默认 `http://localhost:3001`）
> - `ANYTHINGLLM_API_KEY`（**必填**，无默认值；未设置时服务启动即报错）

## 3. 启动 MCP 服务器

在项目目录下执行：

```powershell
# 前台运行（便于看日志，Ctrl+C 停止）
cd C:\Users\Administrator\Documents\trae_projects\projects\anythingllm-mcp\anythingllm-mcp
py server.py
```

```powershell
# 后台运行（推荐生产用）
Start-Process -FilePath "py" -ArgumentList "server.py" -WorkingDirectory "C:\Users\Administrator\Documents\trae_projects\projects\anythingllm-mcp\anythingllm-mcp" -WindowStyle Hidden
```

启动后服务监听：`http://127.0.0.1:8000/mcp`

健康检查（PowerShell）：
```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/mcp" -Method POST `
  -Headers @{"Content-Type"="application/json"; "Accept"="application/json, text/event-stream"} `
  -Body '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{},"clientInfo":{"name":"probe","version":"0.1"}}}'
# 期望：StatusCode 200，返回 SSE 流，含 serverInfo.name="anythingllm-mcp"
```

## 4. 停止 MCP 服务器

**前台运行**：在终端按 `Ctrl+C`。

**后台运行**（任选其一）：
```powershell
# 方式 A：按监听端口找进程并结束
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 方式 B：按命令行匹配
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*server.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## 5. 在 Trae 中接入（项目级 MCP）

项目根目录已提供 `.mcp.json`：
```json
{
  "mcpServers": {
    "anythingllm-mcp": { "url": "http://127.0.0.1:8000/mcp", "type": "http" }
  }
}
```

使用步骤：
1. **先**按第 3 节启动 MCP 服务器（必须先于 Trae 客户端连接）。
2. 在 Trae 中打开本目录（或把 `.mcp.json` 复制到当前 Trae 工作区根目录）。
3. 在 Trae 的 MCP 面板中确认 `anythingllm-mcp` 处于 connected 状态；若未自动加载，重启 Trae 或在面板手动 reload。
4. 在新聊天会话中提问，例如：「我的工作区有多少个文件？」 — Agent 会自动调用 `ask_first_workspace` 工具。

> 注意：`.mcp.json` 中的 `url` 必须与服务器实际监听地址一致（默认 `127.0.0.1:8000`）。如需改端口，修改 `server.py` 末尾 `mcp.run(... port=8000)` 与 `.mcp.json` 中的 URL。

## 6. 工具说明

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `ask_first_workspace` | `question: str` | 调 `GET /api/v1/workspaces` 取 id 最小工作区的 slug；调 `POST /api/v1/workspace/{slug}/chat`（`mode=query`）发起提问；返回 `[工作区: <name>]\n<AI回答>\n\n来源(N): <title1>, ...` |

`mode=query` 表示：仅当向量库中存在相关来源时才使用 LLM，不召回聊天历史 —— 适合「针对工作区内容提问」的场景。如需让 LLM 用通用知识作答，可改 `server.py` 中 `"mode": "query"` 为 `"chat"` 或 `"automatic"`。
