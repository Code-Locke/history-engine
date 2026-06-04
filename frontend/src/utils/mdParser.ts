
export interface TurnNarrative {
  turn: number;
  title: string;
  content: string;
}

export function parseMarkdownNarrative(mdText: string): TurnNarrative[] {
  const narratives: TurnNarrative[] = [];
  
  // "## Turn X: 타이틀" 패턴 또는 "## 턴 X: 타이틀" 패턴 매칭
  const turnRegex = /##\s+(?:Turn|턴)\s+(\d+):\s*(.*)/gi;
  const sections = mdText.split(turnRegex);
  
  // split 결과: [공백/서문, "Turn 번호", "타이틀", "본문 내용", ...]
  for (let i = 1; i < sections.length; i += 3) {
    const turn = parseInt(sections[i], 10);
    const title = sections[i + 1].trim();
    const content = sections[i + 2].trim();
    
    narratives.push({
      turn,
      title,
      content
    });
  }
  
  return narratives.sort((a, b) => a.turn - b.turn);
}