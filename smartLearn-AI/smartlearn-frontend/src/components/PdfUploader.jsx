import { useRef, useState } from 'react';
import { uploadPdf } from '../api.js';

export default function PdfUploader({ chatId, onUpload, uploading, setUploading, setError }) {
  const fileInputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFile = async (file) => {
    if (!file) return;
    if (file.type !== 'application/pdf') {
      setError('只支持 PDF 文件');
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const result = await uploadPdf(file, chatId);
      onUpload(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    handleFile(e.dataTransfer.files[0]);
  };

  return (
    <div
      className={`upload-zone ${dragOver ? 'drag-over' : ''} ${uploading ? 'uploading' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      onClick={() => fileInputRef.current?.click()}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="application/pdf"
        style={{ display: 'none' }}
        onChange={(e) => handleFile(e.target.files[0])}
      />
      {uploading ? (
        <span>上传中...</span>
      ) : (
        <span>拖入 PDF 或点击上传</span>
      )}
    </div>
  );
}
