# History Engine DB Schema

작성일: 2026-05-21
대상 브랜치: `feat/db-schema`
목표: `feat/db` 브랜치의 임시 DB 모델을 최종 MVP 스키마로 교체할 수 있게 테이블, 관계, JSONL 매핑 규칙을 확정한다.

## 1. 설계 원칙

- 시뮬레이션 1회는 `simulation_runs` 한 행으로 저장한다.
- significant event 1개는 `significant_events` 한 행으로 저장한다.
- LLM narration과 token usage는 `narrations`에 event와 1:1로 분리한다.
- world/entity/relation snapshot은 run 기준이 아니라 event 기준으로 저장한다.
- 원본 JSONL 한 줄은 `significant_events.raw_record JSONB`에 반드시 보존한다.
- 자주 조회하는 필드는 JSONB에만 두지 않고 컬럼으로 빼서 index를 둔다.
- MVP에서는 Alembic 없이 SQLAlchemy `Base.metadata.create_all()`로 초기화한다.

## 2. SQLAlchemy 타입 기준

- Primary key: `Integer`, autoincrement
- Time: `DateTime(timezone=True)`
- JSON: PostgreSQL `JSONB`
- Text body: `Text`
- Short labels: `String`
- Boolean: `Boolean`
- Numeric score/cost: `Float`

SQLAlchemy 구현 시 JSONB는 다음 import를 사용한다.

```python
from sqlalchemy.dialects.postgresql import JSONB
```

## 3. Tables

### 3.1 `simulation_runs`

시뮬레이션 실행 1회를 나타내는 최상위 테이블이다.

| Column              | Type                      | Nullable | Description                      |
|---------------------|---------------------------|----------|----------------------------------|
| `id`                | `Integer PK`              | no       | 내부 run id                        |
| `seed`              | `Integer`                 | yes      | random seed. random 실행이면 null 가능 |
| `theme`             | `String`                  | no       | theme 이름. 예: `fantasy`           |
| `narrator_style`    | `String`                  | yes      | narration style                  |
| `model`             | `String`                  | yes      | LLM model                        |
| `turn_count`        | `Integer`                 | yes      | 설정된 전체 turn 수                    |
| `entity_count`      | `Integer`                 | yes      | 설정된 entity 수                     |
| `node_count`        | `Integer`                 | yes      | 설정된 world node 수                 |
| `edge_density`      | `Float`                   | yes      | world generation edge density    |
| `started_at`        | `DateTime(timezone=True)` | yes      | 실행 시작 시각                         |
| `finished_at`       | `DateTime(timezone=True)` | yes      | 실행 종료 시각                         |
| `status`            | `String`                  | no       | `running`, `completed`, `failed` |
| `early_end`         | `Boolean`                 | no       | 조기 종료 여부. 기본값 `False`            |
| `output_md_path`    | `String`                  | yes      | 생성된 markdown report 경로           |
| `output_jsonl_path` | `String`                  | yes      | 생성된 JSONL 경로                     |
| `config`            | `JSONB`                   | yes      | 실행 config 원본 또는 요약               |
| `meta`              | `JSONB`                   | yes      | world/theme metadata             |
| `created_at`        | `DateTime(timezone=True)` | no       | DB row 생성 시각                     |

추천 index:

- `ix_simulation_runs_created_at`
- `ix_simulation_runs_status`
- `ix_simulation_runs_theme`

### 3.2 `run_entities`

각 run에 등장한 entity의 기본 정보를 저장한다. 최초/최종 상태 요약용이며, event별 상태는 `event_entity_snapshots`에서 조회한다.

| Column              | Type                               | Nullable | Description                               |
|---------------------|------------------------------------|----------|-------------------------------------------|
| `id`                | `Integer PK`                       | no       | 내부 entity row id                          |
| `run_id`            | `Integer FK -> simulation_runs.id` | no       | 소속 run                                    |
| `name`              | `String`                           | no       | entity 이름                                 |
| `archetype`         | `String`                           | yes      | theme archetype                           |
| `alive`             | `Boolean`                          | yes      | 최종 생존 여부                                  |
| `capital_node_name` | `String`                           | yes      | 수도 node 이름                                |
| `personality`       | `JSONB`                            | yes      | aggression, expansionism, greed, paranoia |
| `initial_resources` | `JSONB`                            | yes      | 시작 자원                                     |
| `final_resources`   | `JSONB`                            | yes      | 최종 자원                                     |

추천 constraint/index:

- Unique: `(run_id, name)`
- Index: `(run_id, name)`

### 3.3 `world_nodes`

각 run의 world node 기본 정보를 저장한다. 최초/최종 owner 요약용이며, event별 node 상태는 `event_world_snapshots`에서 조회한다.

| Column          | Type                               | Nullable | Description        |
|-----------------|------------------------------------|----------|--------------------|
| `id`            | `Integer PK`                       | no       | 내부 node row id     |
| `run_id`        | `Integer FK -> simulation_runs.id` | no       | 소속 run             |
| `name`          | `String`                           | no       | node 이름            |
| `initial_owner` | `String`                           | yes      | 시작 owner           |
| `final_owner`   | `String`                           | yes      | 최종 owner           |
| `is_capital`    | `Boolean`                          | no       | 수도 node 여부         |
| `resources`     | `JSONB`                            | yes      | node resource dict |
| `features`      | `JSONB`                            | yes      | node feature list  |

추천 constraint/index:

- Unique: `(run_id, name)`
- Index: `(run_id, name)`
- Index: `(run_id, final_owner)`

### 3.4 `world_edges`

각 run의 world graph edge를 저장한다. 현재 JSONL의 `edges_snapshot`은 event마다 반복되므로 import 시 첫 event의 edge 목록으로 run-level edge를 구성해도 된다.

| Column           | Type                               | Nullable | Description          |
|------------------|------------------------------------|----------|----------------------|
| `id`             | `Integer PK`                       | no       | 내부 edge row id       |
| `run_id`         | `Integer FK -> simulation_runs.id` | no       | 소속 run               |
| `node_a`         | `String`                           | no       | edge endpoint A      |
| `node_b`         | `String`                           | no       | edge endpoint B      |
| `traversal_cost` | `Float`                            | yes      | 추후 edge cost가 생기면 저장 |

저장 규칙:

- undirected graph이므로 `node_a <= node_b` 순서로 정렬해서 저장한다.
- 같은 edge 중복 저장을 막기 위해 `(run_id, node_a, node_b)` unique constraint를 둔다.

### 3.5 `significant_events`

중요 사건을 저장하는 핵심 테이블이다. Timeline, event detail, filtering의 기준이 된다.

| Column          | Type                               | Nullable | Description                         |
|-----------------|------------------------------------|----------|-------------------------------------|
| `id`            | `Integer PK`                       | no       | 내부 event id                         |
| `run_id`        | `Integer FK -> simulation_runs.id` | no       | 소속 run                              |
| `turn`          | `Integer`                          | no       | event 발생 turn                       |
| `event_index`   | `Integer`                          | no       | run 안에서의 event 순서. JSONL line 순서 기준 |
| `event_type`    | `String`                           | no       | 예: `war_declared`, `elimination`    |
| `actor`         | `String`                           | yes      | 사건 주체 entity                        |
| `target_entity` | `String`                           | yes      | 대상 entity                           |
| `target_node`   | `String`                           | yes      | 대상 node                             |
| `description`   | `Text`                             | no       | 규칙 기반 사건 설명                         |
| `metadata`      | `JSONB`                            | yes      | event metadata                      |
| `raw_record`    | `JSONB`                            | no       | JSONL 원본 한 줄 전체                     |
| `created_at`    | `DateTime(timezone=True)`          | no       | DB row 생성 시각                        |

추천 index:

- `(run_id, turn)`
- `(run_id, event_index)`
- `(run_id, event_type)`
- `(run_id, actor)`
- `(run_id, target_entity)`
- `(run_id, target_node)`

### 3.6 `narrations`

LLM narration 결과와 token usage를 저장한다. Event와 1:1 관계다.

| Column            | Type                                  | Nullable | Description           |
|-------------------|---------------------------------------|----------|-----------------------|
| `id`              | `Integer PK`                          | no       | 내부 narration id       |
| `event_id`        | `Integer FK -> significant_events.id` | no       | 연결 event              |
| `narrator_style`  | `String`                              | yes      | narrator style        |
| `model`           | `String`                              | yes      | LLM model             |
| `narration_text`  | `Text`                                | yes      | LLM narration 본문      |
| `prompt_tokens`   | `Integer`                             | no       | prompt token 수. 기본값 0 |
| `output_tokens`   | `Integer`                             | no       | output token 수. 기본값 0 |
| `narration_error` | `Text`                                | yes      | narration 실패 메시지      |
| `created_at`      | `DateTime(timezone=True)`             | no       | DB row 생성 시각          |

추천 constraint/index:

- Unique: `event_id`
- Index: `event_id`
- P1에서 narration 검색을 붙이면 PostgreSQL full-text index 고려

### 3.7 `event_world_snapshots`

특정 event 시점의 world node 상태를 저장한다. D3 graph와 event detail 화면의 node 상태 재현에 사용한다.

| Column       | Type                                  | Nullable | Description        |
|--------------|---------------------------------------|----------|--------------------|
| `id`         | `Integer PK`                          | no       | 내부 snapshot row id |
| `event_id`   | `Integer FK -> significant_events.id` | no       | 기준 event           |
| `node_name`  | `String`                              | no       | node 이름            |
| `owner`      | `String`                              | yes      | 해당 시점 owner        |
| `is_capital` | `Boolean`                             | no       | 수도 node 여부         |
| `resources`  | `JSONB`                               | yes      | resource dict      |
| `features`   | `JSONB`                               | yes      | feature list       |

추천 constraint/index:

- Unique: `(event_id, node_name)`
- Index: `(event_id, owner)`

### 3.8 `event_entity_snapshots`

특정 event 시점의 entity 상태를 저장한다. Entity timeline과 event detail 화면에 사용한다.

| Column        | Type                                  | Nullable | Description        |
|---------------|---------------------------------------|----------|--------------------|
| `id`          | `Integer PK`                          | no       | 내부 snapshot row id |
| `event_id`    | `Integer FK -> significant_events.id` | no       | 기준 event           |
| `entity_name` | `String`                              | no       | entity 이름          |
| `alive`       | `Boolean`                             | no       | 해당 시점 생존 여부        |
| `resources`   | `JSONB`                               | yes      | resource dict      |
| `node_count`  | `Integer`                             | no       | 해당 시점 보유 node 수    |

추천 constraint/index:

- Unique: `(event_id, entity_name)`
- Index: `(entity_name)`

### 3.9 `event_relation_snapshots`

특정 event 시점의 entity 관계 matrix를 pair 단위로 저장한다.

| Column     | Type                                  | Nullable | Description        |
|------------|---------------------------------------|----------|--------------------|
| `id`       | `Integer PK`                          | no       | 내부 relation row id |
| `event_id` | `Integer FK -> significant_events.id` | no       | 기준 event           |
| `entity_a` | `String`                              | no       | 관계 주체 A            |
| `entity_b` | `String`                              | no       | 관계 주체 B            |
| `score`    | `Float`                               | no       | 관계 점수              |

저장 규칙:

- 같은 관계가 양방향으로 들어올 수 있으므로 `entity_a < entity_b`가 되도록 정렬한다.
- 자기 자신과의 관계는 저장하지 않는다.

추천 constraint/index:

- Unique: `(event_id, entity_a, entity_b)`
- Index: `(entity_a, entity_b)`

## 4. JSONL Import Mapping

현재 `engine/output.py`가 쓰는 JSONL 한 줄은 significant event 하나다. 실제 key 이름은 다음과 같다.

| JSONL key            | Target table/column                |
|----------------------|------------------------------------|
| `turn`               | `significant_events.turn`          |
| `event_type`         | `significant_events.event_type`    |
| `actor`              | `significant_events.actor`         |
| `target_entity`      | `significant_events.target_entity` |
| `target_node`        | `significant_events.target_node`   |
| `description`        | `significant_events.description`   |
| `metadata`           | `significant_events.metadata`      |
| whole record         | `significant_events.raw_record`    |
| `narration`          | `narrations.narration_text`        |
| `narrator_style`     | `narrations.narrator_style`        |
| `prompt_tokens`      | `narrations.prompt_tokens`         |
| `output_tokens`      | `narrations.output_tokens`         |
| `narration_error`    | `narrations.narration_error`       |
| `world_snapshot[]`   | `event_world_snapshots`            |
| `entity_snapshot[]`  | `event_entity_snapshots`           |
| `relations_snapshot` | `event_relation_snapshots`         |
| `edges_snapshot[]`   | `world_edges`                      |

Important implementation note:

- `feat/db`의 현재 임시 코드에는 `data.get("event", "unknown")`가 들어가 있는데, 실제 JSONL key는 `event_type`이다.
- 따라서 최종 import 코드는 반드시 `data.get("event_type", "unknown")`를 사용해야 한다.

## 5. Import Flow

1. API가 simulation을 시작하면 `simulation_runs`에 `status="running"` 행을 만든다.
2. engine 실행 후 생성된 최신 JSONL 경로를 `simulation_runs.output_jsonl_path`에 기록한다.
3. JSONL을 line by line으로 읽는다.
4. 각 line마다 `significant_events` 행을 만든다.
5. 같은 event에 대해 `narrations` 행을 만든다.
6. `world_snapshot`을 `event_world_snapshots`에 bulk insert한다.
7. `entity_snapshot`을 `event_entity_snapshots`에 bulk insert한다.
8. `relations_snapshot`을 pair list로 펼쳐 `event_relation_snapshots`에 bulk insert한다.
9. 첫 event 또는 마지막 event의 `edges_snapshot`을 기준으로 `world_edges`를 구성한다.
10. import가 끝나면 `simulation_runs.status="completed"`와 `finished_at`을 갱신한다.
11. 중간 실패 시 rollback 후 `simulation_runs.status="failed"`로 기록한다.

## 6. Minimum Query Contract

API 담당자가 우선 붙일 조회 단위는 다음이다.

### Run list

`GET /api/runs`

필요 데이터:

- `id`
- `theme`
- `narrator_style`
- `status`
- `started_at`
- `finished_at`
- event count
- prompt/output token sum

### Run detail

`GET /api/runs/{run_id}`

필요 데이터:

- `simulation_runs` row
- entity summary
- world node summary
- world edge list
- event count
- token usage summary

### Run event timeline

`GET /api/runs/{run_id}/events`

필터 후보:

- `event_type`
- `actor`
- `target_entity`
- `target_node`
- `turn_from`
- `turn_to`

정렬:

- `turn ASC`
- `event_index ASC`

### Event detail

`GET /api/events/{event_id}`

필요 데이터:

- event 기본 정보
- narration
- token usage
- metadata

### Event snapshots

`GET /api/events/{event_id}/snapshot/world`
`GET /api/events/{event_id}/snapshot/entities`
`GET /api/events/{event_id}/snapshot/relations`

프론트엔드 D3 graph는 world snapshot과 `world_edges`를 함께 받아서 그린다.

## 7. `feat/db` 임시 모델 교체 가이드

현재 `feat/db:api/models.py`의 임시 구조:

- `SimulationRun`
  - `id`
  - `theme`
  - `narrator_style`
- `Event`
  - `id`
  - `run_id`
  - `turn_number`
  - `event_type`
  - `narration`
  - `raw_data`

교체 방향:

- `SimulationRun.__tablename__`은 `runs` 대신 `simulation_runs`로 변경한다.
- 임시 `Event`는 `SignificantEvent`로 교체하고 `__tablename__ = "significant_events"`를 사용한다.
- `turn_number`는 실제 JSONL key와 맞춰 `turn`으로 변경한다.
- `narration` 컬럼은 event 테이블에서 빼고 `Narration` 모델로 분리한다.
- `raw_data`는 `raw_record JSONB`로 이름을 바꾼다.
- `Column(JSON)` 대신 PostgreSQL `JSONB`를 쓴다.

## 8. P0 Acceptance Criteria

- DB 문서만 보고 SQLAlchemy model을 작성할 수 있다.
- JSONL 한 줄의 모든 주요 필드가 어느 테이블로 가는지 명확하다.
- run 목록, event timeline, event detail, snapshot 조회를 만들 수 있다.
- 원본 JSONL이 `raw_record`에 보존되어 데이터 손실 없이 재처리할 수 있다.
