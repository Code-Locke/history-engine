"""
output.py — Simulation output writer for history_engine.

Owns:
  - SimOutput: context-managed class that holds open file handles for the
    .md (markdown chronicle) and .jsonl (structured event log) output files
  - Markdown chronicle: written incrementally, turn by turn, throughout the run
  - JSONL event log: one record per significant event, written at narration time
  - Output filename generation (timestamped + seeded)
  - runs/ directory creation

Does NOT own:
  - Rich terminal output (main.py)
  - Event detection or significance filtering (events.py)
  - LLM narration (narrator.py)
  - Any simulation state mutation

--- Usage ---

    from output import SimOutput

    with SimOutput(seed=CFG.simulation.random_seed, meta=meta) as out:
        out.write_run_header(entities, world, relations, meta)

        for turn in range(1, total_turns + 1):
            out.write_turn_header(turn, total_turns)

            for actor in living:
                out.write_action(actor.name, result)

            if sim_events:
                out.write_significant_event(event, narration_result, world, entities, relations)

            if elimination:
                out.write_elimination(entity_name, turn)

        out.write_sim_end(entities, world, relations, turn, early_end, sig_event_index)

--- Output files ---

Both files are written to the /root/runs/ directory:

    runs/sim_YYYYMMDD_HHMMSS_seed{n}.md
    runs/sim_YYYYMMDD_HHMMSS_seed{n}.jsonl

--- Markdown structure ---

  # Run header (metadata table, entity table, world resources table)
  ## Turn Log
  ### Turn N                     ← compact: action table + relations line
  ### Turn N ⚔️ war_declared     ← significant: expanded + narration blockquote
  ## Final State
    - Territory table
    - Resource totals table
    - Significant events index table
    - LLM Usage table (omitted when --no-narrate)

--- JSONL structure ---

One JSON object per line, written only for significant events:

  {
    "turn":              int,
    "event_type":        str,
    "actor":             str,
    "target_entity":     str | null,
    "target_node":       str | null,
    "description":       str,
    "metadata":          dict,
    "narration":         str | null,
    "narrator_style":    str | null,
    "prompt_tokens":     int,
    "output_tokens":     int,
    "narration_error":   str | null,
    "world_snapshot":    [ { name, owner, is_capital, resources } ],
    "entity_snapshot":   [ { name, alive, resources, node_count } ],
    "relations_snapshot":{ entity_a: { entity_b: score } }
  }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from .actions import ActionResult
from .entity import Entity, RelationshipMatrix
from .events import SimEvent
from .narrator import NarrationResult
from .world import World


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent.parent / "runs"

# Emoji labels for significant event types — used in markdown turn headers.
EVENT_EMOJI: dict[str, str] = {
    "elimination":         "☠️",
    "war_declared":        "⚔️",
    "alliance_formed":     "🤝",
    "capital_lost":        "🏚️",
    "near_death_survived": "💀",
    "unexpected_conquest": "⚡",
}


# ---------------------------------------------------------------------------
# SimOutput
# ---------------------------------------------------------------------------

class SimOutput:
    """
    Context-managed output writer for a single simulation run.

    Opens .md and .jsonl file handles on entry, flushes and closes them
    on exit. All write_* methods append to those handles incrementally —
    nothing is buffered in memory beyond the current turn's action rows.

    Args:
        seed: The simulation random seed (used in filenames).
        meta: The metadata dict from the world JSON (produced by worldgen.py).
              Used in the run header. Pass {} if unavailable.
    """

    def __init__(self, seed: int, meta: dict[str, Any]) -> None:
        self._seed = seed
        self._meta = meta
        self._md_path:   Optional[Path] = None
        self._jsonl_path: Optional[Path] = None
        self._md_file   = None
        self._jsonl_file = None

        # Accumulated index of significant events for the final summary table.
        self._sig_event_index: list[dict[str, Any]] = []

        # Per-turn action rows — flushed to markdown at end of each turn.
        self._turn_action_rows: list[tuple[str, ActionResult]] = []

        # Whether any significant event fired this turn — determines header style.
        self._turn_has_sig_event: bool = False

        # Pending turn header state — written lazily so we can decide the style.
        self._pending_turn: Optional[int] = None
        self._pending_total: Optional[int] = None

        # Cumulative LLM token counters — accumulated across all narration calls.
        self._total_prompt_tokens: int = 0
        self._total_output_tokens: int = 0
        self._narration_count: int = 0

    # -----------------------------------------------------------------------
    # Context manager
    # -----------------------------------------------------------------------

    def __enter__(self) -> "SimOutput":
        RUNS_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"sim_{timestamp}_seed{self._seed}"

        self._md_path    = RUNS_DIR / f"{stem}.md"
        self._jsonl_path = RUNS_DIR / f"{stem}.jsonl"

        self._md_file    = open(self._md_path,    "w", encoding="utf-8")
        self._jsonl_file = open(self._jsonl_path, "w", encoding="utf-8")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._md_file:
            self._md_file.flush()
            self._md_file.close()
        if self._jsonl_file:
            self._jsonl_file.flush()
            self._jsonl_file.close()
        return False  # Do not suppress exceptions.

    # -----------------------------------------------------------------------
    # Public path properties
    # -----------------------------------------------------------------------

    @property
    def md_path(self) -> Optional[Path]:
        """Path to the markdown chronicle file. None before __enter__."""
        return self._md_path

    @property
    def jsonl_path(self) -> Optional[Path]:
        """Path to the JSONL event log file. None before __enter__."""
        return self._jsonl_path

    # -----------------------------------------------------------------------
    # Public token usage properties
    # -----------------------------------------------------------------------

    @property
    def total_prompt_tokens(self) -> int:
        """Cumulative prompt tokens consumed across all narration calls this run."""
        return self._total_prompt_tokens

    @property
    def total_output_tokens(self) -> int:
        """Cumulative output tokens consumed across all narration calls this run."""
        return self._total_output_tokens

    @property
    def narration_count(self) -> int:
        """Number of narration calls made this run (including errored ones)."""
        return self._narration_count

    # -----------------------------------------------------------------------
    # Public write interface
    # -----------------------------------------------------------------------

    def write_run_header(
        self,
        entities: list[Entity],
        world: World,
        relations: RelationshipMatrix,
        meta: dict[str, Any],
    ) -> None:
        """
        Write the top-of-file run header: metadata table, entity table,
        and starting world resource table.

        Call once, before the turn loop begins.
        """
        from .config import CFG

        seed_display = str(self._seed) if self._seed != -1 else "random"
        started_at   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        narrator_style = meta.get("narrator_style", CFG.narrator.narrator_style)
        world_theme    = meta.get("theme", "—")

        lines: list[str] = []

        # --- Title ---
        lines.append("# history_engine — Simulation Run\n")

        # --- Metadata table ---
        lines.append("| Field          | Value                         |")
        lines.append("|----------------|-------------------------------|")
        lines.append(f"| Seed           | `{seed_display}`              |")
        lines.append(f"| Turns          | `{CFG.simulation.turn_count}` |")
        lines.append(f"| Entities       | `{CFG.simulation.entity_count}` |")
        lines.append(f"| Nodes          | `{CFG.world.node_count}`      |")
        lines.append(f"| Edge Density   | `{CFG.world.edge_density}`    |")
        lines.append(f"| Theme          | `{world_theme}`               |")
        lines.append(f"| Narrator Style | `{narrator_style}`            |")
        lines.append(f"| Model          | `{CFG.narrator.model}`        |")
        lines.append(f"| Started        | `{started_at}`                |")
        lines.append("")

        lines.append("---\n")

        # --- Entity table ---
        lines.append("## Entities\n")
        lines.append("| Name | Archetype | Aggression | Expansionism | Greed | Paranoia | Capital |")
        lines.append("|------|-----------|------------|--------------|-------|----------|---------|")

        for entity in entities:
            p = entity.personality
            archetype = self._meta.get("archetypes", {}).get(entity.name, "—")
            capital_node = next(
                (n.name for n in world.all_nodes() if n.owner == entity.name and n.is_capital),
                "—",
            )
            lines.append(
                f"| {entity.name} "
                f"| {archetype} "
                f"| {p.get('aggression', 0.0):.2f} "
                f"| {p.get('expansionism', 0.0):.2f} "
                f"| {p.get('greed', 0.0):.2f} "
                f"| {p.get('paranoia', 0.0):.2f} "
                f"| {capital_node} |"
            )

        lines.append("")
        lines.append("---\n")

        # --- World map starting resources ---
        lines.append("## World Map — Starting Resources\n")

        # Derive resource types from the world nodes — the union of all
        # resource keys that actually exist in this generated world.
        resource_types = sorted({
            r for node in world.all_nodes() for r in node.resources
        })
        header_cols = " | ".join(r.capitalize() for r in resource_types)
        sep_cols    = " | ".join("---" for _ in resource_types)

        lines.append(f"| Node | {header_cols} | Controller |")
        lines.append(f"|------|{sep_cols}|------------|")

        for node in sorted(world.all_nodes(), key=lambda n: n.name):
            resource_vals = " | ".join(
                str(node.resources.get(r, 0)) for r in resource_types
            )
            controller = node.owner if node.owner else "—"
            lines.append(f"| {node.name} | {resource_vals} | {controller} |")

        lines.append("")
        lines.append("---\n")

        # --- Turn log section heading ---
        lines.append("## Turn Log\n")
        lines.append("---\n")

        self._md_writelines(lines)

    def write_turn_header(self, turn: int, total_turns: int) -> None:
        """
        Stage a pending turn header.

        The header is not written immediately — we need to know whether this
        turn contains a significant event before choosing compact vs expanded
        style. The header is flushed by _flush_pending_turn_header(), called
        from write_action() rows and write_significant_event().

        Call at the start of each turn, before any write_action() calls.
        """
        # Flush any previous pending turn that was never explicitly closed.
        self._end_turn()

        self._pending_turn        = turn
        self._pending_total       = total_turns
        self._turn_has_sig_event  = False
        self._turn_action_rows    = []

    def write_action(self, actor_name: str, result: ActionResult) -> None:
        """
        Buffer a single resolved action for the current turn.

        Action rows are accumulated and written together at end-of-turn
        so they can be formatted as a single markdown table.
        """
        self._turn_action_rows.append((actor_name, result))

    def write_significant_event(
        self,
        event: SimEvent,
        narration: Optional[NarrationResult],
        world: World,
        entities: list[Entity],
        relations: RelationshipMatrix,
    ) -> None:
        """
        Write a significant event block into the markdown chronicle and
        append one record to the JSONL log.

        This flushes any buffered action rows for the current turn first,
        then writes the event callout and narration blockquote.

        Accumulates prompt and output token counts from the NarrationResult
        for the run-level LLM usage summary written at sim end.

        Call once per SimEvent, after narration is available.

        Args:
            event:      The SimEvent from events.py.
            narration:  The NarrationResult from narrator.py, or None if
                        narration was skipped (--no-narrate).
            world:      Current world state (for snapshot).
            entities:   Current entity list (for snapshot).
            relations:  Current relationship matrix (for snapshot).
        """
        self._turn_has_sig_event = True
        self._flush_pending_turn_header(significant=True)
        self._flush_action_table()

        # --- Accumulate token usage ---
        if narration is not None:
            self._narration_count    += 1
            self._total_prompt_tokens += narration.prompt_tokens
            self._total_output_tokens += narration.output_tokens

        # Extract narration text for markdown (only on clean success).
        narration_text: Optional[str] = None
        if narration and not narration.error:
            narration_text = narration.narration

        # --- Markdown: relations line ---
        self._write_relations_line(relations, entities)

        # --- Markdown: narration blockquote ---
        if narration_text:
            self._md_write("")
            for para in narration_text.strip().split("\n\n"):
                self._md_write(f"> {para.strip()}")
            self._md_write("")

        self._md_write("---\n")

        # --- Accumulate for final summary index ---
        self._sig_event_index.append({
            "turn":         event.turn,
            "event_type":   event.event_type,
            "description":  event.description,
        })

        # --- JSONL record ---
        record = self._build_jsonl_record(event, narration, world, entities, relations)
        self._jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._jsonl_file.flush()

    def write_elimination(self, entity_name: str, turn: int) -> None:
        """
        Write an inline elimination notice into the markdown chronicle.

        Call when an entity is eliminated, after write_significant_event()
        for the elimination event.
        """
        self._md_write(f"\n**{entity_name} has been eliminated on turn {turn}.**\n")

    def write_turn_end(
        self,
        entities: list[Entity],
        relations: RelationshipMatrix,
    ) -> None:
        """
        Close out the current turn in the markdown chronicle.

        For routine turns (no significant event fired), this flushes the
        pending turn header and action table, writes the relations line,
        and appends a section divider.

        For significant turns, write_significant_event() already flushed
        the header, table, and relations line — so this is a no-op if
        _pending_turn is already cleared.

        Call once per turn, at the bottom of the turn loop, after all
        actors have resolved their actions.

        Args:
            entities:  Current entity list (for the relations line).
            relations: Current relationship matrix.
        """
        if self._pending_turn is None:
            # Significant turn — already fully written. Nothing to do.
            return

        # Routine turn — flush header and action table, then write relations.
        self._flush_pending_turn_header(significant=False)
        self._flush_action_table()
        self._write_relations_line(relations, entities)
        self._md_write("\n---\n")

    def write_sim_end(
        self,
        entities: list[Entity],
        world: World,
        relations: RelationshipMatrix,
        final_turn: int,
        early_end: bool,
    ) -> None:
        """
        Write the final state section at the end of the simulation.

        Includes: territory table, resource totals table, significant
        events index table, LLM usage table (when narration was active),
        and a closing footer line.

        Call once, after the turn loop completes.
        """
        # Flush the last turn's buffered actions if it had no significant event.
        self._end_turn()

        lines: list[str] = []

        lines.append("---\n")
        lines.append(f"## Final State — Turn {final_turn}\n")

        if early_end:
            lines.append(
                "> Early termination — only one entity remains.\n"
            )

        # --- Territory ---
        lines.append("### Territory\n")
        lines.append("| Node | Controller | Capital |")
        lines.append("|------|------------|---------|")

        for node in sorted(world.all_nodes(), key=lambda n: n.name):
            controller = node.owner if node.owner else "—"
            capital    = "✓" if node.is_capital else ""
            lines.append(f"| {node.name} | {controller} | {capital} |")

        lines.append("")

        # --- Resource totals ---
        lines.append("### Resource Totals (accumulated)\n")

        # Derive resource types from the union of all entity resource keys.
        resource_types = sorted({
            r for entity in entities for r in entity.resources
        })
        header_cols = " | ".join(r.capitalize() for r in resource_types)
        sep_cols    = " | ".join("---" for _ in resource_types)

        lines.append(f"| Entity | {header_cols} | Status |")
        lines.append(f"|--------|{sep_cols}|--------|")

        for entity in entities:
            resource_vals = " | ".join(
                str(entity.resources.get(r, 0)) for r in resource_types
            )
            status = "Alive" if entity.alive else "Eliminated"
            lines.append(f"| {entity.name} | {resource_vals} | {status} |")

        lines.append("")

        # --- Relations (surviving entities only) ---
        alive = [e for e in entities if e.alive]
        if len(alive) >= 2:
            lines.append("### Final Relations\n")
            lines.append("| Pair | Relation |")
            lines.append("|------|----------|")

            seen: set[frozenset[str]] = set()
            for a in alive:
                for b in alive:
                    pair = frozenset({a.name, b.name})
                    if a.name == b.name or pair in seen:
                        continue
                    seen.add(pair)
                    score = relations.get(a.name, b.name)
                    lines.append(f"| {a.name} / {b.name} | `{score:+.2f}` |")

            lines.append("")

        # --- Significant events index ---
        if self._sig_event_index:
            lines.append("### Significant Events\n")
            lines.append("| Turn | Type | Summary |")
            lines.append("|------|------|---------|")

            for entry in self._sig_event_index:
                emoji = EVENT_EMOJI.get(entry["event_type"], "•")
                lines.append(
                    f"| {entry['turn']} "
                    f"| {emoji} `{entry['event_type']}` "
                    f"| {entry['description']} |"
                )

            lines.append("")

        # --- LLM usage summary (omitted entirely when no narrations ran) ---
        if self._narration_count > 0:
            total_tokens = self._total_prompt_tokens + self._total_output_tokens
            lines.append("### LLM Usage\n")
            lines.append("| Narrations | Prompt Tokens | Output Tokens | Total Tokens |")
            lines.append("|------------|---------------|---------------|--------------|")
            lines.append(
                f"| {self._narration_count} "
                f"| {self._total_prompt_tokens:,} "
                f"| {self._total_output_tokens:,} "
                f"| {total_tokens:,} |"
            )
            lines.append("")

        # --- Footer ---
        total_sig = len(self._sig_event_index)
        lines.append("---\n")
        lines.append(
            f"*Simulation complete. "
            f"{total_sig} significant event{'s' if total_sig != 1 else ''} "
            f"narrated across {final_turn} turn{'s' if final_turn != 1 else ''}.*"
        )
        lines.append(f"*Chronicle: `{self._md_path}`*")
        lines.append(f"*Events log: `{self._jsonl_path}`*")

        self._md_writelines(lines)

    # -----------------------------------------------------------------------
    # Internal helpers — markdown
    # -----------------------------------------------------------------------

    def _flush_pending_turn_header(self, significant: bool = False) -> None:
        """
        Write the staged turn header to the markdown file.

        If significant=True, the header includes the event emoji and type label.
        For routine turns, a plain turn number header is written.

        This is called lazily — the first write_action() or
        write_significant_event() call flushes the pending header.
        """
        if self._pending_turn is None:
            return

        turn  = self._pending_turn
        total = self._pending_total

        if significant:
            # Significant header — emoji and type filled in later by caller.
            # Write a plain significant marker; event type is appended in
            # write_significant_event after we know which event fired.
            self._md_write(f"### Turn {turn} / {total}\n")
        else:
            self._md_write(f"### Turn {turn} / {total}\n")

        # Mark as flushed.
        self._pending_turn  = None
        self._pending_total = None

    def _flush_action_table(self) -> None:
        """
        Write all buffered action rows as a markdown table.

        Called at end-of-turn (either by _end_turn for routine turns,
        or by write_significant_event for significant turns).
        """
        if not self._turn_action_rows:
            return

        lines = [
            "| Entity | Action | Target | Outcome |",
            "|--------|--------|--------|---------|",
        ]

        for actor_name, result in self._turn_action_rows:
            action  = result.action.upper()
            target  = result.target_node or result.target_entity or "—"
            outcome = result.description
            lines.append(f"| {actor_name} | {action} | {target} | {outcome} |")

        lines.append("")
        self._md_writelines(lines)
        self._turn_action_rows = []

    def _write_relations_line(
        self,
        relations: RelationshipMatrix,
        entities: list[Entity],
    ) -> None:
        """
        Write a compact inline relations summary for the current turn.

        Only alive entity pairs are included. Format:
          **Relations:** A / B `+0.45` · A / C `-0.30` · B / C `0.00`
        """
        alive = [e for e in entities if e.alive]
        if len(alive) < 2:
            return

        seen: set[frozenset[str]] = set()
        parts: list[str] = []

        for a in alive:
            for b in alive:
                pair = frozenset({a.name, b.name})
                if a.name == b.name or pair in seen:
                    continue
                seen.add(pair)
                score = relations.get(a.name, b.name)
                parts.append(f"{a.name} / {b.name} `{score:+.2f}`")

        if parts:
            self._md_write("**Relations:** " + " · ".join(parts) + "\n")

    def _end_turn(self) -> None:
        """
        Flush any buffered state for a routine (non-significant) turn.

        Called at the start of the next write_turn_header() and at
        write_sim_end(), to ensure no turn's data is silently dropped.
        """
        if self._pending_turn is not None:
            # Header was staged but never flushed — routine turn with no events.
            self._flush_pending_turn_header(significant=False)
            self._flush_action_table()

            # Write a compact relations line if we have entity context.
            # We don't have entities here — relations line is skipped for
            # turns that ended before any write_significant_event() was called.
            # main.py should call write_turn_end() explicitly for relations.
            self._md_write("---\n")

    def _md_write(self, line: str) -> None:
        """Write a single line to the markdown file."""
        self._md_file.write(line + "\n")

    def _md_writelines(self, lines: list[str]) -> None:
        """Write multiple lines to the markdown file."""
        for line in lines:
            self._md_file.write(line + "\n")

    # -----------------------------------------------------------------------
    # Internal helpers — JSONL
    # -----------------------------------------------------------------------

    def _build_jsonl_record(
        self,
        event: SimEvent,
        narration: Optional[NarrationResult],
        world: World,
        entities: list[Entity],
        relations: RelationshipMatrix,
    ) -> dict[str, Any]:
        """
        Build a flat, self-contained JSONL record for a significant event.

        Includes the full NarrationResult metadata (style, token counts, error)
        alongside the narration text — making each record a complete audit trail
        of the LLM call that produced it.

        Also includes a full world snapshot, entity snapshot, and relation matrix
        at the moment the event fired — enough for report.py to reconstruct
        the world state at each significant turn without replaying the sim.

        Args:
            event:     The SimEvent that fired.
            narration: The NarrationResult from narrator.py, or None if narration
                       was skipped (--no-narrate). When None, all LLM fields are
                       null/zero in the record.
            world:     Current world state.
            entities:  Current entity list.
            relations: Current relationship matrix.
        """
        world_snapshot = [
            {
                "name":       node.name,
                "owner":      node.owner,
                "is_capital": node.is_capital,
                "resources":  dict(node.resources),
                "features":   list(node.features),
            }
            for node in sorted(world.all_nodes(), key=lambda n: n.name)
        ]

        entity_snapshot = [
            {
                "name":       entity.name,
                "alive":      entity.alive,
                "resources":  dict(entity.resources),
                "node_count": len(world.get_nodes_by_owner(entity.name)),
            }
            for entity in entities
        ]

        # Extract all LLM metadata from the NarrationResult, if present.
        narration_text:  Optional[str] = None
        narrator_style:  Optional[str] = None
        prompt_tokens:   int = 0
        output_tokens:   int = 0
        narration_error: Optional[str] = None

        if narration is not None:
            narrator_style  = narration.narrator_style or None
            prompt_tokens   = narration.prompt_tokens
            output_tokens   = narration.output_tokens
            narration_error = narration.error
            # Only include narration text on clean success — errors fall back
            # to the mechanical description, which is already in "description".
            if not narration.error:
                narration_text = narration.narration

        return {
            "turn":               event.turn,
            "event_type":         event.event_type,
            "actor":              event.actor,
            "target_entity":      event.target_entity,
            "target_node":        event.target_node,
            "description":        event.description,
            "metadata":           event.metadata,
            "narration":          narration_text,
            "narrator_style":     narrator_style,
            "prompt_tokens":      prompt_tokens,
            "output_tokens":      output_tokens,
            "narration_error":    narration_error,
            "world_snapshot":     world_snapshot,
            "entity_snapshot":    entity_snapshot,
            "relations_snapshot": relations.to_dict(),
            "edges_snapshot":     [                          # ← ADD THIS
                {"node_a": a, "node_b": b}
                for a, b, _ in world.all_edges()
            ],
        }