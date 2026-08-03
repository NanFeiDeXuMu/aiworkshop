import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { askQuestionStream } from '../api.js';

export default function ChatPanel({ chatId, enabled, onBusy, disabled, onJumpToPage, initialMessages, onMessagesChange }) {
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState(initialMessages || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);

  // Sync store → local when restoring a session with more messages
  useEffect(() => {
    if (initialMessages && initialMessages.length > messages.length) {
      setMessages(initialMessages);
    }
  }, [initialMessages, messages.length]);

  // Persist messages on unmount (session switch) — includes partial
  // streaming messages so nothing is lost.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  useEffect(() => {
    return () => {
      onMessagesChange?.(messagesRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Notify parent when busy state changes
  useEffect(() => {
    onBusy?.(loading);
  }, [loading, onBusy]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // A ref to persistMessages so async callbacks (which may fire after
  // unmount) can still save the final result to the store.
  const persistRef = useRef(onMessagesChange);
  persistRef.current = onMessagesChange;

  const handleSend = async (e) => {
    e.preventDefault();
    const text = message.trim();
    if (!text || !enabled || loading) return;
    setMessage('');
    setError(null);

    const userMsg = { role: 'user', content: text };
    let streamMsg = null; // built by onChunk, read by onDone for persistence

    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    // Start a fresh assistant bubble that grows with each chunk.
    // We intentionally do NOT abort on unmount so the stream survives
    // session switches.  Callbacks use persistRef to save even post‑unmount.
    askQuestionStream(chatId, text, {
      onChunk: (content) => {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.streaming) {
            const updated = { ...last, content: last.content + content };
            streamMsg = updated;
            return [...prev.slice(0, -1), updated];
          }
          const first = { role: 'assistant', content, citations: [], sources: [], streaming: true };
          streamMsg = first;
          return [...prev, first];
        });
      },
      onDone: (citations) => {
        const finalMsg = { ...(streamMsg || {}), citations, streaming: false, role: 'assistant' };
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.streaming) {
            return [...prev.slice(0, -1), finalMsg];
          }
          return prev;
        });

        // Persist directly (not via setMessages) so it works even after
        // the component has unmounted due to a session switch.
        const snapshot = [...(messagesRef.current || [])];
        const last = snapshot[snapshot.length - 1];
        if (last && last.role === 'assistant' && last.streaming) {
          snapshot[snapshot.length - 1] = finalMsg;
        }
        persistRef.current?.(snapshot);
        setLoading(false);
      },
      onError: (msg) => {
        setError(msg);
        setLoading(false);
      },
    });
  };

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.length === 0 && !loading && (
          <div className="chat-empty">
            <span>💬 开始提问吧</span>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} onJumpToPage={onJumpToPage} />
        ))}
        {error && <div className="chat-error">{error}</div>}
        <div ref={bottomRef} />
      </div>

      <form className="chat-input-row" onSubmit={handleSend}>
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={enabled ? '输入你的问题...' : '请先上传 PDF'}
          disabled={disabled || loading}
        />
        <button type="submit" disabled={!enabled || !message.trim() || loading}>
          发送
        </button>
      </form>
    </div>
  );
}

function MessageBubble({ msg, onJumpToPage }) {
  const isUser = msg.role === 'user';
  const isStreaming = msg.streaming === true;

  return (
    <div className={`chat-bubble ${isUser ? 'chat-user' : 'chat-assistant'}`}>
      <div className="chat-bubble-role">{isUser ? '' : '🤖 助手'}</div>
      {isUser ? (
        <div className="chat-bubble-text">{msg.content}</div>
      ) : (
        <>
          <div className="chat-bubble-text markdown-body">
            <ReactMarkdown
              remarkPlugins={[remarkMath]}
              rehypePlugins={[rehypeKatex]}
            >
              {msg.content}
            </ReactMarkdown>
            {isStreaming && <span className="cursor-blink">▍</span>}
          </div>
          {!isStreaming && msg.citations && msg.citations.length > 0 && (
            <div className="chat-citations">
              {msg.citations.map((page) => (
                <button
                  key={page}
                  className="citation-chip"
                  onClick={() => onJumpToPage?.(page)}
                  title={`跳转到第 ${page} 页`}
                >
                  第 {page} 页
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
