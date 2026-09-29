# 实施计划:pet-hospital-mcp(阶段一)

## Context(背景)

`prompt-step1.md` 要求在 `pet-hospital-mcp/` 目录从零开发一个独立的 Python MCP 服务,把现有 Go 宠物医院 REST API(`e:\trae文件夹\my-mcp-projects\pet-hospital-mcp-teaching`)的 `GET /api/v1/pets` 能力暴露给 AI Agent。

关键技术约束:
- 使用 **官方 Python SDK `mcp==2.0.0`**(2026-07-28 发布的稳定版)
- 使用 **MCP 协议版本 `2026-07-28`**(无状态核心)
- 必须使用 `MCPServer`,不得使用 `mcp.server.fastmcp.FastMCP`
- 不得实现旧协议的 `initialize`、`Mcp-Session-Id`、会话存储
- 本次只实现一个工具:`list_pets`
- 不得修改 Go 宠物医院服务

## 研究结论(已完成)

### 1. mcp 2.0.0 SDK API(已确认)
- 入口:`from mcp.server import MCPServer`
- 服务器实例:`mcp = MCPServer("pet-hospital-mcp")`
- 工具注册:`@mcp.tool()` 装饰器,用 Python 类型注解 + Pydantic 模型作为 schema
- 启动:`mcp.run(transport="streamable-http", host=..., port=..., path="/mcp", stateless_http=True, json_response=True)`
  - `stateless_http=True` 是 2026-07-28 无状态核心的关键选项
  - `json_response=True` 让响应用纯 JSON 而非 SSE(适合无状态、无 MRTR 的简单工具)
- 客户端:`from mcp import Client`,`async with Client("http://host:port/mcp") as client`
- 工具失败:SDK 2.x 通过返回值或抛出 `ToolError` 标记(详见实施时验证)

### 2. MCP 2026-07-28 协议(已确认)
- 无 `initialize`/`initialized` 握手
- 无 `Mcp-Session-Id` header
- 单一 POST 端点(如 `/mcp`),响应是 JSON 或 SSE
- 可选 `server/discover` RPC(客户端可发现能力,但非必需)
- 每个请求自带 `MCP-Protocol-Version` header、`Mcp-Method`/`Mcp-Name` header
- 移除了 GET stream endpoint 和 protocol-level sessions

### 3. Go API 真实接口(已确认)

**`GET /api/v1/pets`** 由 `handleList` 处理(api.go:365),调用 `s.db.List(buildQuery(r))`。响应信封:
```json
{ "code": 200, "message": "ok", "data": {...}, "time": "..." }
```

`data` 结构(store.go:104 `Result`):
```json
{
  "items": [Pet],
  "total": int,
  "page": int,
  "pageSize": int,
  "totalPages": int,
  "totalCost": float
}
```

**`Pet` 字段**(model.go:87,与 `records`/`charges` 可能 null 或数组,需兼容):
- 主档:`id, name, species, breed, gender, ageMonths, color, chipNo, ownerName, ownerPhone, ownerAddr, doctor, disease, status, allergy, note`
- 子资源:`records: []MedicalRecord`, `charges: []Treatment`(JSON 中可能是 null 或数组)
- 派生:`totalCost, visitCount, createdAt, updatedAt`

**真实查询参数(buildQuery, api.go:271)**:
- `q, name, ownerName, ownerPhone, species, doctor, disease, status, sortBy, order, page, pageSize, min, max`

**真实枚举值(来自 `/api/v1/meta` + model.go)**:
- `species`: `犬, 猫, 兔, 鸟, 仓鼠, 爬宠, 其他`(7 个)
- `status`: `待就诊, 就诊中, 住院中, 已康复, 慢性病随访`(5 个)
- `sortBy`: `id, name, ownerName, species, doctor, disease, status, totalCost, visitCount, createdAt, updatedAt`(11 个,来自 meta 的 sortFields)
- `order`: 实际接受任意字符串,`desc`(大小写不敏感)为降序,其他视为升序;空默认 `desc`。严格化只允许 `asc, desc`
- `page`: 后端 < 1 自动改为 1
- `pageSize`: 后端 < 1 改为 20,> 500 截断为 500
- `min`/`max`: float,只在使用时触发(`HasCost=true`),`min` 默认 0,`max` 默认 1e18

## 目录结构(最终交付)

```text
pet_hospital_mcp/                          ← 在 e:\trae文件夹\ 下创建
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md
├── .gitignore
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py
│       ├── __main__.py                   ← python -m pet_hospital_mcp
│       ├── config.py                      ← 环境变量配置
│       ├── server.py                      ← MCPServer 装配 + /health 路由
│       ├── rest_client.py                 ← httpx 异步客户端(超时+重试)
│       ├── errors.py                      ← 统一错误结构 + 错误码
│       ├── logging_config.py             ← JSON 日志 + 脱敏
│       └── tools/
│           ├── __init__.py                ← register_all(mcp)
│           └── list_pets.py               ← 唯一工具
└── tests/
    ├── __init__.py
    ├── conftest.py                        ← 公共 fixtures
    ├── test_rest_client.py
    ├── test_list_pets.py
    ├── test_server.py
    └── test_stateless_flow.py
```

## 各文件职责

### `pyproject.toml`
- `requires-python = ">=3.11"`
- 依赖:`mcp==2.0.0`, `httpx`, `pydantic>=2`, `structlog`(选 structlog 简化脱敏)
- 测试依赖:`pytest`, `pytest-asyncio`, `respx`(httpx Mock)
- `[project.scripts]`:`pet-hospital-mcp = "pet_hospital_mcp.__main__:main"`

### `config.py`
- `Settings(BaseSettings)` 读取环境变量(用 pydantic-settings)
- `MCP_HOST`(默认 `127.0.0.1`)、`MCP_PORT`(默认 `8081`)
- `PET_HOSPITAL_BASE_URL`(默认 `http://127.0.0.1:8080`)
- `MCP_PATH`(默认 `/mcp`)、`LOG_LEVEL`(默认 `INFO`)
- `REQUEST_TIMEOUT`(默认 10s)、`MAX_RETRIES`(默认 2)

### `errors.py`
- `ErrorCode` 枚举:`VALIDATION_ERROR, BACKEND_TIMEOUT, BACKEND_UNAVAILABLE, BACKEND_API_ERROR, BACKEND_INVALID_RESPONSE, INTERNAL_ERROR`
- `ToolError(Exception)`:包含 `code`, `message`, `details`
- `to_error_dict()` 返回 `{"error": {"code", "message", "details"}}`

### `logging_config.py`
- structlog 配置(JSON 输出)
- 必含字段:`timestamp, tool_name, params, status, duration_ms`
- 脱敏函数:递归处理 dict/list,对 `ownerPhone/owner_phone, ownerAddr/owner_addr, chipNo/chip_no` 字段做掩码(保留前 3 后 4,或固定 `***`)

### `rest_client.py`
- `PetHospitalClient`:`AsyncClient`(httpx)
- 方法:`async def list_pets(params: dict) -> dict`(返回 Go API 的 `data` 部分)
- 超时:`httpx.Timeout(REQUEST_TIMEOUT)`
- 重试:有限次(2 次),仅对 5xx 和连接错误;4xx 不重试
- 解析响应信封:校验 `code==200`,否则抛 `BackendAPIError`
- 兼容 `records`/`charges` 为 null:在 Pydantic 模型层处理

### `tools/list_pets.py`
- `ListPetsInput(BaseModel)`:严格校验 14 个参数
  - `q, name, owner_name, owner_phone, species, doctor, disease, status, min, max, sort_by, order, page, page_size`
  - 注意:工具参数用 **snake_case**(任务要求),但发给 Go API 时转 **camelCase**(`ownerName, ownerPhone, sortBy, page, pageSize`)
  - `species` 枚举校验(7 值)、`status` 枚举校验(5 值)、`sort_by` 枚举校验(11 值)、`order` 枚举校验(`asc, desc`)
  - `page >= 1`、`1 <= page_size <= 500`、`min >= 0`、`max >= 0`、`min <= max`
  - `extra="forbid"` 拒绝未知字段
  - 自定义校验拒绝 NaN/Infinity(`math.isfinite`)
- `MedicalRecord, Treatment, Pet(BaseModel)`:兼容 `records`/`charges` 为 null(用 `Optional[List[...]] = None`,并在 `model_validator` 中把 null 转 `[]` 或保留 None)
- `ListPetsOutput(BaseModel)`:对应 Go `data`(`items, total, page, page_size, total_pages, total_cost`)
- `list_pets` 异步函数:用 `@mcp.tool()` 注册,内部:
  1. 记录开始时间
  2. 调用 `PetHospitalClient.list_pets(input.to_camel_params())`
  3. 用 `ListPetsOutput` 校验后端响应
  4. 记录日志(脱敏)
  5. 返回 `ListPetsOutput`
  6. 任何异常都包装为 `ToolError`,返回统一错误结构
- 工具 docstring:说明用途、参数、适用场景、返回值

### `server.py`
- 创建 `MCPServer("pet-hospital-mcp", version="1.0.0")`
- 注册工具:`register_tools(mcp)` 调用 `tools/list_pets.py` 的注册函数
- 用 `mcp.run(transport="streamable-http", host=..., port=..., path="/mcp", stateless_http=True, json_response=True)` 启动
- **`/health` 端点**:SDK 2.x 的 `mcp.run()` 通常用 Starlette/uvicorn 装配,需在 ASGI 层加 `/health` 路由
  - 实施时确认 SDK 2.0 是否提供 `mcp.http_app()` 返回 ASGI app,然后在 Starlette 中混合 `/health` + `/mcp`
  - 备选:用 `uvicorn` 直接跑 `mcp.run()`,再起独立 `http.server` 提供 `/health`(不推荐,违反单进程)
  - **首选方案**:用 SDK 提供的 ASGI app 组装,在 Starlette `Router` 上挂 `/health`(同步 endpoint)和 `/mcp`(SDK 的 StreamableHTTP ASGI app)

### `__main__.py`
- `main()`:加载 `Settings`,配置日志,调用 `server.main()`
- 支持 `python -m pet_hospital_mcp` 和 `pet-hospital-mcp` 命令

### `README.md`
- 安装:`pip install -e .`
- 启动顺序:先 Go 服务(`cd my-mcp-projects/pet-hospital-mcp-teaching && go run .`),再 MCP 服务(`pet-hospital-mcp`)
- 环境变量说明:`MCP_HOST, MCP_PORT, PET_HOSPITAL_BASE_URL`
- MCP 端点:`http://127.0.0.1:8081/mcp`
- 实际 SDK 版本:`mcp==2.0.0`;协议版本:`2026-07-28`;使用 `MCPServer`
- MCP Inspector 验证:`npx @modelcontextprotocol/inspector`
- SDK 2.x 客户端验证示例
- `list_pets` 调用示例(JSON-RPC)
- `/health` curl 示例
- 单元测试:`cd pet_hospital_mcp && pytest -q`,预期全部通过

### `UPGRADE_PROMPT.md`
- 阶段二的提示词模板(占位,不实现阶段二)

## 测试策略

### `tests/conftest.py`
- `mock_pet_hospital()` fixture:返回 respx MockTransport
- `sample_pet_payload()`:Go 响应信封样本(含 null `records`/`charges` 和数组两种)
- `mcp_server()` fixture:实例化 MCPServer + 注册工具(进程内测试)

### `tests/test_list_pets.py`
1. 正常调用:确认请求路径 `/api/v1/pets`、所有过滤/排序/分页参数正确转发为 camelCase
2. 输入校验失败:无效 `species`/`status`/`sort_by`/`order`、`page<1`、`pageSize>500`、`min>max`、NaN/Infinity、未知字段
3. Go 返回 4xx/5xx
4. 超时(httpx.TimeoutException)和连接异常(httpx.ConnectError)
5. 后端返回非法 JSON 或不符合数据模型(如缺字段)
6. `records`/`charges` 为 null 和数组两种表现均能解析

### `tests/test_server.py`
7. 工具注册:通过 `Client(mcp)` 进程内调用,确认 `list_tools` 返回 `list_pets` 且 JSON Schema 正确
8. `/health` 返回 200

### `tests/test_stateless_flow.py`
9. 用 `httpx.AsyncClient` 直接 POST `/mcp`(不通过 SDK Client):
   - 不发送 `initialize`
   - 不要求/不返回 `Mcp-Session-Id`
   - 调用 `server/discover` 可发现 `list_pets`(若 SDK 2.x 支持)
   - 调用 `tools/call` 命名 `list_pets` 成功
   - 验证 `/health`

## 实施步骤顺序

1. 创建目录结构 + `pyproject.toml` + `.gitignore`
2. `config.py`、`errors.py`、`logging_config.py`
3. `rest_client.py`(用 respx 测试)
4. `tools/list_pets.py`(Pydantic 模型 + 工具函数)
5. `server.py`(MCPServer + ASGI 装配 + `/health`)
6. `__main__.py`
7. 测试文件(全部)
8. `README.md`、`UPGRADE_PROMPT.md`
9. 安装 `pip install -e .`,运行 `pytest -q`

## 关键决策点(需在实施时验证)

### A. SDK 2.0 的 `stateless_http` 选项确切签名
- 已有证据(搜索结果):`mcp.run(transport="streamable-http", stateless_http=True, json_response=True)`
- 实施时:`pip show mcp` 后,检查 `mcp/server/__init__.py` 的 `MCPServer.run` 签名
- 备选:直接用 `StreamableHTTPServerTransport` 自行装配 Starlette

### B. 工具失败状态的 SDK 2.x 标记方式
- 任务要求:不沿用 1.x 的 `CallToolResult.isError`,按 2.x 实际规定
- 实施时:检查 `mcp.server` 的 `ToolError` 或 `CallToolResult` 类签名
- 倾向:抛 `mcp.server.ToolError` 或在返回值中用结构化错误(SDK 2.x 推荐结构化输出)

### C. `/health` 端点装配
- 首选:`mcp.http_app()` 返回 ASGI,再用 Starlette `Mount("/mcp", app=...)` + `Route("/health", ...)`
- 备选:若 SDK 不暴露 ASGI app 构造器,则在 `mcp.run()` 之外用 `uvicorn` + 自定义 ASGI

## 验证(端到端)

1. 启动 Go 服务:`cd e:\trae文件夹\my-mcp-projects\pet-hospital-mcp-teaching && go run . -seed`
2. 启动 MCP 服务:`cd e:\trae文件夹\pet_hospital_mcp && pet-hospital-mcp`
3. `curl http://127.0.0.1:8081/health` 返回 200
4. `curl -X POST http://127.0.0.1:8081/mcp -H "Content-Type: application/json" -H "Accept: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` 看到 `list_pets`
5. 调用 `list_pets`:`{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬","page":1,"page_size":5}}}`
6. 单元测试:`cd e:\trae文件夹\pet_hospital_mcp && pytest -q` 全部通过

## 最终交付清单

完成后输出:
1. 修改后的目录结构(树形)
2. 实际修改/创建的文件列表
3. 测试命令和真实测试结果
4. 如何启动 Go 服务和 MCP 服务
5. 如何验证无状态 MCP 连接和调用 `list_pets`
6. 明确声明:未实现阶段二工具
