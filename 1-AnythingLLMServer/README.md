# 1-AnythingLLMServer

向 AnythingLLM 工作区**上传文档并触发向量化嵌入**的单文件网页工具。

- **形态**：纯静态单文件 `upload.html`，双击即可在浏览器打开，无需后端、无需构建。
- **依赖**：本机已运行 AnythingLLM（默认 `http://localhost:3001`）。

## 功能

1. 自动加载 AnythingLLM 中的工作区，取 **id 最小**的一个作为目标。
2. 选择本地文件上传（`POST /api/v1/document/upload`）到文档桶。
3. 将上传得到的文档 `location` 嵌入目标工作区（`POST /api/v1/workspace/{slug}/update-embeddings`），完成向量化。
4. 结果以可读文本 + 原始 JSON 形式展示。

## API Key 配置

密钥**不再硬编码**在页面源码中：

- 首次打开页面时弹窗输入 AnythingLLM 的 API Key，仅保存在**本机浏览器** `localStorage`。
- 需要更换时，点击页面上的「更换密钥」按钮，清空后重新输入。

> 若 API Key 为空，页面会给出提示并停止加载工作区，不会发起任何请求。

## 使用方式

1. 启动 AnythingLLM，确认其监听在 `http://localhost:3001`（如不同，请修改 `upload.html` 中的 `BASE` 常量）。
2. 用浏览器打开 `upload.html`。
3. 弹窗中输入 API Key。
4. 页面显示目标工作区后，选择文件并点击「上传并嵌入」。

## 涉及接口

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/api/v1/workspaces` | GET | 列出工作区 |
| `/api/v1/document/upload` | POST | 上传文档到文档桶 |
| `/api/v1/workspace/{slug}/update-embeddings` | POST | 将文档嵌入（向量化）到工作区 |

## 安全说明

- 前端页面无法安全保存密钥，本工具改用"运行时输入 + 本机 localStorage"的方式规避硬编码泄露。
- 如需让密钥**完全不进入浏览器**，应改造为后端代理，由服务端持有密钥。
