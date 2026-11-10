# YAgent

基于 Practice02 重构的终端 LLM Agent，核心逻辑与界面完全分离，支持工具调用、技能、MCP、会话持久化与上下文压缩。

## 分层结构

```
YAgent/
├── main.py                  # 组合根：装配依赖、注入 AGENT.md、启动 TUI
├── AGENT.md                 # 项目级指引（启动时注入，可由 config.ini 改名/关闭）
├── config.ini.example       # 配置模板（复制为 config.ini 后填写，config.ini 不入库）
├── core/                    # 核心层（无界面、无网络依赖）
│   ├── models.py            # 消息、工具、流式增量、界面事件
│   ├── ports.py             # 抽象协议：LLMClient / Tool / EventSink
│   ├── conversation.py      # 对话历史
│   ├── tools.py             # 工具注册表（含调用耗时日志）
│   ├── engine.py            # 对话编排、工具循环、压缩、会话
│   ├── context.py           # 上下文归档与 recallContext 检索
│   ├── session_store.py     # 会话持久化（.agents/sessions）
│   ├── skills.py            # 技能发现与加载
│   ├── system_prompt.py     # 技能索引注入（仅 name + description）
│   ├── trace.py             # 文件日志（.agents/logs/agent.log）
│   └── slash.py             # 斜杠命令解析
├── infra/                   # 基础设施层
│   ├── config.py            # config.ini 读取（[llm]/[search]/[agent]）
│   ├── llm.py               # OpenAI 兼容流式客户端
│   ├── mcp_client.py        # MCP 客户端（stdio / Streamable HTTP）
│   └── tools.py             # 内置工具实现 + 路径边界
├── ui/tui/                  # 表现层（Textual）
│   ├── app.py               # 事件渲染、/resume、/compact
│   └── app.tcss             # 样式
└── tests/                   # 上下文压缩单测
```

依赖方向：`ui → core ← infra`。核心层不知道 Textual、requests 的存在，界面层只消费 `ChatEvent`。

## 内置工具（19 个）

| 分类 | 工具 |
|---|---|
| 时间 | `getDate`、`isLeapYear` |
| 探查 | `getCwd`、`listDir`、`getFileType`、`readFile` |
| 写入 | `createFile`、`editFile`、`mkdir`、`rename` |
| 删除 | `deleteFile`、`rmtree` |
| 网络 | `searchweb`（百度→Bing 自动降级）、`fetchpage`、`fetchWeb`、`runCli` |
| 技能 | `createSkill`、`loadSkill` |
| 归档 | `recallContext` |

MCP 工具在启动后异步接入，名称带 `[MCP:server]` 来源标记，重名自动追加后缀。

### 文件工具边界

所有路径参数都会先解析到 `YAGENT_FS_ROOT`（默认启动目录，可由 `config.ini [agent] fs_root` 覆盖）之内：

- 越界返回 `[路径越界]`，不会访问根目录之外的路径；
- `rmtree` / `deleteFile` / `rename` 拒绝操作工作根目录本身或文件系统根。

## 配置

首次启动若无 `config.ini` 会自动生成模板。也可直接复制 `config.ini.example`：

```powershell
cd E:\LLM\agent\YAgent
pip install -r requirements.txt
Copy-Item config.ini.example config.ini
# 编辑 config.ini 或设置环境变量
$env:YAGENT_API_KEY = "你的密钥"
py main.py
```

| 段 | 字段 | 默认值 | 说明 |
|---|---|---|---|
| `[llm]` | `api_key` | — | 可由环境变量 `YAGENT_API_KEY` 覆盖 |
| | `base_url` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| | `model` | `deepseek-v4-flash` | 模型名 |
| | `context_window` | 128000 | 可选，用于压缩阈值 |
| `[search]` | `provider` | `auto` | `auto` / `baidu` / `bing` |
| | `api_key` | — | 百度千帆 AI 搜索 Key |
| | `base_url` | 千帆搜索端点 | 一般无需修改 |
| `[agent]` | `agents_file` | `AGENT.md` | 项目指引文件名（兼容 `AGENTS.md`/`agents.md`） |
| | `agents_enabled` | `true` | 是否注入项目指引 |
| | `agents_max_chars` | `4000` | 注入内容上限，超出截断 |
| | `fs_root` | 启动目录 | 文件工具允许访问的根目录 |

> 真实密钥不要写入仓库：`config.ini` 已被 `.gitignore` 忽略，仓库只保留 `config.ini.example`。

## AGENT.md

启动时按 `agents_file` → `AGENT.md` → `AGENTS.md` → `agents.md` 顺序查找（含上一级目录），只注入一次；超长内容按 `agents_max_chars` 截断；缺失只打印提示不报错。`/new` 与会话恢复（`/resume`）都会保留该指引。

## 会话、压缩与日志

- `/save`、`/resume`、`/delete`：会话保存在 `.agents/sessions/`。
- `/compact`：把历史归档到 `.agents/context/` 并生成摘要；需要细节时用 `recallContext` 回捞。
- 运行日志：`.agents/logs/agent.log`（工具名、耗时、参数/结果长度、会话操作，1MB 轮转）。

## 运行

输入消息回车发送；`/help` 查看全部命令，`Ctrl+C` 退出。

## 扩展新界面

新界面（Web / Desktop）只需实现一个消费 `ChatEvent` 的回调并复用 `ChatEngine`：

```python
engine = ChatEngine(OpenAICompatClient(config), build_registry())
engine.send("你好", lambda event: render(event))
```

| 事件 | 含义 |
|---|---|
| `ASSISTANT_DELTA` | 追加流式文本 |
| `ASSISTANT_END` | 当前回复结束 |
| `TOOL_START` | 开始执行工具 |
| `TOOL_END` | 工具执行结果 |

新增工具时实现 `Tool` 协议并在 `infra/tools.py` 的 `build_registry()` 注册即可。
