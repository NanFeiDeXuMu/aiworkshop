from services import rag as rag_module
rag = importlib.reload(rag_module)
def preview_records(records: list[dict], columns: list[str], rows: int = 5):
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for preview_records") from exc

    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    usable_columns = [column for column in columns if column in frame.columns]
    return frame[usable_columns].head(rows)


pd.set_option("display.max_colwidth", 150)
pdf1_pages = rag.extract_pages_for_rag(DAY3_DIR / "pdf1.pdf")
print(f"Extracted pages from pdf1.pdf: {len(pdf1_pages)}")
preview_records(pdf1_pages, columns=["page", "text"], rows=10)
