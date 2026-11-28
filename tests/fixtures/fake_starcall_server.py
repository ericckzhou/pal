"""A stand-in for the StarcallOS MCP server (stdio), so the client tests need no Node and no StarcallOS database.

It exposes the same tool names, input fields and JSON result shapes as packages/mcp-server, and reports errors the
same way (a tool result with isError and the error text). Behaviour is chosen per run by flags and per call by words
in the query:

  startup flags: --startup ok|schema|dbmissing|crash|hang   --drop-tool NAME   --drop-input NAME
                 --drop-field NAME   --pid-file PATH
  query words:   sleep:N (wait N seconds)  hits:N  semantic (no keyword_rank)  crash  schema  boom
"""

import argparse
import json
import os
import sys
import time

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

MARKER = "FAKE-STARCALL-MARKER"  # tests assert this text never reaches pal's database

p = argparse.ArgumentParser()
p.add_argument("--scopes", default="passages")
p.add_argument("--embed-url")
p.add_argument("--no-embeddings", action="store_true")
p.add_argument("--startup", default="ok")
p.add_argument("--drop-tool", action="append", default=[])
p.add_argument("--drop-input", action="append", default=[])
p.add_argument("--drop-field", action="append", default=[])
p.add_argument("--pid-file")
opts = p.parse_args()

if opts.pid_file:
    with open(opts.pid_file, "w") as f:
        f.write(str(os.getpid()))

if opts.startup == "schema":
    sys.stderr.write(
        "starcall-mcp: The StarcallOS database is 2 migration(s) behind this code (next: 0042_next.sql). "
        "Open StarcallOS once so it can upgrade the database, then retry.\n"
    )
    sys.exit(1)
if opts.startup == "dbmissing":
    sys.stderr.write("starcall-mcp: StarcallOS database not found at C:\\nowhere\\stellaria.db\n")
    sys.exit(1)
if opts.startup == "crash":
    sys.stderr.write("starcall-mcp: something unexpected\n")
    sys.exit(2)
if opts.startup == "hang":
    time.sleep(3600)

scopes = set(opts.scopes.split(","))
server = Server("starcall-mcp")


def _schema(query_max: int, limit_max: int) -> dict:
    props = {
        "query": {"type": "string", "minLength": 1, "maxLength": query_max},
        "limit": {"type": "integer", "minimum": 1, "maximum": limit_max, "default": 6},
    }
    for name in opts.drop_input:
        props.pop(name, None)
    return {"type": "object", "properties": props, "required": ["query"]}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = []
    if "passages" in scopes:
        tools.append(types.Tool(name="search_passages", description="search", inputSchema=_schema(500, 10)))
        tools.append(types.Tool(name="debug_env", description="test only", inputSchema={"type": "object"}))
    if "concepts" in scopes:
        tools.append(types.Tool(name="search_concepts", description="concepts", inputSchema=_schema(200, 20)))
    return [t for t in tools if t.name not in opts.drop_tool]


def _words(query: str) -> dict[str, str]:
    out = {}
    for w in query.split():
        k, _, v = w.partition(":")
        out[k] = v
    return out


def _passages(query: str, limit: int) -> dict:
    words = _words(query)
    n = min(int(words.get("hits") or 3), limit)
    semantic = "semantic" in words
    results = []
    for i in range(n):
        hit = {
            "chunk_id": 100 + i,
            "source_id": 7,
            "page_start": i + 1,
            "page_end": i + 1,
            "section_path": ["Chapter 1"],
            "heading": "Intro",
            "text": f"{MARKER} passage {i} for {query}",
            "source_title": "Fake Book",
            "score": round(0.03 - i * 0.001, 4),
            "keyword_rank": None if semantic else i + 1,
            "vector_rank": i + 1,
        }
        for field in opts.drop_field:
            hit.pop(field, None)
        results.append(hit)
    return {"notice": "reference material", "query": query, "mode_used": "keyword", "results": results}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    query = str(arguments.get("query", ""))
    words = _words(query)
    if "sleep" in words:
        await anyio.sleep(float(words["sleep"]))
    if "crash" in words:
        os._exit(3)
    if "schema" in words:
        raise RuntimeError(
            "Error: The StarcallOS database is 1 migration(s) behind this code (next: 0042_next.sql). "
            "Open StarcallOS once so it can upgrade the database, then retry."
        )
    if "boom" in words:
        raise RuntimeError("Error: something broke")
    if name == "search_passages":
        out = _passages(query, int(arguments.get("limit", 6)))
    elif name == "search_concepts":
        out = {
            "notice": "reference material",
            "results": [{
                "concept_id": 5, "name": "Gradient Descent", "source_id": 7, "source_title": "Fake Book",
                "importance": "high", "definition": f"{MARKER} definition", "matched_in": ["name"],
            }],
        }
    elif name == "debug_env":
        out = {"keys": sorted(os.environ)}
    else:
        raise RuntimeError(f"unknown tool {name}")
    return [types.TextContent(type="text", text=json.dumps(out))]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


anyio.run(main)
