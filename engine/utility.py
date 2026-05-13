"""
utility.py — Personality-driven action scoring and softmax selection
for history_engine.

Owns:
  - Scoring every legal (action, target) pair for a given entity
  - Softmax sampling over scores to select an action
  - Temperature-controlled randomness (variance produces surprise,
    surprise produces story)

Does NOT own:
  - Action legality checks (actions.py — legal_actions())
  - Action resolution (actions.py — resolve_*())
  - World state or entity state (world.py, entity.py)
  - Event detection or logging (events.py)
  - LLM narration (narrator.py)

Entry point:
  choose_action(entity, world, entities, relations, temperature)
    → (action_name: str, target_entity_name: str | None)

The main loop in main.py calls choose_action() once per living entity per
turn, then dispatches the result to the appropriate resolve_* function
in actions.py.

--- Scoring model ---

Each legal action (sometimes paired with a target entity) receives a raw
utility score. Scores are shaped by the acting entity's personality vector:

  aggression   → weights attack actions
  expansionism → weights explore + expand actions
  greed        → weights exploit actions
  paranoia     → discounts aggression when outmatched, boosts diplomacy

Scores are then passed through softmax with a temperature parameter:
  - Low temperature  → near-deterministic (picks highest score)
  - High temperature → near-uniform (picks randomly)
  - Default (1.0)    → balanced variance

Softmax sampling is intentional. Do NOT replace with argmax. Variance
produces surprise events that generate story.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .actions import legal_actions
from .config import CFG
from .entity import Entity, RelationshipMatrix
from .world import World


# ---------------------------------------------------------------------------
# Scored option
# ---------------------------------------------------------------------------

@dataclass
class ScoredOption:
    """
    A single (action, target) pair with its computed utility score.

    Attributes:
        action:        Action name (matches actions.py catalogue).
        target_entity: Name of the target entity, or None for untargeted actions.
        score:         Raw utility score before softmax normalisation.
    """
    action: str
    target_entity: Optional[str]
    score: float

    def __repr__(self) -> str:
        target_str = f" → {self.target_entity}" if self.target_entity else ""
        return f"<ScoredOption {self.action}{target_str} score={self.score:.3f}>"


# ---------------------------------------------------------------------------
# Scoring functions (one per action type)
# ---------------------------------------------------------------------------

def _score_explore(entity: Entity, world: World) -> float:
    """
    Explore score: expansionism + bonus per unexplored neighbor.

    Entities that are expansionist and surrounded by unknown territory
    score this highly. As the map fills in, this naturally decays.
    """
    expansionism = entity.get_trait("expansionism")
    unexplored = world.get_unexplored_neighbors(entity.name)
    count_bonus = len(unexplored) * CFG.utility.unexplored_bonus_scale

    return CFG.utility.base_score + expansionism + count_bonus


def _score_expand(entity: Entity, world: World) -> float:
    """
    Expand score: expansionism + bonus from resource value of claimable neutrals.

    Greed also contributes here — resource-rich neutral territory is
    attractive to greedy entities as well as expansionist ones.
    """
    expansionism = entity.get_trait("expansionism")
    greed = entity.get_trait("greed")

    neutral_targets = [
        n for n in world.get_reachable_targets(entity.name)
        if n.is_neutral()
    ]
    total_neutral_resources = sum(n.total_resources() for n in neutral_targets)
    resource_bonus = total_neutral_resources * CFG.utility.neutral_resource_scale

    # Greed contributes a fraction — expanding into rich territory appeals
    # to greedy entities, but expansionism is still the primary driver.
    return CFG.utility.base_score + expansionism + (greed * 0.3) + resource_bonus


def _score_exploit(entity: Entity, world: World) -> float:
    """
    Exploit score: greed + bonus from harvestable resource volume.

    The more resources sitting in owned nodes, the more attractive
    harvesting becomes. Greedy entities lean hard into this.
    """
    greed = entity.get_trait("greed")

    owned_nodes = world.get_nodes_by_owner(entity.name)
    harvestable = sum(n.total_resources() for n in owned_nodes)
    resource_bonus = harvestable * CFG.utility.exploit_resource_scale

    return CFG.utility.base_score + greed + resource_bonus


def _score_attack(
    entity: Entity,
    target: Entity,
    world: World,
    relations: RelationshipMatrix,
) -> float:
    """
    Attack score: aggression, discounted by paranoia when outmatched,
    boosted by hostility toward the target.

    Paranoia's role: when the target is stronger (more total resources or
    more territory), paranoia discounts the attack score. A high-aggression,
    low-paranoia entity will attack even when outmatched. A high-paranoia
    entity pulls back unless they have a clear advantage.
    """
    aggression = entity.get_trait("aggression")
    paranoia = entity.get_trait("paranoia")

    # --- Hostility bonus: negative relations make attack more attractive ---
    relation = relations.get(entity.name, target.name)
    hostility_bonus = 0.0
    if relation < 0:
        hostility_bonus = abs(relation) * CFG.utility.hostility_attack_bonus

    # --- Power ratio: compare total resources ---
    actor_power = entity.total_resources() + 1   # +1 to avoid division by zero
    target_power = target.total_resources() + 1
    power_ratio = actor_power / target_power      # >1 = we're stronger

    # Bonus when stronger, penalty when weaker
    power_modifier = (power_ratio - 1.0) * CFG.utility.power_ratio_scale

    # --- Territory ratio: compare node counts ---
    actor_nodes = len(world.get_nodes_by_owner(entity.name))
    target_nodes = len(world.get_nodes_by_owner(target.name))
    # +1 to avoid div-by-zero
    territory_ratio = actor_nodes / (target_nodes + 1)

    # --- Paranoia discount: scales with how outmatched we are ---
    paranoia_penalty = 0.0
    if power_ratio < 1.0 or territory_ratio < 1.0:
        # We're outmatched on at least one axis
        weakness = max(1.0 - power_ratio, 1.0 - territory_ratio)
        paranoia_penalty = paranoia * weakness * CFG.utility.paranoia_discount_scale

    score = (
        CFG.utility.base_score
        + aggression
        + hostility_bonus
        + power_modifier
        - paranoia_penalty
    )

    # Floor at a small positive value — even terrible odds produce
    # occasional surprise attacks via softmax sampling.
    return max(CFG.utility.base_score * 0.5, score)


def _score_propose_treaty(
    entity: Entity,
    target: Entity,
    relations: RelationshipMatrix,
) -> float:
    """
    Treaty score: inverse of aggression, boosted by paranoia and by
    existing warmth in the relationship.

    Treaties are the conservative diplomatic option. Paranoid entities
    prefer the safety of agreements. Already-warm relations make
    acceptance more likely, which makes proposing more attractive.
    """
    aggression = entity.get_trait("aggression")
    paranoia = entity.get_trait("paranoia")

    relation = relations.get(entity.name, target.name)

    # Base diplomatic inclination: non-aggressive, paranoid entities
    # lean toward diplomacy.
    diplomatic_base = (1.0 - aggression) * 0.5 + paranoia * CFG.utility.diplomacy_paranoia_bonus

    # Warmth bonus: warmer relations → more attractive to propose
    warmth_bonus = 0.0
    if relation > -0.5:
        warmth_bonus = (relation + 0.5) * CFG.utility.relation_treaty_bonus

    # Slight penalty for proposing to someone who already dislikes you —
    # not zero (there's value in trying), but discounted.
    hostility_discount = 0.0
    if relation < -0.3:
        hostility_discount = abs(relation) * 0.15

    return CFG.utility.base_score + diplomatic_base + warmth_bonus - hostility_discount


def _score_offer_alliance(
    entity: Entity,
    target: Entity,
    relations: RelationshipMatrix,
) -> float:
    """
    Alliance score: similar to treaty but requires warmer existing relations
    and offers a bigger payoff.

    Alliances are the ambitious diplomatic option. They score poorly when
    relations are cold (why would you ally with someone you barely trust?)
    but ramp up when relations are already positive.
    """
    aggression = entity.get_trait("aggression")
    paranoia = entity.get_trait("paranoia")

    relation = relations.get(entity.name, target.name)

    # Alliance only makes sense when there's existing warmth
    if relation < CFG.utility.alliance_warmth_threshold:
        return CFG.utility.base_score * 0.5  # near-zero but not impossible via softmax

    diplomatic_base = (1.0 - aggression) * 0.4 + paranoia * CFG.utility.diplomacy_paranoia_bonus

    # Warmth bonus is steeper than treaty — alliances reward existing trust
    warmth_bonus = relation * CFG.utility.relation_alliance_bonus

    return CFG.utility.base_score + diplomatic_base + warmth_bonus


# ---------------------------------------------------------------------------
# Score all legal options
# ---------------------------------------------------------------------------

def score_all_options(
    entity: Entity,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
) -> list[ScoredOption]:
    """
    Enumerate and score every legal (action, target) pair for the entity.

    Returns a list of ScoredOption objects. If no actions are legal
    (shouldn't happen unless the entity is trapped), returns an empty list.
    """
    actions = legal_actions(entity, world, entities, relations)
    options: list[ScoredOption] = []

    if "explore" in actions:
        options.append(ScoredOption(
            action="explore",
            target_entity=None,
            score=_score_explore(entity, world),
        ))

    if "expand" in actions:
        options.append(ScoredOption(
            action="expand",
            target_entity=None,
            score=_score_expand(entity, world),
        ))

    if "exploit" in actions:
        options.append(ScoredOption(
            action="exploit",
            target_entity=None,
            score=_score_exploit(entity, world),
        ))

    if "attack" in actions:
        # Score each attackable enemy independently
        attackable_targets = _get_attackable_targets(entity, world, entities)
        for target in attackable_targets:
            options.append(ScoredOption(
                action="attack",
                target_entity=target.name,
                score=_score_attack(entity, target, world, relations),
            ))

    if "propose_treaty" in actions:
        # Score treaty and alliance for each eligible entity
        diplomacy_targets = _get_diplomacy_targets(entity, entities, relations)
        for target in diplomacy_targets:
            options.append(ScoredOption(
                action="propose_treaty",
                target_entity=target.name,
                score=_score_propose_treaty(entity, target, relations),
            ))

    if "offer_alliance" in actions:
        diplomacy_targets = _get_diplomacy_targets(entity, entities, relations)
        for target in diplomacy_targets:
            options.append(ScoredOption(
                action="offer_alliance",
                target_entity=target.name,
                score=_score_offer_alliance(entity, target, relations),
            ))

    return options


# ---------------------------------------------------------------------------
# Target enumeration helpers
# ---------------------------------------------------------------------------

def _get_attackable_targets(
    entity: Entity,
    world: World,
    entities: list[Entity],
) -> list[Entity]:
    """
    Return living entities that own at least one node adjacent to
    the acting entity's territory.
    """
    reachable = world.get_reachable_targets(entity.name)
    target_names = {
        n.owner for n in reachable
        if n.owner is not None and n.owner != entity.name
    }
    return [
        e for e in entities
        if e.name in target_names and e.is_alive()
    ]


def _get_diplomacy_targets(
    entity: Entity,
    entities: list[Entity],
    relations: RelationshipMatrix,
) -> list[Entity]:
    """
    Return living entities eligible for diplomatic proposals —
    anyone not already at max alliance.
    """
    return [
        e for e in entities
        if e.name != entity.name
        and e.is_alive()
        and relations.get(entity.name, e.name) < RelationshipMatrix.ALLIED
    ]


# ---------------------------------------------------------------------------
# Softmax sampling
# ---------------------------------------------------------------------------

def softmax_sample(options: list[ScoredOption], temperature: float) -> ScoredOption:
    """
    Select one ScoredOption via softmax-weighted random sampling.

    Args:
        options:     Non-empty list of scored options.
        temperature: Controls randomness.
                       < 1.0 → more deterministic (favours high scores)
                       = 1.0 → balanced
                       > 1.0 → more uniform (more surprise)
                     Must be > 0.

    Returns:
        The sampled ScoredOption.

    Raises:
        ValueError: If options is empty or temperature <= 0.
    """
    if not options:
        raise ValueError("Cannot sample from empty options list.")
    if temperature <= 0:
        raise ValueError(f"Temperature must be > 0, got {temperature}")

    scores = np.array([o.score for o in options], dtype=np.float64)

    # Shift scores for numerical stability (subtract max before exp)
    scaled = scores / temperature
    shifted = scaled - np.max(scaled)
    exp_scores = np.exp(shifted)
    probabilities = exp_scores / np.sum(exp_scores)

    index = np.random.choice(len(options), p=probabilities)
    return options[index]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def choose_action(
    entity: Entity,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    temperature: float = CFG.utility.softmax_temperature,
) -> tuple[str, Optional[str]]:
    """
    Score all legal actions for the entity and sample one via softmax.

    Args:
        entity:      The acting entity.
        world:       The world graph.
        entities:    All entities in the simulation (for target enumeration).
        relations:   The relationship matrix.
        temperature: Softmax temperature (default 1.0).

    Returns:
        (action_name, target_entity_name) where target_entity_name is None
        for untargeted actions (explore, expand, exploit).

    Raises:
        RuntimeError: If the entity has no legal actions (should not happen
                      in normal simulation — means the entity is isolated
                      with no territory).
    """
    options = score_all_options(entity, world, entities, relations)

    if not options:
        raise RuntimeError(
            f"Entity '{entity.name}' has no legal actions. "
            f"This should not happen — check world connectivity and entity state."
        )

    chosen = softmax_sample(options, temperature)
    return (chosen.action, chosen.target_entity)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def score_summary(
    entity: Entity,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
) -> str:
    """
    Human-readable dump of all scored options for an entity.
    Useful for debugging and observer-mode tooling.
    """
    options = score_all_options(entity, world, entities, relations)

    if not options:
        return f"{entity.name}: no legal actions."

    # Compute softmax probabilities for display
    scores = np.array([o.score for o in options], dtype=np.float64)
    shifted = scores - np.max(scores)
    exp_scores = np.exp(shifted)
    probabilities = exp_scores / np.sum(exp_scores)

    lines = [f"Utility scores for {entity.name}:"]
    sorted_pairs = sorted(
        zip(options, probabilities),
        key=lambda pair: pair[1],
        reverse=True,
    )
    for option, prob in sorted_pairs:
        target_str = f" → {option.target_entity}" if option.target_entity else ""
        lines.append(
            f"  {option.action:<18s}{target_str:<20s}  "
            f"score={option.score:6.3f}  p={prob:5.1%}"
        )

    return "\n".join(lines)