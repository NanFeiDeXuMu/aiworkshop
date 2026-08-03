import json
import os
import re
import sys
import uuid
from pathlib import Path

# Ensure the backend package is on sys.path regardless of launch directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from fastapi import FastAPI, Query, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf.errors import PdfReadError

from services.rag import prepare_rag_chat_record, answer_chat_turn, stream_answer_document, append_history

load_dotenv()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="SmartLearn AI",
    description="AI-powered learning assistant — parses PDF lecture slides and answers course questions.",
    version="0.1.0",
)

_origins_raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173")
_allowed_origins: list = []
for o in _origins_raw.split(","):
    o = o.strip()
    if not o:
        continue
    # Convert wildcard patterns (https://*.example.com) to regex for Vercel preview URLs
    if "*" in o:
        pattern = re.escape(o).replace(r"\*", r"[^.]+")
        _allowed_origins.append(re.compile(f"^{pattern}$"))
    else:
        _allowed_origins.append(o)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# chat_id → RAG-ready document record (Day 3)
documents: dict[str, dict] = {}

# Startup identifier — changes on every boot; frontend uses it to detect restarts
STARTUP_ID = uuid.uuid4().hex


@app.on_event("startup")
async def _on_startup():
    """Clean up state from previous runs so every boot is a fresh start."""
    global STARTUP_ID
    STARTUP_ID = uuid.uuid4().hex

    # Log configured CORS origins for debugging
    origin_display = [
        o.pattern if hasattr(o, "pattern") else o for o in _allowed_origins
    ]
    print(f"[startup] CORS allowed origins: {origin_display}")

    # Ensure upload directory exists
    os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AskResponse(BaseModel):
    answer: str
    citations: list[int]
    sources: list[dict] = []


class ChatRequest(BaseModel):
    chat_id: str
    message: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[int]
    sources: list[dict]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cleanup_partial_upload(chat_id: str) -> None:
    """Remove any files left behind after a failed upload for *chat_id*."""
    chat_id = chat_id.strip()
    pdf_path = UPLOAD_DIR / f"{chat_id}.pdf"
    try:
        if pdf_path.exists():
            pdf_path.unlink()
    except OSError:
        pass
    # Also clean up any artifact files that may have been partially written
    artifact_dir = Path(__file__).resolve().parent / "artifacts"
    if artifact_dir.is_dir():
        for f in artifact_dir.glob(f"{chat_id}__*"):
            try:
                f.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "startup_id": STARTUP_ID}


@app.post("/upload")
async def upload_pdf(
    chat_id: str = Query(...),
    file: UploadFile = File(...),
):
    # 1. Validate chat_id
    if not chat_id or not chat_id.strip():
        raise HTTPException(status_code=400, detail="chat_id must not be empty")

    # 2. Read file bytes
    contents = await file.read()

    # 3. Validate: not empty
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # 4. Validate: MIME type
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail=f"File must be a PDF. Received: {file.content_type}",
        )

    # 5. Validate: file size
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is 10 MB. "
            f"Received: {len(contents) / (1024 * 1024):.1f} MB",
        )

    original_filename = file.filename or "unknown.pdf"

    # 6. Build the Day-3 RAG record (saves PDF + builds FAISS index)
    try:
        document = prepare_rag_chat_record(
            chat_id=chat_id.strip(),
            filename=original_filename,
            pdf_bytes=contents,
            upload_root=str(UPLOAD_DIR),
        )
    except PdfReadError:
        _cleanup_partial_upload(chat_id)
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid PDF or is corrupted",
        )
    except ValueError as e:
        _cleanup_partial_upload(chat_id)
        msg = str(e)
        if "no extractable text" in msg.lower() or "scanned" in msg.lower():
            raise HTTPException(status_code=422, detail="不支持OCR")
        raise HTTPException(status_code=400, detail=msg)
    except Exception as e:
        _cleanup_partial_upload(chat_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process document: {e}",
        )

    # 7. Store the RAG-ready record
    documents[chat_id.strip()] = document

    # 8. Return the same visible Day-2 JSON shape
    pages = document["pages"]
    full_text = " ".join(p["text"] for p in pages)
    return {
        "status": "ok",
        "filename": original_filename,
        "pages": len(pages),
        "characters": len(full_text),
    }


@app.get("/documents/{chat_id}/file")
async def serve_pdf(chat_id: str):
    """Serve the uploaded PDF file for a given *chat_id*."""
    document = documents.get(chat_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"No document found for chat_id: {chat_id}")

    file_path = document.get("saved_pdf_path")
    if file_path is None:
        raise HTTPException(status_code=404, detail="No saved PDF path in document record")

    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved PDF file is missing from disk")

    return FileResponse(str(path), media_type="application/pdf")


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    """Multi-turn chat with fresh retrieval per turn.

    Looks up the Day‑3 document record, runs retrieval for the current
    message, appends the turn to in-memory history, and returns the answer
    together with citations and source previews.
    """
    chat_id = body.chat_id.strip()
    message = body.message.strip()

    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id must not be empty")
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")

    document = documents.get(chat_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"No document found for chat_id: {chat_id}")

    result = answer_chat_turn(document=document, message=message)
    return ChatResponse(
        answer=result["answer"],
        citations=result["citations"],
        sources=result["sources"],
    )


@app.get("/ask", response_model=AskResponse)
async def ask_question(chat_id: str = Query(...), question: str = Query(...)):
    """Answer a question with RAG retrieval, using the Day‑3 document record."""
    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty")

    document = documents.get(chat_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"No document found for chat_id: {chat_id}")

    result = answer_chat_turn(document=document, message=question)
    return AskResponse(
        answer=result["answer"],
        citations=result["citations"],
        sources=result["sources"],
    )


@app.get("/ask/stream")
async def ask_question_stream(chat_id: str = Query(...), question: str = Query(...)):
    """Streaming RAG answer via SSE — yields real LLM chunks."""
    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty")

    document = documents.get(chat_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"No document found for chat_id: {chat_id}")

    async def event_stream():
        full_answer = ""
        try:
            for event in stream_answer_document(document, question):
                if event["type"] == "chunk":
                    full_answer += event["content"]
                    yield f"data: {json.dumps({'type': 'chunk', 'content': event['content']})}\n\n"
                elif event["type"] == "done":
                    citations = event.get("citations", [])
                    sources = event.get("sources", [])
                    # Append the completed turn into in-memory history
                    append_history(document, question, {
                        "answer": full_answer,
                        "citations": citations,
                        "sources": sources,
                    })
                    yield f"data: {json.dumps({'type': 'done', 'citations': citations, 'sources': sources})}\n\n"
                elif event["type"] == "error":
                    yield f"data: {json.dumps({'type': 'error', 'detail': event.get('detail', 'Unknown error')})}\n\n"
                    return
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
