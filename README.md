# history_engine

An autonomous Python world simulation where AI-driven entities compete across a graph-based world map, with an LLM layer generating emergent narrative storytelling. No player input. Entities act, the world changes, the LLM narrates. Stories are _earned by the mechanics_, not invented from nothing.

---

## Core Design Philosophy

- **Graph over grid.** The world is a graph of named, meaningful nodes. Every location carries narrative weight. Geography is abstracted — think hand-drawn campaign map, not pixel grid.
- **Layered AI.** Three tiers of decision-making:
    - **Rules (Physics Layer):** What is legal. Deterministic, fast, cheap.
    - **Utility AI (Strategy Layer):** What to do. Personality vectors score available actions via softmax sampling.
    - **LLM (Narrative Layer):** Why it matters. Fires only on significant events, not every tick.
- **Exploit is the engine.** Resource asymmetry creates pressure. Pressure creates story.
- **History is first-class.** Nodes and entities accumulate event logs. The LLM always has context about _what happened here before._
- **Extensibility by design.** Themes, narrator styles, and archetypes are data-driven TOML files. No code changes required to add new ones.

---

## Project Structure

```
history_engine/
├── engine/
│   ├── main.py           # Simulation loop
│   ├── world.py          # Graph, nodes, edges
│   ├── entity.py         # Entity state, personality, relationships
│   ├── actions.py        # Legal action definitions + resolution
│   ├── utility.py        # Action scoring per entity (softmax sampling)
│   ├── events.py         # Event detection, significance filtering, history logging
│   ├── narrator.py       # LLM prompt construction + Anthropic API calls
│   ├── output.py         # JSONL enrichment, terminal output (Rich)
│   └── config.py         # Config loader and typed schema
├── config/
│   ├── config.toml       # Tunable simulation parameters
│   └── presets.toml      # Narrator style presets
├── themes/
│   ├── fantasy.toml      # Fantasy theme (entities, nodes, resources)
│   ├── scifi.toml        # Sci-fi theme
│   └── hunter.toml       # Hunter theme
├── runs/                 # Output destination for simulation reports
├── report.py             # Post-run HTML report generator (D3.js)
├── world.json            # Last simulation world state snapshot
└── pyrightconfig.json    # Pylance type checking configuration
```

---

## Entity Personality Vector

Each entity has a personality that shapes utility scoring:

```python
{
  "aggression":   float,  # Weight toward Exterminate actions
  "expansionism": float,  # Weight toward Explore + Expand actions
  "greed":        float,  # Weight toward Exploit actions
  "paranoia":     float,  # Pulls back from aggression when outnumbered
}
```

Entities with identical stats still diverge through softmax-sampled action selection — variance produces surprise, surprise produces story.

---

## Relationship Matrix

Entities track numerical relations with each other:

- Range: `-1.0` (hostile) to `+1.0` (allied)
- Updated by: attacks, treaties, proximity pressure, resource competition
- Fed into LLM prompts as context for narration

---

## Theme System

Themes are self-contained TOML files in `themes/`. Each theme defines its own entity archetypes, node names, resource types, and flavor text. Adding a new theme requires no code changes — drop a `.toml` into `themes/` and reference it in `config.toml`.

Shared narrator style presets live in `config/presets.toml`:

|Style|Character|
|---|---|
|`chronicle`|Dry historical record|
|`mythic`|Epic, legendary register|
|`clinical`|Detached, analytical|
|`noir`|Cynical, atmospheric|
|`wikipedia`|Encyclopedic neutrality|
|`sibylline`|Prophetic, oracular|
|`elegy`|Mournful, reflective|
|`gossip`|Breathless, irreverent|
|`fable`|Moral, allegorical|
|`scripture`|Sacred, authoritative|

---

## Event Significance

Not every turn event triggers a narration. Significant events include:

|Event Type|Why It Matters|
|---|---|
|`elimination`|An entity is removed from the world|
|`war_declared`|Formal shift in relations|
|`alliance_formed`|Unexpected cooperation|
|`capital_lost`|Existential threat to an entity|
|`near_death_survived`|Dramatic reversal|
|`unexpected_conquest`|Low-probability outcome|

---

## LLM Narration Pipeline

- Narration is **event-gated** — LLM calls never fire on every tick, only on significant events
- Prompts are constructed with full entity history, node history, relationship matrix, and narrator style
- Token usage is tracked across the simulation run
- Output is written to `.jsonl` for post-processing
- **Claude Haiku** is used for narration at scale (cost-efficient for high event volume)
- A `--prompt-only` dry run flag exists to validate prompts locally before any live API spend

---

## Output

Each simulation run produces:

- **Terminal output** — Rich-formatted live event feed
- **JSONL log** — Structured event + narration records
- **HTML report** — D3.js force-directed graph of the world map, event sidebar, and narration panel; generated post-run by `report.py`

---

## Running the Simulation

```bash
# Standard run
python -m engine.main

# Dry run (validates prompts, no API calls)
python -m engine.main --prompt-only

# Generate HTML report from last run
python report.py

# Run backend API server (프론트엔드 대시보드에 시뮬레이션 이벤트 및 스냅샷 데이터를 공급하는 Fast API 서버 구동)
uvicorn api.main:app --reload

# Run frontend development server (Vite 기반의 React 대시보드 웹 UI 애플리케이션을 개발 모드로 실행)
npm run dev

```

---

## Dependencies

|Package|Purpose|
|---|---|
|`networkx`|Graph structure and traversal|
|`anthropic`|LLM narration via API|
|`numpy`|Softmax sampling|
|`rich`|Readable terminal output|
|`dataclasses`|Clean event/entity models (stdlib)|
|`tomllib`|TOML config parsing (stdlib, Python 3.11+)|

---

## Expansion Roadmap

The simulation engine is the core. The planned expansion builds a full-stack demonstration system around it:

### Phase 1 — Current (Complete)

Autonomous Python simulation with LLM narration, theme system, and HTML report output. Fully runnable as a local CLI tool.

### Phase 2 — Backend & Database

- Expose the simulation engine as a **FastAPI** service
- Persist simulation runs, entity histories, and narration records to a **PostgreSQL** database
- Enable replay, cross-run comparison, and narrative corpus queries

### Phase 3 — React Web UI

- **React** frontend for launching simulations, browsing run history, and reading generated narratives
- Communicates with the FastAPI backend over a documented REST API
- D3.js world graph, event feed, and narration panel — consistent with the existing HTML report output
- Built and served as static files via FastAPI; no separate hosting required

### Phase 4 — Demo Infrastructure

- **Docker Compose** to orchestrate the Python backend, React frontend, and database as a single-command local stack
- **Tailscale** for private, zero-cost networking between the demo machine and any connected browser — no public hosting required
- Designed for demonstration without ongoing cloud infrastructure costs

### Future Experiments

- **Strategy variant rulesets** — different 4X balances (e.g. no Exterminate, or Exterminate-only)
- **Personality evolution** — entities shift personality traits based on simulation outcomes
- **Multi-sim batch runs** — run N simulations in parallel and compare narrative corpora
