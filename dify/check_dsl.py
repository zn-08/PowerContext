"""Static checks for the Dify DSL: parse it and validate the graph and templates."""

from __future__ import annotations

import re
import sys

import yaml

TEMPLATE = re.compile(r"\{\{#([^#]+)#\}\}")


def collect_templates(node: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "prompt_template":
                    for entry in value or []:
                        found.append((entry.get("id", "?"), entry.get("text", "")))
                elif isinstance(value, (dict, list)):
                    stack.append(value)
                elif isinstance(value, str):
                    found.append((key, value))
        elif isinstance(current, list):
            stack.extend(current)
    return found


def main() -> int:
    path = sys.argv[1]
    with open(path, encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)

    problems: list[str] = []
    if doc.get("kind") != "app":
        problems.append(f"kind is {doc.get('kind')!r}, expected 'app'")
    if not doc.get("version"):
        problems.append("missing version")
    if doc.get("app", {}).get("mode") != "advanced-chat":
        problems.append("app.mode is not advanced-chat")

    graph = doc.get("workflow", {}).get("graph", {})
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    if len(nodes) != len(graph.get("nodes", [])):
        problems.append("duplicate node ids")
    for edge in edges:
        for side in ("source", "target"):
            if edge.get(side) not in nodes:
                problems.append(f"edge {edge.get('id')} {side}={edge.get(side)!r} is not a node")

    types = [n.get("data", {}).get("type") for n in nodes.values()]
    if types.count("start") != 1:
        problems.append(f"expected exactly one start node, found {types.count('start')}")
    if types.count("answer") != 1:
        problems.append(f"expected exactly one answer node, found {types.count('answer')}")

    # every node must be reachable from the start node
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        adjacency.setdefault(edge.get("source"), []).append(edge.get("target"))
    start = next((nid for nid, n in nodes.items() if n.get("data", {}).get("type") == "start"), None)
    seen: set[str] = set()
    stack = [start] if start else []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacency.get(current, []))
    unreachable = set(nodes) - seen
    if unreachable:
        problems.append(f"unreachable nodes: {sorted(unreachable)}")

    # every node except the answer must have an outgoing edge
    answer = next((nid for nid, n in nodes.items() if n.get("data", {}).get("type") == "answer"), None)
    dangling = [nid for nid in seen if nid not in (start, answer) and not adjacency.get(nid)]
    if dangling:
        problems.append(f"nodes without an outgoing edge: {dangling}")

    # templates may only reference declared inputs and known node outputs
    allowed = {
        "start.session_id",
        "start.player_input",
        "ctx.body",
        "llm.text",
    }
    for node_id, node in nodes.items():
        for where, text in collect_templates(node.get("data", {})):
            for reference in TEMPLATE.findall(text or ""):
                if reference not in allowed:
                    problems.append(f"{node_id}/{where}: unknown template reference {reference!r}")

    # the env variable trick is fragile: the flow must not depend on it
    for node_id, node in nodes.items():
        for where, text in collect_templates(node.get("data", {})):
            if "env." in (text or ""):
                problems.append(f"{node_id}/{where}: still references an env variable")

    # every node that can fail needs a fallback so the main flow cannot break
    for node_id, node in nodes.items():
        data = node.get("data", {})
        if data.get("type") in ("http-request", "llm"):
            if "error_strategy" not in data:
                problems.append(f"{node_id}: no error_strategy, a failure would block the flow")
            else:
                default = data["error_strategy"].get("default_value")
                if not default:
                    problems.append(f"{node_id}: error_strategy without a default_value")

    print(f"{path}: version={doc.get('version')} nodes={len(nodes)} edges={len(edges)}")
    for node_id, node in nodes.items():
        data = node.get("data", {})
        print(f"  node {node_id:<8} type={data.get('type'):<13} title={data.get('title')}")
    if problems:
        print("PROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: no problems found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
