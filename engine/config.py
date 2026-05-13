"""
config.py — Configuration loader and typed schema for history_engine.

Owns:
  - SimConfig: nested dataclass schema for all tunable values
  - load_config(): reads config.toml, validates all fields, returns SimConfig
  - CFG: module-level SimConfig instance imported by all other modules

Does NOT own:
  - Simulation logic of any kind
  - Default values (those live in config.toml)

--- Usage ---

All other modules import the singleton:

    from config import CFG

    # Then use as:
    CFG.utility.softmax_temperature
    CFG.combat.prob_min
    CFG.narrator.model
    CFG.narrator.narrator_style
    # etc.

--- Validation policy ---

Bad values raise ConfigError at startup with a descriptive message.
There are no silent fallbacks. If config.toml is malformed or a value
is out of range, the simulation will not start.

--- TOML loading ---

Requires Python 3.11+ for stdlib tomllib.
On 3.10 or earlier, install the `tomli` backport:
    pip install tomli
and swap the import below accordingly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Python 3.11+ ships tomllib in stdlib.
# For 3.10 and below, fall back to the tomli backport.
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError(
            "Python < 3.11 requires the 'tomli' package: pip install tomli"
        ) from exc


# ---------------------------------------------------------------------------
# Config file path
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.toml"

ALLOWED_MODELS = {
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
}

ALLOWED_NARRATOR_STYLES = {
    "chronicle",
    "mythic",
    "clinical",
    "noir",
    "wikipedia",
    "sibylline",
    "gossip",
    "elegy",
    "scripture"
}


# ---------------------------------------------------------------------------
# ConfigError
# ---------------------------------------------------------------------------

class ConfigError(ValueError):
    """Raised when config.toml contains an invalid or missing value."""


# ---------------------------------------------------------------------------
# Sub-dataclasses (one per TOML section)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimulationConfig:
    turn_count:    int
    entity_count:  int
    random_seed:   int   # -1 means non-deterministic


@dataclass(frozen=True)
class WorldConfig:
    node_count:        int
    edge_density:      float
    traversal_cost_min: int
    traversal_cost_max: int


@dataclass(frozen=True)
class NarratorConfig:
    model:                      str
    max_tokens:                 int
    history_context_turns:      int
    node_history_context_turns: int
    narrator_style:             str   # one of ALLOWED_NARRATOR_STYLES


@dataclass(frozen=True)
class UtilityConfig:
    softmax_temperature:      float
    base_score:               float
    unexplored_bonus_scale:   float
    neutral_resource_scale:   float
    exploit_resource_scale:   float
    hostility_attack_bonus:   float
    power_ratio_scale:        float
    paranoia_discount_scale:  float
    relation_treaty_bonus:    float
    relation_alliance_bonus:  float
    alliance_warmth_threshold: float
    diplomacy_paranoia_bonus: float


@dataclass(frozen=True)
class CombatConfig:
    prob_min:                     float
    prob_max:                     float
    zero_resource_defense_penalty: float
    attack_relation_delta:        float


@dataclass(frozen=True)
class DiplomacyConfig:
    treaty_delta_accept:   float
    treaty_delta_reject:   float
    alliance_delta_accept: float
    alliance_delta_reject: float


@dataclass(frozen=True)
class EventsConfig:
    war_threshold:                float
    near_death_win_prob_threshold: float


# ---------------------------------------------------------------------------
# Top-level SimConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimConfig:
    """
    Fully validated configuration for a history_engine simulation run.

    Populated by load_config() from config.toml.
    Imported as the module-level singleton CFG.

    All fields are frozen — treat as read-only after load.
    """
    simulation: SimulationConfig
    world:      WorldConfig
    narrator:   NarratorConfig
    utility:    UtilityConfig
    combat:     CombatConfig
    diplomacy:  DiplomacyConfig
    events:     EventsConfig


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _require_key(section: dict, key: str, section_name: str) -> Any:
    """Raise ConfigError if key is missing from section."""
    if key not in section:
        raise ConfigError(
            f"[{section_name}] missing required key: '{key}'"
        )
    return section[key]


def _require_int(section: dict, key: str, section_name: str) -> int:
    value = _require_key(section, key, section_name)
    if not isinstance(value, int):
        raise ConfigError(
            f"[{section_name}] '{key}' must be an integer, got {type(value).__name__}: {value!r}"
        )
    return value


def _require_float(section: dict, key: str, section_name: str) -> float:
    value = _require_key(section, key, section_name)
    if not isinstance(value, (int, float)):
        raise ConfigError(
            f"[{section_name}] '{key}' must be a number, got {type(value).__name__}: {value!r}"
        )
    return float(value)


def _require_str(section: dict, key: str, section_name: str) -> str:
    value = _require_key(section, key, section_name)
    if not isinstance(value, str):
        raise ConfigError(
            f"[{section_name}] '{key}' must be a string, got {type(value).__name__}: {value!r}"
        )
    return value


def _require_section(raw: dict, key: str) -> dict:
    if key not in raw:
        raise ConfigError(f"Missing required TOML section: [{key}]")
    if not isinstance(raw[key], dict):
        raise ConfigError(f"[{key}] must be a TOML table, not a scalar.")
    return raw[key]


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_simulation(raw: dict) -> SimulationConfig:
    s = _require_section(raw, "simulation")
    sn = "simulation"

    turn_count   = _require_int(s, "turn_count", sn)
    entity_count = _require_int(s, "entity_count", sn)
    random_seed  = _require_int(s, "random_seed", sn)

    if turn_count < 1:
        raise ConfigError(f"[simulation] 'turn_count' must be >= 1, got {turn_count}")
    if entity_count < 2:
        raise ConfigError(f"[simulation] 'entity_count' must be >= 2, got {entity_count}")
    if random_seed < -1:
        raise ConfigError(f"[simulation] 'random_seed' must be >= -1, got {random_seed}")

    return SimulationConfig(
        turn_count=turn_count,
        entity_count=entity_count,
        random_seed=random_seed,
    )


def _parse_world(raw: dict, entity_count: int) -> WorldConfig:
    s = _require_section(raw, "world")
    sn = "world"

    node_count        = _require_int(s, "node_count", sn)
    edge_density      = _require_float(s, "edge_density", sn)
    traversal_cost_min = _require_int(s, "traversal_cost_min", sn)
    traversal_cost_max = _require_int(s, "traversal_cost_max", sn)

    if node_count <= entity_count:
        raise ConfigError(
            f"[world] 'node_count' ({node_count}) must be > 'entity_count' ({entity_count})"
        )
    if not (0.0 <= edge_density <= 1.0):
        raise ConfigError(
            f"[world] 'edge_density' must be in [0.0, 1.0], got {edge_density}"
        )
    if traversal_cost_min < 1:
        raise ConfigError(
            f"[world] 'traversal_cost_min' must be >= 1, got {traversal_cost_min}"
        )
    if traversal_cost_max < traversal_cost_min:
        raise ConfigError(
            f"[world] 'traversal_cost_max' ({traversal_cost_max}) must be "
            f">= 'traversal_cost_min' ({traversal_cost_min})"
        )

    return WorldConfig(
        node_count=node_count,
        edge_density=edge_density,
        traversal_cost_min=traversal_cost_min,
        traversal_cost_max=traversal_cost_max,
    )


def _parse_narrator(raw: dict) -> NarratorConfig:
    s = _require_section(raw, "narrator")
    sn = "narrator"

    model                      = _require_str(s, "model", sn)
    max_tokens                 = _require_int(s, "max_tokens", sn)
    history_context_turns      = _require_int(s, "history_context_turns", sn)
    node_history_context_turns = _require_int(s, "node_history_context_turns", sn)
    narrator_style             = _require_str(s, "narrator_style", sn)

    if model not in ALLOWED_MODELS:
        raise ConfigError(
            f"[narrator] 'model' must be one of {sorted(ALLOWED_MODELS)}, got {model!r}"
        )
    if max_tokens < 1:
        raise ConfigError(f"[narrator] 'max_tokens' must be >= 1, got {max_tokens}")
    if history_context_turns < 0:
        raise ConfigError(
            f"[narrator] 'history_context_turns' must be >= 0, got {history_context_turns}"
        )
    if node_history_context_turns < 0:
        raise ConfigError(
            f"[narrator] 'node_history_context_turns' must be >= 0, got {node_history_context_turns}"
        )
    if narrator_style not in ALLOWED_NARRATOR_STYLES:
        raise ConfigError(
            f"[narrator] 'narrator_style' must be one of "
            f"{sorted(ALLOWED_NARRATOR_STYLES)}, got {narrator_style!r}"
        )

    return NarratorConfig(
        model=model,
        max_tokens=max_tokens,
        history_context_turns=history_context_turns,
        node_history_context_turns=node_history_context_turns,
        narrator_style=narrator_style,
    )


def _parse_utility(raw: dict) -> UtilityConfig:
    s = _require_section(raw, "utility")
    sn = "utility"

    softmax_temperature       = _require_float(s, "softmax_temperature", sn)
    base_score                = _require_float(s, "base_score", sn)
    unexplored_bonus_scale    = _require_float(s, "unexplored_bonus_scale", sn)
    neutral_resource_scale    = _require_float(s, "neutral_resource_scale", sn)
    exploit_resource_scale    = _require_float(s, "exploit_resource_scale", sn)
    hostility_attack_bonus    = _require_float(s, "hostility_attack_bonus", sn)
    power_ratio_scale         = _require_float(s, "power_ratio_scale", sn)
    paranoia_discount_scale   = _require_float(s, "paranoia_discount_scale", sn)
    relation_treaty_bonus     = _require_float(s, "relation_treaty_bonus", sn)
    relation_alliance_bonus   = _require_float(s, "relation_alliance_bonus", sn)
    alliance_warmth_threshold = _require_float(s, "alliance_warmth_threshold", sn)
    diplomacy_paranoia_bonus  = _require_float(s, "diplomacy_paranoia_bonus", sn)

    if softmax_temperature <= 0:
        raise ConfigError(
            f"[utility] 'softmax_temperature' must be > 0, got {softmax_temperature}"
        )
    if base_score <= 0:
        raise ConfigError(f"[utility] 'base_score' must be > 0, got {base_score}")

    return UtilityConfig(
        softmax_temperature=softmax_temperature,
        base_score=base_score,
        unexplored_bonus_scale=unexplored_bonus_scale,
        neutral_resource_scale=neutral_resource_scale,
        exploit_resource_scale=exploit_resource_scale,
        hostility_attack_bonus=hostility_attack_bonus,
        power_ratio_scale=power_ratio_scale,
        paranoia_discount_scale=paranoia_discount_scale,
        relation_treaty_bonus=relation_treaty_bonus,
        relation_alliance_bonus=relation_alliance_bonus,
        alliance_warmth_threshold=alliance_warmth_threshold,
        diplomacy_paranoia_bonus=diplomacy_paranoia_bonus,
    )


def _parse_combat(raw: dict) -> CombatConfig:
    s = _require_section(raw, "combat")
    sn = "combat"

    prob_min                      = _require_float(s, "prob_min", sn)
    prob_max                      = _require_float(s, "prob_max", sn)
    zero_resource_defense_penalty = _require_float(s, "zero_resource_defense_penalty", sn)
    attack_relation_delta         = _require_float(s, "attack_relation_delta", sn)

    if not (0.0 < prob_min < 0.5):
        raise ConfigError(
            f"[combat] 'prob_min' must be in (0.0, 0.5), got {prob_min}"
        )
    if not (0.5 < prob_max < 1.0):
        raise ConfigError(
            f"[combat] 'prob_max' must be in (0.5, 1.0), got {prob_max}"
        )
    if not (0.0 <= zero_resource_defense_penalty <= 1.0):
        raise ConfigError(
            f"[combat] 'zero_resource_defense_penalty' must be in [0.0, 1.0], "
            f"got {zero_resource_defense_penalty}"
        )
    if attack_relation_delta >= 0:
        raise ConfigError(
            f"[combat] 'attack_relation_delta' must be negative, got {attack_relation_delta}"
        )

    return CombatConfig(
        prob_min=prob_min,
        prob_max=prob_max,
        zero_resource_defense_penalty=zero_resource_defense_penalty,
        attack_relation_delta=attack_relation_delta,
    )


def _parse_diplomacy(raw: dict) -> DiplomacyConfig:
    s = _require_section(raw, "diplomacy")
    sn = "diplomacy"

    treaty_delta_accept   = _require_float(s, "treaty_delta_accept", sn)
    treaty_delta_reject   = _require_float(s, "treaty_delta_reject", sn)
    alliance_delta_accept = _require_float(s, "alliance_delta_accept", sn)
    alliance_delta_reject = _require_float(s, "alliance_delta_reject", sn)

    if treaty_delta_accept <= 0:
        raise ConfigError(
            f"[diplomacy] 'treaty_delta_accept' must be > 0, got {treaty_delta_accept}"
        )
    if treaty_delta_reject >= 0:
        raise ConfigError(
            f"[diplomacy] 'treaty_delta_reject' must be < 0, got {treaty_delta_reject}"
        )
    if alliance_delta_accept <= 0:
        raise ConfigError(
            f"[diplomacy] 'alliance_delta_accept' must be > 0, got {alliance_delta_accept}"
        )
    if alliance_delta_reject >= 0:
        raise ConfigError(
            f"[diplomacy] 'alliance_delta_reject' must be < 0, got {alliance_delta_reject}"
        )

    return DiplomacyConfig(
        treaty_delta_accept=treaty_delta_accept,
        treaty_delta_reject=treaty_delta_reject,
        alliance_delta_accept=alliance_delta_accept,
        alliance_delta_reject=alliance_delta_reject,
    )


def _parse_events(raw: dict) -> EventsConfig:
    s = _require_section(raw, "events")
    sn = "events"

    war_threshold                 = _require_float(s, "war_threshold", sn)
    near_death_win_prob_threshold = _require_float(s, "near_death_win_prob_threshold", sn)

    if war_threshold >= 0:
        raise ConfigError(
            f"[events] 'war_threshold' must be negative, got {war_threshold}"
        )
    if not (0.5 < near_death_win_prob_threshold < 1.0):
        raise ConfigError(
            f"[events] 'near_death_win_prob_threshold' must be in (0.5, 1.0), "
            f"got {near_death_win_prob_threshold}"
        )

    return EventsConfig(
        war_threshold=war_threshold,
        near_death_win_prob_threshold=near_death_win_prob_threshold,
    )


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> SimConfig:
    """
    Read config.toml from `path`, validate all values, and return a SimConfig.

    Raises:
        FileNotFoundError: If the TOML file does not exist.
        ConfigError:       If any value is missing, wrong type, or out of range.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"config.toml not found at {path}. "
            f"Copy config.toml to the project root before running."
        )

    with open(path, "rb") as f:
        try:
            raw = tomllib.load(f)
        except Exception as exc:
            raise ConfigError(f"Failed to parse config.toml: {exc}") from exc

    # Simulation is parsed first because world validation depends on entity_count.
    simulation = _parse_simulation(raw)

    return SimConfig(
        simulation=simulation,
        world=_parse_world(raw, simulation.entity_count),
        narrator=_parse_narrator(raw),
        utility=_parse_utility(raw),
        combat=_parse_combat(raw),
        diplomacy=_parse_diplomacy(raw),
        events=_parse_events(raw),
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: The global config instance. Import this everywhere:
#:     from config import CFG
#: All other modules read from CFG at call time — not at import time —
#: so test code can rebind CFG before calling into any module.
CFG: SimConfig = load_config()