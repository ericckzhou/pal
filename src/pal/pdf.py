"""PDF text extraction for retrieval: Docling when it and its models are on this PC, pypdf otherwise.

Both engines return text *blocks* ({heading, page, text}) in reading order; ingest.py packs them into chunks.

Privacy: both engines run entirely on this PC. Docling's models are fetched once by `pal pdf-models` (an owner-run
command, the only network step). While converting, Hugging Face is forced offline, and under `pal serve` the
egress guard refuses any other connection, so document bytes never leave the machine.
"""

import io
import logging
import os
import re
import threading
from collections import Counter
from functools import cache
from pathlib import Path

# Must be set before huggingface_hub is first imported (Docling imports it lazily, below).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

log = logging.getLogger("pal.pdf")

ENGINE_ENV = "PAL_PDF_ENGINE"  # auto (default) | docling | pypdf
PARSER_PYPDF = "pypdf-2"
PARSER_DOCLING = "docling-1"
_EDGE_LINES = 2  # lines at the top and bottom of a page that may be a running header/footer


class PdfError(ValueError):
    pass


def models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "pal" / "docling-models"


@cache
def docling_ready() -> bool:
    """Docling is used when it's installed and its models were downloaded (unless forced off).

    Checked once per process: installing Docling or its models takes a pal restart anyway.
    """
    engine = os.environ.get(ENGINE_ENV, "auto")
    if engine == "pypdf":
        return False
    try:
        import docling  # noqa: F401
    except ImportError:
        if engine == "docling":
            log.warning("%s=docling but Docling isn't installed; using pypdf", ENGINE_ENV)
        return False
    folder = models_dir()
    if not (folder.is_dir() and any(folder.iterdir())):
        log.warning("Docling is installed but its models aren't; run `pal pdf-models`. Using pypdf meanwhile")
        return False
    return True


def current_parser() -> str:
    return PARSER_DOCLING if docling_ready() else PARSER_PYPDF


def extract(filename: str, data: bytes) -> tuple[str | None, list[dict], str]:
    """Return (title or None, blocks, parser id). Raises PdfError if the file can't be read."""
    if docling_ready():
        try:
            title, blocks = _docling(filename, data)
            return title, blocks, PARSER_DOCLING
        except Exception:
            log.warning("Docling could not convert %s; falling back to pypdf", filename, exc_info=True)
    title, blocks = _pypdf(data)
    return title, blocks, PARSER_PYPDF


# ---------- pypdf ----------

def _pypdf(data: bytes) -> tuple[str | None, list[dict]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        title = reader.metadata.title if reader.metadata else None
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise PdfError(f"Could not read this PDF ({type(exc).__name__}).") from exc
    blocks = []
    for page_no, text in enumerate(strip_running_lines(pages), start=1):
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join words hyphenated across a line break
        blocks.extend({"heading": None, "page": page_no, "text": p.strip()}
                      for p in re.split(r"\n\s*\n", text) if p.strip())
    return (str(title) if title else None), blocks


def _line_key(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.lower().split()))


def strip_running_lines(pages: list[str]) -> list[str]:
    """Drop headers/footers: lines near a page edge that recur (page numbers aside) on at least half the pages."""
    if len(pages) < 3:
        return pages
    split = [text.splitlines() for text in pages]
    edge_rows = [_edge_rows(lines) for lines in split]
    edges: Counter[str] = Counter()
    for lines, rows in zip(split, edge_rows, strict=True):
        edges.update({_line_key(lines[i]) for i in rows})
    running = {k for k, n in edges.items() if n >= max(3, len(pages) / 2)}
    if not running:
        return pages
    return ["\n".join(ln for i, ln in enumerate(lines) if not (i in rows and _line_key(ln) in running))
            for lines, rows in zip(split, edge_rows, strict=True)]


def _edge_rows(lines: list[str]) -> set[int]:
    """Indexes of the first and last few non-blank lines, never more than half the page (short pages keep a body)."""
    filled = [i for i, ln in enumerate(lines) if ln.strip()]
    k = min(_EDGE_LINES, len(filled) // 2)
    return set(filled[:k] + filled[len(filled) - k:]) if k else set()


# ---------- Docling ----------

_lock = threading.Lock()  # one conversion at a time: models are large and the converter isn't documented thread-safe
_converters: dict[bool, object] = {}


def _converter(ocr: bool):
    if ocr not in _converters:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions(artifacts_path=str(models_dir()), do_ocr=ocr, do_table_structure=True)
        _converters[ocr] = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _converters[ocr]


def _docling(filename: str, data: bytes) -> tuple[str | None, list[dict]]:
    from docling.datamodel.base_models import DocumentStream

    with _lock:
        doc = _converter(False).convert(DocumentStream(name=filename, stream=io.BytesIO(data))).document
        if not doc.texts:  # no text layer: a scan. OCR is slow, so only now.
            log.info("no text layer in %s; running OCR", filename)
            doc = _converter(True).convert(DocumentStream(name=filename, stream=io.BytesIO(data))).document
        title, blocks = docling_blocks(doc)
        if not blocks and doc.texts:
            # Everything was classed as page header/footer (odd layouts do this); keep the text rather than lose it.
            blocks = [{"heading": None, "page": next((p.page_no for p in (t.prov or [])), None), "text": t.text.strip()}
                      for t in doc.texts if t.text and t.text.strip()]
    return title, blocks


def docling_blocks(doc) -> tuple[str | None, list[dict]]:
    """Blocks from a DoclingDocument: one per paragraph, list or table, with its section path and first page.

    Page headers/footers are excluded by Docling (they're "furniture"). The document title is returned separately
    and dropped from the heading path, as with Markdown H1s.
    """
    from docling_core.transforms.chunker import HierarchicalChunker

    title = next((t.text.strip() for t in doc.texts if getattr(t.label, "value", t.label) == "title"), None)
    blocks = []
    for chunk in HierarchicalChunker().chunk(dl_doc=doc):
        text = (chunk.text or "").strip()
        if not text:
            continue
        headings = [h.strip() for h in (chunk.meta.headings or []) if h and h.strip()]
        if headings and title and headings[0] == title:
            headings = headings[1:]
        page = next((p.page_no for item in chunk.meta.doc_items for p in (item.prov or [])), None)
        blocks.append({"heading": " › ".join(headings) or None, "page": page, "text": text})
    return title, blocks


def download_models() -> Path:
    """Fetch Docling's layout, table and OCR models into models_dir(). Needs the network; run outside `pal serve`."""
    os.environ["HF_HUB_OFFLINE"] = "0"
    from docling.utils.model_downloader import download_models as fetch

    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fetch(output_dir=folder, progress=True, with_layout=True, with_tableformer=True, with_rapidocr=True,
          with_code_formula=False, with_picture_classifier=False)
    return folder
