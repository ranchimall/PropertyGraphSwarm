#!/usr/bin/env python3
"""
executor.py

Walks a PropertyGraph and drives execution:
  1. Wrap every node in its NodeAgent (via agents.make_agent).
  2. Start from "root" nodes (no incoming edges) — or an explicit start list.
  3. Visit a node: call agent.act().
  4. For each outgoing edge: call agent.handle_relation(edge, target_agent),
     and if it returns True, recurse into the target node (once each).

This keeps traversal generic and reusable for *any* property graph matching
the schema, as long as agents.py has NodeAgent subclasses registered for the
node types that appear.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from agents import NodeAgent, make_agent
from graph_loader import PropertyGraph, load_graph


class GraphExecutor:
    def __init__(self, graph: PropertyGraph):
        self.graph = graph
        self.agents: dict[str, NodeAgent] = {
            node_id: make_agent(node, graph) for node_id, node in graph.nodes.items()
        }
        self.visited: set[str] = set()

    def agent_for(self, node_id: str) -> NodeAgent:
        return self.agents[node_id]

    def run(self, start_ids: Optional[Iterable[str]] = None) -> List[str]:
        """
        Execute the graph, returning the visit order (node ids).
        If start_ids is omitted, execution starts from every root node
        (nodes with no incoming edges), in graph order.
        """
        self.visited.clear()
        order: List[str] = []

        starts = list(start_ids) if start_ids is not None else [n.id for n in self.graph.roots()]
        if not starts:
            # Cyclic graph with no natural root — fall back to node insertion order.
            starts = list(self.graph.nodes.keys())

        for node_id in starts:
            self._visit(node_id, order)

        # Catch any nodes never reached from the chosen starting points
        # (e.g. disconnected subgraphs) so nothing silently gets skipped.
        for node_id in self.graph.nodes:
            if node_id not in self.visited:
                self._visit(node_id, order)

        return order

    def _visit(self, node_id: str, order: List[str]) -> None:
        if node_id in self.visited:
            return
        self.visited.add(node_id)
        order.append(node_id)

        agent = self.agent_for(node_id)
        agent.act()

        for edge in self.graph.outgoing(node_id):
            target_agent = self.agent_for(edge.target)
            should_follow = agent.handle_relation(edge, target_agent)
            if should_follow and edge.target not in self.visited:
                self._visit(edge.target, order)


def run_graph(path: str) -> List[str]:
    """Convenience one-liner: load a graph JSON file and execute it."""
    graph = load_graph(path)
    executor = GraphExecutor(graph)
    print(f"=== Executing graph: {graph.meta.get('goal', path)} ===\n")
    order = executor.run()
    print(f"\n=== Done. Visit order: {order} ===")
    return order


if __name__ == "__main__":
    import sys

    json_path = sys.argv[1] if len(sys.argv) > 1 else "property_graph_1785409993.json"
    run_graph(json_path)
