import React, { useState, useEffect, useMemo, useRef } from 'react';

// ── 데이터 구조 인터페이스 정의 ──
interface WorldNode {
  name: string;
  owner: string | null;
  is_capital: boolean;
  resources: Record<string, any>;
  features: string[];
  x?: number; 
  y?: number; 
}

interface TimelineEvent {
  id: number;
  turn: number;
  event_type: string;
  actor: string;
  description: string;
  narration?: string | NarrationObject | null;
}

interface EntityStatus {
  name: string;
  alive: boolean;
  resources: Record<string, any>;
  node_count: number;
}

interface NarrationObject {
  narration_text: string;
}

interface SnapshotData {
  event_id: number;
  turn: number;
  narration: string | NarrationObject | null;
  world_snapshot: WorldNode[];
  entity_snapshot: EntityStatus[];
  relations_snapshot?: Record<string, Record<string, number>>;
}

export default function App() {
  const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>([]);
  // '턴' 중심이 아닌 '이벤트 ID' 중심으로 활성화 상태 관리 (같은 턴 내 개별 이벤트 분리 선택 가능)
  const [activeEventId, setActiveEventId] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<SnapshotData | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  // 🔍 지도 확대/축소 및 드래그 제어 상태
  const [zoom, setZoom] = useState({ scale: 1, x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  // 🖱️ 호버(Hover) 노드 정보 툴팁 상태 관리
  const [hoveredNode, setHoveredNode] = useState<WorldNode | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  const mainPanelRef = useRef<HTMLDivElement | null>(null);

  // 1. 최초 컴포넌트 마운트 시 이벤트 목록 로드
  useEffect(() => {
    fetch('http://localhost:8000/api/simulations/latest/events')
      .then((res) => {
        if (!res.ok) throw new Error("API 서버 연결 실패");
        return res.json();
      })
      .then((data) => {
        if (Array.isArray(data)) {
          // 타임라인 순서대로 정렬
          const sortedData = data.sort((a, b) => a.turn - b.turn || a.id - b.id);
          setTimelineEvents(sortedData);
          if (sortedData.length > 0) {
            setActiveEventId(sortedData[0].id); // 첫 번째 이벤트 개별 활성화
          }
        }
      })
      .catch((err) => console.error("타임라인 데이터 로드 실패:", err));
  }, []);

  // 2. 선택된 개별 이벤트 고유 ID에 따른 스냅샷 실시간 로드
  useEffect(() => {
    if (!activeEventId) {
      setSnapshot(null);
      return;
    }

    setLoading(true);
    fetch(`http://localhost:8000/api/events/${activeEventId}/snapshot`)
      .then((res) => {
        if (!res.ok) throw new Error("스냅샷 응답 실패");
        return res.json();
      })
      .then((data) => {
        setSnapshot(data);
        setLoading(false);
      })
      .catch((err) => {
        console.error("스냅샷 로드 오류:", err);
        setLoading(false);
      });
  }, [activeEventId]);

  // 🛠️ 마우스 휠 패시브 스크롤 브라우저 에러 차단
  useEffect(() => {
    const panel = mainPanelRef.current;
    if (!panel) return;

    const handleNativeWheel = (e: WheelEvent) => {
      e.preventDefault();
      const zoomFactor = 1.15;
      setZoom(prev => {
        let nextScale = prev.scale;
        if (e.deltaY < 0) {
          nextScale = Math.min(prev.scale * zoomFactor, 8);
        } else {
          nextScale = Math.max(prev.scale / zoomFactor, 0.4);
        }
        return { ...prev, scale: nextScale };
      });
    };

    panel.addEventListener('wheel', handleNativeWheel, { passive: false });
    return () => panel.removeEventListener('wheel', handleNativeWheel);
  }, []);

  // 🎨 6가지 세력 색상 매핑 라이브러리
  function getFactionColor(factionName: string | null): string {
    if (!factionName) return '#555566'; 
    const colors: Record<string, string> = {
      'Ashirglen':      '#e05c5c', 
      'Duskveil':       '#b65ce0', 
      'Stormfall':      '#5cc6e0', 
      'Ironenmark':     '#e0b65c', 
      'Stormkelwound':  '#73e05c'  
    };
    return colors[factionName] || '#888899';
  }

  const getRelationStyle = (score: number) => {
    const pct = Math.min(Math.max((score + 1) * 50, 0), 100);
    let color = '#7a7a90';
    if (score > 0.2) color = '#5ce0b6';
    else if (score < -0.2) color = '#e05c5c';
    return { pct, color };
  };

  // 🔍 마우스 드래그 이동 (Pan) 핸들러
  const handleMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName === 'circle' || (e.target as HTMLElement).tagName === 'text') return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - zoom.x, y: e.clientY - zoom.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      setZoom(prev => ({
        ...prev,
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y
      }));
    }
    // 툴팁 위치를 갱신 마우스 위치 기반 트래킹
    if (hoveredNode) {
      const bounds = mainPanelRef.current?.getBoundingClientRect();
      if (bounds) {
        setTooltipPos({
          x: e.clientX - bounds.left + 15,
          y: e.clientY - bounds.top + 15
        });
      }
    }
  };

  const handleMouseUpOrLeave = () => {
    setIsDragging(false);
  };

  // 🖱️ 노드 마우스 이벤트 핸들러 (정보창 팝업용)
  const handleNodeMouseEnter = (e: React.MouseEvent, node: WorldNode) => {
    setHoveredNode(node);
    const bounds = mainPanelRef.current?.getBoundingClientRect();
    if (bounds) {
      setTooltipPos({
        x: e.clientX - bounds.left + 15,
        y: e.clientY - bounds.top + 15
      });
    }
  };

  const handleNodeMouseLeave = () => {
    setHoveredNode(null);
  };

  const zoomIn = () => setZoom(prev => ({ ...prev, scale: Math.min(prev.scale * 1.25, 8) }));
  const zoomOut = () => setZoom(prev => ({ ...prev, scale: Math.max(prev.scale / 1.25, 0.4) }));
  const resetZoom = () => setZoom({ scale: 1, x: 0, y: 0 });

  // 기록관 해설 박스 텍스트 계산
  const renderNarrationContent = () => {
    const currentEvent = timelineEvents.find(e => e.id === activeEventId);
    if (!currentEvent) return "📝 특기할 사항 없음";

    if (snapshot && snapshot.narration) {
      if (typeof snapshot.narration === 'object') {
        return (snapshot.narration as NarrationObject).narration_text || currentEvent.description;
      }
      return snapshot.narration;
    }
    return currentEvent.description || "📝 특기할 사항 없음";
  };

  // 현재 선택된 이벤트 오브젝트 구하기
  const currentActiveEvent = timelineEvents.find(e => e.id === activeEventId);

  return (
    <div style={{
      boxSizing: 'border-box', margin: 0, padding: 0, height: '100vh', width: '100vw',
      backgroundColor: '#0f0f14', color: '#d4d4e0', fontFamily: "'Segoe UI', system-ui, sans-serif",
      fontSize: '14px', overflow: 'hidden',
      display: 'grid',
      gridTemplateRows: '52px 1fr 260px',
      gridTemplateColumns: '350px 1fr',
      gridTemplateAreas: '"header header" "sidebar main" "sidebar bottom"'
    }}>
      
      {/* ── HEADER ── */}
      <header style={{
        gridArea: 'header', display: 'flex', alignItems: 'center', gap: '16px', padding: '0 20px',
        background: '#17171f', borderBottom: '1px solid #2a2a38', whiteSpace: 'nowrap', overflow: 'hidden'
      }}>
        <span style={{ fontSize: '15px', fontWeight: 600, color: '#eeeef8' }}>⚔️ 역사적 가상 시뮬레이션 타임라인 엔진</span>
        <div style={{ fontSize: '12px', color: '#7a7a90', display: 'flex', gap: '18px', alignItems: 'center' }}>
          <span style={{ background: '#5c9ee0', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', color: '#fff', fontWeight:'bold' }}>FINE-GRAINED CHRONICLE</span>
          {currentActiveEvent && <span>조회 중인 시점: <b style={{ color: '#5c9ee0' }}>{currentActiveEvent.turn} 턴 (이벤트 고유 번호: #{currentActiveEvent.id})</b></span>}
        </div>
      </header>

      {/* ── SIDEBAR (개별 이벤트 독립 리스트 구성) ── */}
      <aside style={{ gridArea: 'sidebar', display: 'flex', flexDirection: 'column', background: '#17171f', borderRight: '1px solid #2a2a38', overflow: 'hidden' }}>
        <div style={{ padding: '12px 14px', fontSize: '11px', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#eeeef8', borderBottom: '1px solid #2a2a38', background: '#0f0f14' }}>
          📜 개별 사건 기록 저널 목록 ({timelineEvents.length})
        </div>
        <div style={{ overflowY: 'auto', flex: 1, padding: '2px 0' }}>
          {timelineEvents.map((evt) => {
            const isSelected = activeEventId === evt.id;
            return (
              <button
                key={evt.id}
                onClick={() => setActiveEventId(evt.id)}
                style={{
                  width: '100%', textAlign: 'left', border: 'none', 
                  background: isSelected ? '#252530' : 'transparent',
                  padding: '12px 14px', cursor: 'pointer', borderBottom: '1px solid #2a2a38',
                  borderLeft: `4px solid ${isSelected ? '#5c9ee0' : 'transparent'}`, 
                  transition: 'background 100ms'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                  <span style={{ fontSize: '11px', color: isSelected ? '#5c9ee0' : '#7a7a90', fontFamily: 'monospace', fontWeight: 'bold' }}>
                    TURN {evt.turn.toString().padStart(3, '0')} (ID: #{evt.id})
                  </span>
                  <span style={{ fontSize: '10px', background: isSelected ? '#5c9ee0' : '#23232f', color: isSelected ? '#fff' : '#cbd5e1', padding: '1px 5px', borderRadius: '3px', fontWeight: '600' }}>
                    {evt.event_type}
                  </span>
                </div>
                <div style={{ fontSize: '12px', fontWeight: 700, color: '#eeeef8', marginBottom: '2px' }}>
                  {evt.actor}
                </div>
                <div style={{ fontSize: '11px', color: '#7a7a90', overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                  {evt.description}
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      {/* ── MAIN WORKSPACE (SVG GRAPH VIEW) ── */}
      <main 
        ref={mainPanelRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUpOrLeave}
        onMouseLeave={handleMouseUpOrLeave}
        style={{ 
          gridArea: 'main', position: 'relative', background: '#0f0f14', overflow: 'hidden',
          cursor: isDragging ? 'grabbing' : 'grab'
        }}
      >
        <div style={{ width: '100%', height: '100%' }}>
          <svg width="100%" height="100%" viewBox="0 0 800 580" style={{ display: 'block' }}>
            <g transform={`translate(${zoom.x}, ${zoom.y}) scale(${zoom.scale})`}>
              
              {/* 노드 간 연결선 삭제 완료 */}

              {/* 영토 노드 그리기 */}
              {snapshot?.world_snapshot?.map((node, idx) => {
                const def = { x: 100 + (idx * 85) % 600, y: 120 + (idx * 55) % 350 };
                const posX = node.x ?? def.x;
                const posY = node.y ?? def.y;
                const nColor = getFactionColor(node.owner);

                return (
                  <g 
                    key={node.name} 
                    transform={`translate(${posX}, ${posY})`}
                    onMouseEnter={(e) => handleNodeMouseEnter(e, node)} 
                    onMouseLeave={handleNodeMouseLeave}               
                    style={{ cursor: 'pointer' }}
                  >
                    <circle
                      r={node.is_capital ? 13 : 8}
                      fill={nColor} stroke="#ffffff" strokeWidth={node.is_capital ? "2.5" : "1.5"}
                      style={{ transition: 'transform 150ms', transform: hoveredNode?.name === node.name ? 'scale(1.2)' : 'none' }}
                    />
                    <text y={node.is_capital ? -19 : -14} textAnchor="middle" fill="#f8fafc" fontSize={11 / Math.sqrt(zoom.scale)} fontWeight={node.is_capital ? "bold" : "normal"}>
                      {node.name} {node.is_capital && '👑'}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>

        {/* 🛠️ 문법 오류 해결 완료 파트: 노드 정보창 팝업 UI (Tooltip) */}
        {hoveredNode && (
          <div style={{
            position: 'absolute', 
            left: `${tooltipPos.x}px`, 
            top: `${tooltipPos.y}px`,
            transform: 'translate(0, 0)', zIndex: 100, pointerEvents: 'none',
            background: 'rgba(23, 23, 31, 0.96)', border: `1px solid ${getFactionColor(hoveredNode.owner)}`,
            borderRadius: '6px', padding: '12px', minWidth: '220px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px', borderBottom: '1px solid #2a2a38', paddingBottom: '4px' }}>
              <span style={{ fontSize: '14px', fontWeight: 'bold', color: '#fff' }}>{hoveredNode.name}</span>
              {hoveredNode.is_capital && <span style={{ fontSize: '10px', background: '#e0b65c', color: '#0f0f14', padding: '1px 4px', borderRadius: '3px', fontWeight: 'bold' }}>수도 👑</span>}
            </div>
            
            <div style={{ fontSize: '11px', color: '#7a7a90', marginBottom: '8px' }}>
              소유 세력: <span style={{ color: getFactionColor(hoveredNode.owner), fontWeight: 'bold' }}>{hoveredNode.owner || '중립 영토 (Neutral)'}</span>
            </div>

            {/* 자원 상태 내역 */}
            <div style={{ marginBottom: '6px' }}>
              <div style={{ fontSize: '10px', fontWeight: 'bold', color: '#7a7a90', textTransform: 'uppercase', marginBottom: '2px' }}>📊 보유 자원</div>
              {hoveredNode.resources && Object.keys(hoveredNode.resources).length > 0 ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', fontSize: '11px' }}>
                  {Object.entries(hoveredNode.resources).map(([resName, val]) => (
                    <span key={resName} style={{ background: '#1e1e28', padding: '2px 5px', borderRadius: '3px', color: '#eeeef8' }}>
                      {resName}: <b>{typeof val === 'number' ? val.toFixed(0) : String(val)}</b>
                    </span>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '11px', color: '#555566', fontStyle: 'italic' }}>자원 데이터 없음</div>
              )}
            </div>

            {/* 영토 특징 */}
            <div>
              <div style={{ fontSize: '10px', fontWeight: 'bold', color: '#7a7a90', textTransform: 'uppercase', marginBottom: '2px' }}>⛰️ 지역 특징</div>
              {hoveredNode.features && hoveredNode.features.length > 0 ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                  {hoveredNode.features.map((feat, idx) => (
                    <span key={idx} style={{ background: '#252530', color: '#5c9ee0', fontSize: '10px', padding: '1px 4px', borderRadius: '3px' }}>
                      #{feat}
                    </span>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '11px', color: '#555566', fontStyle: 'italic' }}>특징적 지형 없음</div>
              )}
            </div>
          </div>
        )}

        {/* 플로팅 줌 컨트롤러 패널 */}
        <div style={{
          position: 'absolute', top: '16px', right: '16px', display: 'flex', flexDirection: 'column',
          gap: '6px', background: '#17171f', border: '1px solid #2a2a38', padding: '6px', borderRadius: '6px', zIndex: 10
        }}>
          <button onClick={zoomIn} style={{ width: '32px', height: '32px', background: '#252530', border: '1px solid #2a2a38', color: '#eeeef8', cursor: 'pointer', borderRadius: '4px', fontWeight: 'bold' }}>+</button>
          <button onClick={zoomOut} style={{ width: '32px', height: '32px', background: '#252530', border: '1px solid #2a2a38', color: '#eeeef8', cursor: 'pointer', borderRadius: '4px', fontWeight: 'bold' }}>−</button>
          <button onClick={resetZoom} style={{ width: '32px', height: '32px', background: '#252530', border: '1px solid #2a2a38', color: '#5c9ee0', cursor: 'pointer', borderRadius: '4px', fontSize: '11px', fontWeight: 'bold' }}>리셋</button>
        </div>
      </main>

      {/* ── BOTTOM DASHBOARD PANELS ── */}
      <section style={{
        gridArea: 'bottom', background: '#17171f', borderTop: '1px solid #2a2a38',
        display: 'grid', gridTemplateColumns: '1fr 260px 240px', overflow: 'hidden'
      }}>
        
        {/* 서사 연대기 연동 해설 */}
        <div style={{ padding: '14px 16px', overflowY: 'auto', borderRight: '1px solid #2a2a38' }}>
          <div style={{ fontSize: '10px', fontWeight: 'bold', color: '#7a7a90', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px', borderBottom: '1px solid #2a2a38', paddingBottom: '4px' }}>
            📜 기록관의 연대기 해설 (NARRATION)
          </div>
          <div style={{ fontSize: '13px', lineHeight: '1.7', color: '#cbd5e1', whiteSpace: 'pre-wrap' }}>
            {renderNarrationContent()}
          </div>
        </div>

        {/* 세력권 스탯 현황 */}
        <div style={{ padding: '14px 16px', overflowY: 'auto', borderRight: '1px solid #2a2a38' }}>
          <div style={{ fontSize: '10px', fontWeight: 'bold', color: '#7a7a90', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px', borderBottom: '1px solid #2a2a38', paddingBottom: '4px' }}>
            👑 세력권 스탯 현황 (ENTITIES)
          </div>
          <div>
            {snapshot ? (
              snapshot.entity_snapshot?.map((ent) => (
                <div key={ent.name} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 0', borderBottom: '1px solid #2a2a38' }}>
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: getFactionColor(ent.name) }} />
                  <span style={{ fontSize: '12px', fontWeight: 500, color: ent.alive ? '#eeeef8' : '#7a7a90', textDecoration: ent.alive ? 'none' : 'line-through', flex: 1, overflow:'hidden', textOverflow:'ellipsis' }}>
                    {ent.name}
                  </span>
                  <span style={{ fontSize: '11px', color: '#7a7a90', background: '#0f0f14', padding: '1px 5px', borderRadius: '3px', fontFamily: 'monospace' }}>
                    {ent.alive ? `영토 ${ent.node_count}개` : '멸망'}
                  </span>
                </div>
              ))
            ) : (
              <div style={{ color: '#555566', fontStyle: 'italic', fontSize: '12px', marginTop: '10px' }}>스냅샷 데이터 없음</div>
            )}
          </div>
        </div>

        {/* 외교 관계 현황 */}
        <div style={{ padding: '14px 16px', overflowY: 'auto' }}>
          <div style={{ fontSize: '10px', fontWeight: 'bold', color: '#7a7a90', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px', borderBottom: '1px solid #2a2a38', paddingBottom: '4px' }}>
            🤝 외교 관계 현황 (RELATIONS)
          </div>
          <div>
            {snapshot?.relations_snapshot ? (
              Object.entries(snapshot.relations_snapshot).flatMap(([actor, targets]) => 
                Object.entries(targets).map(([target, score]) => {
                  if (actor >= target) return null;
                  const { pct, color } = getRelationStyle(score);
                  return (
                    <div key={`${actor}-${target}`} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '4px 0', borderBottom: '1px solid #2a2a38', fontSize: '11px' }}>
                      <div style={{ flex: 1, color: '#d4d4e0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {actor.substring(0,5)} ↔ {target.substring(0,5)}
                      </div>
                      <div style={{ width: '46px', height: '5px', background: '#0f0f14', borderRadius: '3px', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: color }} />
                      </div>
                      <div style={{ fontFamily: 'monospace', width: '32px', textAlign: 'right', color: color, fontWeight: 'bold' }}>
                        {score > 0 ? `+${score.toFixed(1)}` : score.toFixed(1)}
                      </div>
                    </div>
                  );
                })
              )
            ) : (
              <div style={{ color: '#555566', fontStyle: 'italic', fontSize: '11px', marginTop: '10px' }}>
                외교 스냅샷 없음
              </div>
            )}
          </div>
        </div>

      </section>
    </div>
  );
}