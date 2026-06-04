// frontend/src/types/simulation.ts

export interface Personality {
  aggression: number;
  expansionism: number;
  greed: number;
  paranoia: number;
}

export interface WorldNode {
  name: string;          // world.json의 식별자이자 이름
  owner: string | null;  // 소유 세력 이름
  features?: string[];
  resources: Record<string, number>;
  is_capital?: boolean;
  explored_by?: string[];
  
  // D3 Force Simulation이 내부적으로 주입하는 런타임 좌표 컴포넌트 (선택적 속성)
  x?: number;
  y?: number;
}

export interface EntityProfile {
  name: string;
  archetype: string;
  personality: Personality;
  resources: Record<string, number>;
}

// 백엔드가 제공하거나 public/world.json의 실제 최상위 루트 규격
export interface WorldJsonData {
  meta: {
    seed: number;
    theme: string;
    narrator_style: string;
    generator_version: number;
  };
  nodes: WorldNode[];
  entities: EntityProfile[];
  relations: Record<string, Record<string, number>>;
}

// 💥 D3 Graph 컴포넌트가 다룰 수 있도록 정제할 전용 포맷 인터페이스
export interface WorldGraphData {
  nodes: WorldNode[];
  links: { source: string; target: string }[];
}

// 마크다운 파싱용 타입
export interface TurnNarrative {
  turn: number;
  title: string;
  content: string;
}