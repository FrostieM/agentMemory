# Code Memory Guide

This guide documents the active v3 code-memory surface. Historical v1/v2 tool
names are retired from the agent-facing contract; use the compact v3 tools and
the read-only UI pages below.

## Active Surfaces

- `memory_impact_check(file_path=...)`: first call before reading or editing a
  source file. Returns the file digest, callers, hot symbols, verdict, and an
  advisory in one compact envelope.
- `memory_search(query=..., kinds=["code_digest"|"chunk"])`: compact discovery
  for indexed files and code chunks.
- `memory_get(kind="code_digest"|"chunk", id=..., fields=...)`: exact fetch for
  the one object you actually need after discovery.
- `/memory/ingest_file`: HTTP ingestion path used by hooks and maintenance
  scripts to refresh code chunks, edges, versions, and digests.
- `memory_impact_check`: compact v3 agent-facing summary for files and symbols.

## Common Workflows

Before editing a file:

```text
memory_impact_check(file_path="src/agent_memory_lite/example.py")
```

If the verdict is `medium` or `high`, inspect the returned callers and hot
symbols before changing signatures. If the file is not indexed, read the file as
a fallback and let the post-tool digest hook refresh it after the edit.

To find code by name or topic:

```text
memory_search(query="impact_check", kinds=["code_digest", "chunk"], limit=10)
memory_get(kind="chunk", id="<chunk_id>", fields=["text"])
```

To refresh a file after a change:

```text
POST /memory/ingest_file
```

with `workspace_id`, `path`, `content`, and `language`.

## Removed Agent Surfaces

The old file-digest and active-edit route family is no longer mounted. Use
`memory_impact_check` for file-level context and `memory_search` /
`memory_get` for compact projections. Coordination should live in task and
plan-step memory rather than short-lived legacy active-edit routes.

## Backstops

Empty workspaces return empty arrays and `not_indexed` verdicts. LLM narrative
failures fall back to heuristic digests during ingestion. Tree-sitter gaps fall
back to chunking without structural symbols; the service remains usable.
