"""
main.py — Simulation loop for history_engine.

Owns:
  - Hydrating a world JSON (produced by worldgen.py) into live World,
    Entity, and RelationshipMatrix objects
  - Running the turn loop for CFG.simulation.turn_count turns
  - Dispatching chosen actions to the appropriate resolve_* function
  - Passing ActionResults to events.process_result()
  - Passing significant SimEvents to narrator.narrate_events()
  - Rich terminal output for the full simulation run
  - Wiring SimOutput (output.py) for .md chronicle and .jsonl event log

Does NOT own:
  - World or entity state (world.py, entity.py)
  - Action legality or resolution (actions.py)
  - Utility scoring / action selection (utility.py)
  - Event detection or logging (events.py)
  - LLM narration (narrator.py)
  - World generation (worldgen.py)
  - File writing (output.py)

--- Usage ---

    # Run with the default world file
    python -m engine.main

    # Specify a world file
    python -m engine.main --world worlds/my_world.json

    # Suppress LLM narration (dry run)
    python -m engine.main --no-narrate

    # Print prompts to stdout for manual copy-paste (no API call)
    python -m engine.main --prompt-only

    # Suppress output files (.md and .jsonl)
    python -m engine.main --no-output

--- Prerequisites ---

    Run worldgen.py first to produce the world JSON:
        python -m engine.worldgen
        python -m engine.worldgen -o worlds/my_world.json
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import zip_longest
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .actions import (
    ActionResult,
    resolve_attack,
    resolve_expand,
    resolve_exploit,
    resolve_explore,
    resolve_propose_treaty,
)

from .config import CFG
from .entity import Entity, RelationshipMatrix
from .events import SimEvent, process_result
from .narrator import NarrationResult, narrate_events, narration_summary
from .output import SimOutput
from .utility import choose_action
from .world import World, WorldEdge, WorldNode


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

console = Console()


# ---------------------------------------------------------------------------
# Hydration — JSON → live objects
# ---------------------------------------------------------------------------

def hydrate_world(data: dict[str, Any]) -> World:
    """
    Build a World graph from the JSON produced by worldgen.py.

    Nodes and edges are reconstructed with their full data. Node history
    and explored_by sets are initialised from the JSON (both empty at
    generation time, but may be non-empty if loading a mid-run checkpoint).
    """
    world = World()

    for node_data in data["nodes"]:
        node = WorldNode(
            name=node_data["name"],
            features=node_data.get("features", []),
            resources=dict(node_data.get("resources", {})),
            owner=node_data.get("owner"),
            is_capital=node_data.get("is_capital", False),
            history=node_data.get("history", []),
            explored_by=set(node_data.get("explored_by", [])),
        )
        world.add_node(node)

    for edge_data in data["edges"]:
        edge = WorldEdge(
            traversal_cost=edge_data.get("traversal_cost", 1),
            contested=edge_data.get("contested", False),
        )
        world.add_edge(edge_data["node_a"], edge_data["node_b"], edge)

    return world


def hydrate_entities(data: dict[str, Any]) -> list[Entity]:
    """
    Build Entity objects from the JSON produced by worldgen.py.
    """
    entities: list[Entity] = []

    for entity_data in data["entities"]:
        entity = Entity(
            name=entity_data["name"],
            personality=dict(entity_data.get("personality", {
                "aggression":   0.5,
                "expansionism": 0.5,
                "greed":        0.5,
                "paranoia":     0.5,
            })),
            resources=dict(entity_data.get("resources", {})),
            alive=entity_data.get("alive", True),
        )
        entities.append(entity)

    return entities


def hydrate_relations(data: dict[str, Any], entity_names: list[str]) -> RelationshipMatrix:
    """
    Build a RelationshipMatrix from the JSON produced by worldgen.py.
    All pairs are initialised to 0.0 first, then overwritten with any
    values present in the JSON.
    """
    matrix = RelationshipMatrix(entity_names, default=0.0)

    relations_data = data.get("relations", {})
    for a, targets in relations_data.items():
        for b, score in targets.items():
            if a in entity_names and b in entity_names and a != b:
                try:
                    matrix.set(a, b, float(score))
                except KeyError:
                    pass  # pair not registered — skip silently

    return matrix


def load_world_file(path: Path) -> tuple[World, list[Entity], RelationshipMatrix, dict[str, Any]]:
    """
    Read and parse a world JSON file, returning hydrated simulation objects
    and the raw meta block.

    Returns:
        (world, entities, relations, meta)

    Raises:
        FileNotFoundError: If the JSON file does not exist.
        ValueError:        If the JSON is malformed or missing required keys.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"World file not found: {path}\n"
            f"Run 'python -m engine.worldgen' to generate one first."
        )

    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse world JSON at {path}: {exc}") from exc

    for key in ("nodes", "edges", "entities", "relations"):
        if key not in data:
            raise ValueError(
                f"World JSON at {path} is missing required key: '{key}'"
            )

    world    = hydrate_world(data)
    entities = hydrate_entities(data)
    entity_names = [e.name for e in entities]
    relations    = hydrate_relations(data, entity_names)
    meta         = data.get("meta", {})

    return world, entities, relations, meta


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------

def dispatch_action(
    action: str,
    target_entity_name: str | None,
    actor: Entity,
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    turn: int,
) -> ActionResult:
    """
    Call the correct resolve_* function based on the chosen action name.

    Args:
        action:             Action name from choose_action().
        target_entity_name: Target entity name, or None for untargeted actions.
        actor:              The acting Entity.
        world:              The world graph.
        entities:           All entities in the simulation.
        relations:          The relationship matrix.
        turn:               Current turn number (needed by resolve_attack).

    Returns:
        ActionResult from the appropriate resolver.
    """
    if action == "explore":
        return resolve_explore(actor, world)

    if action == "expand":
        return resolve_expand(actor, world)

    if action == "exploit":
        return resolve_exploit(actor, world)

    if action == "attack":
        if target_entity_name is None:
            raise RuntimeError(
                f"Action 'attack' chosen by '{actor.name}' with no target entity."
            )
        return resolve_attack(actor, target_entity_name, world, entities, relations, turn)

    if action == "propose_treaty":
        if target_entity_name is None:
            raise RuntimeError(
                f"Action 'propose_treaty' chosen by '{actor.name}' with no target entity."
            )
        return resolve_propose_treaty(actor, target_entity_name, entities, relations, alliance=False)

    if action == "offer_alliance":
        if target_entity_name is None:
            raise RuntimeError(
                f"Action 'offer_alliance' chosen by '{actor.name}' with no target entity."
            )
        return resolve_propose_treaty(actor, target_entity_name, entities, relations, alliance=True)

    raise RuntimeError(
        f"Unknown action '{action}' chosen by '{actor.name}'. "
        f"This should never happen — check utility.py's legal_actions."
    )


# ---------------------------------------------------------------------------
# Rich output helpers
# ---------------------------------------------------------------------------

def print_sim_start(
    entities: list[Entity],
    world: World,
    relations: RelationshipMatrix,
) -> None:
    """Print the initial world and entity state before turn 1."""
    console.print()
    console.print(Rule("[bold]Simulation Start[/bold]"))
    console.print()
    console.print(f"[bold]Entities ({len(entities)}):[/bold]")
    for entity in entities:
        p = entity.personality
        console.print(
            f"  [cyan]{entity.name}[/cyan]  "
            f"agg={p['aggression']:.2f}  "
            f"exp={p['expansionism']:.2f}  "
            f"grd={p['greed']:.2f}  "
            f"par={p['paranoia']:.2f}"
        )
    console.print()
    console.print(f"[bold]World:[/bold]  {world.node_count()} nodes  {world.edge_count()} edges")
    console.print()


def print_turn_header(turn: int, total_turns: int) -> None:
    """Print a separator for each turn."""
    console.print()
    console.print(Rule(f"[dim]Turn {turn} / {total_turns}[/dim]"))


def print_action(actor_name: str, result: ActionResult) -> None:
    """Print a single resolved action result."""
    action_colours = {
        "explore":        "yellow",
        "expand":         "green",
        "exploit":        "blue",
        "attack":         "red",
        "propose_treaty": "magenta",
        "offer_alliance": "magenta",
    }
    colour = action_colours.get(result.action, "white")
    tag = f"[{colour}]{result.action.upper()}[/{colour}]"
    console.print(f"  {tag}  [bold]{actor_name}[/bold]  {result.description}")


def print_significant_events(events: list[SimEvent]) -> None:
    """Print detected significant events before narration."""
    if not events:
        return
    console.print(f"  [bold yellow]⚡ Significant: {', '.join(e.event_type for e in events)}[/bold yellow]")


def print_narrations(results: list[NarrationResult]) -> None:
    """Print each narration in a styled panel."""
    for r in results:
        if r.error:
            console.print(f"  [dim red][narrator error: {r.error}][/dim red]")
            continue

        event_label = r.event.event_type.replace("_", " ").title()
        console.print()
        console.print(Panel(
            r.narration,
            title=f"[italic]{event_label}[/italic]",
            border_style="dim cyan",
            expand=False,
        ))


def print_elimination(entity_name: str, turn: int) -> None:
    """Print a notice when an entity is eliminated."""
    console.print(f"\n  [bold red]✖ {entity_name} has been eliminated (turn {turn})[/bold red]")


def print_sim_end(
    entities: list[Entity],
    world: World,
    turn: int,
    early_end: bool,
) -> None:
    """Print the final world state at the end of the simulation."""
    console.print()
    console.print(Rule("[bold]Simulation End[/bold]"))
    console.print(f"  Ended on turn: {turn}")
    if early_end:
        console.print("  [bold green]Early termination — only one entity remains.[/bold green]")
    console.print()

    alive = [e for e in entities if e.is_alive()]
    dead  = [e for e in entities if not e.is_alive()]

    console.print("[bold]Survivors:[/bold]")
    for entity in alive:
        owned = world.get_nodes_by_owner(entity.name)
        console.print(
            f"  [cyan]{entity.name}[/cyan]  "
            f"nodes={len(owned)}  "
            f"resources={entity.resources}  "
            f"(total: {entity.total_resources()})"
        )

    if dead:
        console.print()
        console.print("[bold]Eliminated:[/bold]")
        for entity in dead:
            console.print(f"  [dim]{entity.name}[/dim]")


def print_narration_summary(results: list[NarrationResult]) -> None:
    """
    Print a Rich table summarising all narration calls made during the run.

    Displays per-event token usage and error status, plus run-level totals.
    Only called when at least one narration was attempted.
    """
    if not results:
        return

    total_prompt = sum(r.prompt_tokens for r in results)
    total_output = sum(r.output_tokens for r in results)
    error_count  = sum(1 for r in results if r.error is not None)

    table = Table(
        title="LLM Narration Summary",
        border_style="dim magenta",
        title_style="bold magenta",
        header_style="bold",
        show_footer=True,
        expand=False,
    )

    table.add_column("Turn",   style="dim",        footer="Total")
    table.add_column("Event",  style="cyan")
    table.add_column("Style",  style="dim")
    table.add_column("Prompt", justify="right",    footer=f"{total_prompt:,}")
    table.add_column("Output", justify="right",    footer=f"{total_output:,}")
    table.add_column("Total",  justify="right",    footer=f"{total_prompt + total_output:,}")
    table.add_column("Status", justify="center")

    for r in results:
        prompt_str = f"{r.prompt_tokens:,}"
        output_str = f"{r.output_tokens:,}"
        total_str  = f"{r.prompt_tokens + r.output_tokens:,}"

        if r.error:
            status = "[bold red]✖ error[/bold red]"
            prompt_str = "[dim]—[/dim]"
            output_str = "[dim]—[/dim]"
            total_str  = "[dim]—[/dim]"
        else:
            status = "[green]✓[/green]"

        table.add_row(
            str(r.turn),
            r.event.event_type.replace("_", " "),
            r.narrator_style or "—",
            prompt_str,
            output_str,
            total_str,
            status,
        )

    console.print()
    console.print(table)

    if error_count:
        console.print(
            f"  [dim red]{error_count} narration error{'s' if error_count != 1 else ''} — "
            f"mechanical fallback used.[/dim red]"
        )


# ---------------------------------------------------------------------------
# Turn loop
# ---------------------------------------------------------------------------

def run_simulation(
    world: World,
    entities: list[Entity],
    relations: RelationshipMatrix,
    meta: dict[str, Any],
    narrate: bool = True,
    prompt_only: bool = False,
    output: SimOutput | None = None,
) -> None:
    """
    Run the main simulation loop.

    Args:
        world:       Hydrated World graph.
        entities:    Hydrated Entity list.
        relations:   Hydrated RelationshipMatrix.
        meta:        Raw meta block from the world JSON.
        narrate:     If False, LLM narration calls are skipped entirely.
        prompt_only: If True, print prompts to stdout instead of calling
                     the API. Implies narrate=True for event detection,
                     but no API call is made.
        output:      SimOutput instance, or None if --no-output was passed.
    """
    import random

    total_turns = CFG.simulation.turn_count
    print_sim_start(entities, world, relations)

    early_end = False

    # Accumulates every NarrationResult produced during the run.
    # Used for the post-run narration summary panel.
    all_narration_results: list[NarrationResult] = []

    for turn in range(1, total_turns + 1):
        print_turn_header(turn, total_turns)

        # --- Stage the turn header in the output writer ---
        if output:
            output.write_turn_header(turn, total_turns)

        # Shuffle entity order each turn — no fixed advantage from position.
        living = [e for e in entities if e.is_alive()]
        random.shuffle(living)

        for actor in living:
            # Re-check alive — an entity can be eliminated mid-turn.
            if not actor.is_alive():
                continue

            # --- Choose action ---
            try:
                action, target_entity_name = choose_action(
                    entity=actor,
                    world=world,
                    entities=entities,
                    relations=relations,
                )
            except RuntimeError as exc:
                console.print(f"  [dim]{actor.name} has no legal actions: {exc}[/dim]")
                continue

            # --- Resolve action ---
            try:
                result = dispatch_action(
                    action=action,
                    target_entity_name=target_entity_name,
                    actor=actor,
                    world=world,
                    entities=entities,
                    relations=relations,
                    turn=turn,
                )
            except RuntimeError as exc:
                console.print(f"  [bold red]Dispatch error for {actor.name}: {exc}[/bold red]")
                continue

            # --- Terminal: print action ---
            print_action(actor.name, result)

            # --- Output: buffer action row ---
            if output:
                output.write_action(actor.name, result)

            # --- Detect events and log history ---
            sim_events: list[SimEvent] = process_result(
                result=result,
                turn=turn,
                world=world,
                entities=entities,
                relations=relations,
            )

            if not sim_events:
                continue

            # --- Terminal: print significant events ---
            print_significant_events(sim_events)

            # --- Narrate or print prompts ---
            narration_results: list[NarrationResult] = []

            if prompt_only:
                narrate_events(
                    events=sim_events,
                    turn=turn,
                    world=world,
                    entities=entities,
                    relations=relations,
                    fallback_on_error=True,
                    prompt_only=True,
                )
            elif narrate:
                narration_results = narrate_events(
                    events=sim_events,
                    turn=turn,
                    world=world,
                    entities=entities,
                    relations=relations,
                    fallback_on_error=True,
                )
                print_narrations(narration_results)
                all_narration_results.extend(narration_results)

            # --- Output: write each significant event ---
            # Pair each SimEvent with its NarrationResult (if any).
            # narration_results may be shorter than sim_events if narration
            # was skipped — zip_longest handles the mismatch gracefully.
            for event, narration in zip_longest(sim_events, narration_results, fillvalue=None):
                if event is None:
                    continue

                # Terminal: print elimination notice inline.
                if event.event_type == "elimination" and event.target_entity:
                    print_elimination(event.target_entity, turn)

                if output:
                    output.write_significant_event(
                        event=event,
                        narration=narration,
                        world=world,
                        entities=entities,
                        relations=relations,
                    )
                    if event.event_type == "elimination" and event.target_entity:
                        output.write_elimination(event.target_entity, turn)

        # --- End of turn: flush routine turn to output ---
        if output:
            output.write_turn_end(entities, relations)

        # --- Early termination check ---
        still_alive = [e for e in entities if e.is_alive()]
        if len(still_alive) <= 1:
            early_end = True
            break

    # --- Terminal: final state ---
    print_sim_end(entities, world, turn, early_end)

    # --- Terminal: narration summary (only when narration ran) ---
    if all_narration_results:
        print_narration_summary(all_narration_results)

    # --- Output: final state ---
    if output:
        output.write_sim_end(
            entities=entities,
            world=world,
            relations=relations,
            final_turn=turn,
            early_end=early_end,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a history_engine simulation from a generated world file.",
    )
    parser.add_argument(
        "--world",
        type=str,
        default="world.json",
        help="Path to the world JSON file produced by worldgen.py (default: world.json)",
    )
    parser.add_argument(
        "--no-narrate",
        action="store_true",
        help="Skip LLM narration calls entirely (dry run — mechanics only)",
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help=(
            "Print the full system + user prompt for each significant event to stdout "
            "instead of calling the API. Use this to copy-paste prompts manually. "
            "Cannot be combined with --no-narrate."
        ),
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Skip writing .md and .jsonl output files entirely.",
    )
    args = parser.parse_args()

    if args.no_narrate and args.prompt_only:
        console.print("[bold red]Error:[/bold red] --no-narrate and --prompt-only are mutually exclusive.")
        sys.exit(1)

    world_path = Path(args.world)

    # --- Load world ---
    try:
        world, entities, relations, meta = load_world_file(world_path)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[bold red]World file error:[/bold red] {exc}")
        sys.exit(1)

    # --- Print header ---
    console.print()
    console.print(Panel(
        Text.assemble(
            (f"World: {world_path.name}\n", "bold white"),
            (f"Seed: {meta.get('seed', 'unknown')}   ", "dim"),
            (f"Theme: {meta.get('theme', 'unknown')}   ", "dim"),
            (f"Entities: {len(entities)}   ", "dim"),
            (f"Nodes: {world.node_count()}   ", "dim"),
            (f"Turns: {CFG.simulation.turn_count}", "dim"),
        ),
        title="[bold cyan]history_engine[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))

    if args.no_narrate:
        console.print("[dim yellow]  Narration disabled (--no-narrate)[/dim yellow]")
    if args.prompt_only:
        console.print("[dim yellow]  Prompt-only mode — no API calls made.[/dim yellow]")
    if args.no_output:
        console.print("[dim yellow]  Output disabled (--no-output)[/dim yellow]")

    # --- Run ---
    seed = meta.get("seed", -1)

    if args.no_output:
        run_simulation(
            world=world,
            entities=entities,
            relations=relations,
            meta=meta,
            narrate=not args.no_narrate,
            prompt_only=args.prompt_only,
            output=None,
        )
    else:
        with SimOutput(seed=seed, meta=meta) as out:
            out.write_run_header(entities, world, relations, meta)
            run_simulation(
                world=world,
                entities=entities,
                relations=relations,
                meta=meta,
                narrate=not args.no_narrate,
                prompt_only=args.prompt_only,
                output=out,
            )
            console.print(
                f"\n[dim]Chronicle: {out.md_path}[/dim]"
                f"\n[dim]Events log: {out.jsonl_path}[/dim]"
            )


if __name__ == "__main__":
    main()