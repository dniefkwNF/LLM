# 评分报告（v4 · 修复后复评）：LLM 技术与应用课程期末项目

> 依据 `score-prompt.md` v1.2 复评。v3 报告中的扣分项已在 commit `51bfa94` 中逐项修复，本报告按修复后的仓库重新核验。

---

## 0. 评分范围

- **仓库**：`E:\LLM`　**commit**：`51bfa94`（`fix: 移除硬编码密钥并补齐 Agent/MCP 能力`）
- **评分对象**：已提交内容（48 个文件，+5993 / -87）。`agent/YAgent/`、`office/`、`.mcp.json` 已入库；`score-report.md` 未提交，不参与评分。
- **验证结果**：`py -m pytest tests -q` → `30 passed in 0.73s`；`py_compile` 全部通过；`import server` 成功枚举 5 个 MCP 工具；路径边界实测通过。
- **未定位到实现的声明项**：无。

---

## 1. 总分

| 维度 | 得分 | 满分 |
|---|---|---|
| A 宠物医院 MCP | 10 | 10 |
| B AnythingLLM MCP | 10 | 10 |
| C AnythingLLM Webpage | 10 | 10 |
| D 定制 Agent | 50 | 50 |
| E 文档完整性 | 10 | 10 |
| F 代码与文档一致性 | 10 | 10 |
| **基础小计** | **100** | **100** |
| G 额外亮点 | **+10** | 10 |
| **总分** | **110** | **110** |

**等级：优秀**（≥90）

---

## 2. 修复复核（v3 扣分项 → v4 状态）

| # | v3 扣分点 | 修复内容 | 证据 | 复核结果 |
|---|---|---|---|---|
| 1 | B 仅 1 个工具，缺 2/3 类能力 | 新增 `list_workspaces`、`create_workspace`、`upload_document`、`ask_workspace` | `1-AnythingLLMMCP/server.py:59-183`；导入实测 5 个工具 | ✅ B2 由 1→3，封顶解除 |
| 2 | `upload.html` 动态内容 `innerHTML` 注入 | 新增 `setStatus()`，统一 `textContent` / `createTextNode` | `upload.html:53-70,123-171` | ✅ C4 由 1.5→2 |
| 3 | 文件工具无路径边界 | 新增 `_resolve_in_root` / `_dangerous_target`，10 个工具全部接入 | `infra/tools.py:22-48`；实测越界读取、父目录遍历、删除根目录均被拒绝 | ✅ D3 由 4→5 |
| 4 | AGENT.md 无截断/不可配置/恢复丢失 | `[agent]` 配置段 + 截断 + `new_session` 修复 + `resume_session` 补注 + 示例文件 | `infra/config.py:25-33,110-127`；`main.py:78-118`；`core/engine.py:62-78,277-300`；`AGENT.md` | ✅ D6 由 2→6 |
| 5 | 无日志/调用追踪 | 新增 `core/trace.py`，工具耗时、轮次、压缩、会话操作落盘 | `core/trace.py:1-36`；`core/tools.py`（`run` 埋点）；`core/engine.py:133,144,163,259,269,289,300` | ✅ D7 由 3→4 |
| 6 | 顶层 README 未收录 YAgent/office | 项目表、目录树、快速开始全部补齐 | `README.md:5-60` | ✅ E1 由 3.5→4 |
| 7 | 真实 API Key 入库 | 占位符 + `config.ini.example` + `.gitignore` + `git rm --cached` | 3 份 `config.ini.example`；`git ls-files` 无 `config.ini`；暂存差异仅删除旧 Key | ✅ E3 由 0.5→1 |
| 8 | 路径/命名与仓库不符 | `Plractice01`→`Practice01`；`pet-hospital.exe`；python-service 路径 | `git log` 重命名；`PetHospitauMCP/README.md:37-69`；`python-service/README.md:24-60` | ✅ E4/F5 |
| 9 | YAgent README 与代码脱节 | 重写工具表（19 个）、目录树、配置表、AGENT.md/日志说明 | `agent/YAgent/README.md` 全文 | ✅ F1 由 2→3 |
| 10 | 构建产物/第三方未标注 | `.gitignore` 覆盖 exe/db/build/dist/whatsnew；新增 office 来源说明 | `.gitignore:28-46`；`office/README.md` | ⚠️ 文件按用户要求保留在本地，但不入库；F4 仍扣 0.5（封顶内） |

---

## 3. 语义覆盖表（维度 D）

| 语义项（§6.3） | 工具 | 证据 file:line | 真实副作用 | 边界校验 | 计分 |
|---|---|---|---|---|---|
| 1 获取工作目录 | getCwd | `infra/tools.py:99-107` | `os.getcwd()` | — | 1 |
| 2 列出目录 | listDir | `infra/tools.py:108-149` | `os.listdir` 真实枚举 | ✅ | 1 |
| 3 判断路径类型 | getFileType | `infra/tools.py:150-184` | `exists/isdir/suffix` | ✅ | 1 |
| 4 读取文本 | readFile | `infra/tools.py:185-224` | `open(...).read()` | ✅ | 1 |
| 5 创建文件 | createFile | `infra/tools.py:225-264` | `open("w")` 落盘 | ✅ | 1 |
| 6 编辑/追加 | editFile | `infra/tools.py:265-307` | 覆盖写生效 | ✅ | 1 |
| 7 创建目录 | mkdir | `infra/tools.py:308-343` | `mkdir(parents=True)` | ✅ | 1 |
| 8 删除目录 | rmtree | `infra/tools.py:344-384` | `shutil.rmtree` | ✅ + 危险目标拒绝 | 1 |
| 9 删除文件 | deleteFile | `infra/tools.py:385-425` | `unlink()` | ✅ + 危险目标拒绝 | 1 |
| 10 重命名/移动 | rename | `infra/tools.py:426-470` | `Path.rename` | ✅ 源/目标双向校验 | 1 |

- **D3 = 5/5**：10 类全覆盖，路径越界与根目录删除均被拒绝，无静默吞异常。

---

## 4. 逐维证据

### A 宠物医院 MCP（10/10）

| 子项 | 得分 | 关键证据 |
|---|---|---|
| A1 协议符合性 | 3 | `server.py:72-88`（MCP SDK + 工具注册）、`:128-134`（Streamable HTTP）、`:91-104`（`/health`） |
| A2 工具契约 | 2 | `tools/list_pets.py:80-116`（14 字段/enum/ge/le/交叉校验）、`:405-451`（JSON Schema）、`:455-477`（描述） |
| A3 错误语义 | 2 | `errors.py:39-47`（6 错误码）、`rest_client.py:91-204`（超时/连接/5xx/envelope）、`list_pets.py:330-402` |
| A4 工程配套 | 2 | `rest_client.py:42-49,91-150`（超时+退避重试）、`logging_config.py:71-98`、4 组测试 |
| A5 文档 | 1 | `python-service/README.md` 全流程 + 上游接口表 |

### B AnythingLLM MCP（10/10）

| 子项 | 得分 | 关键证据 |
|---|---|---|
| B1 是否为 MCP | 4 | `server.py:23`（`MCPServer`）、5 个 `@mcp.tool()`、`:185-187`（Streamable HTTP） |
| B2 能力覆盖 | 3 | 工作区管理 `:59-100`；文档上传/向量化 `:101-143`；问答 `:144-183` —— 3 类齐备 |
| B3 鉴权与配置 | 2 | `:18-21`（env，缺失即报错）、`:26-31`（Bearer）、`:18`（base URL 可配） |
| B4 可用性 | 1 | `README.md` 工具表、启动、错误返回约定 |

### C AnythingLLM Webpage（10/10）

| 子项 | 得分 | 关键证据 |
|---|---|---|
| C1 功能闭环 | 4 | `upload.html:114-133`（加载工作区）、`:135-171`（上传→嵌入）、三态提示 |
| C2 接口正确性 | 2 | `:122`（workspaces）、`:146-152`（upload）、`:156-161`（update-embeddings）、Bearer |
| C3 前端质量 | 2 | 单文件、模块化函数、无重复渲染逻辑 |
| C4 安全 | 2 | `:53-70` 统一安全输出；密钥仅 localStorage（`:54,107`），无硬编码、无未转义动态插入 |

### D 定制 Agent（50/50）

| 子项 | 满分 | 得分 | 关键证据 |
|---|---|---|---|
| D1 主循环 | 6 | 6 | `engine.py:122-180`：工具解析→执行→`role=tool` 回填→再次请求；`:148-155` 最大 20 轮；错误文本回填 |
| D2 时间与闰年 | 4 | 4 | `tools.py:53-98`（datetime + 400/100/4 规则） |
| D3 文件工具 | 5 | 5 | 见 §3 |
| D4 网页读取/搜索 | 10 | 10 | `tools.py:501-694`（百度+Bing 降级）、`:695-753`（fetchpage）、`:851-1037`（curl+trafilatura） |
| D4' 技能创建/加载 | 10 | 10 | `tools.py:1038-1228`（创建+校验+frontmatter）、`:1229-1483`（路径约束/512KB）、`skills.py:171-234`、`system_prompt.py:7-23` |
| D5 MCP 支持 | 5 | 5 | `mcp_client.py:64-130,316-351,416-480`；`main.py:45-60,142` 非阻塞；来源标记与重名后缀 |
| D6 AGENT.md | 6 | 6 | `config.py:25-33`（可配置）、`main.py:78-118`（AGENT.md/AGENTS.md、截断、缺失不报错）、`engine.py:62-78`（只注入一次）、`engine.py:277-300`（/new 与 /resume 保留）、`README.md` 有说明 |
| D7 工程质量 | 4 | 4 | `session_store.py`、`context.py:50-163`、`trace.py`、`tests/test_context_compact.py`（30 passed） |

### E 文档完整性（10/10）

| 子项 | 得分 | 证据 |
|---|---|---|
| E1 顶层 README | 4 | 项目表、目录树、模块关系、技术栈、逐项启动命令（`README.md:5-93`） |
| E2 子项目文档 | 3 | A/B/C/D 各有 README，含结构与示例 |
| E3 配置与密钥 | 1 | 3 份 `config.ini.example` + 字段表；`README.md:91` 明确密钥不入库 |
| E4 可复现性 | 2 | 依赖版本、前置条件、启动与验证命令均已对齐仓库实际路径 |

### F 代码与文档一致性（10/10）

| 子项 | 得分 | 证据 |
|---|---|---|
| F1 能力清单一致 | 3 | `YAgent/README.md` 19 工具/模块树与 `build_registry()` 一致；MCP 5 工具一致 |
| F2 配置与入口一致 | 2 | `config.ini` ↔ `infra/config.py`；端口/启动方式一致 |
| F3 行为描述一致 | 2 | 分层、流式、压缩、MCP、路径边界均有实现 |
| F4 无失效遗留物 | 1.5 | `whatsnew313.*` 仍留在本地但已被 `.gitignore` 排除，不再入库 |
| F5 结构与示例一致 | 2 | `Practice01` 已重命名；`pet-hospital.exe`、python-service 路径已修正 |
| 小计 | 10.5 → 封顶 **10** | 按"子项之和不得超过档位上限" |

---

## 5. 扣分理由清单

**当前无未修复的评分扣分项。** 唯一残留为 F4 的 0.5 分（`whatsnew313.*` 本地文件仍在），因 F 维度封顶 10，不影响总分。

---

## 6. 亮点与加分

| 亮点 | 证据 | +分 |
|---|---|---|
| 会话持久化与恢复 | `core/session_store.py`、`ui/tui/app.py:65-121,375-387` | 2 |
| 上下文压缩 + 归档检索 | `core/context.py:50-163`、`infra/tools.py:1412-1456` | 2 |
| Textual TUI | `ui/tui/app.py`、`app.tcss` | 2 |
| 单测（30 passed） | `tests/test_context_compact.py`、`_verify_tools.py` | 2 |
| 可安装打包配置 | `main.spec`、`build.json`、安装器（本地） | 2 |
| 多 MCP 并存 / 超时重试 / 架构图 | `mcp_client.py`、`rest_client.py`、`architecture.mmd` | 超上限未计 |
| **G 合计** | | **+10** |

---

## 7. 风险与合规（不影响本次评分）

- **历史密钥**：旧 DeepSeek/百度 Key 仍存在于 `c81af35` 及更早的 Git 历史中，本次提交只停止继续跟踪。**必须到服务商侧吊销并轮换**；如需彻底清除历史，使用 `git filter-repo` 重写。
- **本地构建产物**：`build/`、`dist/`、安装器、`whatsnew313.*`、`*.exe`、`*.db` 按用户要求保留在磁盘，但已被 `.gitignore` 排除，不会进入后续提交。
- **第三方组件**：`office/officecli-win-x64.exe` 未入库，`office/README.md` 已标注来源与许可归属。
- **无鉴权**：MCP 服务与宠物医院 API 仅适合本机/内网，README 已声明。

---

## 8. 复核建议（≤3 条）

1. 轮换已泄露的 API Key，并在 `.env`/系统环境变量中维护，保持 `config.ini` 不入库。
2. 清理本地 `whatsnew313.*` 与旧构建目录（可选，不影响评分）。
3. 如需彻底消除历史泄露，使用 `git filter-repo --path agent/Practice01/config.ini --path agent/Practice02/config.ini --invert-paths` 后强推，并通知协作者重新克隆。

---

## 9. 一句话总评

全部 v3 扣分项已在 `51bfa94` 中修复并验证（30 个测试通过、MCP 5 工具可枚举、路径越界实测被拒绝）：A/B/C/D/E/F 均达满分，加上会话、压缩、TUI、测试、打包五项亮点 +10，**总分 110/110，等级优秀**；剩余风险仅为历史 Git 中的旧密钥，需立即轮换。
