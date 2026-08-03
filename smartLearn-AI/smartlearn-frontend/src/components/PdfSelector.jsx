export default function PdfSelector({ pdfs, activeAskId, onSelect }) {
  if (pdfs.length <= 1) return null;

  return (
    <div className="pdf-selector">
      <label>当前课件：</label>
      <select
        value={activeAskId || ''}
        onChange={(e) => onSelect(e.target.value)}
      >
        {pdfs.map((p) => (
          <option key={p.ask_id} value={p.ask_id}>
            {p.filename} ({p.page_count} 页)
          </option>
        ))}
      </select>
    </div>
  );
}
