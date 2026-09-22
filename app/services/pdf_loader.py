import re
import unicodedata
from collections import Counter
from typing import List, Dict, Any
import fitz

_MOJIBAKE_RUPEE_PATTERN = re.compile(r"(?<![A-Za-z])I(?=\d)")


class PDFLoader:
    """
    Generic PDF document loader.
    Extracts text page-by-page with table detection and basic normalization.
    No document-specific logic.
    """

    HEADER_FOOTER_MARGIN_LINES = 5
    MIN_HEADER_FOOTER_REPETITION = 2
    MIN_LINE_LENGTH_FOR_HEADER_FOOTER = 3

    CURRENCY_PATTERNS = [
        (r"[\u20B9\uF156]", "Rs. "),
        (r"\bINR\s*", "Rs. "),
        (r"\bRs\s*\.?\s*", "Rs. "),
    ]

    def __init__(self):
        self._page_texts = []
        self._repeated_line_counts = Counter()

    def _normalize_unicode(self, text: str) -> str:
        """Normalize Unicode characters."""
        return unicodedata.normalize("NFKC", text)

    def _clean_whitespace(self, text: str) -> str:
        """Clean excessive whitespace without removing intentional formatting."""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _fix_mojibake_rupee(self, text: str) -> str:
        """Fix common mojibake where rupee symbol (₹) is extracted as 'I' before digits."""
        return _MOJIBAKE_RUPEE_PATTERN.sub("Rs. ", text)

    def _normalize_currency(self, text: str) -> str:
        """Normalize common currency representations generically."""
        # First fix mojibake rupee symbol
        text = self._fix_mojibake_rupee(text)
        for pattern, replacement in self.CURRENCY_PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def _repair_fragmented_urls(self, text: str) -> str:
        """Repair obviously fragmented URLs from PDF extraction."""
        text = re.sub(r"(https?://)\s+", r"\1", text, flags=re.IGNORECASE)
        text = re.sub(r"(www\.)\s+", r"\1", text, flags=re.IGNORECASE)
        text = re.sub(r"\.\s+([a-z]{2,4})(?:\s|$)", r".\1", text, flags=re.IGNORECASE)
        text = re.sub(r"([a-z])\s+\.\s*([a-z]{2,4})(?:\s|$)", r"\1.\2", text, flags=re.IGNORECASE)
        return text

    def _collect_page_texts(self, document) -> List[str]:
        """Collect raw text from all pages for header/footer analysis."""
        texts = []
        for page_num in range(len(document)):
            page = document.load_page(page_num)
            text = page.get_text("text")
            texts.append(text)
        return texts

    def _detect_repeated_header_footer_lines(self, page_texts: List[str]) -> set:
        """
        Detect repeated header/footer lines by checking only the first and last
        N lines of each page (header/footer zones).
        """
        if len(page_texts) < 2:
            return set()

        header_lines = Counter()
        footer_lines = Counter()

        for text in page_texts:
            lines = text.split("\n")
            # Check first N lines (header zone)
            for line in lines[:self.HEADER_FOOTER_MARGIN_LINES]:
                stripped = line.strip()
                if len(stripped) >= self.MIN_LINE_LENGTH_FOR_HEADER_FOOTER:
                    header_lines[stripped] += 1

            # Check last N lines (footer zone)
            for line in lines[-self.HEADER_FOOTER_MARGIN_LINES:]:
                stripped = line.strip()
                if len(stripped) >= self.MIN_LINE_LENGTH_FOR_HEADER_FOOTER:
                    footer_lines[stripped] += 1

        num_pages = len(page_texts)
        repeated = set()

        # Line must appear on all (or all but one) pages in the same zone
        threshold = max(self.MIN_HEADER_FOOTER_REPETITION, num_pages - 1)

        for line, count in header_lines.items():
            if count >= threshold:
                repeated.add(line)

        for line, count in footer_lines.items():
            if count >= threshold:
                repeated.add(line)

        return repeated

    def _remove_repeated_header_footer(self, text: str, repeated_lines: set) -> str:
        """Remove lines identified as repeated headers/footers."""
        if not repeated_lines:
            return text

        lines = text.split("\n")
        cleaned = []
        for line in lines:
            if line.strip() in repeated_lines:
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    def _extract_tables(self, page) -> List[Dict[str, Any]]:
        """Extract tables from a page with their bounding boxes."""
        tables = []
        try:
            tabs = page.find_tables()
            for table in tabs:
                rows = table.extract()
                if rows and len(rows) > 1:
                    tables.append({
                        "bbox": table.bbox,
                        "rows": rows,
                        "headers": [str(h).strip() for h in rows[0] if h] if rows else [],
                    })
        except Exception as e:
            pass
        return tables

    def _text_contains_table_content(self, text: str, table_rows: List[List[str]], threshold: float = 0.7) -> bool:
        """Check if page text already contains significant table content."""
        if not table_rows or len(table_rows) < 2:
            return False
        table_text = "\n".join(" ".join(str(c) for c in row if c) for row in table_rows)
        table_words = set(table_text.lower().split())
        text_words = set(text.lower().split())
        if not table_words:
            return False
        overlap = len(table_words & text_words) / len(table_words)
        return overlap >= threshold

    def _format_table_rows(self, headers: List[str], rows: List[List[str]]) -> List[str]:
        """Format table rows as structured key-value lines, one per row."""
        if not headers or not rows:
            return []

        formatted = []
        for row in rows[1:]:
            if not row:
                continue
            row_items = []
            for i, cell in enumerate(row):
                if i < len(headers) and cell:
                    header = headers[i].strip()
                    value = str(cell).strip()
                    if value:
                        row_items.append(f"{header}: {value}")
            if row_items:
                formatted.append(" | ".join(row_items))
        return formatted

    def _merge_text_and_tables(self, page_text: str, tables: List[Dict[str, Any]]) -> str:
        """Merge page text with table content, avoiding duplication."""
        if not tables:
            return page_text

        table_sections = []
        for table in tables:
            headers = table.get("headers", [])
            rows = table.get("rows", [])
            if not headers or len(rows) < 2:
                continue

            if self._text_contains_table_content(page_text, rows):
                continue

            formatted_rows = self._format_table_rows(headers, rows)
            if formatted_rows:
                table_sections.append("\n".join(formatted_rows))

        if not table_sections:
            return page_text

        combined = page_text
        if combined:
            combined += "\n\n" + "\n\n".join(table_sections)
        else:
            combined = "\n\n".join(table_sections)

        return combined

    def extract_text(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Extract text from PDF page by page with table detection.
        Returns list of {"page": page_number, "text": text, "tables": [...]}
        """
        document = fitz.open(file_path)
        self._page_texts = self._collect_page_texts(document)
        repeated_lines = self._detect_repeated_header_footer_lines(self._page_texts)

        pages = []

        for page_number in range(len(document)):
            page = document.load_page(page_number)

            raw_text = page.get_text("text")
            raw_text = self._normalize_unicode(raw_text)

            tables = self._extract_tables(page)

            text = self._merge_text_and_tables(raw_text, tables)

            text = self._remove_repeated_header_footer(text, repeated_lines)

            text = self._normalize_currency(text)

            text = self._repair_fragmented_urls(text)

            text = self._clean_whitespace(text)

            table_info = []
            for t in tables:
                if t.get("rows") and len(t["rows"]) > 1:
                    table_info.append({
                        "headers": t.get("headers", []),
                        "row_count": len(t["rows"]) - 1,
                    })

            pages.append({
                "page": page_number + 1,
                "text": text,
                "tables": table_info,
            })

        document.close()

        return pages