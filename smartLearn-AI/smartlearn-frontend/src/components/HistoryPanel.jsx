export default function HistoryPanel({ sessions, activeChatId, onSelect }) {
  if (sessions.length === 0) return null;

  return (
    <div className="history-panel">
      <div className="history-title">历史记录</div>
      <ul className="history-list">
        {sessions.map((s) => (
          <li
            key={s.chat_id}
            className={`history-item ${s.chat_id === activeChatId ? 'history-active' : ''}`}
            onClick={() => onSelect(s.chat_id)}
            title={s.filename}
          >
            <span className="history-filename">{s.filename}</span>
            <span className="history-meta">{s.pages} 页</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
