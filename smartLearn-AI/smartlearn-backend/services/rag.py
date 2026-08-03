# reusabe helpers for text cleaning, page loading, chunking, embeddings and artifact saving.
# call this file and save output into aiworkshop/artifacts/

import json
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def clean_text(text: str) -> str:
    """Normalize one extracted page of PDF text.

    Removes null bytes and soft hyphens, collapses repeated whitespace,
    and normalises noisy line breaks while preserving paragraph boundaries
    (double-newline).
    """
    # 1. Remove null bytes that break string operations and JSON serialisation
    text = text.replace("\x00", "")
    # 2. Remove soft hyphens (U+00AD) — invisible hyphenation points in PDFs
    text = text.replace("\u00ad", "")
    # 3. Normalise line endings (Windows, old-Mac → Unix)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 4. Collapse runs of 2+ consecutive newlines into exactly 2 (paragraph break)
    text = re.sub(r"\n{2,}", "\n\n", text)
    # 5. Split on paragraph breaks, collapse intra-paragraph newlines to spaces
    paragraphs = text.split("\n\n")
    paragraphs = [p.replace("\n", " ").strip() for p in paragraphs]
    text = "\n\n".join(paragraphs)
    # 6. Collapse repeated horizontal whitespace (spaces, tabs) into a single space
    text = re.sub(r"[ \t]+", " ", text)
    # 7. Final strip of leading/trailing whitespace
    return text.strip()


def extract_pages(pdf_bytes: bytes) -> list[dict]:
    """Read a PDF from raw bytes page by page, keeping original page numbers.

    Cleans each page with :func:`clean_text` and skips pages whose cleaned
    text is empty.  No hard page limit.  Uses ``strict=False`` so mildly
    malformed PDFs (common with web uploads) are tolerated.
    """
    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF: {exc}") from exc

    pages: list[dict] = []
    for page_number, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        cleaned = clean_text(raw)
        if cleaned:
            pages.append({"page": page_number, "text": cleaned})

    return pages


def extract_pages_for_rag(pdf_path: str | Path, page_limit: int | None = None) -> list[dict]:
    """Read a PDF file page by page, keeping original page numbers.

    Accepts either a ``str`` or :class:`~pathlib.Path`.  Skips pages whose
    cleaned text is empty.  When ``page_limit`` is set, only the first N
    (non-empty) pages are returned.
    """
    pages = extract_pages(Path(pdf_path).read_bytes())
    if page_limit is not None and page_limit > 0:
        pages = pages[:page_limit]
    return pages


def extract_pages_from_bytes_for_rag(pdf_bytes: bytes) -> list[dict]:
    """Extract page records from raw PDF *bytes* for the backend upload route.

    Thin wrapper around :func:`extract_pages` — same cleaning, same output
    schema ``[{"page": 1, "text": "..."}]``.
    """
    return extract_pages(pdf_bytes)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

VALID_MODES = ("page", "paragraph")
VALID_CHUNK_MODES = ("paragraph", "character", "character_overlap")


def slice_long_text(text: str, chunk_size: int = 1000) -> list[str]:
    """Split one oversized text block into pieces of at most ``chunk_size``.

    Each break is placed at the last natural boundary inside the window —
    paragraph break, then line break, then sentence end, then any
    whitespace — so a new piece never starts in the middle of a word when a
    boundary exists.  Falls back to a hard cut when the window contains no
    boundary at all.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    text = text.strip()
    if not text:
        return []

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            pieces.append(text[start:].strip())
            break
        window = text[start:end]
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? ", "。", " ", "，"):
            idx = window.rfind(sep)
            if idx > 0:
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = len(window)  # no boundary inside the window: hard cut
        piece = text[start : start + cut].strip()
        if piece:
            pieces.append(piece)
        start += cut
        # skip whitespace left at the start of the next piece
        while start < len(text) and text[start].isspace():
            start += 1
    return pieces


def chunk_by_paragraph(
    records: list[dict],
    chunk_size: int = 1000,
    chunk_mode: str = "paragraph",
) -> list[dict]:
    """Pack paragraph-level records into chunks of at most ``chunk_size``.

    Paragraphs are kept whole whenever possible, keep their original order,
    and are never merged across pages.  A single paragraph longer than
    ``chunk_size`` is split with :func:`slice_long_text`.  Returns chunks in
    the uniform schema ``{chunk_id, page, text, chunk_mode}``.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    chunks: list[dict] = []
    buffer: list[str] = []
    buffer_page: int | None = None
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_page, buffer_len
        if buffer:
            chunks.append({
                "chunk_id": len(chunks),
                "page": buffer_page,
                "text": "\n\n".join(buffer),
                "chunk_mode": chunk_mode,
            })
        buffer, buffer_page, buffer_len = [], None, 0

    for record in records:
        page = record["page"]
        paragraph = record["text"].strip()
        if not paragraph:
            continue

        # Oversized single paragraph: flush, then slice it into pieces
        if len(paragraph) > chunk_size:
            flush()
            for piece in slice_long_text(paragraph, chunk_size):
                chunks.append({
                    "chunk_id": len(chunks),
                    "page": page,
                    "text": piece,
                    "chunk_mode": chunk_mode,
                })
            continue

        # +2 accounts for the "\n\n" separator between paragraphs
        added = len(paragraph) if not buffer else len(paragraph) + 2
        if buffer and (buffer_page != page or buffer_len + added > chunk_size):
            flush()
            added = len(paragraph)

        buffer.append(paragraph)
        buffer_page = page
        buffer_len += added

    flush()
    return chunks


def chunk_by_characters(
    records: list[dict],
    chunk_size: int = 1000,
    overlap: int = 0,
    chunk_mode: str = "character",
) -> list[dict]:
    """Create plain fixed-size sliding-window chunks from each record.

    Windows are hard cuts of ``chunk_size`` characters (the last window of a
    record may be shorter); no attempt is made to respect word or paragraph
    boundaries.  With ``overlap > 0`` consecutive windows share that many
    characters and each chunk records it in an ``overlap`` field.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: list[dict] = []
    step = chunk_size - overlap
    for record in records:
        text = record["text"].strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            piece = text[start : start + chunk_size]
            if piece:
                chunk = {
                    "chunk_id": len(chunks),
                    "page": record["page"],
                    "text": piece,
                    "chunk_mode": chunk_mode,
                }
                if overlap > 0:
                    chunk["overlap"] = overlap
                chunks.append(chunk)
            if start + chunk_size >= len(text):
                break
            start += step
    return chunks


def _split_paragraph_records(records: list[dict]) -> list[dict]:
    """Split page-level records into paragraph-level records.

    Keeps the original page number and paragraph order; empty paragraphs
    are dropped.  Each output record gains a ``paragraph`` index.
    """
    paragraphs: list[dict] = []
    for record in records:
        for index, para in enumerate(record["text"].split("\n\n")):
            para = para.strip()
            if para:
                paragraphs.append({
                    "page": record["page"],
                    "paragraph": index,
                    "text": para,
                })
    return paragraphs


def build_chunks(
    records: list[dict],
    mode: str = "paragraph",
    chunk_mode: str = "paragraph",
    chunk_size: int = 1000,
    overlap: int = 0,
) -> list[dict]:
    """Build chunks from page-level records with the requested strategy.

    ``mode`` controls how input records are prepared: ``"paragraph"``
    splits each record's text on blank lines into paragraph-level records
    first; ``"page"`` treats each record as one indivisible block.

    ``chunk_mode`` selects the chunking strategy and must be one of
    ``"paragraph"``, ``"character"`` or ``"character_overlap"``.
    ``overlap`` is only used by ``"character_overlap"`` (and must be > 0
    there); it is ignored by the other modes.

    Every returned chunk has at least ``chunk_id``, ``page``, ``text`` and
    ``chunk_mode``; original page numbers and text order are preserved.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    if chunk_mode not in VALID_CHUNK_MODES:
        raise ValueError(
            f"chunk_mode must be one of {VALID_CHUNK_MODES}, got {chunk_mode!r}"
        )
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if chunk_mode == "character_overlap" and overlap == 0:
        raise ValueError("character_overlap mode requires overlap > 0")

    for i, record in enumerate(records):
        if not isinstance(record, dict) or "page" not in record or "text" not in record:
            raise ValueError(
                f"record {i} must be a dict with 'page' and 'text' keys"
            )
        if not isinstance(record["text"], str):
            raise ValueError(f"record {i} 'text' must be a string")

    if not records:
        return []

    if mode == "paragraph":
        records = _split_paragraph_records(records)

    if chunk_mode == "paragraph":
        return chunk_by_paragraph(
            records, chunk_size=chunk_size, chunk_mode=chunk_mode
        )
    effective_overlap = overlap if chunk_mode == "character_overlap" else 0
    return chunk_by_characters(
        records,
        chunk_size=chunk_size,
        overlap=effective_overlap,
        chunk_mode=chunk_mode,
    )


def save_json(data: Any, path: str | Path) -> None:
    """Save a Python object to a UTF-8 JSON file.

    Creates parent folders if they don't exist.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_json(path: str | Path) -> Any:
    """Read a saved JSON artifact back into Python."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def preview_records(
    records: list[dict],
    columns: list[str] | None = None,
    max_rows: int = 10,
    max_cell_width: int = 80,
) -> None:
    """Print a notebook-style table for inspecting page/chunk artefacts.

    Long cell values are truncated to ``max_cell_width`` characters so the
    table stays readable.  Uses ``tabulate`` if available; otherwise falls
    back to simple aligned printing.
    """
    if not records:
        print("No records to preview.")
        return

    # Tolerate console encodings (e.g. Windows GBK) that cannot render
    # some Unicode characters in extracted PDF text
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    # Determine which columns to show
    if columns is None:
        columns = list(records[0].keys())

    # Limit rows
    display = records[:max_rows] if max_rows > 0 else records

    # Build rows in the selected column order, truncating long cells
    def _cell(value: Any) -> str:
        cell = str(value).replace("\n", " ")
        if len(cell) > max_cell_width:
            cell = cell[: max_cell_width - 1] + "…"
        return cell

    rows = [
        [_cell(record.get(col, "")) for col in columns]
        for record in display
    ]

    # --- tabulate path ---
    try:
        from tabulate import tabulate

        print(tabulate(rows, headers=columns, tablefmt="simple_grid"))
    except ImportError:
        # --- fallback: simple aligned printing ---
        col_widths = [
            max(len(str(col)), max((len(r[i]) for r in rows), default=0))
            for i, col in enumerate(columns)
        ]
        header = "  ".join(
            str(col).ljust(col_widths[i]) for i, col in enumerate(columns)
        )
        print(header)
        print("-" * len(header))
        for row in rows:
            print("  ".join(
                cell.ljust(col_widths[i]) for i, cell in enumerate(row)
            ))

    print(f"\nTotal records: {len(records)}")


# ---------------------------------------------------------------------------
# Embeddings and artifact bundling
# ---------------------------------------------------------------------------

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_CACHE_DIR = str(Path(__file__).resolve().parent.parent / "artifacts" / "rag" / "hf_models")

# Reuse model instances within one session: (model_source, device) -> model
_MODEL_CACHE: dict[tuple[str, str], Any] = {}


def model_tag(model_name: str) -> str:
    """Turn a model name into a filesystem-safe suffix for artifact files.

    ``"sentence-transformers/all-MiniLM-L6-v2"`` becomes
    ``"sentence-transformers-all-MiniLM-L6-v2"``.
    """
    tag = re.sub(r"[^A-Za-z0-9._-]+", "-", model_name).strip("-.")
    if not tag:
        raise ValueError(f"model name {model_name!r} has no safe characters")
    return tag


def resolve_model_source(model_name: str, cache_dir: str | Path | None = None) -> str:
    """Prefer a local cached model folder when it already exists.

    Checks, in order: ``model_name`` itself as a folder, then ``cache_dir``
    joined with the raw name and with its :func:`model_tag`.  If none of
    them exist, returns ``model_name`` unchanged so the library downloads
    from the hub.
    """
    candidates = [Path(model_name)]
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        short_name = model_name.rsplit("/", 1)[-1]
        candidates += [
            cache_dir / model_name,
            cache_dir / model_tag(model_name),
            cache_dir / short_name,
        ]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return model_name


def get_device() -> str:
    """Choose the device for the current machine: CUDA if available, else CPU."""
    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            "torch is required for embeddings; install it with `pip install torch`"
        ) from exc
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_model(
    model_name: str = DEFAULT_MODEL_NAME,
    device: str | None = None,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
):
    """Create or reuse one sentence-transformer model instance.

    Instances are cached per ``(model_source, device)`` so repeated calls in
    one session do not reload weights.  ``device`` defaults to
    :func:`get_device` (CPU-first, CUDA when available).
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is required for embeddings; "
            "install it with `pip install sentence-transformers`"
        ) from exc

    source = resolve_model_source(model_name, cache_dir)
    device = device or get_device()
    key = (source, device)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = SentenceTransformer(source, device=device)
    return _MODEL_CACHE[key]


def embed_texts(model, texts: list[str], batch_size: int = 32):
    """Encode texts into L2-normalised ``float32`` vectors.

    Returns a ``(len(texts), embedding_dim)`` numpy array; an empty input
    yields an empty ``float32`` array of shape ``(0,)``.
    """
    import numpy as np

    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if not texts:
        return np.zeros((0,), dtype=np.float32)
    vectors = model.encode(
        list(texts),
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def artifact_paths_for(
    document_id: str,
    chunk_mode: str,
    model_name: str,
    chunk_size: int,
    overlap: int,
    artifact_root: str | Path = "artifacts",
) -> dict[str, Path]:
    """Decide where one document's artefacts should be saved.

    Chunk and embedding files embed the chunking signature and model tag so
    that different configs for the same document can coexist under
    ``artifact_root``.
    """
    root = Path(artifact_root)
    chunk_sig = f"{chunk_mode}__s{chunk_size}__o{overlap}"
    tag = model_tag(model_name)
    return {
        "raw_pages_path": root / f"{document_id}__pages.json",
        "chunk_path": root / f"{document_id}__chunks__{chunk_sig}.json",
        "embedding_path": root / f"{document_id}__embeddings__{chunk_sig}__{tag}.npy",
        "manifest_path": root / f"{document_id}__manifest.json",
        "index_path": root / f"{document_id}__index__{chunk_sig}__{tag}.faiss",
    }


def ensure_artifacts(
    document_id: str,
    pdf_name: str,
    pages: list[dict],
    chunk_mode: str = "paragraph",
    model_name: str = DEFAULT_MODEL_NAME,
    chunk_size: int = 1000,
    overlap: int = 0,
    batch_size: int = 32,
    artifact_root: str | Path = "artifacts",
) -> dict:
    """Build or reuse the pages -> chunks -> embeddings -> manifest bundle.

    When a manifest with a matching signature (``document_id``,
    ``chunk_mode``, ``chunk_size``, ``overlap``, ``model_name``) already
    exists and all files it references are still on disk, the saved outputs
    are loaded and returned instead of recomputing.  A corrupt or partial
    manifest is treated as a cache miss and rebuilt from scratch.

    Returns a dict with ``manifest``, ``pages``, ``chunks``, ``embeddings``,
    ``paths`` and a ``reused`` flag.
    """
    import numpy as np

    paths = artifact_paths_for(
        document_id, chunk_mode, model_name, chunk_size, overlap, artifact_root
    )
    signature = {
        "document_id": document_id,
        "chunk_mode": chunk_mode,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "model_name": model_name,
    }

    # --- reuse path: signature match + all referenced files present ---
    if paths["manifest_path"].is_file():
        try:
            saved = load_json(paths["manifest_path"])
        except (json.JSONDecodeError, OSError):
            saved = None
        if (
            isinstance(saved, dict)
            and all(saved.get(k) == v for k, v in signature.items())
            and all(paths[k].is_file() for k in ("raw_pages_path", "chunk_path", "embedding_path"))
        ):
            return {
                "manifest": saved,
                "pages": load_json(paths["raw_pages_path"]),
                "chunks": load_json(paths["chunk_path"]),
                "embeddings": np.load(paths["embedding_path"]),
                "paths": paths,
                "reused": True,
            }

    # --- build path ---
    Path(artifact_root).mkdir(parents=True, exist_ok=True)

    save_json(pages, paths["raw_pages_path"])

    chunks = build_chunks(
        pages,
        mode="paragraph",
        chunk_mode=chunk_mode,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    save_json(chunks, paths["chunk_path"])

    model = load_model(model_name)
    embeddings = embed_texts(model, [c["text"] for c in chunks], batch_size=batch_size)
    np.save(paths["embedding_path"], embeddings)

    embedding_dim = int(embeddings.shape[1]) if embeddings.ndim == 2 else 0
    manifest = {
        "document_id": document_id,
        "pdf_name": pdf_name,
        "num_pages": len(pages),
        "chunk_mode": chunk_mode,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "model_name": model_name,
        "num_chunks": len(chunks),
        "embedding_dim": embedding_dim,
        "device": str(model.device),
        "chunk_path": str(paths["chunk_path"]),
        "embedding_path": str(paths["embedding_path"]),
        "raw_pages_path": str(paths["raw_pages_path"]),
        "batch_size": batch_size,
    }
    save_json(manifest, paths["manifest_path"])

    return {
        "manifest": manifest,
        "pages": pages,
        "chunks": chunks,
        "embeddings": embeddings,
        "paths": paths,
        "reused": False,
    }


# ---------------------------------------------------------------------------
# FAISS index helpers
# ---------------------------------------------------------------------------

def build_faiss_index(embeddings: "np.ndarray") -> "faiss.Index":  # noqa: F821
    """Build a FAISS inner-product index from normalised embedding vectors.

    Expects an ``(N, D)`` float32 array of L2-normalised vectors.  Because
    the vectors are unit-length, inner-product search is equivalent to
    cosine-similarity ranking.

    Raises ``ValueError`` when *embeddings* is empty, not 2-D, or not
    float32.
    """
    import numpy as np

    try:
        import faiss
    except ImportError as exc:
        raise ImportError(
            "faiss is required for vector search; "
            "install it with `pip install faiss-cpu` (or `faiss-gpu`)"
        ) from exc

    arr = np.ascontiguousarray(embeddings, dtype=np.float32)

    if arr.ndim != 2:
        raise ValueError(
            f"embeddings must be a 2-D array, got shape {embeddings.shape}"
        )
    if arr.shape[0] == 0:
        raise ValueError("embeddings must contain at least one vector")
    if arr.dtype != np.float32:
        raise ValueError(f"embeddings must be float32, got {arr.dtype}")

    dim = arr.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(arr)
    return index


def save_faiss_index(index: "faiss.Index", index_path: str | Path) -> None:  # noqa: F821
    """Write a FAISS index to a binary ``.faiss`` file on disk.

    Parent directories are created automatically when they don't exist.
    """
    import faiss

    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))


def load_faiss_index(index_path: str | Path) -> "faiss.Index | None":  # noqa: F821
    """Load a previously saved FAISS index from a ``.faiss`` file.

    Returns ``None`` when the file is missing, corrupted, or unreadable so
    callers can fall back to rebuilding.
    """
    import faiss

    index_path = Path(index_path)
    if not index_path.is_file():
        return None
    try:
        return faiss.read_index(str(index_path))
    except (OSError, RuntimeError):
        return None


def relative_path_str(path: str | Path, base: str | Path) -> str:
    """Return *path* relative to *base* for compact display.

    When *path* does not live under *base* the raw *path* is returned
    unchanged (no ``ValueError`` is raised).
    """
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# High-level RAG pipeline
# ---------------------------------------------------------------------------

def ensure_index(
    document_id: str,
    pdf_name: str,
    pages: list[dict] | None = None,
    pdf_path: str | Path | None = None,
    chunk_mode: str = "character_overlap",
    model_name: str = DEFAULT_MODEL_NAME,
    chunk_size: int = 700,
    overlap: int = 120,
    batch_size: int = 32,
    artifact_root: str | Path | None = None,
) -> dict:
    """Build or reuse the full pages → chunks → embeddings → FAISS index bundle.

    At least one of *pages* or *pdf_path* must be supplied.  When *pages* is
    ``None`` the PDF at *pdf_path* is read and its pages extracted first.

    Signature-based caching: when a matching manifest **and** ``.faiss``
    file already exist under *artifact_root* they are loaded from disk
    instead of recomputing.  A corrupt index or partial cache is treated as
    a cache miss and rebuilt.

    Returns a dict with ``manifest``, ``pages``, ``chunks``, ``embeddings``,
    ``index``, ``paths``, ``reused`` and ``index_reused``.
    """
    if pages is None and pdf_path is None:
        raise ValueError("Either pages or pdf_path must be provided")

    if artifact_root is None:
        artifact_root = "artifacts"

    # --- pages extraction (pdf_path path) ---
    if pages is None:
        pages = extract_pages_for_rag(pdf_path)

    # --- chunks + embeddings (delegate to ensure_artifacts) ---
    bundle = ensure_artifacts(
        document_id=document_id,
        pdf_name=pdf_name,
        pages=pages,
        chunk_mode=chunk_mode,
        model_name=model_name,
        chunk_size=chunk_size,
        overlap=overlap,
        batch_size=batch_size,
        artifact_root=artifact_root,
    )

    index_path = bundle["paths"]["index_path"]

    # --- reuse path: manifest reused AND .faiss file on disk ---
    if bundle["reused"]:
        cached_index = load_faiss_index(index_path)
        if cached_index is not None:
            bundle["index"] = cached_index
            bundle["index_reused"] = True
            return bundle

    # --- build path ---
    index = build_faiss_index(bundle["embeddings"])
    save_faiss_index(index, index_path)

    bundle["index"] = index
    bundle["index_reused"] = False
    return bundle


def prepare_rag_document(
    document_id: str,
    filename: str,
    pages: list[dict],
    chunk_mode: str = "character_overlap",
    chunk_size: int = 700,
    overlap: int = 120,
    model_name: str = DEFAULT_MODEL_NAME,
    batch_size: int = 32,
    artifact_root: str | Path | None = None,
) -> dict:
    """Build a server-style document record with RAG index metadata.

    Calls :func:`ensure_index` internally and wraps its result into a dict
    suitable for the SmartLearn backend: ``document_id``, ``filename``,
    ``pages``, ``chunks``, an empty ``history`` list, the FAISS ``index``,
    ``manifest``, ``paths``, and a ``rag_config`` section summarising the
    chunking and embedding settings.
    """
    bundle = ensure_index(
        document_id=document_id,
        pdf_name=filename,
        pages=pages,
        chunk_mode=chunk_mode,
        model_name=model_name,
        chunk_size=chunk_size,
        overlap=overlap,
        batch_size=batch_size,
        artifact_root=artifact_root,
    )

    return {
        "document_id": document_id,
        "filename": filename,
        "pages": bundle["pages"],
        "chunks": bundle["chunks"],
        "history": [],
        "index": bundle["index"],
        "artifacts": {
            "index": str(bundle["paths"]["index_path"]),
            "chunks": str(bundle["paths"]["chunk_path"]),
            "embeddings": str(bundle["paths"]["embedding_path"]),
            "pages": str(bundle["paths"]["raw_pages_path"]),
            "manifest": str(bundle["paths"]["manifest_path"]),
        },
        "manifest": bundle["manifest"],
        "paths": bundle["paths"],
        "chunk_mode": chunk_mode,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "model_name": model_name,
        "batch_size": batch_size,
        "embedding_dim": bundle["manifest"]["embedding_dim"],
        "num_chunks": bundle["manifest"]["num_chunks"],
        "num_pages": bundle["manifest"]["num_pages"],
    }


# ---------------------------------------------------------------------------
# Upload-time helpers
# ---------------------------------------------------------------------------

def prepare_rag_chat_record(
    chat_id: str,
    filename: str,
    pdf_bytes: bytes | None = None,
    pages: list[dict] | None = None,
    upload_root: str | Path | None = None,
    chunk_mode: str = "character_overlap",
    chunk_size: int = 700,
    overlap: int = 120,
    model_name: str = DEFAULT_MODEL_NAME,
    batch_size: int = 32,
    artifact_root: str | Path | None = None,
) -> dict:
    """Build a server-side ``documents[chat_id]`` record at upload time.

    When *pdf_bytes* is supplied the PDF is saved to
    ``upload_root/{chat_id}.pdf`` first.  At least one of *pdf_bytes* or
    *pages* must be provided.

    The returned record includes the saved file path, extracted pages,
    chunked text, FAISS index, RAG metadata, and an empty chat history —
    everything the route layer needs for later retrieval and conversation.
    """
    if pdf_bytes is None and pages is None:
        raise ValueError("Either pdf_bytes or pages must be provided")

    if upload_root is None:
        upload_root = "uploads"
    if artifact_root is None:
        artifact_root = "artifacts"

    upload_root = Path(upload_root)
    upload_root.mkdir(parents=True, exist_ok=True)

    # --- save PDF to disk ---
    file_path = upload_root / f"{chat_id}.pdf"
    if pdf_bytes is not None:
        file_path.write_bytes(pdf_bytes)

    # --- extract pages from bytes if not provided ---
    if pages is None:
        pages = extract_pages_from_bytes_for_rag(pdf_bytes)  # type: ignore[arg-type]

    if not pages:
        raise ValueError("PDF produced no extractable text — it may be a scanned document")

    # --- purge old artifacts so a re-upload with the same chat_id is
    #     never served stale chunks / embeddings / index ---
    artifact_root_path = Path(artifact_root)
    if artifact_root_path.is_dir():
        for stale in artifact_root_path.glob(f"{chat_id}__*"):
            try:
                stale.unlink()
            except OSError:
                pass

    # --- build RAG assets ---
    bundle = ensure_index(
        document_id=chat_id,
        pdf_name=filename,
        pages=pages,
        chunk_mode=chunk_mode,
        model_name=model_name,
        chunk_size=chunk_size,
        overlap=overlap,
        batch_size=batch_size,
        artifact_root=artifact_root,
    )

    return {
        "chat_id": chat_id,
        "filename": filename,
        "saved_pdf_path": str(file_path),
        "pages": pages,
        "chunks": bundle["chunks"],
        "history": [],
        "index": bundle["index"],
        "artifacts": {
            "index": str(bundle["paths"]["index_path"]),
            "chunks": str(bundle["paths"]["chunk_path"]),
            "embeddings": str(bundle["paths"]["embedding_path"]),
            "pages": str(bundle["paths"]["raw_pages_path"]),
            "manifest": str(bundle["paths"]["manifest_path"]),
        },
        "manifest": bundle["manifest"],
        "paths": bundle["paths"],
        "chunk_mode": chunk_mode,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "model_name": model_name,
        "batch_size": batch_size,
        "embedding_dim": bundle["manifest"]["embedding_dim"],
        "num_chunks": bundle["manifest"]["num_chunks"],
        "num_pages": bundle["manifest"]["num_pages"],
    }


def build_upload_response(document: dict) -> dict:
    """Extract the visible upload-success fields for the frontend JSON.

    Returns a dict with ``chat_id``, ``filename``, ``page_count``,
    ``char_count``, and ``text_preview`` — a safe subset of the richer
    internal document record.
    """
    pages = document.get("pages", [])
    full_text = " ".join(p.get("text", "") for p in pages)
    return {
        "chat_id": document.get("chat_id", ""),
        "filename": document.get("filename", ""),
        "page_count": len(pages),
        "char_count": len(full_text),
        "text_preview": full_text[:500],
    }


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def keyword_set(text: str) -> set[str]:
    """Extract a lightweight set of lowercase alphabetic tokens from *text*.

    Tokens shorter than 3 characters are dropped.  Returns an empty set for
    empty or token-less input — never raises.
    """
    tokens = re.findall(r"[A-Za-z]+", text.lower())
    return {t for t in tokens if len(t) >= 3}


def search_bundle(
    question: str,
    bundle: dict,
    top_k: int = 3,
    candidate_pool: int = 60,
    batch_size: int = 1,
    history: list[dict] | None = None,
) -> list[dict]:
    """Retrieve top-k chunks for *question* from an in-memory index bundle.

    1. Embeds the question with the same model recorded in the manifest.
    2. Searches the FAISS index for *candidate_pool* nearest neighbours.
    3. Applies a light lexical rerank (10 % keyword overlap, 90 % vector
       score) and returns the best *top_k* hits.

    Each hit is a dict with ``page``, ``chunk_id``, ``text``, ``score``
    (the blended rerank score), and ``vector_score`` (raw inner-product).
    """
    import numpy as np

    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")

    index = bundle["index"]
    if index.ntotal == 0:
        return []

    chunks = bundle["chunks"]
    model_name = bundle["manifest"]["model_name"]

    # --- embed the question ---
    model = load_model(model_name)
    q_vec = embed_texts(model, [question.strip()], batch_size=batch_size)

    # --- FAISS search: fetch candidate_pool neighbours ---
    pool = min(candidate_pool, index.ntotal)
    distances, indices = index.search(q_vec, pool)
    distances = distances[0]
    indices = indices[0]

    # --- assemble raw hits ---
    question_kw = keyword_set(question)
    raw_hits: list[dict] = []
    for dist, idx in zip(distances, indices):
        if idx < 0 or idx >= len(chunks):  # FAISS padding sentinel
            continue
        chunk = chunks[idx]
        chunk_kw = keyword_set(chunk["text"])
        overlap = len(question_kw & chunk_kw) / max(len(question_kw), 1)
        blended = float(dist) * 0.9 + overlap * 0.1
        raw_hits.append({
            "page": chunk["page"],
            "chunk_id": chunk["chunk_id"],
            "text": chunk["text"],
            "score": round(blended, 4),
            "vector_score": round(float(dist), 4),
        })

    # --- sort by blended score descending, keep top_k ---
    raw_hits.sort(key=lambda h: h["score"], reverse=True)
    return raw_hits[:top_k]


def search_document(
    question: str,
    document: dict,
    top_k: int = 3,
    candidate_pool: int = 60,
    history: list[dict] | None = None,
) -> list[dict]:
    """Retrieve top-k chunks for *question* from a prepared document record.

    Loads the FAISS index — preferring the in-memory object at
    ``document["index"]`` and falling back to the file at
    ``document["artifacts"]["index"]``.  Returns hits in the same format as
    :func:`search_bundle`.
    """
    index = document.get("index")
    if index is None:
        index_path = document.get("artifacts", {}).get("index")
        if index_path is None:
            raise ValueError(
                "document must have either an in-memory 'index' or "
                "an 'artifacts.index' path to a saved .faiss file"
            )
        index = load_faiss_index(index_path)
        if index is None:
            raise ValueError(
                f"failed to load FAISS index from {index_path}; "
                f"the file may be missing or corrupted"
            )

    # Build a lightweight bundle compatible with search_bundle
    bundle = {
        "index": index,
        "chunks": document["chunks"],
        "manifest": document["manifest"],
    }
    return search_bundle(
        question=question,
        bundle=bundle,
        top_k=top_k,
        candidate_pool=candidate_pool,
        history=history,
    )


# ---------------------------------------------------------------------------
# Answer extraction
# ---------------------------------------------------------------------------

def split_sentences(text: str) -> list[str]:
    """Split *text* on sentence-ending punctuation into individual sentences.

    Handles English (``.``, ``!``, ``?``) and Chinese (``。``, ``！``,
    ``？``) boundaries.  Returns an empty list for empty input.
    """
    if not text or not text.strip():
        return []
    parts = re.split(r"(?<=[.!?。！？])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def best_sentence_answer(question: str, hits: list[dict]) -> str:
    """Pick the single best answer sentence from *hits* for a given *question*.

    Scores every sentence in every hit by keyword overlap with the question.
    The winning sentence is returned with a ``(page N)`` tag appended when
    the source page is known.  Falls back to the first sentence of the top
    hit when no keywords match at all.
    """
    if not hits:
        return "No answer found."

    question_kw = keyword_set(question)

    best_score = -1
    best_text = ""
    best_page: int | None = None

    for hit in hits:
        for sentence in split_sentences(hit["text"]):
            sent_kw = keyword_set(sentence)
            if not sent_kw:
                continue
            overlap = len(question_kw & sent_kw) / len(sent_kw)
            if overlap > best_score:
                best_score = overlap
                best_text = sentence
                best_page = hit.get("page")

    # Fallback: first sentence of the top hit
    if best_score <= 0:
        fallback = split_sentences(hits[0]["text"])
        if fallback:
            best_text = fallback[0]
            best_page = hits[0].get("page")

    if best_page is not None:
        return f"{best_text} (page {best_page})"
    return best_text


# ---------------------------------------------------------------------------
# Project-facing helpers
# ---------------------------------------------------------------------------

def extract_citations(answer: str, hits: list[dict] | None = None) -> list[int]:
    """Extract numeric page citations from an answer string.

    Recognises both ``(page N)`` (local extraction) and ``[Page N]`` (LLM)
    formats.  When *hits* is provided, only pages that actually appear in
    the retrieved chunks are kept.  Returns a sorted list of unique page
    numbers — never raises.
    """
    patterns = [
        r"\(page\s+(\d+)\)",   # best_sentence_answer format
        r"\[Page\s+(\d+)\]",    # LLM format
    ]
    pages: set[int] = set()
    for pat in patterns:
        for m in re.finditer(pat, answer, re.IGNORECASE):
            pages.add(int(m.group(1)))

    # Cross-reference with hits when available: only keep pages that
    # actually appear in the retrieved chunks
    if hits:
        hit_pages = {h["page"] for h in hits if "page" in h}
        pages = {p for p in pages if p in hit_pages}

    return sorted(pages)


def build_sources(hits: list[dict]) -> list[dict]:
    """Convert retrieval hits into frontend-friendly source objects.

    Each source keeps ``page``, ``chunk_id``, ``score``, and a ``preview``
    (first 200 characters of the chunk text, truncated with ``…`` when
    longer).
    """
    sources: list[dict] = []
    for hit in hits:
        text = hit.get("text", "")
        preview = text[:200]
        if len(text) > 200:
            preview = preview.rstrip() + "…"
        sources.append({
            "page": hit.get("page"),
            "chunk_id": hit.get("chunk_id"),
            "score": hit.get("score"),
            "preview": preview,
        })
    return sources


def build_grounded_user_prompt(
    question: str,
    hits: list[dict],
    history: list[dict] | None = None,
) -> str:
    """Build a single grounded user-prompt string for answer generation.

    Formats the retrieved *hits* as numbered context blocks tagged with
    their source page.  When *history* is provided the most recent 3 turns
    are prepended so the LLM can track prior questions, while still
    retrieving fresh evidence for the current question.

    Returns a plain string ready to use as the ``"user"`` message content.
    """
    # --- context from retrieved chunks ---
    blocks: list[str] = []
    for h in hits:
        blocks.append(f"[Chunk from page {h['page']}]: {h['text']}")
    context_text = "\n\n".join(blocks)

    # --- recent history (last 3 turns) ---
    history_text = ""
    if history:
        recent = history[-3:]
        lines = []
        for turn in recent:
            lines.append(f"Q: {turn.get('question', '')}")
            lines.append(f"A: {turn.get('answer', '')}")
        history_text = "\n".join(lines)

    if history_text:
        return (
            f"Previous conversation:\n{history_text}\n\n"
            f"Context:\n{context_text}\n\n"
            f"Question: {question}"
        )

    return f"Context:\n{context_text}\n\nQuestion: {question}"


def answer_document(
    document: dict,
    question: str,
    top_k: int = 3,
    candidate_pool: int = 60,
    answer_model: str = "poolside/laguna-s-2.1:free",
) -> dict:
    """Answer a *question* using the prepared *document*'s FAISS index.

    1. Retrieves top-k chunks via :func:`search_document`, passing
       ``document["history"]`` so the retriever can optionally use it.
    2. Builds a grounded prompt with :func:`build_grounded_user_prompt`.
    3. When ``OPENROUTER_API_KEY`` is set, calls the OpenRouter LLM with
       the retrieved chunks as context (citation format ``[Page X]``).
    4. When the API key is missing — or the LLM call fails — falls back to
       a purely local answer via :func:`best_sentence_answer`.

    Always returns ``answer`` (str), ``citations`` (list[int]), and
    ``sources`` (list[dict]).
    """
    import os

    # --- retrieval ---
    history = document.get("history") if isinstance(document.get("history"), list) else None
    hits = search_document(
        question=question,
        document=document,
        top_k=top_k,
        candidate_pool=candidate_pool,
        history=history,
    )

    if not hits:
        return {
            "answer": "No relevant information found in the document.",
            "citations": [],
            "sources": [],
        }

    sources = build_sources(hits)

    # --- try LLM path ---
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        try:
            from openai import OpenAI

            system_prompt = (
                "You are a helpful teaching assistant. "
                "Answer the question based ONLY on the provided context chunks from a PDF document. "
                "Cite factual claims with [Page X]. "
                "If the answer is not in the context, say that the document does not provide enough information. "
                "Never invent a page number. "
                "When writing formulas, use strict LaTeX syntax (e.g. $x^2$ for inline, $$x^2$$ for block)."
            )

            user_prompt = build_grounded_user_prompt(question, hits, history)

            client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            response = client.chat.completions.create(
                model=os.getenv("OPENROUTER_MODEL", answer_model),
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw_answer = response.choices[0].message.content or ""
            citations = extract_citations(raw_answer, hits)
            return {
                "answer": raw_answer,
                "citations": citations,
                "sources": sources,
            }
        except Exception:
            pass  # fall through to local extraction

    # --- local fallback ---
    local_answer = best_sentence_answer(question, hits)
    citations = extract_citations(local_answer, hits)
    return {
        "answer": local_answer,
        "citations": citations,
        "sources": sources,
    }


def stream_answer_document(
    document: dict,
    question: str,
    top_k: int = 3,
    candidate_pool: int = 60,
    answer_model: str = "poolside/laguna-s-2.1:free",
):
    """Generator that streams answer events for SSE.

    Yields dicts: ``{"type": "chunk", "content": "..."}`` for each text
    fragment, then ``{"type": "done", "citations": [...], "sources": [...]}``
    when the stream finishes.  On errors or missing API key falls back to
    a single-chunk local answer with the same event contract.
    """
    import os

    history = document.get("history") if isinstance(document.get("history"), list) else None
    hits = search_document(
        question=question,
        document=document,
        top_k=top_k,
        candidate_pool=candidate_pool,
        history=history,
    )

    if not hits:
        yield {"type": "chunk", "content": "No relevant information found in the document."}
        yield {"type": "done", "citations": [], "sources": []}
        return

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        local_answer = best_sentence_answer(question, hits)
        yield {"type": "chunk", "content": local_answer}
        yield {"type": "done", "citations": extract_citations(local_answer, hits), "sources": build_sources(hits)}
        return

    try:
        from openai import OpenAI

        system_prompt = (
            "You are a helpful teaching assistant. "
            "Answer the question based ONLY on the provided context chunks from a PDF document. "
            "Cite factual claims with [Page X]. "
            "If the answer is not in the context, say that the document does not provide enough information. "
            "Never invent a page number. "
            "When writing formulas, use strict LaTeX syntax (e.g. $x^2$ for inline, $$x^2$$ for block)."
        )
        user_prompt = build_grounded_user_prompt(question, hits, history)

        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        stream = client.chat.completions.create(
            model=os.getenv("OPENROUTER_MODEL", answer_model),
            temperature=0.0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )

        full_answer = ""
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full_answer += delta.content
                yield {"type": "chunk", "content": delta.content}

        citations = extract_citations(full_answer, hits)
        sources = build_sources(hits)
        yield {"type": "done", "citations": citations, "sources": sources}

    except Exception:
        local_answer = best_sentence_answer(question, hits)
        yield {"type": "chunk", "content": local_answer}
        yield {"type": "done", "citations": extract_citations(local_answer, hits), "sources": build_sources(hits)}


def append_history(
    document: dict,
    question: str,
    result: dict,
) -> list[dict]:
    """Append a Q&A entry to *document*'s in-memory history and return the
    updated list.

    Each entry records ``question``, ``answer``, ``citations``,
    ``sources``, and an ISO-format ``timestamp``.  When
    ``document["history"]`` is missing or not a list it is initialised
    first.
    """
    from datetime import datetime, timezone

    if not isinstance(document.get("history"), list):
        document["history"] = []

    entry = {
        "question": question,
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "sources": result.get("sources", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    document["history"].append(entry)
    return document["history"]


def answer_document_turn(
    document: dict,
    question: str,
    top_k: int = 3,
    candidate_pool: int = 60,
    answer_model: str = "poolside/laguna-s-2.1:free",
) -> dict:
    """Answer a *question* **and** persist the turn in the document's history.

    Calls :func:`answer_document` then :func:`append_history`, returning a
    dict with ``answer``, ``citations``, ``sources``, and the updated
    ``history`` list.
    """
    result = answer_document(
        document=document,
        question=question,
        top_k=top_k,
        candidate_pool=candidate_pool,
        answer_model=answer_model,
    )
    history = append_history(document, question, result)
    result["history"] = history
    return result


def answer_chat_turn(
    document: dict,
    message: str,
    top_k: int = 3,
    candidate_pool: int = 60,
    answer_model: str = "poolside/laguna-s-2.1:free",
) -> dict:
    """Route-facing alias for :func:`answer_document_turn`.

    Accepts *message* (the user's chat text) instead of *question* for
    consistency with chat-route naming conventions.  Behaviour is otherwise
    identical.
    """
    return answer_document_turn(
        document=document,
        question=message,
        top_k=top_k,
        candidate_pool=candidate_pool,
        answer_model=answer_model,
    )


# ---------------------------------------------------------------------------
# Simple retrieval evaluation
# ---------------------------------------------------------------------------

def normalize_for_match(text: str) -> str:
    """Normalise *text* so that fuzzy string comparisons are reliable.

    Lowercases, strips surrounding whitespace, removes punctuation, and
    collapses runs of whitespace into single spaces.  Returns ``""`` for
    empty input — never raises.
    """
    if not text or not text.strip():
        return ""
    lower_text = text.lower().strip()
    # Keep only letters, digits, and spaces
    cleaned = re.sub(r"[^a-z0-9 ]", " ", lower_text)
    # Collapse multiple spaces into one
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def contains_any_answer(text: str, answers: list[str]) -> bool:
    """Return ``True`` when *text* contains at least one of the expected
    *answers* after both are normalised.

    Empty *text* or empty *answers* returns ``False`` — never raises.
    """
    if not text or not answers:
        return False
    norm_text = normalize_for_match(text)
    if not norm_text:
        return False
    for answer in answers:
        norm_ans = normalize_for_match(answer)
        if norm_ans and norm_ans in norm_text:
            return True
    return False


def evaluate_questions(
    eval_set: list[dict],
    documents_by_name: dict[str, dict],
    top_k: int = 3,
    candidate_pool: int = 60,
) -> "pandas.DataFrame":  # noqa: F821
    """Run a retrieval + answer evaluation over every question in
    *eval_set* and return a DataFrame summarising the results.

    *eval_set*
        A list of dicts, each describing one test case::

            {
                "question": "...",
                "pdf_name": "pdf1.pdf",
                "answer_pages": [3, 5],
                "answers": ["acceptable answer A", "acceptable answer B"],
            }

    *documents_by_name*
        Mapping from ``pdf_name`` (e.g. ``"pdf1.pdf"``) to a prepared
        document record (the return value of :func:`prepare_rag_document`).

    For each question the function:

    1. Looks up the document by ``pdf_name``.
    2. Calls :func:`answer_document` with the local fallback (no API call
       overhead).
    3. Computes **retrieval_hit**: whether at least one retrieved chunk
       page overlaps the gold *answer_pages*.
    4. Computes **answer_hit**: whether the produced answer contains at
       least one gold *answers* string (after normalisation, see
       :func:`contains_any_answer`).

    Returns a DataFrame with columns: ``question``, ``pdf_name``, ``gold_pages``,
    ``retrieved_pages``, ``local_answer``, ``retrieval_hit``, ``answer_hit``.
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "pandas is required for evaluation; install it with `pip install pandas`"
        ) from exc

    rows: list[dict] = []

    for i, case in enumerate(eval_set):
        question = case.get("question", "")
        pdf_name = case.get("pdf_name", "")
        gold_pages = case.get("answer_pages", [])
        gold_answers = case.get("answers", [])

        # --- lookup document ---
        document = documents_by_name.get(pdf_name)
        if document is None:
            print(f"[eval] skipping Q{i}: pdf_name={pdf_name!r} not found in documents_by_name")
            rows.append({
                "question": question,
                "pdf_name": pdf_name,
                "gold_pages": gold_pages if isinstance(gold_pages, list) else [],
                "retrieved_pages": [],
                "local_answer": "[document not found]",
                "retrieval_hit": False,
                "answer_hit": False,
            })
            continue

        # --- answer ---
        result = answer_document(
            document=document,
            question=question,
            top_k=top_k,
            candidate_pool=candidate_pool,
        )

        # --- retrieved page numbers from sources ---
        retrieved_pages = sorted(set(
            s["page"] for s in result.get("sources", []) if "page" in s
        ))

        # --- retrieval_hit: any retrieved page in gold_pages? ---
        if isinstance(gold_pages, list) and gold_pages:
            retrieval_hit = bool(set(retrieved_pages) & set(gold_pages))
        else:
            retrieval_hit = None  # no gold pages to check against

        # --- answer_hit: answer contains any gold answer string? ---
        if isinstance(gold_answers, list) and gold_answers:
            answer_hit = contains_any_answer(result["answer"], gold_answers)
        else:
            answer_hit = None  # no gold answers to check against

        rows.append({
            "question": question,
            "pdf_name": pdf_name,
            "gold_pages": gold_pages if isinstance(gold_pages, list) else [],
            "retrieved_pages": retrieved_pages,
            "local_answer": result["answer"],
            "retrieval_hit": retrieval_hit,
            "answer_hit": answer_hit,
        })

    return pd.DataFrame(rows)
