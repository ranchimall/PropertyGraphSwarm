#!/usr/bin/env python3
"""
graph_loader.py

Loads a property-graph JSON file (as produced by property_graph_creator.py)
into simple, typed Python objects: Node, Edge, PropertyGraph.

This module has no knowledge of *behavior* — it just parses structure.
Behavior lives in agents.py, and traversal/orchestration lives in executor.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Node:
    id: str
    type: str
    properties: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.properties.get(key, default)


@dataclass
class Edge:
    source: str
    target: str
    relation: str
    properties: Dict[str, Any] = field(default_factory=dict)


class PropertyGraph:
    """
    In-memory representation of a property graph, with convenient lookup
    and traversal helpers on top of the raw nodes/edges lists.
    """

    def __init__(self, meta: Dict[str, Any], nodes: List[Node], edges: List[Edge]):
        self.meta = meta
        self.nodes: Dict[str, Node] = {n.id: n for n in nodes}
        self.edges: List[Edge] = edges

        # Adjacency indexes, built once for O(1) traversal during execution.
        self._outgoing: Dict[str, List[Edge]] = {}
        self._incoming: Dict[str, List[Edge]] = {}
        for e in edges:
            self._outgoing.setdefault(e.source, []).append(e)
            self._incoming.setdefault(e.target, []).append(e)

    # -- lookups -----------------------------------------------------------

    def get_node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def nodes_of_type(self, node_type: str) -> List[Node]:
        return [n for n in self.nodes.values() if n.type == node_type]

    def outgoing(self, node_id: str) -> List[Edge]:
        return self._outgoing.get(node_id, [])

    def incoming(self, node_id: str) -> List[Edge]:
        return self._incoming.get(node_id, [])

    def roots(self) -> List[Node]:
        """
        Nodes with no incoming edges. These are natural entry points for
        traversal — e.g. the Agent nodes that "drive" a house-building
        project, since nothing points at them.
        """
        return [n for n in self.nodes.values() if n.id not in self._incoming]

    def __repr__(self) -> str:
        return f"PropertyGraph(nodes={len(self.nodes)}, edges={len(self.edges)})"


def load_graph(path: str | Path) -> PropertyGraph:
    """Read a property-graph JSON file from disk and return a PropertyGraph."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return graph_from_dict(raw)


def graph_from_dict(raw: Dict[str, Any]) -> PropertyGraph:
    """Build a PropertyGraph from an already-parsed dict (same schema as the JSON file)."""
    nodes = [
        Node(id=n["id"], type=n["type"], properties=n.get("properties", {}) or {})
        for n in raw.get("nodes", [])
    ]
    edges = [
        Edge(
            source=e["source"],
            target=e["target"],
            relation=e["relation"],
            properties=e.get("properties", {}) or {},
        )
        for e in raw.get("edges", [])
    ]
    return PropertyGraph(meta=raw.get("meta", {}), nodes=nodes, edges=edges)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "property_graph_1785409993.json"
    g = load_graph(path)
    print(g)
    print("Roots:", [n.id for n in g.roots()])
