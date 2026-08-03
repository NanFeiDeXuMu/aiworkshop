import { useState, useEffect } from 'react';
import { usePdfStore } from './usePdfStore';
import { setChatContext } from './api';
import PdfUploader from './components/PdfUploader';
import PdfPreview from './components/PdfPreview';
import ChatPanel from './components/ChatPanel';
import HistoryPanel from './components/HistoryPanel';
import './App.css';

const STARTUP_ID_KEY = 'smartlearn_server_startup_id';

/** Generate a short random chat id. */
function freshChatId() {
  return 'c' + Math.random().toString(36).slice(2, 10);
}

export default function App() {
  const {
    sessions,
    activeChatId,
    activeSession,
    activeMessages,
    addSession,
    switchSession,
    updateMessages,
    clearAll,
  } = usePdfStore();

  const [currentPage, setCurrentPage] = useState(null);
  const [previewVersion, setPreviewVersion] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  // Stable upload chatId — generated once and reused until overridden
  const [uploadChatId] = useState(() => freshChatId());

  // On mount, check if backend has restarted. If so, clear all local state.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const apiBase = import.meta.env.VITE_API_URL || '';
        const res = await fetch(`${apiBase}/health`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const currentId = data.startup_id;
        const storedId = localStorage.getItem(STARTUP_ID_KEY);

        if (currentId && currentId !== storedId) {
          clearAll();
        }
        if (currentId) {
          localStorage.setItem(STARTUP_ID_KEY, currentId);
        }
      } catch {
        // Backend unreachable — don't clear state
      }
    })();
    return () => { cancelled = true; };
  }, [clearAll]);

  // Keep the API layer in sync with the current chat session
  useEffect(() => {
    setChatContext(activeChatId);
  }, [activeChatId]);

  const handleJumpToPage = (page) => {
    setCurrentPage(page);
  };

  const handleUpload = (result) => {
    addSession(uploadChatId, result);
    setUploadError(null);
    setCurrentPage(1);
    setPreviewVersion((v) => v + 1);
  };

  return (
    <div className="app">
      {/* ---- left column: branding + upload + history ---- */}
      <div className="app-left">
        <header>
          <h1>SmartLearn AI</h1>
          <span className="subtitle">上传课件，随时提问</span>
        </header>

        <PdfUploader
          chatId={uploadChatId}
          onUpload={handleUpload}
          uploading={uploading}
          setUploading={setUploading}
          setError={setUploadError}
        />

        {activeSession && (
          <div className="active-indicator">
            当前课件：<strong>{activeSession.filename}</strong>（{activeSession.pages} 页）
          </div>
        )}

        {uploadError && <div className="error">{uploadError}</div>}

        <HistoryPanel
          sessions={sessions}
          activeChatId={activeChatId}
          onSelect={(chatId) => {
            switchSession(chatId);
            setCurrentPage(1);
          }}
        />
      </div>

      {/* ---- center column: chat ---- */}
      <div className="app-center">
        <ChatPanel
          key={activeChatId || 'no-chat'}
          chatId={activeChatId}
          enabled={!!activeChatId}
          disabled={!activeChatId}
          onJumpToPage={handleJumpToPage}
          initialMessages={activeMessages}
          onMessagesChange={(msgs) => {
            if (activeChatId) updateMessages(activeChatId, msgs);
          }}
        />
      </div>

      {/* ---- right column: PDF preview ---- */}
      <div className="app-right">
        <PdfPreview
          upload={activeSession}
          activePage={currentPage}
          previewKey={`${activeChatId}-${previewVersion}`}
        />
      </div>
    </div>
  );
}
