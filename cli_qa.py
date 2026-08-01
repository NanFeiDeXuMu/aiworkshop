"""
CLI Q&A Tool — 接收多段文本和问题，通过 OpenRouter API 调用 LLM
以 [Paragraph X] 格式引用段落来回答问题。
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

# ── 加载 .env，绝不打印 key ──────────────────────────────────────────
load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")
if not API_KEY:
    print("Error: OPENROUTER_API_KEY not found in .env file.")
    sys.exit(1)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL = "qwen/qwen3.5-flash-02-23"

client = OpenAI(base_url=OPENROUTER_BASE, api_key=API_KEY)


# ── 工具函数 ──────────────────────────────────────────────────────────

def split_paragraphs(text: str) -> list[str]:
    """按空行分割段落；若无空行则按单行分割。"""
    # 先尝试按空行分割
    if "\n\n" in text:
        return [p.strip() for p in text.split("\n\n") if p.strip()]
    # 无空行则每行视为一个段落
    return [line.strip() for line in text.split("\n") if line.strip()]


def read_text() -> list[str]:
    """读取多行文本，以单独一行的 END 终止。返回段落列表。"""
    print("Paste your text below. Type 'END' on a new line when finished:\n")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)

    return split_paragraphs("\n".join(lines))


def read_text_from_file(filepath: str) -> list[str]:
    """从 .txt 文件读取文本，按空行分割为段落。"""
    if not os.path.isfile(filepath):
        print(f"Error: File not found — {filepath}")
        sys.exit(1)
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    return split_paragraphs(text)


def build_prompt(paragraphs: list[str], question: str) -> str:
    """构造发送给 LLM 的 prompt。"""
    numbered = "\n\n".join(
        f"[Paragraph {i + 1}]\n{p}" for i, p in enumerate(paragraphs)
    )
    return (
        "You are a precise reading-comprehension assistant. "
        "Answer the user's question using ONLY the numbered paragraphs below. "
        "Cite every claim with the paragraph number like [Paragraph X]. "
        "Example: If the text says: [Paragraph 1] The sky is blue. [Paragraph 2] Grass is green. And the question is: What color is the sky? Your answer should be: The sky is blue [Paragraph 1]."
        "If the answer refers to multiple paragraphs, cite all of them. "
        f"If the answer is NOT in the text, say exactly: "
        "The text does not provide this information.\n\n"
        f"=== TEXT ===\n{numbered}\n=== END TEXT ===\n\n"
        f"Question: {question}"
    )


def ask_llm(prompt: str) -> str:
    """通过 OpenRouter 调用 qwen3.5-flash。"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.choices[0].message.content or ""


# ── 主流程 ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI Q&A Tool — answer questions about a text with paragraph citations."
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a .txt file containing the text to analyze.",
    )
    args = parser.parse_args()

    if args.file:
        paragraphs = read_text_from_file(args.file)
    else:
        paragraphs = read_text()

    # 空文本 → 友好提示，不调 API
    if not paragraphs:
        print("Error: No text provided. Please paste at least one paragraph.")
        sys.exit(0)

    print(f"\nLoaded {len(paragraphs)} paragraph(s). Type 'quit' to exit.\n")

    while True:
        question = input("Enter your question: ").strip()
        if question == "":
            continue
        if question.lower() == "quit":
            print("Goodbye!")
            break

        print("\nThinking...\n")
        prompt = build_prompt(paragraphs, question)
        answer = ask_llm(prompt)
        print(answer)
        print()  # 每个回答后空一行


if __name__ == "__main__":
    main()
