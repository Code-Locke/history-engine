"""
events.py — Event detection, significance filtering, and history logging
for history_engine.

Owns:
  - SimEvent: typed record of a simulation-level significant event
  - process_result(): the single entry point called by main.py after every
    action resolution — logs to entities and nodes, detects significance
  - Significance detection for all event types defined in the README

Does NOT own:
  - Action resolution (actions.py)
  - Utility scoring / action selection (utility.py)
  - LLM narration (narrator.py) — significant events are *returned*, not
    narrated here. narrator.py consumes the SimEvent list.

--- Responsibility boundary ---

actions.py resolves mechanics and returns an ActionResult.
events.py reads that result and does two things:
  1. Logging  — writes human-readable strings to entity.history and
                world node history via entity.log() and world.log_event().
  2. Detection — inspects the result for significance thresholds and
                returns a list of SimEvent objects for narrator.py.

No narration happens here. No action resolution happens here.

--- Significant event types ---

  elimination        An entity is removed from the world.
  war_declared       An attack pushes the relation between two entities
                     below the WAR_THRESHOLD.
  alliance_formed    A proposed alliance is accepted.
  capital_lost       A capital node changes hands during an attack.
  near_death_survived  The defender survived an attack that had a high
                     win probability for the attacker (>= NEAR_DEATH_WIN_PROB_THRESHOLD).
  unexpected_conquest  The attacker won despite lower computed strength.

--- Entry point ---

  results = process_result(result, turn, world, entities, relations)
  # results is a (possibly empty) list[SimEvent]
  # Pass to narrator.py for any that warrant narration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .actions import ActionResult
from .config import CFG
from .entity import Entity, RelationshipMatrix
from .world import World


# ---------------------------------------------------------------------------
# SimEvent
# ---------------------------------------------------------------------------

@dataclass
class SimEvent:
    """
    A simulation-level significant event, ready for the narrator.

    These are distinct from HistoryEvent (entity-level log entries) — a
    SimEvent is a signal to narrator.py that something worth narrating
    occurred. The narrator uses it to construct LLM prompts.

    Attributes:
        event_type:     Machine-readable category. One of:
                          "elimination", "war_declared", "alliance_formed",
                          "capital_lost", "near_death_survived",
                          "unexpected_conquest"
        turn:           Simulation turn on which this event occurred.
        actor:          Name of the entity that initiated the action.
        target_entity:  Name of the other entity involved, if any.
        target_node:    Name of the world node involved, if any.
        description:    Human-readable summary. Drawn from ActionResult.description
                        or composed here for synthetic events (e.g. war_declared).
        metadata:       Flat dict of additional context for the narrator.
                        Contents vary by event_type — see _detect_* functions.
    """
    event_type:    str
    turn:          int
    actor:         str
    target_entity: Optional[str] = None
    target_node:   Optional[str] = None
    description:   str = ""
    metadata:      dict = field(default_factory=dict)

    def __repr__(self) -> str:
        parts = [f"turn={self.turn}", f"type={self.event_type!r}", f"actor={self.actor!r}"]
        if self.target_entity:
            parts.append(f"target={self.target_entity!r}")
        if self.target_node:
            parts.append(f"node={self.target_node!r}")
        return f"<SimEvent {' '.join(parts)}>"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def process_result(
    result: ActionResult,
    turn: int,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
) -> list[SimEvent]:
    """
    Log the action result to entity and node histories, then detect and
    return any significant simulation events.

    Called by main.py once per resolved action. Returns a (possibly empty)
    list of SimEvent objects. Pass non-empty lists to narrator.py.

    Args:
        result:    The ActionResult returned by the resolve_* function.
        turn:      Current simulation turn number.
        world:     The world graph (for node logging).
        entities:  All entities (for looking up actor/target objects).
        relations: The relationship matrix (for war_declared detection).

    Returns:
        list[SimEvent] — empty if nothing significant occurred.
    """
    actor = _find_entity(result.actor, entities)
    target = _find_entity(result.target_entity, entities) if result.target_entity else None

    # -- Log to entity and node histories --
    _log_action(result, turn, actor, target, world)

    # -- Detect significant events --
    significant: list[SimEvent] = []

    if result.action == "attack":
        significant.extend(_detect_attack_events(result, turn, relations))

    elif result.action == "propose_treaty":
        significant.extend(_detect_diplomacy_events(result, turn))

    # elimination is set by actions.py calling defender.eliminate() —
    # we detect it from the flag on the result rather than re-deriving it.
    if result.target_eliminated:
        significant.append(_make_elimination_event(result, turn))

    return significant


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_action(
    result: ActionResult,
    turn: int,
    actor: Entity,
    target: Optional[Entity],
    world: World,
) -> None:
    """
    Write HistoryEvents to entity logs and event strings to node logs.
    One function per action type; dispatched by result.action.
    """
    dispatch = {
        "explore":       _log_explore,
        "expand":        _log_expand,
        "exploit":       _log_exploit,
        "attack":        _log_attack,
        "propose_treaty": _log_diplomacy,
    }
    handler = dispatch.get(result.action)
    if handler:
        handler(result, turn, actor, target, world)


def _log_explore(
    result: ActionResult,
    turn: int,
    actor: Entity,
    target: Optional[Entity],
    world: World,
) -> None:
    if not result.success:
        return

    actor.log(
        turn=turn,
        event_type="explore",
        description=result.description,
        related_node=result.target_node,
    )
    if result.target_node:
        world.log_event(result.target_node, f"[turn {turn}] Scouted by {actor.name}.")


def _log_expand(
    result: ActionResult,
    turn: int,
    actor: Entity,
    target: Optional[Entity],
    world: World,
) -> None:
    if not result.success:
        return

    actor.log(
        turn=turn,
        event_type="expand",
        description=result.description,
        related_node=result.target_node,
    )
    if result.target_node:
        world.log_event(result.target_node, f"[turn {turn}] Claimed by {actor.name}.")


def _log_exploit(
    result: ActionResult,
    turn: int,
    actor: Entity,
    target: Optional[Entity],
    world: World,
) -> None:
    if not result.success:
        return

    actor.log(
        turn=turn,
        event_type="exploit",
        description=result.description,
    )
    for node_name in result.metadata.get("nodes_exploited", []):
        world.log_event(node_name, f"[turn {turn}] Harvested by {actor.name}.")


def _log_attack(
    result: ActionResult,
    turn: int,
    actor: Entity,
    target: Optional[Entity],
    world: World,
) -> None:
    # Log to attacker
    actor.log(
        turn=turn,
        event_type="attack",
        description=result.description,
        related_entity=result.target_entity,
        related_node=result.target_node,
    )

    # Log to defender (if they still exist)
    if target is not None:
        target.log(
            turn=turn,
            event_type="attack_received",
            description=result.description,
            related_entity=actor.name,
            related_node=result.target_node,
        )

    # Log to the contested node
    if result.target_node:
        world.log_event(result.target_node, f"[turn {turn}] {result.description}")


def _log_diplomacy(
    result: ActionResult,
    turn: int,
    actor: Entity,
    target: Optional[Entity],
    world: World,
) -> None:
    is_alliance = result.metadata.get("alliance", False)
    event_type = "offer_alliance" if is_alliance else "propose_treaty"

    actor.log(
        turn=turn,
        event_type=event_type,
        description=result.description,
        related_entity=result.target_entity,
    )

    if target is not None:
        # Log from the target's perspective too
        if result.success:
            target_description = (
                f"{target.name} accepts a {'alliance' if is_alliance else 'treaty'} "
                f"proposed by {actor.name}."
            )
        else:
            target_description = (
                f"{target.name} refuses a {'alliance' if is_alliance else 'treaty'} "
                f"proposed by {actor.name}."
            )
        target.log(
            turn=turn,
            event_type=event_type + "_received",
            description=target_description,
            related_entity=actor.name,
        )


# ---------------------------------------------------------------------------
# Significance detection
# ---------------------------------------------------------------------------

def _detect_attack_events(
    result: ActionResult,
    turn: int,
    relations: RelationshipMatrix,
) -> list[SimEvent]:
    """
    Inspect an attack result for significant events:
      - war_declared       (relation crossed WAR_THRESHOLD this action)
      - capital_lost       (a capital changed hands)
      - near_death_survived (defender survived a high-probability loss)
      - unexpected_conquest (attacker won despite lower strength)

    elimination is handled separately in process_result() so it applies
    to any action type that could theoretically trigger it.
    """
    events: list[SimEvent] = []

    relation_before = result.metadata.get("relation_before")
    relation_after  = result.metadata.get("relation_after")
    win_prob        = result.metadata.get("win_probability", 0.0)

    # -- war_declared --
    if (
        relation_before is not None
        and relation_after is not None
        and relation_before > CFG.events.war_threshold
        and relation_after <= CFG.events.war_threshold
    ):
        events.append(SimEvent(
            event_type="war_declared",
            turn=turn,
            actor=result.actor,
            target_entity=result.target_entity,
            target_node=result.target_node,
            description=(
                f"The attack by {result.actor} on {result.target_entity} has pushed "
                f"relations past the point of no return. War has been declared."
            ),
            metadata={
                "relation_before": relation_before,
                "relation_after":  relation_after,
            },
        ))

    # -- capital_lost --
    if result.capital_lost:
        events.append(SimEvent(
            event_type="capital_lost",
            turn=turn,
            actor=result.actor,
            target_entity=result.target_entity,
            target_node=result.target_node,
            description=(
                f"{result.target_entity}'s capital {result.target_node} "
                f"has fallen to {result.actor}."
            ),
            metadata={
                "win_probability": win_prob,
                "actor_strength":    round(result.actor_strength, 3),
                "defender_strength": round(result.defender_strength, 3),
            },
        ))

    # -- near_death_survived --
    # Attacker had a high win probability but failed — defender survived
    # against the odds.
    if (
        not result.success
        and win_prob >= CFG.events.near_death_win_prob_threshold
    ):
        events.append(SimEvent(
            event_type="near_death_survived",
            turn=turn,
            actor=result.actor,
            target_entity=result.target_entity,
            target_node=result.target_node,
            description=(
                f"{result.target_entity} survived a devastating assault by {result.actor} "
                f"on {result.target_node} — the odds were heavily against them "
                f"({win_prob:.0%} chance of falling)."
            ),
            metadata={
                "win_probability":   win_prob,
                "actor_strength":    round(result.actor_strength, 3),
                "defender_strength": round(result.defender_strength, 3),
            },
        ))

    # -- unexpected_conquest --
    if result.unexpected:
        events.append(SimEvent(
            event_type="unexpected_conquest",
            turn=turn,
            actor=result.actor,
            target_entity=result.target_entity,
            target_node=result.target_node,
            description=(
                f"{result.actor} seized {result.target_node} from {result.target_entity} "
                f"against all expectation — the weaker force prevailed "
                f"({win_prob:.0%} win probability)."
            ),
            metadata={
                "win_probability":   win_prob,
                "actor_strength":    round(result.actor_strength, 3),
                "defender_strength": round(result.defender_strength, 3),
            },
        ))

    return events


def _detect_diplomacy_events(
    result: ActionResult,
    turn: int,
) -> list[SimEvent]:
    """
    Inspect a treaty/alliance result for significant events:
      - alliance_formed    (an offered alliance was accepted)

    Treaty acceptances are not significant on their own — they're common
    diplomatic currency. Alliances are rarer and narratively weightier.
    """
    events: list[SimEvent] = []

    is_alliance = result.metadata.get("alliance", False)

    if result.success and is_alliance:
        events.append(SimEvent(
            event_type="alliance_formed",
            turn=turn,
            actor=result.actor,
            target_entity=result.target_entity,
            description=(
                f"{result.actor} and {result.target_entity} have forged an alliance."
            ),
            metadata={
                "relation_before": result.metadata.get("relation_before"),
                "relation_after":  result.metadata.get("relation_after"),
            },
        ))

    return events


def _make_elimination_event(result: ActionResult, turn: int) -> SimEvent:
    """
    Construct the elimination SimEvent from an ActionResult that carries
    target_eliminated=True.
    """
    return SimEvent(
        event_type="elimination",
        turn=turn,
        actor=result.actor,
        target_entity=result.target_entity,
        target_node=result.target_node,
        description=(
            f"{result.target_entity} has been eliminated from the world "
            f"by {result.actor} at {result.target_node}."
        ),
        metadata={
            "win_probability":   result.metadata.get("win_probability"),
            "actor_strength":    round(result.actor_strength, 3),
            "defender_strength": round(result.defender_strength, 3),
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_entity(name: Optional[str], entities: list[Entity]) -> Optional[Entity]:
    """Return the Entity with the given name, or None if not found."""
    if name is None:
        return None
    return next((e for e in entities if e.name == name), None)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def summarise_events(events: list[SimEvent]) -> str:
    """
    Human-readable summary of a list of SimEvents.
    Useful for terminal output and observer-mode tooling.
    """
    if not events:
        return "No significant events."
    lines = [f"Significant events ({len(events)}):"]
    for event in events:
        target_str = f" vs {event.target_entity}" if event.target_entity else ""
        node_str   = f" @ {event.target_node}" if event.target_node else ""
        lines.append(
            f"  [turn {event.turn}] {event.event_type:<22s} "
            f"{event.actor}{target_str}{node_str}"
        )
    return "\n".join(lines)