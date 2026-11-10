"""AnythingLLM MCP Server.

Tools:
- list_workspaces / create_workspace         : workspace management
- upload_document                            : upload a local file then embed it into a workspace
- ask_workspace / ask_first_workspace        : RAG chat against a workspace

Transport: Streamable HTTP (MCP protocol version 2026-07-28 via mcp SDK 2.0.0).
"""

import os
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer

# AnythingLLM connection config (read from env; API key is required).
ANYTHINGLLM_BASE_URL = os.environ.get("ANYTHINGLLM_BASE_URL", "http://localhost:3001")
ANYTHINGLLM_API_KEY = os.environ.get("ANYTHINGLLM_API_KEY")
if not ANYTHINGLLM_API_KEY:
    raise RuntimeError("环境变量 ANYTHINGLLM_API_KEY 未设置")

mcp = MCPServer(name="anythingllm-mcp")


def _anythingllm_headers() -> dict:
    return {
        "Authorization": f"Bearer {ANYTHINGLLM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _client() -> httpx.Client:
    return httpx.Client(timeout=60.0)


def _list_workspaces(client: httpx.Client) -> list[dict]:
    resp = client.get(
        f"{ANYTHINGLLM_BASE_URL}/api/v1/workspaces",
        headers=_anythingllm_headers(),
    )
    resp.raise_for_status()
    return resp.json().get("workspaces", [])


def _pick_workspace(client: httpx.Client, slug: str = "") -> dict:
    workspaces = _list_workspaces(client)
    if not workspaces:
        raise LookupError("no workspaces found in AnythingLLM.")
    if slug:
        for w in workspaces:
            if w.get("slug") == slug:
                return w
        raise LookupError(f"workspace slug 不存在: {slug}")
    return min(workspaces, key=lambda w: w.get("id", 0))


@mcp.tool()
def list_workspaces() -> str:
    """List all AnythingLLM workspaces with their id, name, slug and document count."""
    try:
        with _client() as client:
            workspaces = _list_workspaces(client)
    except httpx.HTTPError as exc:
        return f"Error: 无法连接 AnythingLLM: {exc}"
    if not workspaces:
        return "当前没有任何工作区。"
    lines = ["id\tname\tslug\tdocuments"]
    for w in sorted(workspaces, key=lambda x: x.get("id", 0)):
        lines.append(
            f"{w.get('id')}\t{w.get('name', '<unnamed>')}\t{w.get('slug', '')}\t"
            f"{len(w.get('documents') or [])}"
        )
    return "\n".join(lines)


@mcp.tool()
def create_workspace(name: str) -> str:
    """Create a new AnythingLLM workspace and return its id / name / slug."""
    name = name.strip()
    if not name:
        return "Error: 工作区名称不能为空。"
    try:
        with _client() as client:
            resp = client.post(
                f"{ANYTHINGLLM_BASE_URL}/api/v1/workspace/new",
                headers=_anythingllm_headers(),
                json={"name": name},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return f"Error: 创建工作区失败: {exc}"
    if data.get("error"):
        return f"Error: {data['error']}"
    ws = data.get("workspace") or {}
    return f"已创建工作区：{ws.get('name', name)}（slug={ws.get('slug', '')}）"


@mcp.tool()
def upload_document(file_path: str, workspace_slug: str = "") -> str:
    """Upload a local file to AnythingLLM and embed it into a workspace.

    file_path is a path on the machine running this MCP server. workspace_slug may be
    omitted, in which case the workspace with the smallest id is used.
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        return f"Error: 文件不存在: {file_path}"
    try:
        with _client() as client:
            ws = _pick_workspace(client, workspace_slug)
            with path.open("rb") as fh:
                resp = client.post(
                    f"{ANYTHINGLLM_BASE_URL}/api/v1/document/upload",
                    headers={"Authorization": f"Bearer {ANYTHINGLLM_API_KEY}"},
                    files={"file": (path.name, fh)},
                    data={"addToWorkspaces": ws["slug"]},
                )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                return f"Error: 上传失败: {data.get('error') or data}"
            docs = data.get("documents") or []
            adds = [d.get("location") for d in docs if d.get("location")]
            if adds:
                embed = client.post(
                    f"{ANYTHINGLLM_BASE_URL}/api/v1/workspace/{ws['slug']}/update-embeddings",
                    headers=_anythingllm_headers(),
                    json={"adds": adds, "deletes": []},
                )
                embed.raise_for_status()
                if embed.json().get("error"):
                    return f"Error: 嵌入失败: {embed.json()['error']}"
    except LookupError as exc:
        return f"Error: {exc}"
    except httpx.HTTPError as exc:
        return f"Error: 上传文档失败: {exc}"
    names = ", ".join(d.get("name", "") for d in docs if d.get("name"))
    return f"已上传并嵌入到工作区 {ws.get('name')}：{names or path.name}"


@mcp.tool()
def ask_workspace(question: str, workspace_slug: str = "") -> str:
    """Ask a question to a specific AnythingLLM workspace (RAG query mode).

    workspace_slug may be omitted to use the workspace with the smallest id.
    Returns the AI answer followed by cited source titles.
    """
    try:
        with _client() as client:
            ws = _pick_workspace(client, workspace_slug)
            chat_resp = client.post(
                f"{ANYTHINGLLM_BASE_URL}/api/v1/workspace/{ws['slug']}/chat",
                headers=_anythingllm_headers(),
                json={"message": question, "mode": "query"},
            )
            chat_resp.raise_for_status()
            data = chat_resp.json()
    except LookupError as exc:
        return f"Error: {exc}"
    except httpx.HTTPError as exc:
        return f"Error: 提问失败: {exc}"

    text = data.get("textResponse", "").strip() or "(no answer)"
    if data.get("error"):
        text = f"AnythingLLM error: {data['error']}"
    sources = data.get("sources") or []
    src_titles = [s.get("title") for s in sources[:5] if isinstance(s, dict) and s.get("title")]
    src_line = ""
    if src_titles:
        src_line = "\n\n来源(" + str(len(sources)) + "): " + ", ".join(src_titles)
    elif sources:
        src_line = "\n\n来源数: " + str(len(sources))
    return f"[工作区: {ws.get('name', '<unnamed>')}]\n{text}{src_line}"


@mcp.tool()
def ask_first_workspace(question: str) -> str:
    """Ask a question to the first (lowest-id) workspace. Kept for backward compatibility."""
    return ask_workspace(question)


def main() -> None:
    # Streamable HTTP transport, MCP protocol 2026-07-28.
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
