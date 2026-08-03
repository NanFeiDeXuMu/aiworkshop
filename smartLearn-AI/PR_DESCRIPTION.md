## What

SmartLearn AI is an AI-powered learning assistant that lets students upload PDF lecture slides and ask course-related questions in natural language. The system extracts text from uploaded PDFs, feeds it as context to an LLM (via OpenRouter), and returns cited, streamed answers. The frontend hides the complexity of PDF-to-answer mapping behind an implicit `ask_id` — users never type IDs; they just upload a file and start asking.

## How

**Backend** (Python / FastAPI):
- `POST /upload` — validates PDFs (MIME type, size ≤10 MB, non-empty text), extracts pages via `pypdf`, assigns a UUID-based `ask_id`, and caches the mapping
- `GET /ask/stream` — validates the question, loads cached pages (memory → disk fallback), and streams the LLM response as SSE events (`chunk`, `done` with citations, `error`)
- `GET /health` — returns a per-boot `startup_id` so the frontend can detect backend restarts and reset state accordingly
- Docker-ready with a multi-layer `Dockerfile` (non-root user, configurable `PORT`, health check)

LLM integration uses the OpenAI-compatible SDK pointed at OpenRouter; a streaming generator yields content tokens one at a time, while the non-streaming `/ask` endpoint is preserved for backward compatibility.

**Frontend** (React / Vite):
- Drag-and-drop PDF upload with instant `ask_id` capture — the ID is stored in `localStorage` and automatically attached to every subsequent question
- Multi-PDF selector: when multiple PDFs are uploaded, a dropdown lets the user switch the active context
- Streaming answer consumption via `fetch` + `ReadableStream` parsing SSE events, with `AbortController`-based cancellation ("Stop" button)
- Markdown and LaTeX rendering via `react-markdown` + `remark-math` + `rehype-katex`, with partial-formula protection (unclosed `$` are stripped before rendering to prevent KaTeX parse errors during streaming)
- Connection-loss detection: if the SSE stream ends without a proper `done` event, the UI displays a "connection lost" warning instead of hanging in the loading state
- Startup consistency: on mount, the frontend calls `/health` and compares `startup_id` with the last known value; a mismatch triggers a full local-state reset

## Proof

- Both backend and frontend compile and build cleanly (`vite build` zero warnings; `python -m py_compile` passes)
- The full upload → stream → render pipeline has been verified end-to-end
- Edge cases tested: empty PDF rejection, scanned-PDF (OCR) rejection, connection-drop during streaming, backend restart state reset, multi-PDF switching

## Limits

- Single-user design — no authentication or session isolation; concurrent users share the same `_ask_files` namespace
- No OCR support — scanned/image-based PDFs are explicitly rejected (422)
- Uploads are ephemeral — container restarts clear all PDFs and mappings by design; persistent storage requires a mounted volume
- LLM quality depends entirely on the OpenRouter free-tier model (`google/gemma-4-26b-a4b-it:free`), which may have rate limits and variable response quality
- LaTeX rendering during streaming relies on `$`-counting heuristics to detect incomplete formulas — edge cases with `$$` block math inside complex markdown structures may flicker briefly
- No WebSocket fallback — streaming uses SSE only; proxy/CDN configurations that don't support chunked transfer encoding will break the streaming experience
