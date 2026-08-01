#!/usr/bin/env python3
"""PDF Summary Tool - Extracts text from PDF and summarizes via LLM."""

import argparse
import os
import sys
from typing import List, Tuple

import pymupdf
from dotenv import load_dotenv
from openai import OpenAI

MAX_CHARS = 8000


def extract_text(pdf_path: str) -> Tuple[List[Tuple[int, str]], int]:
    """Extract text per page from a PDF file.

    Returns (pages, total_pages) where pages is a list of (page_num, text)
    tuples. Raises ValueError if the file can't be opened or no extractable
    text is found.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        raise ValueError(f"无法打开 PDF 文件: {e}")

    pages: List[Tuple[int, str]] = []
    has_text = False

    for i, page in enumerate(doc):
        text = page.get_text().strip()
        page_num = i + 1
        if text:
            has_text = True
        pages.append((page_num, text))

    doc.close()

    if not has_text:
        raise ValueError(
            "该 PDF 中没有可提取的文本内容。"
            "可能是一份扫描文档或图片型 PDF，"
            "建议使用 OCR 工具先进行文字识别。"
        )

    return pages, len(pages)


def build_prompt(pages: List[Tuple[int, str]]) -> str:
    """Build the prompt from extracted pages, truncating if too long."""
    parts: List[str] = []
    total_chars = 0
    truncated = False

    for page_num, text in pages:
        if not text:
            continue

        page_header = f"--- 第 {page_num} 页 ---\n"
        remaining = MAX_CHARS - total_chars

        if remaining <= 0:
            truncated = True
            break

        if len(page_header) + len(text) > remaining:
            available = remaining - len(page_header)
            if available > 0:
                parts.append(page_header + text[:available])
            total_chars = MAX_CHARS
            truncated = True
            break

        parts.append(page_header + text)
        total_chars += len(page_header) + len(text)

    content = "\n\n".join(parts)
    if truncated:
        content += (
            f"\n\n[注意: 文档过长，已截断，仅展示前 {MAX_CHARS} 字符]"
        )

    return content


def call_llm(text_content: str) -> str:
    """Send text to LLM via OpenRouter and return the summary."""
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("未找到 OPENROUTER_API_KEY，请检查 .env 文件")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    system_prompt = (
        "你是一个专业的文档总结助手。请根据提供的 PDF 文本内容，"
        "生成一份结构化的中文摘要。\n\n"
        "要求：\n"
        "1. 输出必须严格包含以下三个部分，使用指定的标题：\n"
        "   - 概述（Overview）\n"
        "   - 要点（Key Points）\n"
        "   - 局限性（Limitations）\n\n"
        '2. 「概述」部分：用 2-4 句话概括文档的核心内容和目的。\n\n'
        '3. 「要点」部分：列出 3-8 个关键要点。'
        '每个要点必须以 [Page X] 开头标注页码引用，'
        '其中 X 是该信息所在的页码。\n\n'
        '4. 「局限性」部分：说明文档可能存在的局限、'
        '缺失的信息或不明确的地方。\n\n'
        "5. 直接输出摘要内容，不要加任何开场白或结束语。"
    )

    response = client.chat.completions.create(
        model="qwen/qwen3.5-flash-02-23",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "以下是 PDF 文档的内容，请为其生成摘要：\n\n"
                    f"{text_content}"
                ),
            },
        ],
        temperature=0.3,
        max_tokens=2000,
    )

    return response.choices[0].message.content


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PDF 摘要工具 - 读取 PDF 文件并生成结构化摘要"
    )
    parser.add_argument(
        "pdf_path",
        type=str,
        nargs="?",
        help="PDF 文件的路径",
    )

    args = parser.parse_args()

    if args.pdf_path is None:
        parser.print_help()
        print("\n错误: 请提供 PDF 文件路径")
        sys.exit(1)

    if not os.path.isfile(args.pdf_path):
        print(f"错误: 找不到文件 '{args.pdf_path}'")
        print("用法: python3 pdf_summary.py <PDF文件路径>")
        sys.exit(1)

    if not args.pdf_path.lower().endswith(".pdf"):
        print(f"错误: '{args.pdf_path}' 不是一个 PDF 文件")
        print("用法: python3 pdf_summary.py <PDF文件路径>")
        sys.exit(1)

    try:
        pages, _total_pages = extract_text(args.pdf_path)
        prompt_text = build_prompt(pages)

        print("正在生成摘要...\n")
        summary = call_llm(prompt_text)
        print(summary)

    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"错误: 处理 PDF 时发生异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
