BEGIN;

CREATE TABLE IF NOT EXISTS simulation_runs (
    id SERIAL NOT NULL,
    seed INTEGER,
    theme VARCHAR NOT NULL,
    narrator_style VARCHAR,
    model VARCHAR,
    turn_count INTEGER,
    entity_count INTEGER,
    node_count INTEGER,
    edge_density FLOAT,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR NOT NULL,
    early_end BOOLEAN NOT NULL,
    output_md_path VARCHAR,
    output_jsonl_path VARCHAR,
    config JSONB,
    meta JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_simulation_runs_created_at
    ON simulation_runs (created_at);
CREATE INDEX IF NOT EXISTS ix_simulation_runs_status
    ON simulation_runs (status);
CREATE INDEX IF NOT EXISTS ix_simulation_runs_theme
    ON simulation_runs (theme);

CREATE TABLE IF NOT EXISTS run_entities (
    id SERIAL NOT NULL,
    run_id INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    archetype VARCHAR,
    alive BOOLEAN,
    capital_node_name VARCHAR,
    personality JSONB,
    initial_resources JSONB,
    final_resources JSONB,
    PRIMARY KEY (id),
    CONSTRAINT uq_run_entities_run_id_name UNIQUE (run_id, name),
    FOREIGN KEY(run_id) REFERENCES simulation_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_run_entities_run_id_name
    ON run_entities (run_id, name);

CREATE TABLE IF NOT EXISTS world_nodes (
    id SERIAL NOT NULL,
    run_id INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    initial_owner VARCHAR,
    final_owner VARCHAR,
    is_capital BOOLEAN NOT NULL,
    resources JSONB,
    features JSONB,
    PRIMARY KEY (id),
    CONSTRAINT uq_world_nodes_run_id_name UNIQUE (run_id, name),
    FOREIGN KEY(run_id) REFERENCES simulation_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_world_nodes_run_id_name
    ON world_nodes (run_id, name);
CREATE INDEX IF NOT EXISTS ix_world_nodes_run_id_final_owner
    ON world_nodes (run_id, final_owner);

CREATE TABLE IF NOT EXISTS world_edges (
    id SERIAL NOT NULL,
    run_id INTEGER NOT NULL,
    node_a VARCHAR NOT NULL,
    node_b VARCHAR NOT NULL,
    traversal_cost FLOAT,
    PRIMARY KEY (id),
    CONSTRAINT uq_world_edges_run_id_nodes UNIQUE (run_id, node_a, node_b),
    CONSTRAINT ck_world_edges_node_order CHECK (node_a <= node_b),
    FOREIGN KEY(run_id) REFERENCES simulation_runs (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS significant_events (
    id SERIAL NOT NULL,
    run_id INTEGER NOT NULL,
    turn INTEGER NOT NULL,
    event_index INTEGER NOT NULL,
    event_type VARCHAR NOT NULL,
    actor VARCHAR,
    target_entity VARCHAR,
    target_node VARCHAR,
    description TEXT NOT NULL,
    metadata JSONB,
    raw_record JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(run_id) REFERENCES simulation_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_significant_events_run_id_turn
    ON significant_events (run_id, turn);
CREATE INDEX IF NOT EXISTS ix_significant_events_run_id_event_index
    ON significant_events (run_id, event_index);
CREATE INDEX IF NOT EXISTS ix_significant_events_run_id_event_type
    ON significant_events (run_id, event_type);
CREATE INDEX IF NOT EXISTS ix_significant_events_run_id_actor
    ON significant_events (run_id, actor);
CREATE INDEX IF NOT EXISTS ix_significant_events_run_id_target_entity
    ON significant_events (run_id, target_entity);
CREATE INDEX IF NOT EXISTS ix_significant_events_run_id_target_node
    ON significant_events (run_id, target_node);

CREATE TABLE IF NOT EXISTS narrations (
    id SERIAL NOT NULL,
    event_id INTEGER NOT NULL,
    narrator_style VARCHAR,
    model VARCHAR,
    narration_text TEXT,
    prompt_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    narration_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (event_id),
    FOREIGN KEY(event_id) REFERENCES significant_events (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_narrations_event_id
    ON narrations (event_id);

CREATE TABLE IF NOT EXISTS event_world_snapshots (
    id SERIAL NOT NULL,
    event_id INTEGER NOT NULL,
    node_name VARCHAR NOT NULL,
    owner VARCHAR,
    is_capital BOOLEAN NOT NULL,
    resources JSONB,
    features JSONB,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_world_snapshots_event_node UNIQUE (event_id, node_name),
    FOREIGN KEY(event_id) REFERENCES significant_events (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_event_world_snapshots_event_id_owner
    ON event_world_snapshots (event_id, owner);

CREATE TABLE IF NOT EXISTS event_entity_snapshots (
    id SERIAL NOT NULL,
    event_id INTEGER NOT NULL,
    entity_name VARCHAR NOT NULL,
    alive BOOLEAN NOT NULL,
    resources JSONB,
    node_count INTEGER NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_entity_snapshots_event_entity UNIQUE (event_id, entity_name),
    FOREIGN KEY(event_id) REFERENCES significant_events (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_event_entity_snapshots_entity_name
    ON event_entity_snapshots (entity_name);

CREATE TABLE IF NOT EXISTS event_relation_snapshots (
    id SERIAL NOT NULL,
    event_id INTEGER NOT NULL,
    entity_a VARCHAR NOT NULL,
    entity_b VARCHAR NOT NULL,
    score FLOAT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_relation_snapshots_event_pair UNIQUE (event_id, entity_a, entity_b),
    CONSTRAINT ck_event_relation_snapshots_entity_order CHECK (entity_a < entity_b),
    FOREIGN KEY(event_id) REFERENCES significant_events (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_event_relation_snapshots_entity_pair
    ON event_relation_snapshots (entity_a, entity_b);

COMMIT;
