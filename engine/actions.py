"""
actions.py — Legal action definitions and resolution for history_engine.

Owns:
  - ActionResult: structured return type for all action resolutions
  - Action legality checks (what is possible given world + entity state)
  - Action resolution (what actually happens mechanically)

Does NOT own:
  - History logging — all .log() calls live in events.py
  - Utility scoring / action selection — lives in utility.py
  - Event significance detection — lives in events.py
  - LLM narration — lives in narrator.py

Every resolve_* function returns an ActionResult. The result carries enough
structured information for events.py to do all logging and significance
detection without needing to re-derive anything.

--- Action catalogue ---

  EXPLORE     Reveal an unexplored adjacent node.
  EXPAND      Claim a neutral adjacent node.
  EXPLOIT     Harvest resources from an owned node.
  ATTACK      Contest an enemy-owned adjacent node via resource wager.
  PROPOSE_TREATY  Offer a diplomatic relation shift to another entity.

--- Combat model ---

  Attacker commits: aggression% of total resources
  Defender commits: mean(aggression, paranoia)% of total resources
                    (zero resources = flat penalty to win probability)
  Strength:         committed_resources * (1 + aggression)
  Winner:           probabilistic draw weighted by relative strength
  Wagered resources are destroyed on both sides (entropy).
  Unexpected conquest: attacker_strength < defender_strength and attacker wins.

--- Diplomacy model ---

  Proposer offers a treaty (small shift) or alliance (larger shift).
  Target accepts with probability: base + current_relation + (1 - paranoia),
  clamped to [CFG.combat.prob_min, CFG.combat.prob_max].
  Relation delta applied symmetrically on accept; smaller penalty on reject.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .config import CFG
from .entity import Entity, RelationshipMatrix
from .world import World


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """
    Structured return value from every resolve_* function.

    events.py reads this to decide what to log and whether any significant
    event thresholds have been crossed. No logging happens inside actions.py.

    Attributes:
        action:            String name of the action that was resolved.
                           One of: "explore", "expand", "exploit",
                                   "attack", "propose_treaty".
        actor:             Name of the entity that took the action.
        target_entity:     Name of the opposing entity, if any.
        target_node:       Name of the world node involved, if any.
        success:           True if the action achieved its primary outcome.
        resources_delta:   Net resource changes per entity name.
                           e.g. {"Ashenveil": {"iron": -2}, "Ironhold": {"iron": -3}}
                           Negative = lost/spent. events.py uses this for logs.
        relation_delta:    Net relation change applied.
                           e.g. 0.20 (positive = warmer, negative = colder).
                           Always from actor's perspective toward target_entity.
        actor_strength:    Computed attacker strength (attack actions only).
        defender_strength: Computed defender strength (attack actions only).
        unexpected:        True if attacker won despite lower strength.
        capital_lost:      True if a capital node changed hands this action.
        target_eliminated: True if the target entity has no nodes remaining.
        node_was_neutral:  True if the contested/claimed node was neutral
                           before the action (for expand narration).
        description:       Short human-readable summary of what happened.
                           Written to be narratable; events.py may pass this
                           directly into history logs.
        metadata:          Catch-all dict for any extra context events.py
                           or narrator.py might want. Keep it flat.
    """
    action:            str
    actor:             str
    success:           bool
    target_entity:     Optional[str]       = None
    target_node:       Optional[str]       = None
    resources_delta:   dict[str, dict[str, int]] = field(default_factory=dict)
    relation_delta:    float               = 0.0
    actor_strength:    float               = 0.0
    defender_strength: float               = 0.0
    unexpected:        bool                = False
    capital_lost:      bool                = False
    target_eliminated: bool                = False
    node_was_neutral:  bool                = False
    description:       str                 = ""
    metadata:          dict                = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Legality checks
# ---------------------------------------------------------------------------

def can_explore(entity: Entity, world: World) -> bool:
    """True if the entity has at least one unexplored adjacent node."""
    return bool(world.get_unexplored_neighbors(entity.name))


def can_expand(entity: Entity, world: World) -> bool:
    """True if the entity has at least one neutral adjacent node."""
    targets = world.get_reachable_targets(entity.name)
    return any(n.is_neutral() for n in targets)


def can_exploit(entity: Entity, world: World) -> bool:
    """True if the entity owns at least one node with resources."""
    return any(
        n.total_resources() > 0
        for n in world.get_nodes_by_owner(entity.name)
    )


def can_attack(entity: Entity, world: World, relations: RelationshipMatrix) -> bool:
    """
    True if the entity has at least one adjacent node owned by a different
    (living) entity. Relations are not checked here — an entity may attack
    regardless of formal diplomatic state. Utility scoring in utility.py
    weights aggression; legality only cares about adjacency.
    """
    targets = world.get_reachable_targets(entity.name)
    return any(not n.is_neutral() and n.owner != entity.name for n in targets)


def can_propose_treaty(
    entity: Entity,
    entities: list[Entity],
    relations: RelationshipMatrix,
) -> bool:
    """
    True if any other living entity exists that is not already fully allied.
    """
    for other in entities:
        if other.name == entity.name or not other.is_alive():
            continue
        if relations.get(entity.name, other.name) < RelationshipMatrix.ALLIED:
            return True
    return False


def legal_actions(
    entity: Entity,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
) -> list[str]:
    """
    Return the list of action names that are currently legal for this entity.
    Called by utility.py to scope the scoring pass.
    """
    actions = []
    if can_explore(entity, world):
        actions.append("explore")
    if can_expand(entity, world):
        actions.append("expand")
    if can_exploit(entity, world):
        actions.append("exploit")
    if can_attack(entity, world, relations):
        actions.append("attack")
    if can_propose_treaty(entity, entities, relations):
        actions.append("propose_treaty")
        actions.append("offer_alliance")
    return actions


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = None, hi: float = None) -> float:
    if lo is None:
        lo = CFG.combat.prob_min
    if hi is None:
        hi = CFG.combat.prob_max
    return max(lo, min(hi, value))


def _compute_strength(committed: int, aggression: float) -> float:
    """Core strength formula: committed * (1 + aggression)."""
    return committed * (1.0 + aggression)


def _wager_amount(total: int, trait: float) -> int:
    """
    Compute how many resources an entity commits, as trait% of total.
    Minimum of 0 — never goes negative.
    """
    return max(0, int(total * trait))


def _spend_wager(entity: Entity, wager: int) -> dict[str, int]:
    """
    Destroy wagered resources proportionally across the entity's pool.
    Returns a delta dict (all values negative) for ActionResult.
    Modifies entity.resources in place.
    """
    total = entity.total_resources()
    delta: dict[str, int] = {}
    if total == 0 or wager == 0:
        return delta

    remaining = wager
    for resource, amount in list(entity.resources.items()):
        if remaining <= 0:
            break
        take = min(amount, remaining)
        entity.resources[resource] -= take
        delta[resource] = -take
        remaining -= take

    return delta


def _win_probability(actor_strength: float, defender_strength: float) -> float:
    """
    Attacker's win probability as a fraction of combined strength.
    Falls back to 50/50 if both sides have zero strength.
    """
    total = actor_strength + defender_strength
    if total == 0:
        return 0.5
    return _clamp(actor_strength / total)


# ---------------------------------------------------------------------------
# Action resolvers
# ---------------------------------------------------------------------------

def resolve_explore(entity: Entity, world: World) -> ActionResult:
    """
    Reveal one unexplored adjacent node. Chosen at random from candidates.
    (utility.py picks the action; actions.py picks the target within it.)
    """
    candidates = world.get_unexplored_neighbors(entity.name)
    if not candidates:
        return ActionResult(
            action="explore",
            actor=entity.name,
            success=False,
            description=f"{entity.name} found no unexplored territory to scout.",
        )

    target = random.choice(candidates)
    world.mark_explored(target.name, entity.name)

    return ActionResult(
        action="explore",
        actor=entity.name,
        success=True,
        target_node=target.name,
        description=(
            f"{entity.name} scouts {target.name} "
            f"[{', '.join(target.features) or 'featureless'}] "
            f"and finds resources: {target.resources}."
        ),
        metadata={"features": target.features, "resources": dict(target.resources)},
    )


def resolve_expand(entity: Entity, world: World) -> ActionResult:
    """
    Claim one neutral adjacent node. Chosen at random from neutral candidates.
    """
    candidates = [
        n for n in world.get_reachable_targets(entity.name)
        if n.is_neutral()
    ]
    if not candidates:
        return ActionResult(
            action="expand",
            actor=entity.name,
            success=False,
            description=f"{entity.name} found no neutral territory to claim.",
        )

    target = random.choice(candidates)
    world.set_owner(target.name, entity.name)
    world.mark_explored(target.name, entity.name)

    return ActionResult(
        action="expand",
        actor=entity.name,
        success=True,
        target_node=target.name,
        node_was_neutral=True,
        description=f"{entity.name} claims {target.name} and raises their banner.",
        metadata={"resources": dict(target.resources)},
    )


def resolve_exploit(entity: Entity, world: World) -> ActionResult:
    """
    Harvest resources from all owned nodes into the entity's treasury.
    Every owned node contributes its full resource values each time.
    """
    owned = world.get_nodes_by_owner(entity.name)
    harvested: dict[str, int] = {}

    for node in owned:
        for resource, amount in node.resources.items():
            if amount > 0:
                harvested[resource] = harvested.get(resource, 0) + amount

    entity.add_resources(harvested)

    if not harvested:
        return ActionResult(
            action="exploit",
            actor=entity.name,
            success=False,
            description=f"{entity.name} worked their lands but gathered nothing of worth.",
        )

    return ActionResult(
        action="exploit",
        actor=entity.name,
        success=True,
        resources_delta={entity.name: harvested},
        description=(
            f"{entity.name} harvests from {len(owned)} node(s): {harvested}. "
            f"Treasury now holds {entity.resources}."
        ),
        metadata={"nodes_exploited": [n.name for n in owned]},
    )


def resolve_attack(
    entity: Entity,
    target_entity_name: str,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    turn: int = 0,
) -> ActionResult:
    """
    Contest an enemy-owned adjacent node via resource wager.

    Resolution:
      1. Pick a target node — highest-value adjacent enemy node.
      2. Compute wagers: attacker uses aggression%, defender uses mean(aggression, paranoia)%.
      3. Destroy wagered resources on both sides (entropy).
      4. Compute strengths, derive win probability, roll.
      5. On attacker win: transfer node ownership.
         On defender win: node stays, nothing else changes.
      6. Adjust relations negatively.
      7. Check for elimination (defender owns no nodes).
      8. Return ActionResult with full context for events.py.
    """
    # -- Find defender entity object --
    defender = next((e for e in entities if e.name == target_entity_name), None)
    if defender is None or not defender.is_alive():
        return ActionResult(
            action="attack",
            actor=entity.name,
            target_entity=target_entity_name,
            success=False,
            description=f"{entity.name} found no living enemy to attack.",
        )

    # -- Pick target node: highest total_resources among adjacent enemy nodes --
    candidates = [
        n for n in world.get_reachable_targets(entity.name)
        if n.owner == target_entity_name
    ]
    if not candidates:
        return ActionResult(
            action="attack",
            actor=entity.name,
            target_entity=target_entity_name,
            success=False,
            description=(
                f"{entity.name} advanced on {target_entity_name} "
                f"but found no adjacent territory to contest."
            ),
        )

    target_node = max(candidates, key=lambda n: n.total_resources())

    # -- Compute wagers --
    attacker_aggression = entity.get_trait("aggression")
    defender_aggression = defender.get_trait("aggression")
    defender_paranoia   = defender.get_trait("paranoia")
    defender_trait      = (defender_aggression + defender_paranoia) / 2.0

    attacker_total = entity.total_resources()
    defender_total = defender.total_resources()

    attacker_wager = _wager_amount(attacker_total, attacker_aggression)
    defender_wager = _wager_amount(defender_total, defender_trait)

    defender_has_no_resources = defender_total == 0

    # -- Destroy wagered resources (entropy) --
    attacker_delta = _spend_wager(entity, attacker_wager)
    defender_delta = _spend_wager(defender, defender_wager)

    # -- Compute strengths --
    actor_strength    = _compute_strength(attacker_wager, attacker_aggression)
    defender_strength = _compute_strength(defender_wager, defender_trait)

    # -- Win probability --
    win_prob = _win_probability(actor_strength, defender_strength)
    if defender_has_no_resources:
        win_prob = _clamp(win_prob + CFG.combat.zero_resource_defense_penalty)

    attacker_wins = random.random() < win_prob
    unexpected    = attacker_wins and (actor_strength < defender_strength)

    # -- Resolve outcome --
    capital_lost    = False
    target_eliminated = False

    if attacker_wins:
        was_capital = target_node.is_capital
        world.set_owner(target_node.name, entity.name)
        world.mark_explored(target_node.name, entity.name)

        if was_capital:
            capital_lost = True
            world.set_capital(target_node.name, False)
            # Assign a new capital to the defender if they still hold nodes
            remaining = world.get_nodes_by_owner(target_entity_name)
            if remaining:
                world.set_capital(remaining[0].name, True)
            else:
                target_eliminated = True
                defender.eliminate(
                    turn=turn,
                    description=(
                        f"{target_entity_name} was eliminated by {entity.name} "
                        f"at {target_node.name}."
                    ),
                )

    # -- Adjust relations --
    relation_delta = CFG.combat.attack_relation_delta
    relation_before = relations.get(entity.name, target_entity_name)
    relations.adjust(entity.name, target_entity_name, relation_delta)

    # -- Build description --
    if attacker_wins:
        outcome_str = f"{entity.name} seizes {target_node.name} from {target_entity_name}."
    else:
        outcome_str = (
            f"{entity.name} attacks {target_node.name} but {target_entity_name} holds the line."
        )

    if unexpected:
        outcome_str += " Against all expectation, the weaker force prevailed."
    if capital_lost:
        outcome_str += f" {target_entity_name}'s capital falls!"
    if target_eliminated:
        outcome_str += f" {target_entity_name} has been eliminated from the world."

    resources_delta = {}
    if attacker_delta:
        resources_delta[entity.name] = attacker_delta
    if defender_delta:
        resources_delta[target_entity_name] = defender_delta

    return ActionResult(
        action="attack",
        actor=entity.name,
        target_entity=target_entity_name,
        target_node=target_node.name,
        success=attacker_wins,
        resources_delta=resources_delta,
        relation_delta=relation_delta,
        actor_strength=actor_strength,
        defender_strength=defender_strength,
        unexpected=unexpected,
        capital_lost=capital_lost,
        target_eliminated=target_eliminated,
        description=outcome_str,
        metadata={
            "attacker_wager": attacker_wager,
            "defender_wager": defender_wager,
            "win_probability": round(win_prob, 3),
            "defender_had_no_resources": defender_has_no_resources,
            "relation_before": round(relation_before, 3),
            "relation_after":  round(relations.get(entity.name, target_entity_name), 3),
        },
    )


def resolve_propose_treaty(
    entity: Entity,
    target_entity_name: str,
    entities: list[Entity],
    relations: RelationshipMatrix,
    alliance: bool = False,
) -> ActionResult:
    """
    Offer a diplomatic relation shift to another entity.

    Two tiers:
      propose_treaty  — smaller shift (TREATY_DELTA_ACCEPT),  lower bar
      offer_alliance  — larger shift (ALLIANCE_DELTA_ACCEPT), higher bar

    Acceptance probability:
      p = base(0.5) + current_relation + (1 - target_paranoia)
      clamped to [CFG.combat.prob_min, CFG.combat.prob_max]

    On accept: relation shifts positively by the tier delta (symmetric).
    On reject: small negative nudge (symmetric) — being rebuffed breeds resentment.
    """
    target = next((e for e in entities if e.name == target_entity_name), None)
    if target is None or not target.is_alive():
        return ActionResult(
            action="propose_treaty",
            actor=entity.name,
            target_entity=target_entity_name,
            success=False,
            description=f"{entity.name}'s envoy found no living party to negotiate with.",
        )

    current_relation = relations.get(entity.name, target_entity_name)
    target_paranoia  = target.get_trait("paranoia")

    accept_prob = _clamp(0.5 + current_relation + (1.0 - target_paranoia))
    accepted    = random.random() < accept_prob

    action_label = "alliance" if alliance else "treaty"
    accept_delta = CFG.diplomacy.alliance_delta_accept if alliance else CFG.diplomacy.treaty_delta_accept
    reject_delta = CFG.diplomacy.alliance_delta_reject if alliance else CFG.diplomacy.treaty_delta_reject

    if accepted:
        relations.adjust(entity.name, target_entity_name, accept_delta)
        relation_delta = accept_delta
        description = (
            f"{entity.name} proposes a {action_label} to {target_entity_name}. "
            f"{target_entity_name} accepts. Relations improve by {accept_delta:+.2f}."
        )
    else:
        relations.adjust(entity.name, target_entity_name, reject_delta)
        relation_delta = reject_delta
        description = (
            f"{entity.name} proposes a {action_label} to {target_entity_name}. "
            f"{target_entity_name} refuses. Relations cool by {abs(reject_delta):.2f}."
        )

    new_relation = relations.get(entity.name, target_entity_name)

    return ActionResult(
        action="propose_treaty",
        actor=entity.name,
        target_entity=target_entity_name,
        success=accepted,
        relation_delta=relation_delta,
        description=description,
        metadata={
            "alliance": alliance,
            "accept_probability": round(accept_prob, 3),
            "relation_before": round(current_relation, 3),
            "relation_after":  round(new_relation, 3),
        },
    )