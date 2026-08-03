const API_BASE = import.meta.env.VITE_API_URL || '';

/**
 * Build the backend PDF file URL for the current chat session.
 * @param {number} page — target page number (1‑based)
 * @returns {string} URL like `/documents/${chat_id}/file#page=${page}`
 */
export function getDocumentFileURL(chatId, page = 1) {
  return `${API_BASE}/documents/${chatId}/file#page=${page}`;
}

/**
 * Minimal PDF preview via an iframe.
 *
 * @param {object|null} upload — the stored upload record (must include
 *   ``chat_id``), or ``null`` when nothing has been uploaded yet.
 * @param {number|null} activePage — the page the iframe should jump to.
 * @param {string|number} previewKey — changing this value forces a full
 *   iframe reload (used after a new upload so stale content isn't shown).
 */
export default function PdfPreview({ upload, activePage, previewKey }) {
  if (!upload || !upload.chat_id) {
    return (
      <div className="pdf-preview-empty">
        <span>📄 请先上传 PDF</span>
      </div>
    );
  }

  const hash = activePage ? `#page=${activePage}` : '';
  const src = `${API_BASE}/documents/${upload.chat_id}/file${hash}`;

  return (
    <iframe
      key={`${upload.chat_id}-p${activePage}-v${previewKey}`}
      src={src}
      className="pdf-preview-iframe"
      title="PDF Preview"
    />
  );
}
