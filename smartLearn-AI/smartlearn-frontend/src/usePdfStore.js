import { useState, useCallback } from 'react';

const SESSIONS_KEY = 'smartlearn_sessions';
const ACTIVE_KEY = 'smartlearn_active_chat_id';
const MESSAGES_KEY = 'smartlearn_messages';

function loadJSON(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage full or unavailable — silent degrade
  }
}

function loadSessions() {
  return loadJSON(SESSIONS_KEY) || [];
}

function loadActiveChatId() {
  return localStorage.getItem(ACTIVE_KEY) || null;
}

function loadMessages() {
  return loadJSON(MESSAGES_KEY) || {};
}

export function usePdfStore() {
  const [sessions, setSessions] = useState(loadSessions);
  const [activeChatId, setActiveChatId] = useState(loadActiveChatId);
  const [messagesByChatId, setMessagesByChatId] = useState(loadMessages);

  // ---- sessions ----
  const addSession = useCallback((chatId, result) => {
    const session = {
      chat_id: chatId,
      filename: result.filename,
      pages: result.pages,
      uploadedAt: Date.now(),
    };
    setSessions((prev) => {
      const next = [session, ...prev];
      saveJSON(SESSIONS_KEY, next);
      return next;
    });
    setActiveChatId(chatId);
    localStorage.setItem(ACTIVE_KEY, chatId);
  }, []);

  const switchSession = useCallback((chatId) => {
    setActiveChatId(chatId);
    localStorage.setItem(ACTIVE_KEY, chatId);
  }, []);

  // ---- messages cache ----
  const updateMessages = useCallback((chatId, updater) => {
    setMessagesByChatId((prev) => {
      const current = prev[chatId] || [];
      const messages = typeof updater === 'function' ? updater(current) : updater;
      const next = { ...prev, [chatId]: messages };
      saveJSON(MESSAGES_KEY, next);
      return next;
    });
  }, []);

  // ---- clear ----
  const clearAll = useCallback(() => {
    setSessions([]);
    setActiveChatId(null);
    setMessagesByChatId({});
    localStorage.removeItem(SESSIONS_KEY);
    localStorage.removeItem(ACTIVE_KEY);
    localStorage.removeItem(MESSAGES_KEY);
  }, []);

  const activeSession = sessions.find((s) => s.chat_id === activeChatId) || null;
  const activeMessages = activeChatId ? (messagesByChatId[activeChatId] || []) : [];

  return {
    sessions,
    activeChatId,
    activeSession,
    activeMessages,
    messagesByChatId,
    addSession,
    switchSession,
    updateMessages,
    clearAll,
  };
}
