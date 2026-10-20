"""AnythingLLM MCP Server (MVP).

Single tool: ask the first AnythingLLM workspace a question and return the AI answer.
Transport: Streamable HTTP (MCP protocol version 2026-07-28 via mcp SDK 2.0.0).
"""

import os

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


@mcp.tool()
def ask_first_workspace(question: str) -> str:
    """Ask a question to the first (lowest-id) AnythingLLM workspace and return the AI answer.

    The workspace's embedded documents are used as context for the answer.
    Returns the AI text response followed by the number of source chunks cited.
    """
    with httpx.Client(timeout=60.0) as client:
        # 1. List workspaces, pick the one with the smallest id.
        resp = client.get(
            f"{ANYTHINGLLM_BASE_URL}/api/v1/workspaces",
            headers=_anythingllm_headers(),
        )
        resp.raise_for_status()
        workspaces = resp.json().get("workspaces", [])
        if not workspaces:
            return "Error: no workspaces found in AnythingLLM."
        first = min(workspaces, key=lambda w: w.get("id", 0))
        slug = first["slug"]
        ws_name = first.get("name", "<unnamed>")

        # 2. Chat with that workspace (non-streaming).
        chat_resp = client.post(
            f"{ANYTHINGLLM_BASE_URL}/api/v1/workspace/{slug}/chat",
            headers=_anythingllm_headers(),
            json={"message": question, "mode": "query"},
        )
        chat_resp.raise_for_status()
        data = chat_resp.json()

    text = data.get("textResponse", "").strip() or "(no answer)"
    if data.get("error"):
        text = f"AnythingLLM error: {data['error']}"
    sources = data.get("sources") or []
    src_titles = []
    for s in sources[:5]:
        t = s.get("title") if isinstance(s, dict) else None
        if t:
            src_titles.append(t)
    src_line = ""
    if src_titles:
        src_line = "\n\n来源(" + str(len(sources)) + "): " + ", ".join(src_titles)
    elif sources:
        src_line = "\n\n来源数: " + str(len(sources))
    return f"[工作区: {ws_name}]\n{text}{src_line}"


def main() -> None:
    # Streamable HTTP transport, MCP protocol 2026-07-28.
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
