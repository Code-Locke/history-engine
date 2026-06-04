"""
entity.py — Entity state, personality, history, and relationship matrix
for history_engine.

Owns:
  - HistoryEvent: typed, turn-stamped record of something that happened
  - Entity: name, resource pool, personality vector, alive state, history log
  - RelationshipMatrix: centralised diplomatic state between all entity pairs

Does NOT own:
  - World graph or node data (world.py)
  - Action resolution (actions.py)
  - Utility scoring / softmax (utility.py)
  - Event significance detection (events.py)
  - LLM narration (narrator.py)

RelationshipMatrix is standalone — instantiated in main.py alongside World
and passed to whatever module needs it. This keeps both Entity and World
free of diplomatic state, and ensures both sides of a relation are always
updated together through a single call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@dataclass
class HistoryEvent:
    """
    A typed, turn-stamped record of something significant that happened to
    an entity or node.

    Attributes:
        turn:           Simulation turn on which this event occurred.
        event_type:     Machine-readable category (e.g. "conquest", "attack_repelled",
                        "treaty_signed", "resource_harvested", "elimination").
        description:    Human-readable string. Fed directly to the LLM narrator
                        as context — write it to be narratable.
        related_entity: Name of another entity involved, if any.
        related_node:   Name of the world node involved, if any.
    """
    turn: int
    event_type: str
    description: str
    related_entity: Optional[str] = None
    related_node: Optional[str] = None

    def __repr__(self) -> str:
        parts = [f"turn={self.turn}", f"type={self.event_type!r}"]
        if self.related_entity:
            parts.append(f"entity={self.related_entity!r}")
        if self.related_node:
            parts.append(f"node={self.related_node!r}")
        return f"<HistoryEvent {' '.join(parts)}: {self.description!r}>"


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """
    A faction, civilisation, or agent competing in the simulation.

    Attributes:
        name:        Unique identifier and narrative label.
        personality: Scoring weights that shape utility decisions in utility.py.
                       aggression  — weight toward Exterminate actions
                       expansionism — weight toward Explore + Expand actions
                       greed       — weight toward Exploit actions
                       paranoia    — pulls back from aggression when outnumbered
                     All values are floats; no enforced range, but [0.0, 1.0]
                     is the intended convention. utility.py is responsible for
                     interpreting these.
        resources:   Treasury — dict of resource type → quantity accumulated
                     via Exploit actions on owned nodes (e.g. {"grain": 12, "iron": 4}).
                     Spending/consuming resources is handled by actions.py.
        alive:       False once the entity has been eliminated. Dead entities
                     remain in memory for historical narration but take no actions.
        history:     Ordered log of HistoryEvent records. First-class — fed to
                     the LLM narrator for contextual narration.
    """
    name: str
    personality: dict[str, float] = field(default_factory=lambda: {
        "aggression":   0.5,
        "expansionism": 0.5,
        "greed":        0.5,
        "paranoia":     0.5,
    })
    resources: dict[str, int] = field(default_factory=dict)
    alive: bool = True
    history: list[HistoryEvent] = field(default_factory=list)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def log(
        self,
        turn: int,
        event_type: str,
        description: str,
        related_entity: Optional[str] = None,
        related_node: Optional[str] = None,
    ) -> HistoryEvent:
        """
        Append a HistoryEvent to this entity's history and return it.
        The caller (typically events.py or actions.py) may pass the same
        event to a node's log for cross-referencing.
        """
        event = HistoryEvent(
            turn=turn,
            event_type=event_type,
            description=description,
            related_entity=related_entity,
            related_node=related_node,
        )
        self.history.append(event)
        return event

    def get_history_by_type(self, event_type: str) -> list[HistoryEvent]:
        """Return all history events of a given type."""
        return [e for e in self.history if e.event_type == event_type]

    def recent_history(self, n: int = 5) -> list[HistoryEvent]:
        """Return the n most recent history events."""
        return self.history[-n:]

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    def add_resources(self, incoming: dict[str, int]) -> None:
        """Merge incoming resources into the treasury."""
        for resource, amount in incoming.items():
            self.resources[resource] = self.resources.get(resource, 0) + amount

    def spend_resources(self, cost: dict[str, int]) -> bool:
        """
        Attempt to spend resources. Returns True and deducts if affordable,
        returns False without modifying treasury if not.
        """
        if not self.can_afford(cost):
            return False
        for resource, amount in cost.items():
            self.resources[resource] -= amount
        return True

    def can_afford(self, cost: dict[str, int]) -> bool:
        """True if the treasury covers all costs in the given dict."""
        return all(self.resources.get(r, 0) >= amt for r, amt in cost.items())

    def total_resources(self) -> int:
        """Sum of all resource values. Quick proxy for economic power."""
        return sum(self.resources.values())

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def eliminate(self, turn: int, description: str) -> None:
        """
        Mark this entity as eliminated. Logs the event internally.
        The significant event detection in events.py should also fire
        an 'elimination' event at the simulation level.
        """
        self.alive = False
        self.log(
            turn=turn,
            event_type="elimination",
            description=description,
        )

    def is_alive(self) -> bool:
        return self.alive

    # ------------------------------------------------------------------
    # Personality helpers
    # ------------------------------------------------------------------

    def get_trait(self, trait: str) -> float:
        """
        Return a personality trait value. Raises if the trait is unknown.
        Valid traits: aggression, expansionism, greed, paranoia.
        """
        if trait not in self.personality:
            raise KeyError(f"Unknown personality trait: {trait!r}")
        return self.personality[trait]

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = "alive" if self.alive else "eliminated"
        return (
            f"<Entity '{self.name}' {status} "
            f"resources={self.resources} "
            f"personality={self.personality}>"
        )

    def summary(self) -> str:
        """Human-readable snapshot of this entity's state."""
        status = "ALIVE" if self.alive else "ELIMINATED"
        lines = [
            f"Entity: {self.name} [{status}]",
            f"  Resources:   {self.resources}  (total: {self.total_resources()})",
            f"  Personality: {self.personality}",
            f"  History:     {len(self.history)} events",
        ]
        if self.history:
            lines.append("  Recent:")
            for event in self.recent_history(3):
                lines.append(f"    [turn {event.turn}] ({event.event_type}) {event.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RelationshipMatrix
# ---------------------------------------------------------------------------

class RelationshipMatrix:
    """
    Centralised diplomatic state between all entity pairs.

    Stores a single float per ordered pair (a, b) in the range [-1.0, +1.0]:
      -1.0 = fully hostile
       0.0 = neutral / unknown
      +1.0 = fully allied

    Both directions of a pair are always set together via set() to prevent
    asymmetric state. Directionality is intentionally NOT supported — relations
    are symmetric. If asymmetric sentiment is ever needed, this is the place
    to revisit.

    Instantiate once in main.py and pass to actions.py, utility.py, narrator.py
    as needed.

    Example:
        matrix = RelationshipMatrix(["Ashenveil", "Ironhold", "Duskreach"])
        matrix.set("Ashenveil", "Ironhold", -0.6)
        print(matrix.get("Ironhold", "Ashenveil"))  # -0.6
    """

    HOSTILE  = -1.0
    NEUTRAL  =  0.0
    ALLIED   =  1.0

    def __init__(self, entity_names: list[str], default: float = 0.0) -> None:
        """
        Initialise the matrix with all pairs set to `default` (0.0 = neutral).

        Args:
            entity_names: List of all entity names in the simulation.
            default:      Starting relation value for all pairs.
        """
        self._relations: dict[tuple[str, str], float] = {}
        self._clamp(default)  # validate default before populating
        for a in entity_names:
            for b in entity_names:
                if a != b:
                    self._relations[(a, b)] = default

    # ------------------------------------------------------------------
    # Core read / write
    # ------------------------------------------------------------------

    def get(self, a: str, b: str) -> float:
        """
        Return the relation score from a's perspective toward b.
        Relations are symmetric — get(a, b) == get(b, a).
        """
        self._require_pair(a, b)
        return self._relations[(a, b)]

    def set(self, a: str, b: str, value: float) -> None:
        """
        Set the relation between a and b (symmetric — both directions updated).
        Value is clamped to [-1.0, +1.0].
        """
        self._require_pair(a, b)
        value = self._clamp(value)
        self._relations[(a, b)] = value
        self._relations[(b, a)] = value

    def adjust(self, a: str, b: str, delta: float) -> float:
        """
        Add delta to the current relation between a and b (symmetric).
        Returns the new clamped value.
        """
        current = self.get(a, b)
        new_value = self._clamp(current + delta)
        self.set(a, b, new_value)
        return new_value

    # ------------------------------------------------------------------
    # Semantic helpers
    # ------------------------------------------------------------------

    def is_hostile(self, a: str, b: str, threshold: float = -0.3) -> bool:
        """True if the relation is at or below the hostility threshold."""
        return self.get(a, b) <= threshold

    def is_allied(self, a: str, b: str, threshold: float = 0.5) -> bool:
        """True if the relation is at or above the alliance threshold."""
        return self.get(a, b) >= threshold

    def is_neutral(self, a: str, b: str, threshold: float = 0.3) -> bool:
        """True if the relation is within ±threshold of zero."""
        return abs(self.get(a, b)) < threshold

    def enemies_of(self, a: str, threshold: float = -0.3) -> list[str]:
        """Return all entity names that are hostile toward a."""
        return [
            b for (x, b) in self._relations
            if x == a and self._relations[(x, b)] <= threshold
        ]

    def allies_of(self, a: str, threshold: float = 0.5) -> list[str]:
        """Return all entity names that are allied with a."""
        return [
            b for (x, b) in self._relations
            if x == a and self._relations[(x, b)] >= threshold
        ]

    def all_relations_for(self, a: str) -> dict[str, float]:
        """Return a dict of {entity_name: relation_score} for all of a's relations."""
        return {
            b: score
            for (x, b), score in self._relations.items()
            if x == a
        }

    # ------------------------------------------------------------------
    # Entity registration (for when entities are added mid-simulation)
    # ------------------------------------------------------------------

    def register(self, new_name: str, existing_names: list[str], default: float = 0.0) -> None:
        """
        Add a new entity to the matrix, initialising all pairs with existing
        entities to `default`. Useful if entities can be added after init.
        """
        default = self._clamp(default)
        for name in existing_names:
            self._relations[(new_name, name)] = default
            self._relations[(name, new_name)] = default

    # ------------------------------------------------------------------
    # Serialisation (for LLM prompt construction in narrator.py)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, dict[str, float]]:
        """
        Return the full matrix as a nested dict:
            { entity_a: { entity_b: score, ... }, ... }
        Useful for serialising into LLM prompts.
        """
        result: dict[str, dict[str, float]] = {}
        for (a, b), score in self._relations.items():
            result.setdefault(a, {})[b] = score
        return result

    def summary(self) -> str:
        """Human-readable snapshot of all relationships."""
        seen: set[frozenset[str]] = set()
        lines = ["RelationshipMatrix:"]
        for (a, b), score in sorted(self._relations.items()):
            pair = frozenset({a, b})
            if pair in seen:
                continue
            seen.add(pair)
            if score <= -0.3:
                label = "HOSTILE"
            elif score >= 0.5:
                label = "ALLIED"
            else:
                label = "neutral"
            lines.append(f"  {a} <-> {b}: {score:+.2f}  ({label})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_pair(self, a: str, b: str) -> None:
        if a == b:
            raise ValueError(f"Cannot get/set relation between an entity and itself: {a!r}")
        if (a, b) not in self._relations:
            raise KeyError(f"Entity pair ({a!r}, {b!r}) not found in matrix. Use register() first.")

    @staticmethod
    def _clamp(value: float) -> float:
        return max(-1.0, min(1.0, value))

    def __repr__(self) -> str:
        entity_names = list({a for (a, _) in self._relations})
        return f"<RelationshipMatrix entities={entity_names}>"