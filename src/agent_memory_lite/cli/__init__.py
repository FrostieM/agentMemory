"""memory-cli — shell-friendly client for v3 HTTP surface.

For agents that have shell access but no MCP support (Aider, Codex CLI,
Bash-only agents, CI scripts). Mirrors the 8 v3 endpoints 1:1, prints
JSON to stdout, exits non-zero on envelope error.
"""
