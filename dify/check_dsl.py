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
        "encode_ctx.payload",
        "encode_mem.payload",
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

    # Dify stores default_value as a list of typed output variables. A scalar
    # passes YAML parsing but prevents Dify from handling failures at runtime.
    for node_id, node in nodes.items():
        data = node.get("data", {})
        if data.get("type") in ("http-request", "llm"):
            if "error_strategy" not in data:
                problems.append(f"{node_id}: no error_strategy, a failure would block the flow")
            elif data["error_strategy"] != "default-value":
                problems.append(
                    f"{node_id}: error_strategy is {data['error_strategy']!r}, "
                    "expected the scalar 'default-value'"
                )
            defaults = data.get("default_value")
            expected = {"text": "string"} if data["type"] == "llm" else {
                "body": "string", "status_code": "number", "headers": "object"
            }
            if not isinstance(defaults, list):
                problems.append(f"{node_id}: default_value must be a typed list")
            else:
                by_key = {entry.get("key"): entry for entry in defaults if isinstance(entry, dict)}
                for key, value_type in expected.items():
                    entry = by_key.get(key, {})
                    if entry.get("type") != value_type or "value" not in entry:
                        problems.append(f"{node_id}: missing {value_type} fallback for {key}")

    # the structure that the importer of Dify expects
    workflow = doc.get("workflow", {})
    for key in ("conversation_variables", "environment_variables"):
        if key not in workflow:
            problems.append(f"workflow.{key} is missing")
    if str(doc.get("version")) != "0.3.0":
        problems.append(
            f"version is {doc.get('version')!r}: Dify only imports a DSL whose version is not "
            "newer than the instance, 0.3.0 is the version Dify 1.x writes and migrates from"
        )
    if "rag_pipeline_variables" in workflow:
        problems.append("workflow.rag_pipeline_variables only exists from DSL version 0.4.0 on")
    for key in ("dependencies", "kind", "version"):
        if key not in doc:
            problems.append(f"{key} is missing")
    if doc.get("kind") != "app":
        problems.append(f"kind is {doc.get('kind')!r}, expected 'app'")
    if doc.get("app", {}).get("mode") != "advanced-chat":
        problems.append("app.mode is not advanced-chat")

    # the fields of a http request node that Dify fills with its defaults
    for node_id, node in nodes.items():
        data = node.get("data", {})
        if data.get("type") != "http-request":
            continue
        if data.get("method") != str(data.get("method", "")).lower():
            problems.append(f"{node_id}: the method {data.get('method')!r} is not lower case")
        if "ssl_verify" not in data:
            problems.append(f"{node_id}: ssl_verify is missing")
        timeout = data.get("timeout", {})
        for key in ("max_connect_timeout", "max_read_timeout", "max_write_timeout"):
            if key not in timeout:
                problems.append(f"{node_id}: timeout.{key} is missing")
        for key in ("connect", "read", "write"):
            if key in timeout:
                problems.append(f"{node_id}: timeout.{key} is not a field of the Dify timeout")
        body = data.get("body", {})
        if body.get("type") == "json":
            items = body.get("data")
            if not isinstance(items, list) or len(items) != 1:
                problems.append(f"{node_id}: a json body carries exactly one entry")
            elif items[0].get("key") not in ("", None):
                problems.append(f"{node_id}: a json body has no key")
        if node_id in ("ctx", "mem"):
            expected_ref = f"{{{{#encode_{node_id}.payload#}}}}"
            if body.get("type") != "raw-text" or body.get("data", [{}])[0].get("value") != expected_ref:
                problems.append(f"{node_id}: request must use the serialized JSON payload")
        if not str(data.get("url", "")).startswith("http"):
            problems.append(f"{node_id}: the url {data.get('url')!r} is not a http address")

    # the model of the keeper
    llm_nodes = [node for node in nodes.values() if node.get("data", {}).get("type") == "llm"]
    if not llm_nodes:
        problems.append("the flow has no llm node")
    for node in llm_nodes:
        model = node.get("data", {}).get("model", {})
        if not model.get("provider") or not model.get("name"):
            problems.append("the llm node has no model provider and name")

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
