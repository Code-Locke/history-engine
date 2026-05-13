"""
worldgen.py — World generation for history_engine.

Standalone script that reads config.toml, presets.toml, and a per-theme TOML
file, generates a complete world (nodes, edges, entities, relations), and
writes a JSON file for main.py to hydrate from.

Owns:
  - Algorithmic name generation (syllable-based, seeded)
  - World graph generation (spanning tree + random edges)
  - Entity creation (personality archetypes with jitter)
  - Feature assignment (terrain / landmark / modifier with exclusions)
  - Capital placement and fog-of-war initialisation
  - JSON serialisation of the generated world

Does NOT own:
  - Simulation logic of any kind
  - Config validation (config.py)
  - World/Entity/RelationshipMatrix classes (world.py, entity.py)
  - Action resolution, utility scoring, events, narration

--- Usage ---

    # Generate with defaults from config.toml
    python worldgen.py

    # Specify output path
    python worldgen.py -o worlds/my_world.json

    # Override seed (ignores config.toml random_seed)
    python worldgen.py --seed 42

    # Use sci-fi theme (default: fantasy)
    python worldgen.py --theme scifi

    # Preview without writing (prints JSON to stdout)
    python worldgen.py --preview

--- Output format ---

    {
        "meta": {
            "seed": 42,
            "theme": "fantasy",
            "narrator_style": "chronicle",
            "generator_version": 1
        },
        "nodes": [
            {
                "name": "Ashenveil",
                "features": ["forest", "ruins", "sacred"],
                "resources": {"grain": 3, "iron": 1},
                "owner": null,
                "is_capital": false,
                "explored_by": []
            },
            ...
        ],
        "edges": [
            {
                "node_a": "Ashenveil",
                "node_b": "Ironhold",
                "traversal_cost": 2
            },
            ...
        ],
        "entities": [
            {
                "name": "Velmoor",
                "personality": {
                    "aggression": 0.82,
                    "expansionism": 0.47,
                    "greed": 0.31,
                    "paranoia": 0.19
                },
                "resources": {"grain": 3, "iron": 1},
                "archetype": "warlord"
            },
            ...
        ],
        "relations": {
            "Velmoor": {"Duskreach": 0.0, ...},
            ...
        }
    }
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

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

from .config import CFG

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENERATOR_VERSION = 1
PRESETS_PATH = Path(__file__).parent.parent / "config" / "presets.toml"
THEMES_DIR   = Path(__file__).parent.parent / "themes"

# Feature assignment probabilities
LANDMARK_CHANCE = 0.6
MODIFIER_CHANCE = 0.4

# Personality jitter range: each trait is offset by uniform(-JITTER, +JITTER)
DEFAULT_JITTER = 0.1

# Maximum re-roll attempts when resolving feature exclusions
MAX_REROLL_ATTEMPTS = 20


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_presets(path: Path = PRESETS_PATH) -> dict[str, Any]:
    """Load and return the raw universal presets dict from presets.toml."""
    if not path.exists():
        raise FileNotFoundError(
            f"presets.toml not found at {path}. "
            f"Place it alongside worldgen.py before running."
        )
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_theme(theme: str, themes_dir: Path = THEMES_DIR) -> dict[str, Any]:
    """
    Load and return the raw theme dict from themes/<theme>.toml.

    The theme file is the single source of truth for names, features,
    exclusions, capital landmarks, and resources for a given theme.
    Adding a new theme requires only placing a new .toml file in the
    themes directory — no code changes.

    Args:
        theme:      The theme name (corresponds to a .toml filename stem).
        themes_dir: Directory containing per-theme TOML files.

    Returns:
        The parsed theme dict.

    Raises:
        FileNotFoundError: If the theme file does not exist.
        ValueError:        If required sections are missing or malformed.
    """
    theme_path = themes_dir / f"{theme}.toml"
    if not theme_path.exists():
        available = _discover_themes(themes_dir)
        raise FileNotFoundError(
            f"Theme file not found: {theme_path}. "
            f"Available themes: {available or ['(none)']}"
        )

    with open(theme_path, "rb") as f:
        data = tomllib.load(f)

    # Validate required sections.
    for section in ("names", "features", "resources"):
        if section not in data:
            raise ValueError(
                f"Theme file '{theme_path}' is missing required section: [{section}]"
            )

    names = data["names"]
    for key in ("prefix", "middle", "suffix"):
        if key not in names or not isinstance(names[key], list):
            raise ValueError(
                f"Theme file '{theme_path}': [names] must have a non-empty list '{key}'."
            )

    features = data["features"]
    for key in ("terrain", "landmark", "modifier"):
        if key not in features or not isinstance(features[key], list):
            raise ValueError(
                f"Theme file '{theme_path}': [features] must have a non-empty list '{key}'."
            )

    resources = data["resources"]
    if not isinstance(resources, dict) or not resources:
        raise ValueError(
            f"Theme file '{theme_path}': [resources] must be a non-empty table."
        )
    for rtype, rmax in resources.items():
        if not isinstance(rmax, int) or rmax < 0:
            raise ValueError(
                f"Theme file '{theme_path}': [resources] '{rtype}' must be a "
                f"non-negative integer, got {rmax!r}"
            )

    return data


def _discover_themes(themes_dir: Path = THEMES_DIR) -> list[str]:
    """
    Return a sorted list of theme names available in the themes directory.
    Each .toml file in the directory is treated as a theme.
    """
    if not themes_dir.exists():
        return []
    return sorted(p.stem for p in themes_dir.glob("*.toml"))


# ---------------------------------------------------------------------------
# Name generation
# ---------------------------------------------------------------------------

class NameGenerator:
    """
    Syllable-based name generator. Combines prefix + middle (optional) + suffix.
    Tracks used names to guarantee uniqueness within a generation run.

    Args:
        theme_data: The raw theme dict (from load_theme()).
        rng:        Seeded random.Random instance.
    """

    def __init__(self, theme_data: dict, rng: random.Random) -> None:
        names_section = theme_data.get("names", {})
        self._prefixes: list[str] = names_section["prefix"]
        self._middles: list[str]  = names_section["middle"]
        self._suffixes: list[str] = names_section["suffix"]
        self._rng = rng
        self._used: set[str] = set()

    def generate(self) -> str:
        """
        Generate a unique name. Raises RuntimeError if the pool is
        exhausted after too many attempts (shouldn't happen with
        reasonable pool sizes and node counts).
        """
        for _ in range(200):
            prefix = self._rng.choice(self._prefixes)
            suffix = self._rng.choice(self._suffixes)

            # ~50% chance of a middle syllable
            if self._rng.random() < 0.5:
                middle = self._rng.choice(self._middles)
                name = prefix + middle + suffix
            else:
                name = prefix + suffix

            # Capitalise properly (prefix is already capitalised in the pool)
            name = name.capitalize()

            if name not in self._used:
                self._used.add(name)
                return name

        raise RuntimeError(
            f"Failed to generate a unique name after 200 attempts. "
            f"Consider adding more syllable fragments to the theme file."
        )


# ---------------------------------------------------------------------------
# Feature generation
# ---------------------------------------------------------------------------

class FeatureGenerator:
    """
    Assigns terrain / landmark / modifier features to nodes, respecting
    exclusion rules from the theme file.

    Args:
        theme_data: The raw theme dict (from load_theme()).
        rng:        Seeded random.Random instance.
    """

    def __init__(self, theme_data: dict, rng: random.Random) -> None:
        features_section = theme_data.get("features", {})
        self._terrain: list[str]  = features_section["terrain"]
        self._landmark: list[str] = features_section["landmark"]
        self._modifier: list[str] = features_section["modifier"]
        self._rng = rng

        # Parse exclusions into a set of frozen pairs for fast lookup.
        # Stored as a flat list under the "exclusions" key in [features].
        raw_exclusions = features_section.get("exclusions", [])
        self._exclusions: set[tuple[str, str, str, str]] = set()
        for exc in raw_exclusions:
            if len(exc) == 4:
                self._exclusions.add((exc[0], exc[1], exc[2], exc[3]))

        # Capital landmark — nested under [features.capital]
        capital_section = features_section.get("capital", {})
        self._capital_landmark: str = capital_section.get("landmark", "fortress")

    def generate(self, is_capital: bool = False) -> list[str]:
        """
        Generate a feature list for a single node.

        Args:
            is_capital: If True, the node receives the capital landmark
                        instead of rolling for one.

        Returns:
            A list of 1–3 feature strings: [terrain, landmark?, modifier?].
        """
        terrain = self._rng.choice(self._terrain)

        # Landmark
        landmark: str | None = None
        if is_capital:
            landmark = self._capital_landmark
        elif self._rng.random() < LANDMARK_CHANCE:
            landmark = self._pick_compatible(terrain, "terrain", "landmark", self._landmark)

        # Modifier
        modifier: str | None = None
        if self._rng.random() < MODIFIER_CHANCE:
            modifier = self._pick_compatible(
                terrain, "terrain", "modifier", self._modifier,
                also_check=(landmark, "landmark") if landmark else None,
            )

        features = [terrain]
        if landmark:
            features.append(landmark)
        if modifier:
            features.append(modifier)

        return features

    def _pick_compatible(
        self,
        fixed_value: str,
        fixed_category: str,
        pick_category: str,
        pool: list[str],
        also_check: tuple[str, str] | None = None,
    ) -> str | None:
        """
        Pick a value from pool that is not excluded by the fixed value.
        If also_check is provided, additionally checks compatibility with
        that (value, category) pair.

        Returns None if no compatible option is found after MAX_REROLL_ATTEMPTS.
        """
        candidates = list(pool)
        self._rng.shuffle(candidates)

        for candidate in candidates[:MAX_REROLL_ATTEMPTS]:
            if self._is_excluded(fixed_category, fixed_value, pick_category, candidate):
                continue
            if also_check and self._is_excluded(
                also_check[1], also_check[0], pick_category, candidate
            ):
                continue
            return candidate

        return None

    def _is_excluded(self, cat_a: str, val_a: str, cat_b: str, val_b: str) -> bool:
        """Check if (cat_a, val_a) is excluded from appearing with (cat_b, val_b)."""
        return (
            (cat_a, val_a, cat_b, val_b) in self._exclusions
            or (cat_b, val_b, cat_a, val_a) in self._exclusions
        )


# ---------------------------------------------------------------------------
# Personality generation
# ---------------------------------------------------------------------------

def _load_archetypes(presets: dict) -> dict[str, dict[str, float]]:
    """
    Extract personality archetypes from presets.toml.
    Returns {archetype_name: {trait: value, ...}, ...}.
    """
    raw = presets.get("personalities", {})
    archetypes: dict[str, dict[str, float]] = {}
    required_traits = {"aggression", "expansionism", "greed", "paranoia"}

    for name, traits in raw.items():
        if not isinstance(traits, dict):
            continue
        if not required_traits.issubset(traits.keys()):
            raise ValueError(
                f"Personality archetype '{name}' is missing traits: "
                f"{required_traits - traits.keys()}"
            )
        archetypes[name] = {
            "aggression":   float(traits["aggression"]),
            "expansionism": float(traits["expansionism"]),
            "greed":        float(traits["greed"]),
            "paranoia":     float(traits["paranoia"]),
        }

    if not archetypes:
        raise ValueError("No personality archetypes found in presets.toml.")

    return archetypes


def _jitter_personality(
    base: dict[str, float],
    rng: random.Random,
    jitter: float = DEFAULT_JITTER,
) -> dict[str, float]:
    """
    Apply uniform jitter to each trait in a personality vector.
    Clamps all values to [0.0, 1.0].
    """
    return {
        trait: max(0.0, min(1.0, value + rng.uniform(-jitter, jitter)))
        for trait, value in base.items()
    }


# ---------------------------------------------------------------------------
# Graph generation
# ---------------------------------------------------------------------------

def _generate_spanning_tree(
    node_names: list[str],
    rng: random.Random,
    cost_min: int,
    cost_max: int,
) -> list[dict[str, Any]]:
    """
    Generate a random spanning tree over all node names to guarantee
    graph connectivity. Returns a list of edge dicts.

    Algorithm: shuffle the names, then connect each node to a random
    node earlier in the shuffled list. This produces a random tree.
    """
    shuffled = list(node_names)
    rng.shuffle(shuffled)

    edges: list[dict[str, Any]] = []
    for i in range(1, len(shuffled)):
        target_idx = rng.randint(0, i - 1)
        edges.append({
            "node_a": shuffled[i],
            "node_b": shuffled[target_idx],
            "traversal_cost": rng.randint(cost_min, cost_max),
        })

    return edges


def _add_random_edges(
    node_names: list[str],
    existing_edges: list[dict[str, Any]],
    edge_density: float,
    rng: random.Random,
    cost_min: int,
    cost_max: int,
) -> list[dict[str, Any]]:
    """
    Add random extra edges beyond the spanning tree, based on edge_density.
    edge_density is the probability that any non-existing edge is added.
    """
    existing_pairs: set[frozenset[str]] = set()
    for edge in existing_edges:
        existing_pairs.add(frozenset({edge["node_a"], edge["node_b"]}))

    new_edges: list[dict[str, Any]] = []
    for i in range(len(node_names)):
        for j in range(i + 1, len(node_names)):
            pair = frozenset({node_names[i], node_names[j]})
            if pair in existing_pairs:
                continue
            if rng.random() < edge_density:
                new_edges.append({
                    "node_a": node_names[i],
                    "node_b": node_names[j],
                    "traversal_cost": rng.randint(cost_min, cost_max),
                })
                existing_pairs.add(pair)

    return new_edges


# ---------------------------------------------------------------------------
# Resource generation
# ---------------------------------------------------------------------------

def _generate_node_resources(
    resource_config: dict[str, int],
    rng: random.Random,
) -> dict[str, int]:
    """
    Generate a random resource dict for a node.
    Each resource type gets a random quantity in [0, max].
    Only includes resources with quantity > 0.
    """
    resources: dict[str, int] = {}
    for resource_type, max_qty in resource_config.items():
        qty = rng.randint(0, max_qty)
        if qty > 0:
            resources[resource_type] = qty
    return resources


# ---------------------------------------------------------------------------
# Adjacency helper (for fog-of-war initialisation)
# ---------------------------------------------------------------------------

def _get_neighbors(node_name: str, edges: list[dict[str, Any]]) -> list[str]:
    """Return the names of all nodes adjacent to node_name in the edge list."""
    neighbors: list[str] = []
    for edge in edges:
        if edge["node_a"] == node_name:
            neighbors.append(edge["node_b"])
        elif edge["node_b"] == node_name:
            neighbors.append(edge["node_a"])
    return neighbors


# ---------------------------------------------------------------------------
# World generation — main function
# ---------------------------------------------------------------------------

def generate_world(
    seed: int | None = None,
    theme: str = "fantasy",
    jitter: float = DEFAULT_JITTER,
    presets_path: Path = PRESETS_PATH,
    themes_dir: Path = THEMES_DIR,
) -> dict[str, Any]:
    """
    Generate a complete world dict ready for JSON serialisation.

    Args:
        seed:         Random seed. None = non-deterministic.
        theme:        Theme name (must match a .toml file in themes_dir).
        jitter:       Personality trait jitter range.
        presets_path: Path to presets.toml (universal data).
        themes_dir:   Directory containing per-theme TOML files.

    Returns:
        A dict matching the output format documented in the module docstring.
        The meta block includes the narrator_style active in config.toml at
        generation time, for record-keeping purposes.
    """
    # -- Resolve seed --
    if seed is None:
        config_seed = CFG.simulation.random_seed
        if config_seed == -1:
            seed = random.randint(0, 2**31 - 1)
        else:
            seed = config_seed

    rng = random.Random(seed)

    # -- Load presets and theme --
    presets    = load_presets(presets_path)
    theme_data = load_theme(theme, themes_dir)

    # -- Generators --
    name_gen    = NameGenerator(theme_data, rng)
    feature_gen = FeatureGenerator(theme_data, rng)
    archetypes  = _load_archetypes(presets)

    # -- Read config --
    node_count      = CFG.world.node_count
    entity_count    = CFG.simulation.entity_count
    edge_density    = CFG.world.edge_density
    cost_min        = CFG.world.traversal_cost_min
    cost_max        = CFG.world.traversal_cost_max
    resource_config = theme_data["resources"]

    # -- Generate entity names and archetypes --
    entity_names: list[str] = []
    entity_data: list[dict[str, Any]] = []
    archetype_names = list(archetypes.keys())

    for i in range(entity_count):
        entity_name = name_gen.generate()
        entity_names.append(entity_name)

        archetype_name = rng.choice(archetype_names)
        base_personality = archetypes[archetype_name]
        personality = _jitter_personality(base_personality, rng, jitter)

        entity_data.append({
            "name": entity_name,
            "personality": {k: round(v, 3) for k, v in personality.items()},
            "resources": {},  # filled in after capital assignment
            "archetype": archetype_name,
        })

    # -- Generate node names --
    node_names: list[str] = []
    for _ in range(node_count):
        node_names.append(name_gen.generate())

    # -- Assign capitals: first N nodes go to entities --
    # Shuffle node list so capital assignment is random
    rng.shuffle(node_names)
    capital_assignments: dict[str, str] = {}  # node_name → entity_name
    for i in range(entity_count):
        capital_assignments[node_names[i]] = entity_names[i]

    # -- Generate edges (spanning tree + random extras) --
    edges = _generate_spanning_tree(node_names, rng, cost_min, cost_max)
    extra_edges = _add_random_edges(
        node_names, edges, edge_density, rng, cost_min, cost_max,
    )
    edges.extend(extra_edges)

    # -- Generate nodes --
    nodes: list[dict[str, Any]] = []
    for node_name in node_names:
        is_capital = node_name in capital_assignments
        owner = capital_assignments.get(node_name)
        resources = _generate_node_resources(resource_config, rng)
        features = feature_gen.generate(is_capital=is_capital)

        # Fog of war: capitals are explored by their owner
        explored_by: list[str] = []
        if owner:
            explored_by.append(owner)

        nodes.append({
            "name": node_name,
            "features": features,
            "resources": resources,
            "owner": owner,
            "is_capital": is_capital,
            "explored_by": explored_by,
        })

    # -- Fog of war: each entity's capital neighbors are explored --
    node_lookup: dict[str, dict] = {n["name"]: n for n in nodes}
    for capital_name, entity_name in capital_assignments.items():
        neighbors = _get_neighbors(capital_name, edges)
        for neighbor_name in neighbors:
            neighbor_node = node_lookup[neighbor_name]
            if entity_name not in neighbor_node["explored_by"]:
                neighbor_node["explored_by"].append(entity_name)

    # -- Seed entity resources from capital node resources --
    for entity in entity_data:
        capital_name = next(
            name for name, owner in capital_assignments.items()
            if owner == entity["name"]
        )
        capital_node = node_lookup[capital_name]
        entity["resources"] = dict(capital_node["resources"])

    # -- Generate relations (all pairs start at 0.0 / neutral) --
    relations: dict[str, dict[str, float]] = {}
    for a in entity_names:
        relations[a] = {}
        for b in entity_names:
            if a != b:
                relations[a][b] = 0.0

    # -- Assemble output --
    # narrator_style is recorded from config.toml at generation time.
    # This is metadata only — main.py reads the active style from CFG,
    # not from the world file. The record exists so saved worlds carry
    # a note of the style they were generated under.
    return {
        "meta": {
            "seed": seed,
            "theme": theme,
            "narrator_style": CFG.narrator.narrator_style,
            "generator_version": GENERATOR_VERSION,
        },
        "nodes": nodes,
        "edges": edges,
        "entities": entity_data,
        "relations": relations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    # Discover available themes from the themes directory before building the parser.
    available_themes = _discover_themes()

    parser = argparse.ArgumentParser(
        description="Generate a world for history_engine.",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="world.json",
        help="Output JSON file path (default: world.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed (overrides config.toml random_seed)",
    )
    parser.add_argument(
        "--theme",
        type=str,
        choices=available_themes if available_themes else None,
        default=available_themes[0] if available_themes else "fantasy",
        help=(
            f"Name and feature theme. Available themes are discovered from the "
            f"themes/ directory (default: {available_themes[0] if available_themes else 'fantasy'}). "
            f"Currently available: {available_themes or ['(none — add a .toml file to themes/)']}"
        ),
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=DEFAULT_JITTER,
        help=f"Personality trait jitter range (default: {DEFAULT_JITTER})",
    )
    parser.add_argument(
        "--presets",
        type=str,
        default=str(PRESETS_PATH),
        help=f"Path to presets.toml (default: {PRESETS_PATH})",
    )
    parser.add_argument(
        "--themes-dir",
        type=str,
        default=str(THEMES_DIR),
        help=f"Directory containing per-theme TOML files (default: {THEMES_DIR})",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Print JSON to stdout instead of writing to file",
    )

    args = parser.parse_args()

    world = generate_world(
        seed=args.seed,
        theme=args.theme,
        jitter=args.jitter,
        presets_path=Path(args.presets),
        themes_dir=Path(args.themes_dir),
    )

    output_json = json.dumps(world, indent=2)

    if args.preview:
        print(output_json)
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_json)
        print(f"World generated: {output_path}")
        print(f"  Seed:           {world['meta']['seed']}")
        print(f"  Theme:          {world['meta']['theme']}")
        print(f"  Narrator style: {world['meta']['narrator_style']}")
        print(f"  Nodes:          {len(world['nodes'])}")
        print(f"  Edges:          {len(world['edges'])}")
        print(f"  Entities:       {len(world['entities'])}")
        for entity in world["entities"]:
            print(f"    {entity['name']:20s}  archetype={entity['archetype']}")


if __name__ == "__main__":
    main()