// frontend/src/components/WorldGraph.tsx
import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import type { WorldNode, WorldGraphData } from '../types/simulation';

interface WorldGraphProps {
  graphData: WorldGraphData;
  onNodeClick: (node: WorldNode) => void;
  entityColors: Record<string, string>;
}

export default function WorldGraph({ graphData, onNodeClick, entityColors }: WorldGraphProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    if (!svgRef.current || !graphData || graphData.nodes.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove(); // 이전 렌더링 노드 찌꺼기 청소

    const width = 800;
    const height = 600;

    // 원본 데이터 불변성(Immutability)을 지키기 위한 얕은 복사 실행
    const nodes: WorldNode[] = graphData.nodes.map(n => ({ ...n }));
    const links = graphData.links.map(l => ({
      source: l.source,
      target: l.target
    }));

    // 시뮬레이션 물리 모델 설정
    const simulation = d3.forceSimulation<WorldNode>(nodes)
      .force("link", d3.forceLink<WorldNode, any>(links).id(d => d.name).distance(100))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(width / 2, height / 2));

    // 1. 연결 선(Edges) 레이어 생성
    const link = svg.append("g")
      .attr("stroke", "#334155")
      .attr("stroke-width", 2)
      .selectAll("line")
      .data(links)
      .join("line");

    // 2. 정점(Nodes) 그룹 레이어 생성
    const node = svg.append("g")
      .selectAll<SVGGElement, WorldNode>("g")
      .data(nodes)
      .join("g")
      .attr("cursor", "pointer")
      .on("click", (_event, d) => onNodeClick(d));

    // 노드 중심부 원 (세력 색상 매핑)
    node.append("circle")
      .attr("r", 14)
      .attr("fill", d => d.owner ? (entityColors[d.owner] ?? "#6366f1") : "#1e293b")
      .attr("stroke", d => d.is_capital ? "#f59e0b" : "#475569") // 수도면 황금색 테두리
      .attr("stroke-width", d => d.is_capital ? 3 : 1.5);

    // 노드 이름 텍스트 라벨 추가
    node.append("text")
      .text(d => d.name)
      .attr("dy", 26)
      .attr("text-anchor", "middle")
      .attr("fill", "#94a3b8")
      .attr("class", "text-xs font-mono selection:bg-transparent");

    // 매 프레임 좌표 실시간 트래킹 및 바인딩
    simulation.on("tick", () => {
      link
        .attr("x1", d => (d.source as any).x ?? 0)
        .attr("y1", d => (d.source as any).y ?? 0)
        .attr("x2", d => (d.target as any).x ?? 0)
        .attr("y2", d => (d.target as any).y ?? 0);

      node.attr("transform", d => `translate(${d.x ?? 0}, ${d.y ?? 0})`);
    });

    return () => {
      simulation.stop();
    };
  }, [graphData, entityColors, onNodeClick]);

  return (
    <div className="relative border border-slate-800 bg-slate-950 rounded-xl overflow-hidden flex-1 h-[600px]">
      <div className="absolute top-3 left-3 bg-slate-900/90 px-3 py-1 rounded text-xs font-mono text-slate-400 border border-slate-800 z-10">
        🛰️ AUTONOMOUS WORLD GRAPH VISUALIZER
      </div>
      <svg ref={svgRef} width="100%" height="100%" viewBox="0 0 800 600" className="w-full h-full" />
    </div>
  );
}