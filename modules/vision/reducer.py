"""Cross-batch reduction for image/PDF Vision results.

Large PDFs are recognized in bounded batches. This module converts those batch
outputs into one page-aware document result without requiring another model
call. It removes repeated headers/footers, de-duplicates repeated paragraphs,
preserves table/page provenance, and reports page gaps or failed batches as
degraded output instead of pretending the document is complete.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Optional

MAX_BATCH_TEXT_CHARS = 2_000_000
MAX_DOCUMENT_TEXT_CHARS = 10_000_000


@dataclass(frozen=True)
class VisionTable:
    page_start: int
    page_end: int
    title: str = ""
    markdown: str = ""
    rows: tuple[tuple[str, ...], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_start": self.page_start,
            "page_end": self.page_end,
            "title": self.title,
            "markdown": self.markdown,
            "rows": [list(row) for row in self.rows],
        }


@dataclass(frozen=True)
class VisionBatchResult:
    page_start: int
    page_end: int
    text: str
    headings: tuple[str, ...] = ()
    tables: tuple[VisionTable, ...] = ()
    warnings: tuple[str, ...] = ()
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_start": self.page_start,
            "page_end": self.page_end,
            "text": self.text,
            "headings": list(self.headings),
            "tables": [table.to_dict() for table in self.tables],
            "warnings": list(self.warnings),
            "success": self.success,
            "error": self.error,
        }


@dataclass(frozen=True)
class PageSection:
    page_start: int
    page_end: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_start": self.page_start,
            "page_end": self.page_end,
            "text": self.text,
        }


@dataclass(frozen=True)
class VisionDocumentResult:
    text: str
    sections: tuple[PageSection, ...]
    headings: tuple[str, ...]
    tables: tuple[VisionTable, ...]
    warnings: tuple[str, ...]
    degraded: bool
    complete_page_ranges: tuple[tuple[int, int], ...]
    missing_pages: tuple[int, ...]
    source_batch_count: int
    successful_batch_count: int

    @property
    def success(self) -> bool:
        return bool(self.text.strip()) and self.successful_batch_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "degraded": self.degraded,
            "text": self.text,
            "sections": [section.to_dict() for section in self.sections],
            "headings": list(self.headings),
            "tables": [table.to_dict() for table in self.tables],
            "warnings": list(self.warnings),
            "complete_page_ranges": [list(value) for value in self.complete_page_ranges],
            "missing_pages": list(self.missing_pages),
            "source_batch_count": self.source_batch_count,
            "successful_batch_count": self.successful_batch_count,
        }


class VisionReductionError(ValueError):
    pass


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _normalise_text(text: str) -> str:
    if not isinstance(text, str):
        raise VisionReductionError("batch text must be a string")
    if len(text) > MAX_BATCH_TEXT_CHARS:
        raise VisionReductionError(
            f"batch text exceeds {MAX_BATCH_TEXT_CHARS} characters"
        )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _candidate_edge_lines(text: str) -> tuple[Optional[str], Optional[str]]:
    lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]
    if not lines:
        return None, None
    return lines[0], lines[-1]


def _repeated_edges(texts: Iterable[str]) -> set[str]:
    firsts: Counter[str] = Counter()
    lasts: Counter[str] = Counter()
    count = 0
    for text in texts:
        count += 1
        first, last = _candidate_edge_lines(text)
        if first and 3 <= len(first) <= 200:
            firsts[first] += 1
        if last and 3 <= len(last) <= 200:
            lasts[last] += 1
    if count < 2:
        return set()
    threshold = max(2, (count + 1) // 2)
    return {
        line
        for line, occurrences in (firsts + lasts).items()
        if occurrences >= threshold
    }


def _remove_edges(text: str, repeated: set[str]) -> str:
    lines = text.splitlines()
    while lines and not _clean_line(lines[0]):
        lines.pop(0)
    while lines and not _clean_line(lines[-1]):
        lines.pop()
    if lines and _clean_line(lines[0]) in repeated:
        lines.pop(0)
    if lines and _clean_line(lines[-1]) in repeated:
        lines.pop()
    return _normalise_text("\n".join(lines)) if lines else ""


def _paragraph_key(paragraph: str) -> str:
    value = re.sub(r"\s+", " ", paragraph).strip().lower()
    value = re.sub(r"[^\w\s]", "", value)
    return value


def _dedupe_paragraphs(text: str, seen: set[str]) -> str:
    retained: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        key = _paragraph_key(paragraph)
        # Very short labels/headings may legitimately recur; de-duplicate only
        # substantive paragraphs.
        if len(key) >= 40 and key in seen:
            continue
        if len(key) >= 40:
            seen.add(key)
        retained.append(paragraph)
    return "\n\n".join(retained)


def _dedupe_strings(values: Iterable[str]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean_line(str(raw))
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return tuple(output)


def _coerce_table(
    raw: VisionTable | Mapping[str, Any],
    *,
    page_start: int,
    page_end: int,
) -> VisionTable:
    if isinstance(raw, VisionTable):
        return raw
    if not isinstance(raw, Mapping):
        raise VisionReductionError("table must be VisionTable or mapping")
    rows_raw = raw.get("rows", [])
    rows: list[tuple[str, ...]] = []
    if isinstance(rows_raw, Iterable) and not isinstance(rows_raw, (str, bytes)):
        for row in rows_raw:
            if isinstance(row, Iterable) and not isinstance(row, (str, bytes)):
                rows.append(tuple(str(cell) for cell in row))
    return VisionTable(
        page_start=int(raw.get("page_start", page_start)),
        page_end=int(raw.get("page_end", page_end)),
        title=str(raw.get("title", "")),
        markdown=str(raw.get("markdown", "")),
        rows=tuple(rows),
    )


def coerce_batch(raw: VisionBatchResult | Mapping[str, Any]) -> VisionBatchResult:
    if isinstance(raw, VisionBatchResult):
        return raw
    if not isinstance(raw, Mapping):
        raise VisionReductionError("batch must be VisionBatchResult or mapping")
    try:
        page_start = int(raw.get("page_start"))
        page_end = int(raw.get("page_end"))
    except (TypeError, ValueError) as exc:
        raise VisionReductionError("batch page range must be integer") from exc
    tables = tuple(
        _coerce_table(table, page_start=page_start, page_end=page_end)
        for table in raw.get("tables", []) or []
    )
    return VisionBatchResult(
        page_start=page_start,
        page_end=page_end,
        text=str(raw.get("text", "")),
        headings=tuple(str(value) for value in raw.get("headings", []) or []),
        tables=tables,
        warnings=tuple(str(value) for value in raw.get("warnings", []) or []),
        success=bool(raw.get("success", True)),
        error=str(raw["error"]) if raw.get("error") is not None else None,
    )


def _validate_batch(batch: VisionBatchResult) -> None:
    if batch.page_start < 1 or batch.page_end < batch.page_start:
        raise VisionReductionError(
            f"invalid page range {batch.page_start}-{batch.page_end}"
        )
    _normalise_text(batch.text)


def reduce_batches(
    batches: Iterable[VisionBatchResult | Mapping[str, Any]],
    *,
    expected_page_count: Optional[int] = None,
) -> VisionDocumentResult:
    """Reduce ordered or unordered Vision batches into one document result."""

    normalized = [coerce_batch(batch) for batch in batches]
    if not normalized:
        raise VisionReductionError("at least one Vision batch is required")
    for batch in normalized:
        _validate_batch(batch)
    normalized.sort(key=lambda item: (item.page_start, item.page_end))

    successful = [batch for batch in normalized if batch.success]
    repeated = _repeated_edges(
        _normalise_text(batch.text) for batch in successful if batch.text.strip()
    )
    seen_paragraphs: set[str] = set()
    sections: list[PageSection] = []
    headings: list[str] = []
    tables: list[VisionTable] = []
    warnings: list[str] = []
    covered_pages: set[int] = set()
    overlap_pages: set[int] = set()

    for batch in normalized:
        pages = set(range(batch.page_start, batch.page_end + 1))
        overlap_pages.update(covered_pages.intersection(pages))
        if batch.success:
            covered_pages.update(pages)
        warnings.extend(batch.warnings)
        if not batch.success:
            warnings.append(
                f"pages {batch.page_start}-{batch.page_end} failed: "
                f"{batch.error or 'unknown error'}"
            )
            continue
        cleaned = _remove_edges(_normalise_text(batch.text), repeated)
        cleaned = _dedupe_paragraphs(cleaned, seen_paragraphs)
        if cleaned:
            sections.append(
                PageSection(
                    page_start=batch.page_start,
                    page_end=batch.page_end,
                    text=cleaned,
                )
            )
        headings.extend(batch.headings)
        tables.extend(batch.tables)

    if overlap_pages:
        warnings.append(
            "overlapping Vision batches covered pages: "
            + ", ".join(str(page) for page in sorted(overlap_pages))
        )

    if expected_page_count is not None:
        if expected_page_count < 1:
            raise VisionReductionError("expected_page_count must be positive")
        expected_pages = set(range(1, expected_page_count + 1))
    elif covered_pages:
        expected_pages = set(range(min(covered_pages), max(covered_pages) + 1))
    else:
        expected_pages = set()
    missing_pages = tuple(sorted(expected_pages - covered_pages))
    if missing_pages:
        warnings.append(
            "missing Vision pages: "
            + ", ".join(str(page) for page in missing_pages)
        )

    document_parts = [
        f"<!-- pages {section.page_start}-{section.page_end} -->\n{section.text}"
        for section in sections
    ]
    text = "\n\n".join(document_parts)
    if len(text) > MAX_DOCUMENT_TEXT_CHARS:
        text = text[:MAX_DOCUMENT_TEXT_CHARS]
        warnings.append(
            f"reduced document truncated to {MAX_DOCUMENT_TEXT_CHARS} characters"
        )

    ranges = tuple(
        (batch.page_start, batch.page_end) for batch in successful
    )
    failed_count = len(normalized) - len(successful)
    degraded = bool(
        failed_count
        or missing_pages
        or overlap_pages
        or warnings
        or not text.strip()
    )
    return VisionDocumentResult(
        text=text,
        sections=tuple(sections),
        headings=_dedupe_strings(headings),
        tables=tuple(tables),
        warnings=_dedupe_strings(warnings),
        degraded=degraded,
        complete_page_ranges=ranges,
        missing_pages=missing_pages,
        source_batch_count=len(normalized),
        successful_batch_count=len(successful),
    )


__all__ = [
    "MAX_BATCH_TEXT_CHARS",
    "MAX_DOCUMENT_TEXT_CHARS",
    "PageSection",
    "VisionBatchResult",
    "VisionDocumentResult",
    "VisionReductionError",
    "VisionTable",
    "coerce_batch",
    "reduce_batches",
]
