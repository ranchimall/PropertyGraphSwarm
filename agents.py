#!/usr/bin/env python3
"""
agents.py

Behavior layer for the property graph. Every *node* in the graph gets wrapped
in a NodeAgent subclass chosen by the node's "type" field (Project, Room,
Agent, Material, ...). NodeAgent.act() defines what happens when the executor
visits that node; NodeAgent.handle_relation() defines what happens when the
executor follows an outgoing edge from that node to another one.

Note: the graph's own node type happens to be literally called "Agent"
(architect / contractor) — that's a *domain* concept (an actor in the house-
building process). Don't confuse it with NodeAgent, which is our *code*
wrapper around every node, regardless of its domain type.

To support a new node type, just subclass NodeAgent and register it in
AGENT_REGISTRY (or call register_agent as a decorator).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Type

from graph_loader import Edge, Node, PropertyGraph


class NodeAgent:
    """Base class for all node behaviors."""

    def __init__(self, node: Node, graph: PropertyGraph):
        self.node = node
        self.graph = graph

    # Called once when the executor first visits this node.
    def act(self) -> None:
        print(f"[{self.node.type}] {self.node.id}: (no custom behavior defined)")

    # Called for each outgoing edge from this node, before the executor
    # recurses into the target node. Return False to prune traversal past
    # this edge (e.g. to stop at a leaf, or to skip a relation you don't
    # care about).
    def handle_relation(self, edge: Edge, target: "NodeAgent") -> bool:
        print(f"    {self.node.id} --{edge.relation}--> {target.node.id}")
        return True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.node.id})"


# --------------------------------------------------------------------------
# Registry: node "type" string -> NodeAgent subclass
# --------------------------------------------------------------------------

AGENT_REGISTRY: Dict[str, Type[NodeAgent]] = {}


def register_agent(node_type: str) -> Callable[[Type[NodeAgent]], Type[NodeAgent]]:
    """Class decorator: register a NodeAgent subclass for a given node type."""

    def _wrap(cls: Type[NodeAgent]) -> Type[NodeAgent]:
        AGENT_REGISTRY[node_type] = cls
        return cls

    return _wrap


def make_agent(node: Node, graph: PropertyGraph) -> NodeAgent:
    """Factory: instantiate the right NodeAgent subclass for a node."""
    cls = AGENT_REGISTRY.get(node.type, NodeAgent)
    return cls(node, graph)


# --------------------------------------------------------------------------
# Concrete behaviors for this graph's node types
# --------------------------------------------------------------------------

@register_agent("Project")
class ProjectAgent(NodeAgent):
    def act(self) -> None:
        goal = self.graph.meta.get("goal", self.node.id)
        print(f"[Project] '{goal}' — kicking off ({self.node.id})")

    def handle_relation(self, edge: Edge, target: NodeAgent) -> bool:
        if edge.relation == "contains":
            print(f"    {self.node.id} plans to include room: {target.node.id}")
            return True
        return super().handle_relation(edge, target)


@register_agent("Room")
class RoomAgent(NodeAgent):
    def act(self) -> None:
        function = self.node.get("function", "unspecified purpose")
        print(f"[Room] {self.node.id}: designing space for '{function}'")


@register_agent("Material")
class MaterialAgent(NodeAgent):
    def act(self) -> None:
        material_type = self.node.get("type", "unknown material")
        print(f"[Material] {self.node.id}: sourcing '{material_type}'")


@register_agent("Agent")
class ActorAgent(NodeAgent):
    """
    Represents a human/AI actor in the process (architect, contractor, ...).
    This is the graph's "Agent" node *type* — see module docstring.
    """

    def act(self) -> None:
        role = self.node.get("role", "worker")
        print(f"[Actor] {self.node.id}: acting as {role}")

    def handle_relation(self, edge: Edge, target: NodeAgent) -> bool:
        if edge.relation == "creates":
            print(f"    {self.node.id} ({self.node.get('role')}) creates {target.node.id}")
            return True
        if edge.relation == "requires":
            print(f"    {self.node.id} ({self.node.get('role')}) requires {target.node.id}")
            return True
        return super().handle_relation(edge, target)
