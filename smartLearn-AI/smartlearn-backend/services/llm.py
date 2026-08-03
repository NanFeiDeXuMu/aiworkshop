
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

SYSTEM_PROMPT = (
    "You answer messages only from the supplied PDF text. "
    "Cite factual claims with [Page X]. "
    "If the answer is not in the PDF, say that the document does not provide enough information. "
    "Never invent a page number."
)


def _build_messages(pages: list[dict], message: str) -> list[dict]:
    """Build the messages list for the LLM call."""
    document_text = "\n\n".join(
        f"### [Page {page['page']}]\n{page['text']}"
        for page in pages
        if page["text"]
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"PDF text:\n{document_text}\n\nmessage: {message}",
        },
    ]


def answer_from_pages(pages: list[dict], message: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    response = client.chat.completions.create(
        model=os.getenv("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free"),
        temperature=0.0,
        messages=_build_messages(pages, message),
    )
    return response.choices[0].message.content or ""


def stream_answer_from_pages(pages: list[dict], message: str):
    """Generator that yields content chunks from the LLM via streaming."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    stream = client.chat.completions.create(
        model=os.getenv("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free"),
        temperature=0.0,
        messages=_build_messages(pages, message),
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
