const API_BASE = import.meta.env.VITE_API_URL || '';

/** Module-level chat context — set once per upload session. */
let _chatId = null;

/**
 * Configure the chat context so `askQuestion(message)` knows which
 * document to query.  Call this from the parent after a successful upload.
 */
export function setChatContext(chatId) {
  _chatId = chatId;
}

export async function uploadPdf(file, chatId) {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    headers: { 'X-Chat-Id': chatId },
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Upload failed (${res.status})`);
  }

  return res.json();
}

/**
 * Ask one question against the current chat session.
 * @param {string} message — the user's question
 * @param {string} [chatId] — optional explicit chat session (overrides module context)
 * @returns {Promise<{answer: string, citations: number[], sources: object[]}>}
 */
export async function askQuestion(message, chatId) {
  const effectiveChatId = chatId || _chatId;
  if (!effectiveChatId) throw new Error('No active chat session — upload a PDF first');
  const params = new URLSearchParams({ chat_id: effectiveChatId, question: message });
  const res = await fetch(`${API_BASE}/ask?${params}`);

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ask failed (${res.status})`);
  }

  return res.json();
}

/**
 * Stream a question to the backend via SSE.
 */
export async function askQuestionStream(chatId, question, { onChunk, onDone, onError, signal }) {
  const params = new URLSearchParams({ chat_id: chatId, question });
  const res = await fetch(`${API_BASE}/ask/stream?${params}`, { signal });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ask failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const jsonStr = line.slice(6);
        try {
          const data = JSON.parse(jsonStr);
          if (data.type === 'chunk') {
            onChunk(data.content);
          } else if (data.type === 'done') {
            finished = true;
            onDone(data.citations || []);
          } else if (data.type === 'error') {
            finished = true;
            onError(data.detail || 'Unknown error');
          }
        } catch {
          // Skip unparseable JSON lines
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      return;
    }
    finished = true;
    onError(e.message);
  }

  if (!finished) {
    onError('连接中断，请检查后端服务是否运行');
  }
}

/**
 * Multi-turn chat message via POST /chat.
 * Returns { answer, citations, sources }.
 */
export async function chatMessage(chatId, message) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, message }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Chat failed (${res.status})`);
  }

  return res.json();
}
