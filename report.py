"""
report.py — Post-processing report generator for history_engine.

Reads a .jsonl event log from a simulation run and generates a single
self-contained interactive HTML file with a D3.js force-directed world
graph, event timeline sidebar, narration panel, and entity/relation tables.

Usage:
    python report.py --input runs/sim_20260413_091402_seed48291.jsonl
    python report.py                          # auto-selects most recent .jsonl in runs/

Output:
    runs/<stem>.html  — fully self-contained, no server required
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent / "runs"

EVENT_EMOJI = {
    "elimination":          "☠️",
    "war_declared":         "⚔️",
    "alliance_formed":      "🤝",
    "capital_lost":         "🏚️",
    "near_death_survived":  "💀",
    "unexpected_conquest":  "⚡",
}

# Color palette for entity ownership (up to 20 entities).
# Chosen for visibility on a dark background.
ENTITY_PALETTE = [
    "#e05c5c",  # red
    "#5c9ee0",  # blue
    "#5ce07a",  # green
    "#e0c45c",  # gold
    "#c45ce0",  # purple
    "#5ce0d4",  # teal
    "#e07a5c",  # orange
    "#a3e05c",  # lime
    "#e05cbb",  # pink
    "#5c7ae0",  # indigo
    "#e0d45c",  # yellow
    "#5ce0a3",  # mint
    "#e05c7a",  # rose
    "#5cbbe0",  # sky
    "#b4e05c",  # chartreuse
    "#7a5ce0",  # violet
    "#5ce0c4",  # aqua
    "#e0a35c",  # amber
    "#5c5ce0",  # cobalt
    "#d4e05c",  # olive
]

# Sidebar border colors per event type
EVENT_TYPE_COLORS = {
    "elimination":          "#e05c5c",
    "war_declared":         "#e0935c",
    "alliance_formed":      "#5ce07a",
    "capital_lost":         "#e0c45c",
    "near_death_survived":  "#c45ce0",
    "unexpected_conquest":  "#5c9ee0",
}

NEUTRAL_NODE_COLOR = "#555566"
D3_CDN = "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML report from a history_engine .jsonl event log."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to the .jsonl event log. Defaults to the most recently modified file in runs/.",
    )
    return parser.parse_args()


def resolve_input(path: Path | None) -> Path:
    """Return the input path, auto-selecting if none given."""
    if path is not None:
        if not path.exists():
            sys.exit(f"[report.py] Input file not found: {path}")
        return path

    if not RUNS_DIR.exists():
        sys.exit(f"[report.py] runs/ directory not found at {RUNS_DIR}")

    candidates = sorted(
        RUNS_DIR.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        sys.exit(f"[report.py] No .jsonl files found in {RUNS_DIR}")

    return candidates[0]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_records(path: Path) -> list[dict]:
    """Parse all JSONL records from the input file."""
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[report.py] Warning: skipping malformed line {line_no}: {exc}", file=sys.stderr)
    return records


def collect_entities(records: list[dict]) -> list[str]:
    """
    Collect all unique entity names that appear anywhere across all snapshots.
    Returns them in a stable order (sorted) so palette assignment is deterministic.
    """
    names: set[str] = set()
    for rec in records:
        for node in rec.get("world_snapshot", []):
            if node.get("owner"):
                names.add(node["owner"])
        for ent in rec.get("entity_snapshot", []):
            names.add(ent["name"])
    return sorted(names)


def build_color_map(entity_names: list[str]) -> dict[str, str]:
    """Assign a consistent color to each entity name."""
    return {
        name: ENTITY_PALETTE[i % len(ENTITY_PALETTE)]
        for i, name in enumerate(entity_names)
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def build_html(records: list[dict], input_path: Path) -> str:
    """Assemble and return the full HTML report as a string."""

    entity_names = collect_entities(records)
    color_map = build_color_map(entity_names)

    # Metadata derived from records
    filename = input_path.name
    first_turn = records[0]["turn"] if records else 0
    last_turn = records[-1]["turn"] if records else 0
    event_count = len(records)

    # Embed data as JSON for JS consumption
    records_json = json.dumps(records, ensure_ascii=False)
    color_map_json = json.dumps(color_map, ensure_ascii=False)
    emoji_map_json = json.dumps(EVENT_EMOJI, ensure_ascii=False)
    event_colors_json = json.dumps(EVENT_TYPE_COLORS, ensure_ascii=False)
    neutral_color_json = json.dumps(NEUTRAL_NODE_COLOR, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>History Engine Report — {filename}</title>
<script src="{D3_CDN}"></script>
<style>
  /* ── Reset & Base ─────────────────────────────────────────────── */
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:          #0f0f14;
    --surface:     #17171f;
    --surface2:    #1e1e28;
    --surface3:    #252530;
    --border:      #2a2a38;
    --text:        #d4d4e0;
    --text-dim:    #7a7a90;
    --text-bright: #eeeef8;
    --accent:      #5c9ee0;
    --header-h:    52px;
    --sidebar-w:   320px;
    --font:        'Segoe UI', system-ui, sans-serif;
    --mono:        'Cascadia Code', 'Fira Code', 'Courier New', monospace;
    --radius:      6px;
    --transition:  200ms ease;
  }}

  html, body {{
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    line-height: 1.55;
    overflow: hidden;
  }}

  /* ── Layout ───────────────────────────────────────────────────── */
  #app {{
    display: grid;
    grid-template-rows: var(--header-h) 1fr;
    grid-template-columns: var(--sidebar-w) 1fr;
    grid-template-areas:
      "header header"
      "sidebar main";
    height: 100vh;
    width: 100vw;
  }}

  /* ── Header ───────────────────────────────────────────────────── */
  #header {{
    grid-area: header;
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 0 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    overflow: hidden;
  }}

  #header .run-title {{
    font-size: 15px;
    font-weight: 600;
    color: var(--text-bright);
    flex-shrink: 0;
  }}

  #header .run-meta {{
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    gap: 18px;
  }}

  #header .run-meta span b {{
    color: var(--text);
  }}

  #header .edge-note {{
    margin-left: auto;
    font-size: 11px;
    color: var(--text-dim);
    font-style: italic;
    flex-shrink: 0;
  }}

  /* ── Sidebar ──────────────────────────────────────────────────── */
  #sidebar {{
    grid-area: sidebar;
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border-right: 1px solid var(--border);
    overflow: hidden;
  }}

  #sidebar-header {{
    padding: 10px 14px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }}

  #event-list {{
    overflow-y: auto;
    flex: 1;
    padding: 6px 0;
  }}

  #event-list::-webkit-scrollbar {{ width: 5px; }}
  #event-list::-webkit-scrollbar-track {{ background: transparent; }}
  #event-list::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

  .event-card {{
    padding: 10px 14px 10px 16px;
    cursor: pointer;
    border-left: 3px solid transparent;
    border-bottom: 1px solid var(--border);
    transition: background var(--transition);
    position: relative;
  }}

  .event-card:hover {{ background: var(--surface2); }}
  .event-card.active {{ background: var(--surface3); }}

  .event-card .card-turn {{
    font-size: 10px;
    color: var(--text-dim);
    font-family: var(--mono);
    margin-bottom: 2px;
  }}

  .event-card .card-type {{
    font-size: 12px;
    font-weight: 600;
    color: var(--text-bright);
    display: flex;
    align-items: center;
    gap: 5px;
    margin-bottom: 3px;
  }}

  .event-card .card-actor {{
    font-size: 12px;
    color: var(--accent);
    font-weight: 500;
    margin-bottom: 3px;
  }}

  .event-card .card-desc {{
    font-size: 11px;
    color: var(--text-dim);
    line-height: 1.4;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}

  /* ── Main panel ───────────────────────────────────────────────── */
  #main {{
    grid-area: main;
    display: grid;
    grid-template-rows: 1fr 280px;
    overflow: hidden;
  }}

  /* ── Graph panel ──────────────────────────────────────────────── */
  #graph-panel {{
    position: relative;
    background: var(--bg);
    overflow: hidden;
  }}

  #graph-panel svg {{
    width: 100%;
    height: 100%;
    display: block;
  }}

  #graph-empty {{
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-dim);
    font-size: 13px;
    pointer-events: none;
  }}

  /* Node labels */
  .node-label {{
    font-family: var(--font);
    font-size: 11px;
    fill: var(--text);
    pointer-events: none;
    text-anchor: middle;
    dominant-baseline: central;
  }}

  .node-label-bg {{
    fill: rgba(15,15,20,0.75);
    rx: 3;
  }}

  /* Tooltip */
  #tooltip {{
    position: absolute;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 8px 12px;
    font-size: 12px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 150ms;
    max-width: 240px;
    z-index: 10;
    line-height: 1.6;
  }}

  #tooltip.visible {{ opacity: 1; }}
  #tooltip .tt-name {{ font-weight: 600; color: var(--text-bright); margin-bottom: 4px; }}
  #tooltip .tt-row {{ color: var(--text-dim); }}
  #tooltip .tt-row b {{ color: var(--text); }}

  /* ── Info panel ───────────────────────────────────────────────── */
  #info-panel {{
    background: var(--surface);
    border-top: 1px solid var(--border);
    display: grid;
    grid-template-columns: 1fr 240px 220px;
    overflow: hidden;
  }}

  .info-section {{
    padding: 12px 16px;
    overflow-y: auto;
    border-right: 1px solid var(--border);
  }}

  .info-section:last-child {{ border-right: none; }}

  .info-section::-webkit-scrollbar {{ width: 4px; }}
  .info-section::-webkit-scrollbar-track {{ background: transparent; }}
  .info-section::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}

  .section-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--text-dim);
    margin-bottom: 8px;
    padding-bottom: 5px;
    border-bottom: 1px solid var(--border);
  }}

  /* Narration */
  #narration-text {{
    font-size: 13px;
    line-height: 1.7;
    color: var(--text);
    white-space: pre-wrap;
  }}

  #narration-empty {{
    color: var(--text-dim);
    font-style: italic;
    font-size: 13px;
  }}

  /* Entity table */
  .entity-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
  }}

  .entity-row:last-child {{ border-bottom: none; }}

  .entity-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  .entity-name {{
    font-size: 12px;
    font-weight: 500;
    color: var(--text-bright);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}

  .entity-name.dead {{
    color: var(--text-dim);
    text-decoration: line-through;
  }}

  .entity-res {{
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--mono);
    flex-shrink: 0;
  }}

  .entity-status {{
    font-size: 10px;
    flex-shrink: 0;
  }}

  /* Relations table */
  .relation-row {{
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 0;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
  }}

  .relation-row:last-child {{ border-bottom: none; }}

  .rel-pair {{
    flex: 1;
    color: var(--text);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}

  .rel-score {{
    font-family: var(--mono);
    font-size: 11px;
    width: 44px;
    text-align: right;
    flex-shrink: 0;
  }}

  .rel-bar-wrap {{
    width: 48px;
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
    flex-shrink: 0;
  }}

  .rel-bar {{
    height: 100%;
    border-radius: 3px;
    transition: width var(--transition), background var(--transition);
  }}

  .rel-label {{
    font-size: 10px;
    width: 44px;
    text-align: right;
    flex-shrink: 0;
  }}

  /* Graph edges */
  .graph-edge {{
    stroke: #3a3a50;
    stroke-width: 1.5px;
    stroke-linecap: round;
  }}

  /* Legend */
  #legend {{
    position: absolute;
    bottom: 10px;
    right: 12px;
    background: rgba(23,23,31,0.85);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 8px 12px;
    font-size: 11px;
    color: var(--text-dim);
    pointer-events: none;
  }}

  #legend .leg-row {{
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 3px;
  }}

  #legend .leg-row:last-child {{ margin-bottom: 0; }}

  #legend .leg-swatch {{
    width: 14px;
    height: 14px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  #legend .leg-capital {{
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 0 0 2px var(--accent);
    flex-shrink: 0;
  }}

  #legend .leg-edge {{
    width: 14px;
    height: 2px;
    background: #3a3a50;
    border-radius: 1px;
    flex-shrink: 0;
  }}
</style>
</head>
<body>
<div id="app">

  <!-- Header -->
  <header id="header">
    <span class="run-title">⚔ History Engine Report</span>
    <div class="run-meta">
      <span><b id="meta-file">—</b></span>
      <span>Turns <b id="meta-turns">—</b></span>
      <span><b id="meta-events">—</b> significant events</span>
    </div>

  </header>

  <!-- Sidebar -->
  <aside id="sidebar">
    <div id="sidebar-header">Events</div>
    <div id="event-list"></div>
  </aside>

  <!-- Main -->
  <div id="main">

    <!-- Graph -->
    <div id="graph-panel">
      <svg id="graph-svg"></svg>
      <div id="graph-empty">Select an event to view the world snapshot.</div>
      <div id="tooltip"></div>
      <div id="legend">
        <div class="leg-row">
          <div class="leg-capital"></div>
          <span>Capital node</span>
        </div>
        <div class="leg-row">
          <div class="leg-swatch" style="background:#555566;"></div>
          <span>Neutral / unowned</span>
        </div>
        <div class="leg-row">
          <div class="leg-edge"></div>
          <span>Adjacency edge</span>
        </div>
      </div>
    </div>

    <!-- Info panel -->
    <div id="info-panel">
      <div class="info-section" id="narration-section">
        <div class="section-label">Narration</div>
        <div id="narration-empty">Select an event from the sidebar.</div>
        <div id="narration-text" style="display:none;"></div>
      </div>
      <div class="info-section" id="entities-section">
        <div class="section-label">Entities</div>
        <div id="entity-list"></div>
      </div>
      <div class="info-section" id="relations-section">
        <div class="section-label">Relations</div>
        <div id="relation-list"></div>
      </div>
    </div>

  </div>
</div>

<script>
// ═══════════════════════════════════════════════════════════════════════════
// Embedded simulation data
// ═══════════════════════════════════════════════════════════════════════════

const RECORDS      = {records_json};
const COLOR_MAP    = {color_map_json};
const EMOJI_MAP    = {emoji_map_json};
const EVENT_COLORS = {event_colors_json};
const NEUTRAL_COLOR = {neutral_color_json};
const META_FILE    = {json.dumps(filename)};
const META_FIRST   = {first_turn};
const META_LAST    = {last_turn};
const META_EVENTS  = {event_count};

// ═══════════════════════════════════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════════════════════════════════

function nodeColor(owner) {{
  if (!owner) return NEUTRAL_COLOR;
  return COLOR_MAP[owner] || NEUTRAL_COLOR;
}}

function totalResources(resObj) {{
  if (!resObj || typeof resObj !== 'object') return 0;
  return Object.values(resObj).reduce((a, b) => a + (b || 0), 0);
}}

function relLabel(score) {{
  if (score >= 0.5)  return 'Allied';
  if (score <= -0.3) return 'Hostile';
  return 'Neutral';
}}

function relColor(score) {{
  if (score >= 0.5)  return '#5ce07a';
  if (score <= -0.3) return '#e05c5c';
  return '#7a7a90';
}}

// Flatten relations_snapshot into an array of {{a, b, score}} pairs (no dupes)
function flattenRelations(relSnap) {{
  if (!relSnap || typeof relSnap !== 'object') return [];
  const seen = new Set();
  const out = [];
  for (const [a, bMap] of Object.entries(relSnap)) {{
    if (!bMap || typeof bMap !== 'object') continue;
    for (const [b, score] of Object.entries(bMap)) {{
      const key = [a, b].sort().join('||');
      if (!seen.has(key)) {{
        seen.add(key);
        out.push({{ a, b, score }});
      }}
    }}
  }}
  return out.sort((x, y) => x.score - y.score);
}}

// ═══════════════════════════════════════════════════════════════════════════
// Header population
// ═══════════════════════════════════════════════════════════════════════════

document.getElementById('meta-file').textContent   = META_FILE;
document.getElementById('meta-turns').textContent  = META_FIRST === META_LAST
  ? META_FIRST
  : META_FIRST + '–' + META_LAST;
document.getElementById('meta-events').textContent = META_EVENTS;

// ═══════════════════════════════════════════════════════════════════════════
// Sidebar — event cards
// ═══════════════════════════════════════════════════════════════════════════

const eventList = document.getElementById('event-list');

RECORDS.forEach((rec, idx) => {{
  const emoji     = EMOJI_MAP[rec.event_type] || '●';
  const typeLabel = rec.event_type.replace(/_/g, ' ');
  const borderColor = EVENT_COLORS[rec.event_type] || '#555566';

  const card = document.createElement('div');
  card.className = 'event-card';
  card.dataset.idx = idx;
  card.style.borderLeftColor = borderColor;

  card.innerHTML = `
    <div class="card-turn">Turn ${{rec.turn}}</div>
    <div class="card-type">${{emoji}} ${{typeLabel}}</div>
    <div class="card-actor">${{rec.actor || '—'}}</div>
    <div class="card-desc">${{escapeHtml(rec.description || '')}}</div>
  `;

  card.addEventListener('click', () => selectEvent(idx));
  eventList.appendChild(card);
}});

function escapeHtml(s) {{
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}}

// ═══════════════════════════════════════════════════════════════════════════
// D3 force graph
// ═══════════════════════════════════════════════════════════════════════════

const svg    = d3.select('#graph-svg');
const gGroup = svg.append('g').attr('class', 'graph-root');

// Zoom/pan
const zoom = d3.zoom()
  .scaleExtent([0.2, 5])
  .on('zoom', (event) => {{
    gGroup.attr('transform', event.transform);
  }});
svg.call(zoom);

// Layer groups — edges first so they render behind nodes
const edgeGroup  = gGroup.append('g').attr('class', 'edges');
const nodeGroup  = gGroup.append('g').attr('class', 'nodes');
const labelGroup = gGroup.append('g').attr('class', 'labels');

let simulation   = null;
let currentNodes = [];

// Canonical edge list — extracted once from the first record that has it.
// Topology is static across the sim; ownership changes, connections do not.
const EDGES = (() => {{
  for (const rec of RECORDS) {{
    if (rec.edges_snapshot && rec.edges_snapshot.length > 0) {{
      return rec.edges_snapshot; // [{{ node_a, node_b }}, ...]
    }}
  }}
  return [];
}})();

const tooltip = document.getElementById('tooltip');

function graphWidth()  {{ return document.getElementById('graph-panel').clientWidth;  }}
function graphHeight() {{ return document.getElementById('graph-panel').clientHeight; }}

function buildGraph(worldSnapshot) {{
  const graphEmpty = document.getElementById('graph-empty');

  if (!worldSnapshot || worldSnapshot.length === 0) {{
    graphEmpty.style.display = 'flex';
    edgeGroup.selectAll('*').remove();
    nodeGroup.selectAll('*').remove();
    labelGroup.selectAll('*').remove();
    if (simulation) {{ simulation.stop(); simulation = null; }}
    return;
  }}

  graphEmpty.style.display = 'none';

  const W = graphWidth();
  const H = graphHeight();

  // Compute radius scale: min 8, max 28, based on total resources
  const resourceValues = worldSnapshot.map(n => totalResources(n.resources));
  const maxRes = Math.max(...resourceValues, 1);
  const rScale = d3.scaleSqrt().domain([0, maxRes]).range([8, 28]);

  // Preserve existing positions for smooth transitions
  const prevPositions = {{}};
  currentNodes.forEach(n => {{
    prevPositions[n.name] = {{ x: n.x, y: n.y, vx: n.vx, vy: n.vy }};
  }});

  // Build node data
  const nodes = worldSnapshot.map(n => {{
    const prev = prevPositions[n.name];
    return {{
      id:         n.name,
      name:       n.name,
      owner:      n.owner || null,
      is_capital: n.is_capital || false,
      resources:  n.resources || {{}},
      features:   n.features || [],
      r:          rScale(totalResources(n.resources)),
      x:          prev ? prev.x : W / 2 + (Math.random() - 0.5) * 200,
      y:          prev ? prev.y : H / 2 + (Math.random() - 0.5) * 200,
      vx:         prev ? prev.vx : 0,
      vy:         prev ? prev.vy : 0,
    }};
  }});

  currentNodes = nodes;

  // Build a name → node object map for edge resolution
  const nodeById = {{}};
  nodes.forEach(n => {{ nodeById[n.id] = n; }});

  // Resolve edges to live node objects — skip any whose endpoints aren't in
  // this snapshot (shouldn't happen, but guards against malformed data)
  const links = EDGES
    .filter(e => nodeById[e.node_a] && nodeById[e.node_b])
    .map(e => ({{ source: nodeById[e.node_a], target: nodeById[e.node_b] }}));

  // Stop any existing simulation
  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(nodes)
    .force('link',    d3.forceLink(links).distance(1000).strength(0.4))
    .force('charge',  d3.forceManyBody().strength(-90))
    .force('center',  d3.forceCenter(W / 2, H / 2).strength(0.08))
    .force('collide', d3.forceCollide().radius(d => d.r + 50).strength(0.5))
    .alphaDecay(0.08)
    .on('tick', ticked);

  // ── Edges ────────────────────────────────────────────────────────────────

  const edgeLines = edgeGroup.selectAll('.graph-edge')
    .data(links);

  edgeLines.enter()
    .append('line')
    .attr('class', 'graph-edge')
    .attr('x1', d => d.source.x)
    .attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x)
    .attr('y2', d => d.target.y)
    .attr('opacity', 0)
    .transition().duration(300)
    .attr('opacity', 1);

  edgeLines
    .attr('x1', d => d.source.x)
    .attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x)
    .attr('y2', d => d.target.y);

  edgeLines.exit()
    .transition().duration(200)
    .attr('opacity', 0)
    .remove();

  // ── Node circles ────────────────────────────────────────────────────────

  // Outer ring for capitals (drawn behind main circle)
  const capitalRing = nodeGroup.selectAll('.capital-ring')
    .data(nodes.filter(n => n.is_capital), d => d.id);

  capitalRing.enter()
    .append('circle')
    .attr('class', 'capital-ring')
    .attr('cx', d => d.x)
    .attr('cy', d => d.y)
    .attr('r', d => d.r + 5)
    .attr('fill', 'none')
    .attr('stroke', '#ffffff')
    .attr('stroke-width', 1.5)
    .attr('opacity', 0)
    .transition().duration(300)
    .attr('opacity', 0.6);

  capitalRing
    .transition().duration(300)
    .attr('r', d => d.r + 5)
    .attr('stroke', '#ffffff');

  capitalRing.exit()
    .transition().duration(200)
    .attr('opacity', 0)
    .remove();

  // Second ring (accent color) for capitals
  const capitalRing2 = nodeGroup.selectAll('.capital-ring2')
    .data(nodes.filter(n => n.is_capital), d => d.id);

  capitalRing2.enter()
    .append('circle')
    .attr('class', 'capital-ring2')
    .attr('cx', d => d.x)
    .attr('cy', d => d.y)
    .attr('r', d => d.r + 9)
    .attr('fill', 'none')
    .attr('stroke', d => nodeColor(d.owner))
    .attr('stroke-width', 1)
    .attr('opacity', 0)
    .transition().duration(300)
    .attr('opacity', 0.4);

  capitalRing2
    .transition().duration(300)
    .attr('r', d => d.r + 9)
    .attr('stroke', d => nodeColor(d.owner));

  capitalRing2.exit()
    .transition().duration(200)
    .attr('opacity', 0)
    .remove();

  // Main node circles
  const circles = nodeGroup.selectAll('.node-circle')
    .data(nodes, d => d.id);

  circles.enter()
    .append('circle')
    .attr('class', 'node-circle')
    .attr('cx', d => d.x)
    .attr('cy', d => d.y)
    .attr('r', 0)
    .attr('fill', d => nodeColor(d.owner))
    .attr('stroke', '#0f0f14')
    .attr('stroke-width', 1.5)
    .style('cursor', 'pointer')
    .on('mousemove', onNodeMouseMove)
    .on('mouseleave', onNodeMouseLeave)
    .call(d3.drag()
      .on('start', dragStarted)
      .on('drag',  dragged)
      .on('end',   dragEnded)
    )
    .transition().duration(300)
    .attr('r', d => d.r);

  circles
    .transition().duration(300)
    .attr('r', d => d.r)
    .attr('fill', d => nodeColor(d.owner));

  circles.exit()
    .transition().duration(200)
    .attr('r', 0)
    .remove();

  // ── Labels ───────────────────────────────────────────────────────────────

  const labels = labelGroup.selectAll('.node-label')
    .data(nodes, d => d.id);

  labels.enter()
    .append('text')
    .attr('class', 'node-label')
    .attr('x', d => d.x)
    .attr('y', d => d.y)
    .attr('opacity', 0)
    .text(d => d.name)
    .transition().duration(300)
    .attr('opacity', 1);

  labels
    .transition().duration(300)
    .text(d => d.name);

  labels.exit()
    .transition().duration(200)
    .attr('opacity', 0)
    .remove();

  // ── Tick ─────────────────────────────────────────────────────────────────

  function ticked() {{
    edgeGroup.selectAll('.graph-edge')
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    nodeGroup.selectAll('.capital-ring')
      .attr('cx', d => d.x)
      .attr('cy', d => d.y);

    nodeGroup.selectAll('.capital-ring2')
      .attr('cx', d => d.x)
      .attr('cy', d => d.y);

    nodeGroup.selectAll('.node-circle')
      .attr('cx', d => d.x)
      .attr('cy', d => d.y);

    labelGroup.selectAll('.node-label')
      .attr('x', d => d.x)
      .attr('y', d => d.y + d.r + 13);
  }}

  // ── Drag ─────────────────────────────────────────────────────────────────

  function dragStarted(event, d) {{
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  }}

  function dragged(event, d) {{
    d.fx = event.x; d.fy = event.y;
  }}

  function dragEnded(event, d) {{
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
  }}
}}

// ── Tooltip ────────────────────────────────────────────────────────────────

function onNodeMouseMove(event, d) {{
  const resEntries = Object.entries(d.resources || {{}});
  const resStr = resEntries.length
    ? resEntries.map(([k, v]) => `${{k}}: ${{v}}`).join(', ')
    : 'none';
  const featStr = (d.features || []).join(', ') || 'none';
  const ownerStr = d.owner || 'Neutral';
  const capitalStr = d.is_capital ? ' ★ Capital' : '';

  tooltip.innerHTML = `
    <div class="tt-name">${{escapeHtml(d.name)}}${{capitalStr}}</div>
    <div class="tt-row"><b>Owner:</b> ${{escapeHtml(ownerStr)}}</div>
    <div class="tt-row"><b>Resources:</b> ${{escapeHtml(resStr)}}</div>
    <div class="tt-row"><b>Features:</b> ${{escapeHtml(featStr)}}</div>
  `;

  const panel = document.getElementById('graph-panel');
  const rect  = panel.getBoundingClientRect();
  let tx = event.clientX - rect.left + 14;
  let ty = event.clientY - rect.top  - 10;

  // Keep tooltip inside panel
  if (tx + 250 > rect.width)  tx = event.clientX - rect.left - 254;
  if (ty + 100 > rect.height) ty = event.clientY - rect.top  - 110;

  tooltip.style.left = tx + 'px';
  tooltip.style.top  = ty + 'px';
  tooltip.classList.add('visible');
}}

function onNodeMouseLeave() {{
  tooltip.classList.remove('visible');
}}

// ── Resize ─────────────────────────────────────────────────────────────────

window.addEventListener('resize', () => {{
  if (simulation) {{
    simulation
      .force('center', d3.forceCenter(graphWidth() / 2, graphHeight() / 2).strength(0.08))
      .alpha(0.3)
      .restart();
  }}
}});

// ═══════════════════════════════════════════════════════════════════════════
// Info panel — narration
// ═══════════════════════════════════════════════════════════════════════════

function updateNarration(rec) {{
  const empty = document.getElementById('narration-empty');
  const text  = document.getElementById('narration-text');

  if (rec.narration) {{
    empty.style.display = 'none';
    text.style.display  = 'block';
    text.textContent    = rec.narration;
  }} else {{
    empty.style.display = 'block';
    text.style.display  = 'none';
    empty.textContent   = rec.description || 'No narration available.';
  }}
}}

// ═══════════════════════════════════════════════════════════════════════════
// Info panel — entity list
// ═══════════════════════════════════════════════════════════════════════════

function updateEntities(entitySnapshot) {{
  const list = document.getElementById('entity-list');
  list.innerHTML = '';

  if (!entitySnapshot || entitySnapshot.length === 0) {{
    list.innerHTML = '<span style="color:var(--text-dim);font-size:12px;">No data.</span>';
    return;
  }}

  // Sort: alive first, then by total resources descending
  const sorted = [...entitySnapshot].sort((a, b) => {{
    if (a.alive !== b.alive) return a.alive ? -1 : 1;
    return totalResources(b.resources) - totalResources(a.resources);
  }});

  sorted.forEach(ent => {{
    const color = COLOR_MAP[ent.name] || NEUTRAL_COLOR;
    const res   = totalResources(ent.resources);
    const alive = ent.alive !== false;

    const row = document.createElement('div');
    row.className = 'entity-row';
    row.innerHTML = `
      <div class="entity-dot" style="background:${{color}};${{alive ? '' : 'opacity:0.35;'}}"></div>
      <div class="entity-name${{alive ? '' : ' dead'}}">${{escapeHtml(ent.name)}}</div>
      <div class="entity-res">${{res}} res</div>
      <div class="entity-status">${{alive ? '🟢' : '☠️'}}</div>
    `;
    list.appendChild(row);
  }});
}}

// ═══════════════════════════════════════════════════════════════════════════
// Info panel — relations
// ═══════════════════════════════════════════════════════════════════════════

function updateRelations(relSnap) {{
  const list = document.getElementById('relation-list');
  list.innerHTML = '';

  const pairs = flattenRelations(relSnap);

  if (pairs.length === 0) {{
    list.innerHTML = '<span style="color:var(--text-dim);font-size:12px;">No data.</span>';
    return;
  }}

  pairs.forEach(pair => {{
    const score   = pair.score;
    const color   = relColor(score);
    const label   = relLabel(score);
    // Bar: 0 = left center, 1 = full right, -1 = full left
    // We'll show a 0-to-100% bar where 50% = neutral, 100% = allied, 0% = hostile
    const pct     = Math.round((score + 1) / 2 * 100);

    const row = document.createElement('div');
    row.className = 'relation-row';
    row.innerHTML = `
      <div class="rel-pair" title="${{escapeHtml(pair.a + ' ↔ ' + pair.b)}}">${{escapeHtml(pair.a)}} ↔ ${{escapeHtml(pair.b)}}</div>
      <div class="rel-bar-wrap">
        <div class="rel-bar" style="width:${{pct}}%;background:${{color}};"></div>
      </div>
      <div class="rel-score" style="color:${{color}};">${{score.toFixed(2)}}</div>
      <div class="rel-label" style="color:${{color}};">${{label}}</div>
    `;
    list.appendChild(row);
  }});
}}

// ═══════════════════════════════════════════════════════════════════════════
// Event selection
// ═══════════════════════════════════════════════════════════════════════════

let activeIdx = -1;

function selectEvent(idx) {{
  if (idx === activeIdx) return;
  activeIdx = idx;

  // Update sidebar active state
  document.querySelectorAll('.event-card').forEach((card, i) => {{
    card.classList.toggle('active', i === idx);
  }});

  // Scroll card into view
  const activeCard = eventList.querySelector('.event-card.active');
  if (activeCard) {{
    activeCard.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
  }}

  const rec = RECORDS[idx];

  buildGraph(rec.world_snapshot || []);
  updateNarration(rec);
  updateEntities(rec.entity_snapshot || []);
  updateRelations(rec.relations_snapshot || {{}});
}}

// Auto-select first event on load
if (RECORDS.length > 0) {{
  selectEvent(0);
}}
</script>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args       = parse_args()
    input_path = resolve_input(args.input)
    records    = load_records(input_path)

    if not records:
        sys.exit(f"[report.py] No valid records found in {input_path}")

    html        = build_html(records, input_path)
    output_path = input_path.with_suffix(".html")

    output_path.write_text(html, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()