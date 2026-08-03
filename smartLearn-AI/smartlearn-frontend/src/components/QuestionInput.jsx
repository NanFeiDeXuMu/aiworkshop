import { useState } from 'react';

export default function QuestionInput({ onAsk, onStop, disabled, asking }) {
  const [question, setQuestion] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (asking) {
      onStop?.();
      return;
    }
    if (!question.trim() || disabled) return;
    onAsk(question.trim());
    setQuestion('');
  };

  return (
    <form className="question-input" onSubmit={handleSubmit}>
      <input
        type="text"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder={
          disabled
            ? '请先上传 PDF'
            : asking
              ? '正在生成回答...'
              : '输入你的问题，例如：第三章讲了什么？'
        }
        disabled={disabled || asking}
      />
      {asking ? (
        <button type="submit" className="stop-btn">
          停止
        </button>
      ) : (
        <button type="submit" disabled={disabled || !question.trim()}>
          发送
        </button>
      )}
    </form>
  );
}
