"""File-type validation and text/table recovery for PDF, DOCX, XLSX, CSV and images."""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field

from ai_engine.extraction.financial_extractor import PageContent
from ai_engine.ocr.engine import OCREngine

log = logging.getLogger("intellicredit.parsers")

ALLOWED_EXTENSIONS = {
    "pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "csv": "csv", "jpg": "image", "jpeg": "image", "png": "image",
}
MAX_PDF_PAGES = 400
MAX_TABLE_PAGES = 80
MAX_SHEET_ROWS = 20000


class DocumentParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedDocument:
    kind: str
    pages: list[PageContent]
    page_count: int
    is_scanned: bool = False
    ocr_used: bool = False
    ocr_confidence: float | None = None
    structured_tables: list[tuple[str, list[list]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def detect_kind(data: bytes, filename: str) -> str:
    """Verify the declared extension against the file's magic bytes."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentParseError("unsupported_type", f"Unsupported file type '.{ext or '?'}'. Allowed: PDF, DOCX, XLSX, CSV, JPG, PNG.")
    if not data:
        raise DocumentParseError("empty_file", "The uploaded file is empty.")
    expected = ALLOWED_EXTENSIONS[ext]
    head = data[:8]
    if head.startswith(b"%PDF"):
        actual = "pdf"
    elif head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff"):
        actual = "image"
    elif head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise DocumentParseError("corrupt_file", "The file looks like an Office document but is corrupt.") from exc
        if "word/document.xml" in names:
            actual = "docx"
        elif "xl/workbook.xml" in names:
            actual = "xlsx"
        else:
            actual = "zip"
    elif b"\x00" not in data[:4096]:
        actual = "csv"
    else:
        actual = "binary"
    if actual != expected:
        raise DocumentParseError(
            "type_mismatch", f"File content does not match its '.{ext}' extension (detected: {actual})."
        )
    return expected


def _layout_lines(page) -> str:
    """Rebuild visual lines from word boxes so table rows stay on one line."""
    words = page.get_text("words")
    if not words:
        return ""
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[list] = []
    for w in words:
        yc = (w[1] + w[3]) / 2
        height = w[3] - w[1]
        if lines and abs(lines[-1][0] - yc) <= max(2.5, height * 0.45):
            lines[-1][1].append(w)
        else:
            lines.append([yc, [w]])
    out = []
    for _, ws in lines:
        ws.sort(key=lambda w: w[0])
        parts = [ws[0][4]]
        for prev, cur in zip(ws, ws[1:]):
            gap = cur[0] - prev[2]
            parts.append(("    " if gap > 12 else " ") + cur[4])
        out.append("".join(parts))
    return "\n".join(out)


def _parse_pdf(data: bytes, ocr: OCREngine | None) -> ParsedDocument:
    import fitz
    import pdfplumber

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentParseError("corrupt_file", f"The PDF could not be opened: {exc}") from exc
    if doc.needs_pass:
        raise DocumentParseError("password_protected", "The PDF is password-protected. Upload an unlocked copy.")
    if doc.page_count == 0:
        raise DocumentParseError("empty_document", "The PDF contains no pages.")
    if doc.page_count > MAX_PDF_PAGES:
        raise DocumentParseError("too_many_pages", f"PDF has {doc.page_count} pages; the limit is {MAX_PDF_PAGES}.")

    pages: list[PageContent] = []
    warnings: list[str] = []
    ocr_pages, ocr_confs, blank_pages = 0, [], 0
    text_page_numbers: list[int] = []
    for idx in range(doc.page_count):
        page = doc[idx]
        text = _layout_lines(page)
        if len(text.strip()) >= 30:
            pages.append(PageContent(page_number=idx + 1, text=text, method="text"))
            text_page_numbers.append(idx + 1)
            continue
        has_images = bool(page.get_images(full=True))
        if not has_images and not text.strip():
            blank_pages += 1
            continue
        if ocr is None:
            warnings.append(f"Page {idx + 1} appears scanned but no OCR engine is available; its content was not read.")
            if text.strip():
                pages.append(PageContent(page_number=idx + 1, text=text, method="text"))
            continue
        pix = page.get_pixmap(dpi=220)
        result = ocr.image_to_text(pix.tobytes("png"))
        ocr_pages += 1
        ocr_confs.append(result.confidence)
        if result.confidence < 0.6:
            warnings.append(f"Page {idx + 1}: low OCR quality (confidence {result.confidence:.0%}); verify extracted values.")
        pages.append(PageContent(page_number=idx + 1, text=result.text, method="ocr", ocr_confidence=result.confidence))

    if not pages:
        raise DocumentParseError("empty_document", "No readable text or images were found in the PDF.")

    # Table extraction for digital pages (bounded for very large reports)
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            by_num = {p.page_number: p for p in pages}
            for pno in text_page_numbers[:MAX_TABLE_PAGES]:
                ppage = pdf.pages[pno - 1]
                if not re.search(r"\d", by_num[pno].text):
                    continue
                tables = ppage.extract_tables() or []
                clean = [[[("" if c is None else str(c).replace("\n", " ").strip()) for c in row] for row in t] for t in tables if t]
                by_num[pno].tables = clean
    except Exception as exc:  # table extraction is best-effort
        log.warning("pdfplumber table extraction failed: %s", exc)
        warnings.append("Table extraction failed for this PDF; values were read from page text only.")

    structured = [(f"page {p.page_number}", row_t) for p in pages for row_t in p.tables]
    if blank_pages:
        warnings.append(f"{blank_pages} blank page(s) skipped.")
    return ParsedDocument(
        kind="pdf", pages=pages, page_count=doc.page_count, is_scanned=ocr_pages > 0 and ocr_pages >= len(pages) / 2,
        ocr_used=ocr_pages > 0, ocr_confidence=(sum(ocr_confs) / len(ocr_confs)) if ocr_confs else None,
        structured_tables=structured, warnings=warnings,
    )


def _parse_docx(data: bytes) -> ParsedDocument:
    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentParseError("corrupt_file", f"The DOCX could not be opened: {exc}") from exc
    paragraphs = [p.text for p in d.paragraphs if p.text.strip()]
    tables = []
    for t in d.tables:
        rows = [[c.text.strip() for c in r.cells] for r in t.rows]
        tables.append(rows)
    if not paragraphs and not tables:
        raise DocumentParseError("empty_document", "The DOCX contains no text.")
    pages: list[PageContent] = []
    chunk = 40
    for i in range(0, max(1, len(paragraphs)), chunk):
        seg = paragraphs[i: i + chunk]
        if seg:
            n = len(pages) + 1
            pages.append(PageContent(page_number=n, text="\n".join(seg), method="docx", label=f"Section {n}"))
    for t in tables:
        n = len(pages) + 1
        pages.append(PageContent(page_number=n, text="\n".join("    ".join(r) for r in t), tables=[t], method="docx",
                                 label=f"Table {n}"))
    return ParsedDocument(kind="docx", pages=pages, page_count=len(pages),
                          structured_tables=[(f"table {i + 1}", t) for i, t in enumerate(tables)])


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "strftime"):
        return v.strftime("%d-%m-%Y")
    return str(v).strip()


def _parse_xlsx(data: bytes) -> ParsedDocument:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise DocumentParseError("corrupt_file", f"The XLSX could not be opened: {exc}") from exc
    pages: list[PageContent] = []
    structured: list[tuple[str, list[list]]] = []
    warnings: list[str] = []
    for idx, ws in enumerate(wb.worksheets):
        rows = []
        for r_i, row in enumerate(ws.iter_rows(values_only=True)):
            if r_i >= MAX_SHEET_ROWS:
                warnings.append(f"Sheet '{ws.title}' truncated at {MAX_SHEET_ROWS} rows.")
                break
            if any(v not in (None, "") for v in row):
                rows.append(list(row))
        if not rows:
            continue
        text_rows = [[_cell_text(v) for v in r] for r in rows]
        text = "\n".join("    ".join(c for c in r if c) for r in text_rows[:3000])
        pages.append(PageContent(page_number=idx + 1, text=f"[Sheet: {ws.title}]\n{text}", tables=[text_rows],
                                 method="spreadsheet", label=ws.title))
        structured.append((ws.title, rows))
    wb.close()
    if not pages:
        raise DocumentParseError("empty_document", "The workbook contains no data.")
    return ParsedDocument(kind="xlsx", pages=pages, page_count=len(pages), structured_tables=structured, warnings=warnings)


def _parse_csv(data: bytes) -> ParsedDocument:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 always decodes
        raise DocumentParseError("encoding_error", "CSV encoding could not be determined.")
    if not text.strip():
        raise DocumentParseError("empty_document", "The CSV file is empty.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if len(rows) > MAX_SHEET_ROWS:
        rows = rows[:MAX_SHEET_ROWS]
    page_text = "\n".join("    ".join(c.strip() for c in r if c.strip()) for r in rows[:3000])
    return ParsedDocument(kind="csv", pages=[PageContent(page_number=1, text=page_text, tables=[rows], method="csv")],
                          page_count=1, structured_tables=[("csv", rows)])


def _parse_image(data: bytes, ocr: OCREngine | None) -> ParsedDocument:
    from PIL import Image

    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception as exc:
        raise DocumentParseError("corrupt_file", "The image file is corrupt or unreadable.") from exc
    if ocr is None:
        raise DocumentParseError("ocr_unavailable", "No OCR engine is configured, so this image cannot be read.")
    result = ocr.image_to_text(data)
    if not result.text.strip():
        raise DocumentParseError("ocr_no_text", "OCR could not recognise any text in this image.")
    warnings = []
    if result.confidence < 0.6:
        warnings.append(f"Low OCR quality (confidence {result.confidence:.0%}); verify all extracted values.")
    return ParsedDocument(kind="image", pages=[PageContent(page_number=1, text=result.text, method="ocr",
                                                           ocr_confidence=result.confidence)],
                          page_count=1, is_scanned=True, ocr_used=True, ocr_confidence=result.confidence,
                          warnings=warnings)


def parse_document(data: bytes, kind: str, ocr: OCREngine | None) -> ParsedDocument:
    if kind == "pdf":
        return _parse_pdf(data, ocr)
    if kind == "docx":
        return _parse_docx(data)
    if kind == "xlsx":
        return _parse_xlsx(data)
    if kind == "csv":
        return _parse_csv(data)
    if kind == "image":
        return _parse_image(data, ocr)
    raise DocumentParseError("unsupported_type", f"Unsupported document kind: {kind}")
