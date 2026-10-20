"""Small wrapper so agents call MCP tools and get plain dicts back."""

import json
import os
import sys
from contextlib import asynccontextmanager

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


class Tools:
    def __init__(self, client: Client):
        self._client = client

    async def call(self, name: str, **arguments) -> dict:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            raise RuntimeError(f"MCP tool {name} failed: {result.content}")
        return json.loads(result.content[0].text)


@asynccontextmanager
async def launch(module: str, env: dict | None = None):
    """Start an MCP tool server as a subprocess (stdio) and connect to it."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", module], env={**os.environ, **(env or {})})
    async with Client(params) as client:
        yield Tools(client)
