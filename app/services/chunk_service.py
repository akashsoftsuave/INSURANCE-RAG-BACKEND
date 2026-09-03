import re
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, field


@dataclass
class TextSegment:
    text: str
    page: int
    start_char: int
    end_char: int
    segment_type: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    text: str
    page: int
    section: str
    chunk_index: int
    chunk_id: str
    document: str
    segment_type: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


class ChunkService:

    NUMBERED_HEADING = re.compile(r"^\d+(\.\d+)+\.?\s+[A-Z]", re.MULTILINE)
    SINGLE_NUMBERED_HEADING = re.compile(r"^\d+\.\s+[A-Z]", re.MULTILINE)
    ALL_CAPS_HEADING = re.compile(r"^[A-Z][A-Z\s]{3,}$", re.MULTILINE)
    TABLE_ROW = re.compile(r"^.+?:\s*.+\s*\|\s*.+", re.MULTILINE)
    BULLET = re.compile(r"^[\s]*[•·\-\*]\s+", re.MULTILINE)
    KEY_VALUE = re.compile(r"^[A-Za-z][A-Za-z\s]{1,40}:\s*.+", re.MULTILINE)
    WHITESPACE_ONLY = re.compile(r"^\s*$")
    DATE_PATTERN = re.compile(r"^\d{1,2}\s+[A-Za-z]+\s+\d{4}$|^[A-Za-z]+\s+\d{1,2},?\s+\d{4}$", re.MULTILINE)
    TIME_PATTERN = re.compile(r"^\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}", re.IGNORECASE)
    ALL_CAPS_NAME = re.compile(r"^[A-Z][A-Z\s]{2,}$")
    PAGE_MARKER = re.compile(r"^Page\s+\d+$", re.IGNORECASE)

    # Pattern to detect key on one line, value on next
    KEY_ONLY = re.compile(r"^[A-Za-z][A-Za-z\s]{1,40}$")
    VALUE_ONLY = re.compile(r"^[\d\w\s\.,\-\+%\(\)\/]+$")

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 150,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunk_size = chunk_size * 2

    def _is_heading_line(self, stripped: str, prev_stripped: str, next_stripped: str, is_first_content: bool = False) -> bool:
        """Detect if a line is a section heading. Thin wrapper over _classify_line
        so heading detection has a single source of truth."""
        if not stripped or len(stripped) < 3:
            return False

        return self._classify_line(stripped, prev_stripped, next_stripped, is_first_content) == "heading"

    def extract_sections_from_page(self, page_text: str, page_num: int, doc_name: str) -> List[TextSegment]:
        """Extract logical sections from a single page using generic structural signals."""
        segments = []

        if not page_text.strip():
            return segments

        lines = page_text.split('\n')
        current_section = "Unknown"
        current_text = []
        section_start = 0
        char_pos = 0

        def _is_skip_line(stripped: str) -> bool:
            """Check if a line should be skipped (page markers, etc.)."""
            if self.PAGE_MARKER.match(stripped):
                return True
            if self.WHITESPACE_ONLY.match(stripped):
                return True
            return False

        first_content_added = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            prev_stripped = lines[i - 1].strip() if i > 0 else ""
            next_stripped = lines[i + 1].strip() if i < len(lines) - 1 else ""

            if not first_content_added and _is_skip_line(stripped):
                char_pos += len(line) + 1
                continue

            is_first_content = not first_content_added
            first_content_added = True

            is_heading = self._is_heading_line(stripped, prev_stripped, next_stripped, is_first_content)

            if is_heading:
                if current_text:
                    text_content = '\n'.join(current_text).strip()
                    if text_content:
                        segments.append(TextSegment(
                            text=text_content,
                            page=page_num,
                            start_char=section_start,
                            end_char=char_pos,
                            segment_type="section",
                            metadata={"section": current_section}
                        ))
                current_text = [line]
                current_section = stripped
                section_start = char_pos
            else:
                current_text.append(line)

            char_pos += len(line) + 1

        if current_text:
            text_content = '\n'.join(current_text).strip()
            if text_content:
                segments.append(TextSegment(
                    text=text_content,
                    page=page_num,
                    start_char=section_start,
                    end_char=char_pos,
                    segment_type="section",
                    metadata={"section": current_section}
                ))

        if not segments:
            segments.append(TextSegment(
                text=page_text.strip(),
                page=page_num,
                start_char=0,
                end_char=len(page_text),
                segment_type="section",
                metadata={"section": "Unknown"}
            ))

        return segments

    def _classify_line(self, stripped: str, prev_stripped: str, next_stripped: str, is_first_content: bool = False) -> str:
        """Classify a line by its structural role."""
        if not stripped:
            return "blank"

        if self.PAGE_MARKER.match(stripped):
            return "paragraph"

        if self.NUMBERED_HEADING.match(stripped) or self.SINGLE_NUMBERED_HEADING.match(stripped):
            return "heading"

        if self.ALL_CAPS_HEADING.match(stripped) and len(stripped) < 80:
            if self.DATE_PATTERN.match(stripped) or self.TIME_PATTERN.match(stripped):
                return "paragraph"
            if self.ALL_CAPS_NAME.match(stripped) and len(stripped.split()) <= 4:
                return "paragraph"
            return "heading"

        if self.TABLE_ROW.match(stripped):
            return "table_row"

        if self.KEY_VALUE.match(stripped):
            return "key_value"

        if self.BULLET.match(stripped):
            return "bullet"

        # Check for key-value split across lines (key on one line, value on next)
        if (self.KEY_ONLY.match(stripped) and next_stripped and
            self.VALUE_ONLY.match(next_stripped) and not self.KEY_VALUE.match(next_stripped)):
            return "key_value_split"

        if len(stripped) <= 60:
            if self.DATE_PATTERN.match(stripped) or self.TIME_PATTERN.match(stripped):
                return "paragraph"
            if self.ALL_CAPS_NAME.match(stripped) and len(stripped.split()) <= 4:
                return "paragraph"
            if self.PAGE_MARKER.match(stripped):
                return "paragraph"
            if is_first_content:
                return "paragraph"
            is_short = True
            followed_by_content = bool(next_stripped and len(next_stripped) > 30)
            preceded_by_blank = (prev_stripped == "" or self.WHITESPACE_ONLY.match(prev_stripped))
            if is_short and followed_by_content and preceded_by_blank:
                return "heading"

        return "paragraph"

    def _group_into_logical_units(self, text: str) -> List[str]:
        """Group lines into logical semantic units (paragraphs, tables, lists, key-value blocks)."""
        lines = text.split('\n')
        if not lines:
            return []

        units = []
        current_unit = []
        current_type = None
        pending_headings = []

        i = 0
        first_content_seen = False
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            prev_stripped = lines[i - 1].strip() if i > 0 else ""
            next_stripped = lines[i + 1].strip() if i < len(lines) - 1 else ""

            is_first = not first_content_seen and stripped != ""
            if is_first:
                first_content_seen = True

            line_type = self._classify_line(stripped, prev_stripped, next_stripped, is_first)

            # Handle key-value split across lines - merge key and value
            if line_type == "key_value_split" and i + 1 < len(lines):
                merged_line = stripped + ": " + lines[i + 1].strip()
                line = merged_line
                stripped = merged_line.strip()
                line_type = "key_value"
                i += 1  # Skip the value line since we merged it

            if line_type in ("table_row", "key_value", "bullet"):
                # Structural lines: start new unit if type changes
                if current_type != line_type:
                    if current_unit:
                        if pending_headings:
                            current_unit = pending_headings + current_unit
                            pending_headings = []
                        units.append('\n'.join(current_unit))
                        current_unit = []
                    current_type = line_type

                # Add heading prefix if any
                if pending_headings:
                    current_unit = pending_headings + current_unit
                    pending_headings = []

                current_unit.append(line)

                # For table/key_value, consume consecutive same-type lines
                if line_type in ("table_row", "key_value"):
                    j = i + 1
                    while j < len(lines):
                        next_line = lines[j]
                        next_stripped = next_line.strip()
                        next_type = self._classify_line(next_stripped, lines[j-1].strip(), lines[j+1].strip() if j+1 < len(lines) else "", False)
                        if next_type in ("table_row", "key_value", "bullet", "blank"):
                            # Also handle key-value split in consecutive lines
                            if next_type == "key_value_split" and j + 1 < len(lines):
                                merged = next_stripped + ": " + lines[j + 1].strip()
                                current_unit.append(merged)
                                j += 2
                            else:
                                current_unit.append(next_line)
                                j += 1
                        else:
                            break
                    i = j - 1

            elif line_type == "heading":
                pending_headings.append(line)

            else:
                # Paragraph or other: if we have pending headings, prepend them
                if pending_headings:
                    current_unit = pending_headings + current_unit
                    pending_headings = []
                if not current_unit:
                    current_type = line_type
                current_unit.append(line)

            i += 1

        if pending_headings:
            current_unit = pending_headings + current_unit

        if current_unit:
            units.append('\n'.join(current_unit))

        merged = []
        for unit in units:
            if merged and len(merged[-1]) + len(unit) < self.chunk_size:
                merged[-1] += '\n' + unit
            else:
                merged.append(unit)

        return merged

    def split_large_segment(self, segment: TextSegment, doc_name: str, chunk_counter: int) -> Tuple[List[Chunk], int]:
        """Split a large segment while preserving logical units."""
        chunks = []
        text = segment.text
        page = segment.page
        section = segment.metadata.get("section", "Unknown")

        if len(text) <= self.chunk_size:
            chunk_id = f"{doc_name.replace('.pdf', '')}_p{page}_c{chunk_counter}"
            return [Chunk(
                text=text,
                page=page,
                section=section,
                chunk_index=chunk_counter,
                chunk_id=chunk_id,
                document=doc_name,
                segment_type=segment.segment_type,
                metadata={**segment.metadata, "char_length": len(text)}
            )], chunk_counter + 1

        units = self._group_into_logical_units(text)

        current_chunk = ""
        for unit in units:
            if len(current_chunk) + len(unit) + 1 <= self.chunk_size:
                if current_chunk:
                    current_chunk += "\n" + unit
                else:
                    current_chunk = unit
            else:
                if current_chunk:
                    chunk_id = f"{doc_name.replace('.pdf', '')}_p{page}_c{chunk_counter}"
                    chunks.append(Chunk(
                        text=current_chunk.strip(),
                        page=page,
                        section=section,
                        chunk_index=chunk_counter,
                        chunk_id=chunk_id,
                        document=doc_name,
                        segment_type=segment.segment_type,
                        metadata={**segment.metadata, "char_length": len(current_chunk)}
                    ))
                    chunk_counter += 1
                current_chunk = unit

        if current_chunk:
            chunk_id = f"{doc_name.replace('.pdf', '')}_p{page}_c{chunk_counter}"
            chunks.append(Chunk(
                text=current_chunk.strip(),
                page=page,
                section=section,
                chunk_index=chunk_counter,
                chunk_id=chunk_id,
                document=doc_name,
                segment_type=segment.segment_type,
                metadata={**segment.metadata, "char_length": len(current_chunk)}
            ))
            chunk_counter += 1

        return chunks, chunk_counter

    def create_chunks(self, pages: List[Dict], document: str = "unknown") -> Tuple[List[Dict], Dict]:
        """Create chunks from pages, preserving page boundaries and semantic structure."""
        all_chunks = []
        chunk_counter = 1
        stats = {
            "cross_page_chunks": 0,
            "empty_chunks": 0,
            "missing_page_metadata": 0,
            "missing_chunk_index": 0,
            "broken_key_value_pairs": 0,
            "broken_table_rows": 0,
        }

        for page_data in pages:
            page_num = page_data["page"]
            page_text = page_data.get("text", "")

            if not page_text.strip():
                stats["empty_chunks"] += 1
                continue

            segments = self.extract_sections_from_page(page_text, page_num, document)

            for segment in segments:
                if not segment.text.strip():
                    stats["empty_chunks"] += 1
                    continue

                sub_chunks, chunk_counter = self.split_large_segment(segment, document, chunk_counter)

                for chunk in sub_chunks:
                    if not chunk.text.strip():
                        stats["empty_chunks"] += 1
                        continue

                    if chunk.page != page_num:
                        stats["cross_page_chunks"] += 1

                    if chunk.chunk_index is None:
                        stats["missing_chunk_index"] += 1

                    all_chunks.append({
                        "text": chunk.text,
                        "page": chunk.page,
                        "section": chunk.section,
                        "chunk_index": chunk.chunk_index,
                        "chunk_id": chunk.chunk_id,
                        "document": chunk.document,
                        "segment_type": chunk.segment_type,
                        "metadata": chunk.metadata,
                    })

        return all_chunks, stats