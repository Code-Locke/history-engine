import React, { useState, useEffect, useMemo, useRef } from 'react';

// world.json의 상세 국가 노드 규격 반영
interface NodeData {
  id: string | number;
  name: string;
  x?: number;
  y?: number;
  color?: string;
  is_capital?: boolean;
  features?: string[] | string;
  resources?: any; 
  explored_by?: string[] | string;
  description?: string;
  [key: string]: any; 
}

interface EdgeData {
  source: string | number;
  target: string | number;
}

interface WorldData {
  theme?: string;
  seed?: number;
  totalTurns?: number;
  nodes?: NodeData[];
  edges?: EdgeData[];
  [key: string]: any; 
}

interface ParsedTurn {
  id: number;
  title: string;
  content: string;
}

const DEFAULT_MAP_DATA: WorldData = {
  theme: "fantasyStyle",
  seed: 395558637,
  nodes: [
    { id: "node_1", name: "Thornhold", color: '#f59e0b', is_capital: true, features: ["Mountain Fortress"], resources: ["iron", "grain"], explored_by: ["Thornhold Pioneer"] },
    { id: "node_2", name: "Dawngate", color: '#3b82f6', is_capital: false, features: ["Coastal Port"], resources: ["grain", "gold", "gold"], explored_by: ["Dawngate Scout"] },
    { id: "node_3", name: "Drakeanhaven", color: '#374151', is_capital: true, features: ["Dragon Nest"], resources: { iron: 3, timber: 1, gold: 5 }, explored_by: ["Royal Expedition"] },
    { id: "node_4", name: "Ravenilkeep", color: '#10b981', is_capital: false, features: ["Dense Forest"], resources: "timber, timber", explored_by: ["Raven Ranger"] },
    { id: "node_5", name: "Grimarmark", color: '#dc2626', is_capital: false, features: ["Glacier Valley"], resources: ["iron", "gold"], explored_by: ["Tundra Nomad"] }
  ],
  edges: [
    { source: "node_1", target: "node_2" },
    { source: "node_3", target: "node_4" },
    { source: "node_2", target: "node_5" }
  ]
};

function App() {
  const [worldData, setWorldData] = useState<WorldData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [zoomScale, setZoomScale] = useState<number>(1);
  
  // 선택된 노드의 ID 상태 관리
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  
  const [turns, setTurns] = useState<ParsedTurn[]>([]);
  const [selectedTurn, setSelectedTurn] = useState<number>(1);

  const viewportRef = useRef<HTMLDivElement>(null);

  const handleZoomIn = () => setZoomScale(prev => Math.min(prev + 0.1, 2.5));
  const handleZoomOut = () => setZoomScale(prev => Math.max(prev - 0.1, 0.4));
  const handleZoomReset = () => setZoomScale(1.0);

  // 마크다운 파서
  const parseMarkdownTurns = (text: string): ParsedTurn[] => {
    if (!text) return [];
    const turnSections = text.split(/(?=###\s*Turn\s*\d+)/i);
    const parsed: ParsedTurn[] = [];

    turnSections.forEach((section) => {
      const trimmed = section.trim();
      if (!trimmed) return;

      const match = trimmed.match(/###\s*Turn\s*(\d+)/i);
      if (match) {
        const turnId = parseInt(match[1], 10);
        const lines = trimmed.split('\n');
        const firstLine = lines[0].replace(/###/g, '').trim();
        const contentBody = lines.slice(1).join('\n').trim();

        parsed.push({
          id: turnId,
          title: firstLine,
          content: contentBody
        });
      }
    });
    return parsed.sort((a, b) => a.id - b.id);
  };

  // 데이터 보정 및 좌표 비중첩 정렬 가동
  const positionedNodes = useMemo(() => {
    if (!worldData || !worldData.nodes) return [];
    const rawNodes = worldData.nodes;
    const finalNodes: Array<NodeData & { x: number; y: number }> = [];
    
    const minX = 250, maxX = 950;
    const minY = 200, maxY = 700;
    const MIN_DISTANCE = 170; 

    rawNodes.forEach((node, index) => {
      const safeId = node.id !== undefined && node.id !== null ? String(node.id) : `node_idx_${index}_${node.name || 'unknown'}`;
      const safeName = node.name || `Region ${safeId}`;

      if (node.x !== undefined && node.y !== undefined) {
        finalNodes.push({ ...node, id: safeId, name: safeName, x: node.x, y: node.y });
        return;
      }

      let placed = false;
      let attempts = 0;
      let randX = 0;
      let randY = 0;

      while (!placed && attempts < 500) {
        randX = Math.floor(Math.random() * (maxX - minX + 1)) + minX;
        randY = Math.floor(Math.random() * (maxY - minY + 1)) + minY;

        const isTooClose = finalNodes.some((placedNode) => {
          const dx = placedNode.x - randX;
          const dy = placedNode.y - randY;
          return Math.sqrt(dx * dx + dy * dy) < MIN_DISTANCE;
        });

        if (!isTooClose) {
          placed = true;
        }
        attempts++;
      }
      finalNodes.push({ ...node, id: safeId, name: safeName, x: randX, y: randY });
    });
    return finalNodes;
  }, [worldData]);

  // 선택된 국가 세부 데이터 바인딩
  const activeNodeDetails = useMemo(() => {
    if (!selectedNodeId) return null;
    return positionedNodes.find(n => String(n.id) === String(selectedNodeId)) || null;
  }, [selectedNodeId, positionedNodes]);

  // 정중앙 시작 스크롤 포커싱
  useEffect(() => {
    if (!loading && viewportRef.current && positionedNodes.length > 0) {
      const sumX = positionedNodes.reduce((acc, n) => acc + n.x, 0);
      const sumY = positionedNodes.reduce((acc, n) => acc + n.y, 0);
      const centerX = sumX / positionedNodes.length;
      const centerY = sumY / positionedNodes.length;

      const clientWidth = viewportRef.current.clientWidth;
      const clientHeight = viewportRef.current.clientHeight;

      viewportRef.current.scrollLeft = centerX - clientWidth / 2;
      viewportRef.current.scrollTop = centerY - clientHeight / 2;
    }
  }, [loading, positionedNodes]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [worldRes, chronicleRes] = await Promise.all([
        fetch('http://localhost:8000/data/world.json'),
        fetch('http://localhost:8000/data/latest-chronicle')
      ]);

      if (!worldRes.ok) throw new Error('world.json 로딩 실패');

      const worldJson = await worldRes.json();
      
      if (!worldJson.nodes) {
        worldJson.nodes = worldJson.factions || worldJson.locations || DEFAULT_MAP_DATA.nodes;
        worldJson.edges = worldJson.connections || DEFAULT_MAP_DATA.edges;
      }
      setWorldData(worldJson);

      if (chronicleRes.ok) {
        const mdText = await chronicleRes.text();
        const parsedTurns = parseMarkdownTurns(mdText);
        setTurns(parsedTurns);
        if (parsedTurns.length > 0) {
          setSelectedTurn(parsedTurns[0].id);
        }
      }
    } catch (err: any) {
      setWorldData(DEFAULT_MAP_DATA);
      const fallbackMarkdown = `### Turn 1 / 100\n| Entity | Action |\n| Thornhold | PROPOSE_TREATY |`;
      setTurns(parseMarkdownTurns(fallbackMarkdown));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const edges = worldData?.edges || [];
  const currentTurnData = turns.find(t => t.id === selectedTurn);

  const renderAttribute = (attr: any) => {
    if (!attr) return 'None';
    if (Array.isArray(attr)) return attr.join(', ');
    return String(attr);
  };

  // 💡 [수정 핵심] 보유 여부 문구 대신 수량(숫자)을 집계하여 출력하는 자원 해석기
  const renderCategorizedResources = (node: NodeData) => {
    const rawData = node.resources ?? node.resource ?? node.productions ?? node.resource_list ?? null;
    
    // 자원별 수량을 보관할 맵 초기화
    const resourceCounts: Record<string, number> = {
      grain: 0,
      iron: 0,
      timber: 0,
      gold: 0
    };

    let debugRawString = '';

    if (rawData) {
      if (Array.isArray(rawData)) {
        // 1. 배열 형태일 때: ["iron", "grain", "iron"] -> 각 아이템 개수 합산
        debugRawString = JSON.stringify(rawData);
        rawData.forEach(item => {
          const name = String(item).toLowerCase().trim();
          Object.keys(resourceCounts).forEach(key => {
            if (name.includes(key)) {
              resourceCounts[key] += 1;
            }
          });
        });
      } else if (typeof rawData === 'object') {
        // 2. 객체 형태일 때: { iron: 4, gold: 2 } -> 선언된 숫자 값을 직접 맵핑
        debugRawString = JSON.stringify(rawData);
        Object.keys(rawData).forEach(k => {
          const name = k.toLowerCase().trim();
          const value = Number(rawData[k]);
          Object.keys(resourceCounts).forEach(key => {
            if (name.includes(key) && !isNaN(value)) {
              resourceCounts[key] += value;
            }
          });
        });
      } else {
        // 3. 문자열 형태일 때: "grain, iron, grain" -> 단어 등장 빈도나 숫자 매칭 파싱
        const strVal = String(rawData).toLowerCase();
        debugRawString = strVal;
        Object.keys(resourceCounts).forEach(key => {
          // 단순 쉼표 분할 기반 빈도 측정 fallback
          const matches = strVal.split(key).length - 1;
          if (matches > 0) {
            // 만약 "iron: 5" 처럼 숫자가 포함되어 있는지 정규식 체크 후 가중치 부여
            const regex = new RegExp(`${key}\\s*[:=]\\s*(\\d+)`);
            const matchResult = strVal.match(regex);
            if (matchResult && matchResult[1]) {
              resourceCounts[key] = parseInt(matchResult[1], 10);
            } else {
              resourceCounts[key] = matches;
            }
          }
        });
      }
    }

    const targetResources = ['grain', 'iron', 'timber', 'gold'];
    
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '4px' }}>
        {targetResources.map((res) => {
          const count = resourceCounts[res];
          const hasResource = count > 0;
          
          const labelMap: Record<string, { name: string, color: string, icon: string }> = {
            grain: { name: '곡물 (Grain)', color: '#eab308', icon: '🌾' },
            iron: { name: '철광 (Iron)', color: '#64748b', icon: '⛏️' },
            timber: { name: '목재 (Timber)', color: '#15803d', icon: '🪵' },
            gold: { name: '황금 (Gold)', color: '#d97706', icon: '🪙' }
          };

          return (
            <div key={`res-item-${res}`} style={{ display: 'flex', alignItems: 'center', gap: '6px', opacity: hasResource ? 1 : 0.35 }}>
              <span>{labelMap[res].icon}</span>
              <span style={{ fontWeight: hasResource ? 'bold' : 'normal', color: hasResource ? labelMap[res].color : '#94a3b8' }}>
                {labelMap[res].name}: 
              </span>
              <span style={{ 
                fontSize: '0.85rem', 
                fontWeight: '900', 
                color: hasResource ? '#111827' : '#94a3b8',
                backgroundColor: hasResource ? '#e2e8f0' : 'transparent',
                padding: hasResource ? '1px 6px' : '0',
                borderRadius: '4px'
              }}>
                {count}
              </span>
            </div>
          );
        })}
        <div style={{ fontSize: '0.65rem', color: '#cbd5e1', marginTop: '6px' }}>
          데이터 원본 원시 로그: {debugRawString || 'None'}
        </div>
      </div>
    );
  };

  // 공백 클릭 핸들러
  const handleBackgroundClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      setSelectedNodeId(null);
    }
  };

  return (
    <div className="full-page-container">
      
      {/* ─── 🗺️ [왼쪽 구역] Live 월드맵 시각화 ─── */}
      <main className="main-content">
        <header className="main-header">
          <h1 style={{ fontSize: '1.3rem', fontWeight: '900', margin: 0, color: '#111827' }}>
            HISTORY_ENGINE // LIVE WORLD MAP VISUALIZER
          </h1>
          <p style={{ margin: '4px 0 0 0', fontSize: '0.8rem', color: '#6b7280' }}>
            SEED: {worldData?.seed} | THEME: {worldData?.theme}
          </p>
        </header>

        <div 
          ref={viewportRef}
          className="graph-viewport" 
          style={{ overflow: 'auto', position: 'relative', width: '100%', height: '100%' }}
        >
          <div 
            onClick={handleBackgroundClick}
            style={{ 
              transform: `scale(${zoomScale})`, 
              transformOrigin: 'top left',
              transition: 'transform 0.1s ease-out',
              width: '1400px', 
              height: '1000px',
              position: 'relative',
              backgroundSize: '40px 40px',
              backgroundImage: 'linear-gradient(to right, #f1f5f9 1px, transparent 1px), linear-gradient(to bottom, #f1f5f9 1px, transparent 1px)',
              cursor: 'default'
            }}
          >
            {/* 🔗 관계선 레이어 */}
            <svg style={{ position: 'absolute', width: '100%', height: '100%', pointerEvents: 'none', zIndex: 1 }}>
              {edges.map((edge, index) => {
                const sourceId = edge.source !== undefined && edge.source !== null ? String(edge.source) : '';
                const targetId = edge.target !== undefined && edge.target !== null ? String(edge.target) : '';

                const sourceNode = positionedNodes.find(n => String(n.id) === sourceId);
                const targetNode = positionedNodes.find(n => String(n.id) === targetId);
                if (!sourceNode || !targetNode) return null;
                return (
                  <line
                    key={`edge-${index}`}
                    x1={sourceNode.x}
                    y1={sourceNode.y}
                    x2={targetNode.x}
                    y2={targetNode.y}
                    stroke="#cbd5e1"
                    strokeWidth="2.5"
                    strokeDasharray="4,4"
                  />
                );
              })}
            </svg>

            {/* 🟠 국가 노드 레이어 */}
            {positionedNodes.map((node) => {
              const isSelected = selectedNodeId !== null && String(selectedNodeId) === String(node.id);
              const currentFixedId = String(node.id); 

              return (
                <div
                  key={`map-node-${currentFixedId}`}
                  onClick={(e) => {
                    e.stopPropagation(); 
                    setSelectedNodeId(currentFixedId);
                  }}
                  style={{
                    position: 'absolute',
                    left: `${node.x}px`,
                    top: `${node.y}px`,
                    transform: 'translate(-50%, -50%)',
                    zIndex: 2,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    cursor: 'pointer'
                  }}
                >
                  <div
                    style={{
                      width: isSelected ? '32px' : '24px',
                      height: isSelected ? '24px' : '24px',
                      backgroundColor: node.color || '#3b82f6',
                      borderRadius: '50%',
                      border: isSelected ? '4px solid #111827' : '3px solid #ffffff',
                      boxShadow: isSelected ? '0 0 12px rgba(0,0,0,0.4)' : '0 4px 6px rgba(0,0,0,0.15)',
                      transition: 'all 0.15s ease-out'
                    }}
                  />
                  <span
                    style={{
                      marginTop: '6px',
                      backgroundColor: isSelected ? '#111827' : 'rgba(255, 255, 255, 0.95)',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      fontSize: '0.8rem',
                      fontWeight: 'bold',
                      color: isSelected ? '#ffffff' : '#0f172a',
                      border: '1px solid #cbd5e1',
                      whiteSpace: 'nowrap',
                      boxShadow: '0 2px 4px rgba(0,0,0,0.05)'
                    }}
                  >
                    {node.name} {node.is_capital ? '👑' : ''}
                  </span>
                </div>
              );
            })}
          </div>

          {/* 확대/축소 패널 */}
          <div 
            style={{
              position: 'fixed',
              bottom: '24px',
              right: '384px',
              display: 'flex',
              gap: '8px',
              background: 'white',
              padding: '8px',
              borderRadius: '8px',
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              zIndex: 10,
              transition: 'right 0.2s ease-out'
            }}
          >
            <button className="zoom-btn" onClick={handleZoomOut}>-</button>
            <span className="zoom-info" onClick={handleZoomReset} style={{ cursor: 'pointer', userSelect: 'none', fontWeight: 'bold' }}>
              {Math.round(zoomScale * 100)}%
            </span>
            <button className="zoom-btn" onClick={handleZoomIn}>+</button>
          </div>
        </div>
      </main>

      {/* ─── 📦 [오른쪽 구역] 사이드바 전체 레이아웃 ─── */}
      <aside className="sidebar-right">
        
        <section className="turn-select-zone" style={{ height: '25%', minHeight: '180px', overflowY: 'auto' }}>
          <h3 style={{ fontSize: '0.9rem', fontWeight: 'bold', color: '#111827', margin: '0 0 12px 0' }}>
            ⏱️ 턴 선택 구역 ({turns.length}개 턴 검색됨)
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {turns.map((turn) => (
              <button
                key={`turn-btn-${turn.id}`}
                onClick={() => setSelectedTurn(turn.id)}
                style={{
                  textAlign: 'left',
                  padding: '10px 12px',
                  background: selectedTurn === turn.id ? '#111827' : '#ffffff',
                  color: selectedTurn === turn.id ? '#ffffff' : '#374151',
                  border: '1px solid #d1d5db',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  fontSize: '0.82rem',
                  fontWeight: selectedTurn === turn.id ? 'bold' : 'normal',
                  transition: 'all 0.1s ease',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis'
                }}
              >
                {selectedTurn === turn.id ? '📍 ' : ''}{turn.title}
              </button>
            ))}
          </div>
        </section>

        {/* 📋 국가 세부 정보 연동 구역 */}
        <section style={{ padding: '16px 20px', borderBottom: '2px solid #111827', backgroundColor: '#f8fafc', height: '35%', overflowY: 'auto' }}>
          <h3 style={{ fontSize: '0.9rem', fontWeight: 'bold', color: '#111827', margin: '0 0 10px 0' }}>
            🔍 선택된 국가 세부 정보
          </h3>
          {activeNodeDetails ? (
            <div style={{ fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <p style={{ margin: 0 }}><strong>국가명:</strong> <span style={{ color: activeNodeDetails.color, fontWeight: 'bold' }}>{activeNodeDetails.name}</span></p>
              <p style={{ margin: 0 }}><strong>ID:</strong> {activeNodeDetails.id} | <strong>좌표:</strong> ({Math.round(activeNodeDetails.x || 0)}, {Math.round(activeNodeDetails.y || 0)})</p>
              <p style={{ margin: 0 }}><strong>수도 여부 (is_capital):</strong> {activeNodeDetails.is_capital ? '👑 Yes (Capital)' : '❌ No'}</p>
              
              <div style={{ marginTop: '4px', borderTop: '1px dashed #cbd5e1', paddingTop: '6px' }}>
                <p style={{ margin: '0 0 2px 0' }}><strong>특징 (features):</strong></p>
                <span style={{ color: '#0284c7', fontWeight: '500' }}>{renderAttribute(activeNodeDetails.features)}</span>
              </div>

              {/* 자원 분석 출력기 연동 */}
              <div>
                <p style={{ margin: '0 0 2px 0' }}><strong>자원 현황 (resources):</strong></p>
                {renderCategorizedResources(activeNodeDetails)}
              </div>

              <div style={{ marginTop: '4px' }}>
                <p style={{ margin: '0 0 2px 0' }}><strong>탐색 주체 (explored_by):</strong></p>
                <span style={{ color: '#4b5563', fontStyle: 'italic' }}>{renderAttribute(activeNodeDetails.explored_by)}</span>
              </div>
            </div>
          ) : (
            <p style={{ margin: 0, fontSize: '0.8rem', color: '#94a3b8', lineHeight: '1.5' }}>
              지도의 동그라미 노드를 클릭하면 해당 월드의 속성(features, resources 등)이 여기에 실시간 표출됩니다.
            </p>
          )}
        </section>

        {/* 👇 턴 진행 상황 리포트 구역 */}
        <section className="turn-progress-zone" style={{ height: '40%', flexGrow: 1 }}>
          <h3 style={{ fontSize: '0.9rem', fontWeight: 'bold', color: '#111827', margin: 0 }}>
            📊 턴 진행 상황 및 결과 리포트
          </h3>
          
          <div style={{ background: '#ffffff', padding: '8px 12px', borderRadius: '6px', border: '1px solid #e5e7eb', fontSize: '0.82rem' }}>
            <strong>동기화 타깃:</strong> <span style={{ color: '#2563eb', fontWeight: 'bold' }}>{currentTurnData ? currentTurnData.title : `Turn ${selectedTurn}`}</span>
          </div>

          <div style={{ 
            fontSize: '0.82rem', 
            color: '#1e293b', 
            lineHeight: '1.5', 
            whiteSpace: 'pre-wrap', 
            backgroundColor: '#ffffff', 
            padding: '12px', 
            borderRadius: '6px', 
            border: '1px solid #e5e7eb', 
            flexGrow: 1, 
            overflowY: 'auto',
            fontFamily: 'monospace'
          }}>
            {currentTurnData ? currentTurnData.content : "표출할 마크다운 로그 본문이 존재하지 않습니다."}
          </div>
        </section>

      </aside>

    </div>
  );
}

export default App;