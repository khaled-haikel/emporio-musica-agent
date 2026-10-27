"""Extract the store policy PDF into data/policies.md.

The PDF is small, so the whole Markdown file goes into the system prompt (no RAG).
pypdf's "layout" mode keeps the visual structure; the clean-up below turns it into
Markdown deterministically, so re-running the script always yields the same file.

Usage: python scripts/extract_policies.py
"""

import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT = DATA_DIR / "policies.md"

PAGE_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
TABLE_GAP = re.compile(r"\s{3,}")
NUMBERED_ITEM = re.compile(r"^(\d+)\.\s{2,}(.*)")
SECTION = re.compile(r"^\d+\.\s+\S")
SUBSECTION = re.compile(r"^\d+\.\d+\s+\S")


def _is_heading(text: str, pattern: re.Pattern) -> bool:
    return bool(pattern.match(text)) and len(text) < 60 and ":" not in text and not text.endswith(".")


def to_markdown(raw_pages: list[str]) -> str:
    out: list[str] = []
    kind = "blank"  # kind of the last emitted line: blank, heading, para, bullet, table, line
    pending_blank = False

    def emit(line: str, new_kind: str) -> None:
        nonlocal kind
        out.append(line)
        kind = new_kind

    for page in raw_pages:
        for raw in page.splitlines():
            line = raw.rstrip()
            text = line.strip()
            indent = len(line) - len(line.lstrip())

            if text == PAGE_HEADER or re.fullmatch(r"Página \d+", text):
                continue
            if not text:
                pending_blank = True
                continue

            cells = TABLE_GAP.split(text)
            numbered = NUMBERED_ITEM.match(text)

            # Table rows are separated by blank lines in the layout output: keep them in one table.
            if kind == "table" and not numbered:
                if len(cells) == 1 and indent >= 20:  # wrapped cell
                    out[-1] = out[-1][: -len(" |")] + " " + text + " |"
                    pending_blank = False
                    continue
                if len(cells) >= 2:
                    emit("| " + " | ".join(cells) + " |", "table")
                    pending_blank = False
                    continue

            if pending_blank:
                if kind != "blank":
                    emit("", "blank")
                pending_blank = False

            if text.startswith("•"):
                emit("- " + " ".join(text.lstrip("• ").split()), "bullet")
            elif numbered:
                emit(f"{numbered.group(1)}. " + " ".join(numbered.group(2).split()), "bullet")
            elif kind == "bullet" and indent >= 4:
                out[-1] += " " + " ".join(text.split())
            elif len(cells) >= 2:
                if kind != "blank":
                    emit("", "blank")
                emit("| " + " | ".join(cells) + " |", "table")
                emit("|" + "---|" * len(cells), "table")
            elif _is_heading(text, SUBSECTION):
                if kind != "blank":
                    emit("", "blank")
                emit("### " + text, "heading")
            elif _is_heading(text, SECTION):
                if kind != "blank":
                    emit("", "blank")
                emit("## " + text, "heading")
            elif kind == "para" and indent < 10:
                out[-1] += " " + text
            else:
                if kind in ("table", "heading"):
                    emit("", "blank")
                emit(text, "para" if indent < 10 else "line")

    body = "\n".join(out).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body + "\n"


def main() -> None:
    pdf_path = next(DATA_DIR.glob("pol*ticas_da_loja.pdf"))
    reader = PdfReader(pdf_path)
    pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]
    header = (
        f"<!-- Generated from data/{pdf_path.name} by scripts/extract_policies.py. "
        "Do not edit by hand. -->\n\n"
    )
    OUTPUT.write_text(header + to_markdown(pages), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(reader.pages)} pages)")


if __name__ == "__main__":
    main()
