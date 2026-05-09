"""One-shot verification harness for v2.1.5.

Ingests 8 v2.1.x source files into the running HTTP service, then
calls every code-memory endpoint (find_symbols, graph_neighbors,
symbol_history, breaking_changes, soft_neighbors, file_digest,
code_overview, code_graph) and prints a green / red summary.

Run from repo root with the HTTP service up on 127.0.0.1:8765.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

WS = "agentLight"
BASE = "http://127.0.0.1:8765"


def post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read())


def step(label: str, ok: bool, detail: str = "") -> None:
    icon = "OK" if ok else "FAIL"
    print(f"[{icon}] {label}: {detail}")


def main() -> int:
    files = [
        "src/agent_memory_lite/extraction/symbol_edges_python.py",
        "src/agent_memory_lite/extraction/symbol_edges_ts.py",
        "src/agent_memory_lite/extraction/signature_similarity.py",
        "src/agent_memory_lite/ingestion/file_pipeline.py",
        "src/agent_memory_lite/api/routes/code_graph.py",
        "src/agent_memory_lite/extraction/signature_extractor.py",
        "src/agent_memory_lite/extraction/file_narrative_llm.py",
        "src/agent_memory_lite/ingestion/file_persist_similar_sigs.py",
    ]
    print(f"=== Ingest {len(files)} files into {WS!r} ===")
    total_chunks = total_edges = total_versions = 0
    for path in files:
        body = {
            "workspace_id": WS,
            "path": path,
            "content": pathlib.Path(path).read_text(encoding="utf-8"),
            "language": "python",
        }
        resp = post("/memory/ingest_file", body)
        total_chunks += resp.get("chunks_written", 0)
        total_edges += resp.get("edges_written", 0)
        total_versions += resp.get("versions_written", 0)
        print(
            f"  {path:65}  chunks={resp.get('chunks_written', 0):>3}  "
            f"edges={resp.get('edges_written', 0):>3}  "
            f"versions={resp.get('versions_written', 0):>3}"
        )
    print(f"\nTotals: chunks={total_chunks} edges={total_edges} versions={total_versions}")

    print("\n=== Endpoint smoke ===")

    r = post("/memory/find_symbols", {"workspace_id": WS, "name_prefix": "extract_"})
    qns = [h["qualified_name"] for h in r["hits"]]
    step("v1.4 find_symbols(prefix=extract_)", len(qns) >= 2, f"{len(qns)} hits — {qns[:4]}")

    r = post(
        "/memory/graph_neighbors",
        {"workspace_id": WS, "qualified_name": "extract_python_edges", "direction": "upstream"},
    )
    callers = [e["src_qualified_name"] for e in r["upstream"]]
    step("v1.5 graph_neighbors(extract_python_edges, upstream)", True, f"{len(callers)} callers")

    r = post(
        "/memory/symbol_history",
        {"workspace_id": WS, "qualified_name": "extract_python_edges"},
    )
    step("v1.6 symbol_history", r["total"] >= 1, f"total={r['total']}")

    r = post("/memory/breaking_changes", {"workspace_id": WS, "since_days": 365})
    step("v1.6 breaking_changes(365d)", True, f"total={r['total']}")

    r = post(
        "/memory/soft_neighbors",
        {
            "workspace_id": WS,
            "src_qualified_name": "extract_python_edges",
            "edge_kinds": ["co_changed", "similar_signature"],
        },
    )
    kinds = {n["edge_kind"] for n in r["neighbors"]}
    step("v1.7+v2.1.4 soft_neighbors", True, f"{r['total']} neighbors, kinds={kinds}")

    r = post(
        "/memory/file_digest",
        {
            "workspace_id": WS,
            "file_path": "src/agent_memory_lite/extraction/symbol_edges_python.py",
        },
    )
    src = r["structured"].get("narrative_source", "?")
    step(
        "v1.8+v2.1.3 file_digest",
        r["symbol_count"] > 0,
        f"symbols={r['symbol_count']}  source={src}",
    )

    r = get(f"/memory/code_overview?workspace_id={WS}")
    counts = r["counts"]
    step(
        "v2.0 code_overview",
        counts["files"] > 0 and counts["edges"] > 0,
        f"files={counts['files']} chunks={counts['chunks']} symbols={counts['symbols']} "
        f"edges={counts['edges']} versions={counts['versions']} soft={counts['soft_edges']}",
    )

    r = get(f"/memory/code_graph?workspace_id={WS}&max_nodes=50")
    step(
        "v2.1.2 code_graph(overview)",
        len(r["nodes"]) > 0,
        f"nodes={len(r['nodes'])}  links={len(r['links'])}  truncated={r['truncated']}",
    )

    print("\n=== UI smoke ===")
    for ui in ("/ui", "/ui/code", "/ui/graph"):
        try:
            with urllib.request.urlopen(f"{BASE}{ui}", timeout=5) as resp:
                ok = resp.status == 200 and b"html" in resp.read(200).lower()
                step(f"GET {ui}", ok)
        except urllib.error.HTTPError as exc:
            step(f"GET {ui}", False, str(exc))

    print("\n=== DONE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
