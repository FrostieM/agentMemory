"""HTTP / FastAPI surface.

`create_app` is intentionally not re-exported at package import time to keep
`from agent_memory_lite.api.errors import ...` cheap (no FastAPI route
registration triggered). Use `from agent_memory_lite.api.app import create_app`
explicitly when you need the app factory.
"""
