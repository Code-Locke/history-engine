"""
world.py — Graph, nodes, and edges for history_engine.

Owns:
  - WorldNode: named location with resources, ownership, and history
  - WorldEdge: connection between nodes with traversal cost and contested state
  - World: networkx graph wrapper with helper methods for the simulation loop

Does NOT own:
  - Entity state or personality (entity.py)
  - Action resolution logic (actions.py)
  - Event significance detection (events.py)
  - LLM narration (narrator.py)

Graph is undirected — edges are symmetric. If directed traversal is ever needed
(e.g. one-way mountain passes), swap nx.Graph for nx.DiGraph here.
"""

from __future__ import annotations

import networkx as nx
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class WorldNode:
    """
    A named location in the world graph.

    Attributes:
        name:        Unique identifier and narrative label for the location.
        features:    Qualitative descriptors (e.g. ["mountain", "river", "ruin"]).
                     Used by the LLM for flavour; no mechanical effect here.
        resources:   Dict of resource type → quantity (e.g. {"grain": 3, "iron": 1}).
                     Asymmetry in resources is the engine of conflict.
        owner:       Name of the entity that controls this node, or None if neutral.
        is_capital:  True if this node is the owning entity's capital. Losing a
                     capital is a significant event (see events.py).
        history:     Ordered log of event strings. First-class — fed directly to
                     the LLM narrator for contextual narration.
        explored_by: Set of entity names that have revealed this node.
    """
    name: str
    features: list[str] = field(default_factory=list)
    resources: dict[str, int] = field(default_factory=dict)
    owner: Optional[str] = None
    is_capital: bool = False
    history: list[str] = field(default_factory=list)
    explored_by: set[str] = field(default_factory=set)

    def log(self, event: str) -> None:
        """Append an event string to this node's history."""
        self.history.append(event)

    def is_neutral(self) -> bool:
        """True if no entity owns this node."""
        return self.owner is None

    def is_explored_by(self, entity_name: str) -> bool:
        """True if the given entity has explored this node."""
        return entity_name in self.explored_by

    def total_resources(self) -> int:
        """Sum of all resource values. Useful for quick utility comparisons."""
        return sum(self.resources.values())

    def __repr__(self) -> str:
        owner_str = self.owner or "neutral"
        capital_str = " [CAPITAL]" if self.is_capital else ""
        return f"<WorldNode '{self.name}' owner={owner_str}{capital_str} resources={self.resources}>"


@dataclass
class WorldEdge:
    """
    A connection between two nodes.

    Attributes:
        traversal_cost: Abstract movement cost. Higher = harder to cross.
                        Used by utility scoring to weight distant actions.
        contested:      True if two or more entities are actively competing
                        for control of this edge's endpoints. Updated by
                        actions.py during conflict resolution.
    """
    traversal_cost: int = 1
    contested: bool = False

    def __repr__(self) -> str:
        status = "contested" if self.contested else "open"
        return f"<WorldEdge cost={self.traversal_cost} {status}>"


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------

class World:
    """
    The world graph. Wraps networkx.Graph and exposes helpers for the
    simulation loop and downstream modules.

    Nodes are stored as WorldNode objects in the graph's node attribute dict
    under the key 'data'. Edges are stored similarly under 'data'.

    Example:
        world = World()
        world.add_node(WorldNode(name="Ashenveil", features=["forest"], resources={"timber": 4}))
        world.add_node(WorldNode(name="Ironhold", features=["mountain"], resources={"iron": 5}, is_capital=True))
        world.add_edge("Ashenveil", "Ironhold", WorldEdge(traversal_cost=2))
    """

    def __init__(self) -> None:
        self._graph: nx.Graph = nx.Graph()

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_node(self, node: WorldNode) -> None:
        """Add a WorldNode to the graph. Raises if name already exists."""
        if node.name in self._graph:
            raise ValueError(f"Node '{node.name}' already exists in the world.")
        self._graph.add_node(node.name, data=node)

    def add_edge(self, name_a: str, name_b: str, edge: Optional[WorldEdge] = None) -> None:
        """
        Connect two existing nodes with a WorldEdge.
        Defaults to WorldEdge() (cost=1, not contested) if none provided.
        """
        self._require_node(name_a)
        self._require_node(name_b)
        if edge is None:
            edge = WorldEdge()
        self._graph.add_edge(name_a, name_b, data=edge)

    # ------------------------------------------------------------------
    # Node access
    # ------------------------------------------------------------------

    def get_node(self, name: str) -> WorldNode:
        """Return the WorldNode for the given name. Raises if not found."""
        self._require_node(name)
        return self._graph.nodes[name]["data"]

    def all_nodes(self) -> list[WorldNode]:
        """Return all WorldNodes in the graph."""
        return [self._graph.nodes[n]["data"] for n in self._graph.nodes]

    def get_nodes_by_owner(self, entity_name: str) -> list[WorldNode]:
        """Return all nodes owned by the given entity."""
        return [n for n in self.all_nodes() if n.owner == entity_name]

    def get_neutral_nodes(self) -> list[WorldNode]:
        """Return all unowned nodes."""
        return [n for n in self.all_nodes() if n.is_neutral()]

    def get_capital(self, entity_name: str) -> Optional[WorldNode]:
        """Return the capital node of the given entity, or None if none exists."""
        for node in self.get_nodes_by_owner(entity_name):
            if node.is_capital:
                return node
        return None

    # ------------------------------------------------------------------
    # Edge access
    # ------------------------------------------------------------------

    def get_edge(self, name_a: str, name_b: str) -> WorldEdge:
        """Return the WorldEdge between two nodes. Raises if not found."""
        if not self._graph.has_edge(name_a, name_b):
            raise ValueError(f"No edge between '{name_a}' and '{name_b}'.")
        return self._graph.edges[name_a, name_b]["data"]

    def all_edges(self) -> list[tuple[str, str, WorldEdge]]:
        """Return all edges as (name_a, name_b, WorldEdge) triples."""
        return [
            (a, b, self._graph.edges[a, b]["data"])
            for a, b in self._graph.edges
        ]

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def get_neighbors(self, name: str) -> list[WorldNode]:
        """Return WorldNodes directly connected to the named node."""
        self._require_node(name)
        return [self._graph.nodes[n]["data"] for n in self._graph.neighbors(name)]

    def get_neighbor_names(self, name: str) -> list[str]:
        """Return names of nodes directly connected to the named node."""
        self._require_node(name)
        return list(self._graph.neighbors(name))

    def shortest_path(self, name_a: str, name_b: str) -> list[str]:
        """
        Return the shortest path between two nodes as a list of node names,
        weighted by traversal_cost. Returns empty list if no path exists.
        """
        self._require_node(name_a)
        self._require_node(name_b)
        try:
            return nx.shortest_path(
                self._graph,
                source=name_a,
                target=name_b,
                weight=lambda u, v, d: d["data"].traversal_cost,
            )
        except nx.NetworkXNoPath:
            return []

    def are_adjacent(self, name_a: str, name_b: str) -> bool:
        """True if the two nodes share a direct edge."""
        return self._graph.has_edge(name_a, name_b)

    def nodes_within_cost(self, name: str, max_cost: int) -> list[WorldNode]:
        """
        Return all nodes reachable from the named node within max_cost
        traversal cost (inclusive). Uses Dijkstra internally.
        """
        self._require_node(name)
        lengths = nx.single_source_dijkstra_path_length(
            self._graph,
            source=name,
            cutoff=max_cost,
            weight=lambda u, v, d: d["data"].traversal_cost,
        )
        return [
            self._graph.nodes[n]["data"]
            for n in lengths
            if n != name
        ]

    # ------------------------------------------------------------------
    # Mutation helpers (called by actions.py, not directly by main loop)
    # ------------------------------------------------------------------

    def set_owner(self, node_name: str, entity_name: Optional[str]) -> None:
        """Transfer ownership of a node. Pass None to make it neutral."""
        self.get_node(node_name).owner = entity_name

    def set_contested(self, name_a: str, name_b: str, state: bool) -> None:
        """Mark the edge between two nodes as contested or not."""
        self.get_edge(name_a, name_b).contested = state

    def mark_explored(self, node_name: str, entity_name: str) -> None:
        """Record that an entity has explored the given node."""
        self.get_node(node_name).explored_by.add(entity_name)

    def log_event(self, node_name: str, event: str) -> None:
        """Append an event string to a node's history log."""
        self.get_node(node_name).log(event)

    def set_capital(self, node_name: str, is_capital: bool) -> None:
        """Set or unset the capital flag on a node."""
        self.get_node(node_name).is_capital = is_capital

    # ------------------------------------------------------------------
    # Queries useful for utility scoring and event detection
    # ------------------------------------------------------------------

    def get_frontier_nodes(self, entity_name: str) -> list[WorldNode]:
        """
        Return nodes owned by entity_name that are adjacent to at least one
        node NOT owned by entity_name. These are expansion/attack candidates.
        """
        frontier = []
        for node in self.get_nodes_by_owner(entity_name):
            neighbors = self.get_neighbors(node.name)
            if any(n.owner != entity_name for n in neighbors):
                frontier.append(node)
        return frontier

    def get_reachable_targets(self, entity_name: str) -> list[WorldNode]:
        """
        Return all nodes adjacent to any node owned by entity_name that are
        not themselves owned by entity_name. These are immediate action targets.
        """
        owned_names = {n.name for n in self.get_nodes_by_owner(entity_name)}
        targets: dict[str, WorldNode] = {}
        for name in owned_names:
            for neighbor in self.get_neighbors(name):
                if neighbor.name not in owned_names:
                    targets[neighbor.name] = neighbor
        return list(targets.values())

    def get_unexplored_neighbors(self, entity_name: str) -> list[WorldNode]:
        """
        Return nodes adjacent to entity-owned territory that have not yet
        been explored by this entity. Explore action candidates.
        """
        owned_names = {n.name for n in self.get_nodes_by_owner(entity_name)}
        unexplored: dict[str, WorldNode] = {}
        for name in owned_names:
            for neighbor in self.get_neighbors(name):
                if not neighbor.is_explored_by(entity_name):
                    unexplored[neighbor.name] = neighbor
        return list(unexplored.values())

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def is_connected(self) -> bool:
        """True if the graph has no isolated subgraphs."""
        return nx.is_connected(self._graph)

    def summary(self) -> str:
        """Human-readable snapshot of the world state."""
        lines = [
            f"World: {self.node_count()} nodes, {self.edge_count()} edges",
            f"Connected: {self.is_connected()}",
            "",
        ]
        for node in self.all_nodes():
            explored_count = len(node.explored_by)
            lines.append(
                f"  {node.name:20s}  owner={str(node.owner):15s}  "
                f"resources={node.resources}  explored_by={explored_count} entities"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_node(self, name: str) -> None:
        if name not in self._graph:
            raise ValueError(f"Node '{name}' does not exist in the world.")

    def __repr__(self) -> str:
        return f"<World nodes={self.node_count()} edges={self.edge_count()}>"