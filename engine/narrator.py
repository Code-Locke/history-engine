"""
narrator.py — LLM prompt construction and Anthropic API calls
for history_engine.

Owns:
  - NarrationResult: structured return type carrying the narration string
    and the SimEvent that triggered it
  - NarratorStyle: loaded style block from presets.toml
  - load_narrator_style(): reads the active style from presets.toml,
    with chronicle as the fallback for any missing keys
  - build_prompt(): assembles system + user message from simulation state,
    shaped by the active narrator style
  - narrate_event(): the single public entry point called by main.py —
    takes a SimEvent, pulls world/entity context, calls the Anthropic API,
    returns a NarrationResult
  - narrate_events(): convenience wrapper for a list of SimEvents in one turn

Does NOT own:
  - Event detection or significance filtering (events.py)
  - History logging (events.py)
  - Action resolution (actions.py)
  - Utility scoring or action selection (utility.py)
  - World or entity state mutation of any kind

--- Responsibility boundary ---

events.py detects significance and returns SimEvent objects.
narrator.py receives those SimEvents and constructs LLM prompts from
the simulation state available at that moment. It calls the Anthropic API
and returns narration strings. It does not log, mutate, or score anything.

--- Prompt architecture ---

Two-message structure per Anthropic API convention:

  system  — The narrator's standing role (drawn from the active style),
             the world context (entity states, relationships, node history),
             and the rule that stories are earned by mechanics, not invented.

  user    — The specific significant event: its style-keyed one-line framing,
             the mechanical summary, supporting data, and the style-keyed
             closing instruction.

Context window discipline:
  - Entity history: CFG.narrator.history_context_turns recent events
  - Node history:   CFG.narrator.node_history_context_turns recent events
  - Relationship matrix: full (it's small and narratively essential)
  - Personality vectors: always included (explains *why* entities act)

LLM calls are intentionally event-gated. Do NOT call narrate_event()
in a per-turn loop. Only fire it when events.py returns a non-empty
SimEvent list.

--- Narrator styles ---

Style prompt blocks are defined in presets.toml under [narrator_styles.*].
The active style is set globally in config.toml as narrator.narrator_style.
If a style block is missing an event_* key, narrator.py falls back to the
chronicle style's value for that key. chronicle is the canonical reference.

Available styles: chronicle, mythic, clinical, noir, wikipedia

--- Usage ---

    from narrator import narrate_event, narrate_events

    # Single event
    result = narrate_event(event, turn, world, entities, relations)
    print(result.narration)

    # Batch (multiple significant events in one turn)
    results = narrate_events(events, turn, world, entities, relations)
    for r in results:
        print(r.narration)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anthropic

from .config import CFG
from .entity import Entity, RelationshipMatrix
from .events import SimEvent
from .world import World

# Python 3.11+ ships tomllib in stdlib.
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
# Presets path (mirrors worldgen.py convention)
# ---------------------------------------------------------------------------

PRESETS_PATH = Path(__file__).parent.parent / "config" / "presets.toml"

# The canonical fallback style. All event keys must be present here.
FALLBACK_STYLE = "chronicle"

# Mapping from SimEvent.event_type to the presets.toml key name.
_EVENT_KEY_MAP: dict[str, str] = {
    "elimination":         "event_elimination",
    "war_declared":        "event_war_declared",
    "alliance_formed":     "event_alliance_formed",
    "capital_lost":        "event_capital_lost",
    "near_death_survived": "event_near_death_survived",
    "unexpected_conquest": "event_unexpected_conquest",
}

_GENERIC_EVENT_LABEL = "A significant event has occurred."


# ---------------------------------------------------------------------------
# NarrationResult
# ---------------------------------------------------------------------------

@dataclass
class NarrationResult:
    """
    The output of a single narrate_event() call.

    Attributes:
        event:          The SimEvent that triggered this narration.
        narration:      The LLM-generated narration string. Empty string if the
                        call failed and fallback_on_error=True was set.
        turn:           Simulation turn on which this narration was produced.
        narrator_style: The style name used for this narration.
        prompt_tokens:  Token count for the prompt (from API usage metadata).
        output_tokens:  Token count for the response (from API usage metadata).
        error:          Exception message if the API call failed, else None.
                        If non-None, narration will be the mechanical description
                        from the SimEvent (the fallback).
    """
    event:          SimEvent
    narration:      str
    turn:           int
    narrator_style: str = ""
    prompt_tokens:  int = 0
    output_tokens:  int = 0
    error:          Optional[str] = None

    def __repr__(self) -> str:
        status = "ok" if self.error is None else f"error={self.error!r}"
        return (
            f"<NarrationResult turn={self.turn} "
            f"event={self.event.event_type!r} "
            f"style={self.narrator_style!r} "
            f"tokens={self.prompt_tokens}+{self.output_tokens} "
            f"{status}>"
        )


# ---------------------------------------------------------------------------
# NarratorStyle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarratorStyle:
    """
    A fully resolved narrator style block, ready for use in prompt construction.

    Loaded from presets.toml by load_narrator_style(). Any keys missing from
    the requested style are filled in from the chronicle fallback.

    Attributes:
        name:         The style name (e.g. "mythic").
        role:         The narrator's standing voice instruction (system prompt).
        closing:      The final instruction in the user prompt.
        event_labels: Map of event_type → one-line framing string.
    """
    name:         str
    role:         str
    closing:      str
    event_labels: dict[str, str]


# ---------------------------------------------------------------------------
# Style loading
# ---------------------------------------------------------------------------

def load_narrator_style(
    style_name: str,
    presets_path: Path = PRESETS_PATH,
) -> NarratorStyle:
    """
    Load a narrator style block from presets.toml.

    Any keys missing from the requested style are filled in from the
    chronicle fallback. Raises if the chronicle fallback itself is missing
    (that would be a malformed presets.toml).

    Args:
        style_name:   The style to load (must be a key under [narrator_styles]).
        presets_path: Path to presets.toml.

    Returns:
        A fully resolved NarratorStyle.

    Raises:
        FileNotFoundError: If presets.toml does not exist.
        ValueError:        If the chronicle fallback style is missing from presets.toml.
    """
    if not presets_path.exists():
        raise FileNotFoundError(
            f"presets.toml not found at {presets_path}. "
            f"Place it alongside narrator.py before running."
        )

    with open(presets_path, "rb") as f:
        raw = tomllib.load(f)

    styles_section = raw.get("narrator_styles", {})

    # -- Load the fallback (chronicle) first --
    fallback_raw = styles_section.get(FALLBACK_STYLE)
    if not fallback_raw:
        raise ValueError(
            f"presets.toml is missing the required [{FALLBACK_STYLE}] narrator style. "
            f"The chronicle style is the canonical fallback and must always be present."
        )

    # -- Load the requested style (may be the same as fallback) --
    style_raw = styles_section.get(style_name, {})

    # -- Resolve each field with fallback --
    role    = style_raw.get("role",    fallback_raw.get("role",    ""))
    closing = style_raw.get("closing", fallback_raw.get("closing", ""))

    event_labels: dict[str, str] = {}
    for event_type, toml_key in _EVENT_KEY_MAP.items():
        label = style_raw.get(toml_key) or fallback_raw.get(toml_key, _GENERIC_EVENT_LABEL)
        event_labels[event_type] = label

    return NarratorStyle(
        name=style_name,
        role=role,
        closing=closing,
        event_labels=event_labels,
    )


# ---------------------------------------------------------------------------
# Module-level style singleton
# ---------------------------------------------------------------------------

# Loaded once at import time from the active config + presets.
# narrator.py reads CFG.narrator.narrator_style at import time, consistent
# with how all other modules consume CFG.
_STYLE: NarratorStyle = load_narrator_style(CFG.narrator.narrator_style)


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

def _format_personality(personality: dict[str, float]) -> str:
    """Render a personality vector as a compact, readable string."""
    return ", ".join(f"{trait}: {value:.2f}" for trait, value in personality.items())


def _format_entity_history(entity: Entity, n: int) -> str:
    """
    Return a formatted block of the entity's recent history events,
    capped at n entries.
    """
    recent = entity.recent_history(n)
    if not recent:
        return "  (no recorded history)"
    lines = []
    for evt in recent:
        lines.append(f"  [turn {evt.turn}] ({evt.event_type}) {evt.description}")
    return "\n".join(lines)


def _format_node_history(world: World, node_name: str, n: int) -> str:
    """
    Return a formatted block of a node's recent history, capped at n entries.
    Returns a placeholder if the node doesn't exist or has no history.
    """
    try:
        node = world.get_node(node_name)
    except ValueError:
        return "  (node not found)"
    if not node.history:
        return "  (no recorded history)"
    recent = node.history[-n:] if n > 0 else []
    return "\n".join(f"  {entry}" for entry in recent)


def _format_relations(
    entity_names: list[str],
    relations: RelationshipMatrix,
) -> str:
    """
    Render the relationship matrix as a readable block covering only the
    entity names provided. Deduplicates symmetric pairs.
    """
    seen: set[frozenset[str]] = set()
    lines = []
    for a in entity_names:
        for b in entity_names:
            if a == b:
                continue
            pair = frozenset({a, b})
            if pair in seen:
                continue
            seen.add(pair)
            try:
                score = relations.get(a, b)
            except KeyError:
                continue
            if score <= -0.3:
                label = "HOSTILE"
            elif score >= 0.5:
                label = "ALLIED"
            else:
                label = "neutral"
            lines.append(f"  {a} <-> {b}: {score:+.2f}  ({label})")
    return "\n".join(lines) if lines else "  (no relations recorded)"


def _format_entity_block(entity: Entity, history_n: int) -> str:
    """
    Render a full context block for one entity: status, resources,
    personality, and recent history.
    """
    status = "ALIVE" if entity.is_alive() else "ELIMINATED"
    lines = [
        f"  Name:        {entity.name}  [{status}]",
        f"  Resources:   {entity.resources}  (total: {entity.total_resources()})",
        f"  Personality: {_format_personality(entity.personality)}",
        f"  Recent history ({history_n} events):",
        _format_entity_history(entity, history_n),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_system_prompt(
    event: SimEvent,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    style: NarratorStyle,
) -> str:
    """
    Construct the system prompt: the style's narrator role block plus
    the full world context needed to narrate this event intelligently.

    Includes:
    - Style-specific narrator role and constraints
    - All entity states and recent histories
    - Relationship matrix
    - Relevant node history (for the node involved in the event, if any)
    """
    history_n      = CFG.narrator.history_context_turns
    node_history_n = CFG.narrator.node_history_context_turns

    # -- Entity context --
    living_names = [e.name for e in entities]
    entity_blocks = [_format_entity_block(e, history_n) for e in entities]
    entity_section = (
        "=== ENTITY STATES ===\n"
        + "\n\n".join(entity_blocks)
    )

    # -- Relationship matrix --
    relation_section = (
        "=== RELATIONSHIPS ===\n"
        + _format_relations(living_names, relations)
    )

    # -- Node history (only if a specific node is involved) --
    node_section = ""
    if event.target_node:
        node_history_str = _format_node_history(world, event.target_node, node_history_n)
        try:
            node = world.get_node(event.target_node)
            node_features = ", ".join(node.features) if node.features else "no notable features"
            node_resources = str(node.resources) if node.resources else "no resources"
            node_owner = node.owner or "neutral"
        except ValueError:
            node_features = "unknown"
            node_resources = "unknown"
            node_owner = "unknown"

        node_section = (
            f"=== NODE: {event.target_node} ===\n"
            f"  Features:  {node_features}\n"
            f"  Resources: {node_resources}\n"
            f"  Owner:     {node_owner}\n"
            f"  History ({node_history_n} entries):\n"
            + node_history_str
        )

    # -- Assemble --
    sections = [style.role, entity_section, relation_section]
    if node_section:
        sections.append(node_section)

    return "\n\n".join(sections)


def _build_user_prompt(
    event: SimEvent,
    turn: int,
    style: NarratorStyle,
) -> str:
    """
    Construct the user message: the specific significant event to narrate,
    framed using the active style's event label and closing instruction.
    """
    label = style.event_labels.get(event.event_type, _GENERIC_EVENT_LABEL)

    lines = [
        f"TURN {turn} — SIGNIFICANT EVENT: {event.event_type.upper()}",
        "",
        label,
        "",
        "Mechanical summary:",
        f"  {event.description}",
    ]

    if event.metadata:
        lines.append("")
        lines.append("Supporting data:")
        for key, value in event.metadata.items():
            lines.append(f"  {key}: {value}")

    lines += [
        "",
        style.closing,
    ]

    return "\n".join(lines)


def build_prompt(
    event: SimEvent,
    turn: int,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    style: Optional[NarratorStyle] = None,
) -> tuple[str, str]:
    """
    Construct and return the (system_prompt, user_prompt) pair for a
    given SimEvent.

    Exposed publicly for testing and prompt inspection without making
    a live API call.

    Args:
        event:     The significant event to narrate.
        turn:      Current simulation turn.
        world:     The world graph.
        entities:  All entities in the simulation.
        relations: The relationship matrix.
        style:     NarratorStyle to use. Defaults to the module-level
                   singleton loaded from config + presets at import time.

    Returns:
        (system_prompt, user_prompt) as a tuple of strings.
    """
    if style is None:
        style = _STYLE
    system_prompt = _build_system_prompt(event, world, entities, relations, style)
    user_prompt   = _build_user_prompt(event, turn, style)
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def _call_api(system_prompt: str, user_prompt: str) -> tuple[str, int, int]:
    """
    Make one Anthropic API call. Returns (narration_text, prompt_tokens, output_tokens).

    Uses the model and max_tokens from CFG.narrator.
    Raises anthropic.APIError (or subclasses) on failure — callers handle.
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment

    message = client.messages.create(
        model=CFG.narrator.model,
        max_tokens=CFG.narrator.max_tokens,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt},
        ],
    )

    narration = ""
    for block in message.content:
        if block.type == "text":
            narration += block.text

    prompt_tokens  = message.usage.input_tokens
    output_tokens  = message.usage.output_tokens

    return narration.strip(), prompt_tokens, output_tokens


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def narrate_event(
    event: SimEvent,
    turn: int,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    fallback_on_error: bool = True,
    style: Optional[NarratorStyle] = None,
    prompt_only: bool = False,
) -> NarrationResult:
    """
    Narrate a single significant SimEvent via the Anthropic API.

    This is the primary entry point for main.py. Call it only when
    events.py has returned a non-empty SimEvent list — not per turn.

    Args:
        event:             The significant event to narrate.
        turn:              Current simulation turn number.
        world:             The world graph (for node context).
        entities:          All entities in the simulation.
        relations:         The relationship matrix.
        fallback_on_error: If True (default), API failures return a
                           NarrationResult with the event's mechanical
                           description as the narration and a non-None
                           error field. If False, the exception propagates.
        style:             NarratorStyle override. Defaults to the module-level
                           singleton (i.e. the style set in config.toml).
        prompt_only:       If True, print the prompt to stdout instead of
                           calling the API. Returns a NarrationResult with
                           the mechanical description and zero token counts.

    Returns:
        NarrationResult with the narration string, style name, and token usage.
    """
    active_style = style if style is not None else _STYLE
    system_prompt, user_prompt = build_prompt(event, turn, world, entities, relations, active_style)

    if prompt_only:
        print(prompt_preview(event, turn, world, entities, relations, active_style))
        return NarrationResult(
            event=event,
            narration=event.description,
            turn=turn,
            narrator_style=active_style.name,
        )

    try:
        narration, prompt_tokens, output_tokens = _call_api(system_prompt, user_prompt)
        return NarrationResult(
            event=event,
            narration=narration,
            turn=turn,
            narrator_style=active_style.name,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )
    except Exception as exc:
        if not fallback_on_error:
            raise
        # Graceful degradation: use the mechanical description as fallback
        return NarrationResult(
            event=event,
            narration=event.description,
            turn=turn,
            narrator_style=active_style.name,
            error=str(exc),
        )


def narrate_events(
    events: list[SimEvent],
    turn: int,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    fallback_on_error: bool = True,
    style: Optional[NarratorStyle] = None,
    prompt_only: bool = False,
) -> list[NarrationResult]:
    """
    Narrate a list of significant SimEvents, one API call per event.

    Multiple significant events can occur in a single turn (e.g. an attack
    that simultaneously triggers war_declared, capital_lost, and elimination).
    This function handles all of them sequentially.

    Args:
        events:            List of SimEvents from events.process_result().
        turn:              Current simulation turn number.
        world:             The world graph.
        entities:          All entities in the simulation.
        relations:         The relationship matrix.
        fallback_on_error: Passed through to each narrate_event() call.
        style:             NarratorStyle override. Passed through to each call.
        prompt_only:       Passed through to each narrate_event() call.

    Returns:
        List of NarrationResult objects in the same order as events.
        Empty list if events is empty.
    """
    return [
        narrate_event(event, turn, world, entities, relations, fallback_on_error, style, prompt_only)
        for event in events
    ]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def prompt_preview(
    event: SimEvent,
    turn: int,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    style: Optional[NarratorStyle] = None,
) -> str:
    """
    Return a formatted preview of the system and user prompts that would
    be sent to the API for a given event. No API call is made.

    Useful for debugging prompt quality without burning tokens.
    """
    active_style = style if style is not None else _STYLE
    system_prompt, user_prompt = build_prompt(event, turn, world, entities, relations, active_style)
    divider = "=" * 72
    return (
        f"{divider}\n"
        f"STYLE: {active_style.name}\n"
        f"{divider}\n"
        f"SYSTEM PROMPT\n"
        f"{divider}\n"
        f"{system_prompt}\n\n"
        f"{divider}\n"
        f"USER PROMPT\n"
        f"{divider}\n"
        f"{user_prompt}\n"
        f"{divider}"
    )


def narration_summary(results: list[NarrationResult]) -> str:
    """
    Human-readable summary of a batch of NarrationResults.
    Useful for terminal output and observer-mode tooling.
    """
    if not results:
        return "No narrations produced."

    lines = [f"Narrations ({len(results)}):"]
    total_prompt  = sum(r.prompt_tokens  for r in results)
    total_output  = sum(r.output_tokens  for r in results)
    error_count   = sum(1 for r in results if r.error is not None)

    for r in results:
        status = f"[ERROR: {r.error}]" if r.error else f"[{r.prompt_tokens}+{r.output_tokens} tokens]"
        style_tag = f"  style={r.narrator_style}" if r.narrator_style else ""
        lines.append(f"\n  [{r.event.event_type}] turn={r.turn}{style_tag}  {status}")
        for line in r.narration.splitlines():
            lines.append(f"    {line}")

    lines.append(
        f"\nTotal tokens: {total_prompt} prompt + {total_output} output"
        + (f"  ({error_count} error(s), fallback used)" if error_count else "")
    )
    return "\n".join(lines)