import { useRef, useEffect, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

/**
 * If the text ends with an unclosed $ formula (odd number of $),
 * strip everything from the last $ to avoid KaTeX parse errors.
 * Returns { safeText, clipped } — clipped is the stripped suffix or "".
 */
function sanitizePartialLatex(text) {
  // Find last $ position
  const lastDollar = text.lastIndexOf('$');
  if (lastDollar === -1) return { safeText: text, clipped: '' };

  // Count $ from start to lastDollar (inclusive)
  let count = 0;
  for (let i = 0; i <= lastDollar; i++) {
    if (text[i] === '$') count++;
  }

  if (count % 2 === 0) {
    // Even count — all formulas are closed (including $$ blocks)
    return { safeText: text, clipped: '' };
  }

  // Odd count — unclosed formula. Strip from last $ onward.
  return {
    safeText: text.slice(0, lastDollar),
    clipped: text.slice(lastDollar),
  };
}

export default function AnswerDisplay({ answer, citations, filename, isStreaming }) {
  const bottomRef = useRef(null);

  // Auto-scroll to bottom when content grows
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [answer]);

  const { safeText, clipped } = useMemo(() => {
    if (!answer) return { safeText: '', clipped: '' };
    return sanitizePartialLatex(answer);
  }, [answer]);

  if (!answer) return null;

  return (
    <div className="answer-display">
      <div className="answer-header">
        <span>📄 {filename}</span>
        {isStreaming && <span className="streaming-badge">生成中...</span>}
      </div>
      <div className="answer-body">
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {safeText}
        </ReactMarkdown>
        {clipped && <span className="partial-latex">{clipped}</span>}
        {isStreaming && !clipped && <span className="cursor-blink">▍</span>}
      </div>
      {citations && citations.length > 0 && (
        <div className="answer-citations">
          引用页码：{citations.map((p) => `第 ${p} 页`).join('、')}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
