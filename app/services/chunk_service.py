import re
from bisect import bisect_right

from langchain_text_splitters import RecursiveCharacterTextSplitter

PAGE_SEPARATOR = "\n"


class ChunkService:

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
    ):

        self.chunk_size = chunk_size

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        self.heading_pattern = re.compile(
            r"^(SECTION\s+\d+|ARTICLE\s+\d+|\d+(\.\d+)+|\d+\.\s+[A-Za-z][^\n]*)",
            flags=re.MULTILINE | re.IGNORECASE,
        )

    @staticmethod
    def build_page_offset_map(pages: list[dict]) -> list[dict]:
        """Map each page to the character range it occupies in the concatenated document."""

        offset_map = []
        cursor = 0

        for page in pages:

            text = page["text"]
            start = cursor
            end = start + len(text) - 1

            offset_map.append(
                {
                    "page": page["page"],
                    "start": start,
                    "end": end,
                }
            )

            cursor = end + 1 + len(PAGE_SEPARATOR)

        return offset_map

    @staticmethod
    def concatenate_document(pages: list[dict]) -> str:
        """Join all page texts into a single document string."""

        return PAGE_SEPARATOR.join(page["text"] for page in pages)

    @staticmethod
    def get_pages_from_offsets(
        start: int,
        end: int,
        offset_map: list[dict],
    ) -> tuple[int, int]:
        """Resolve a [start, end) character range to the page numbers it spans."""

        page_starts = [entry["start"] for entry in offset_map]

        start_index = bisect_right(page_starts, start) - 1
        end_index = bisect_right(page_starts, max(start, end - 1)) - 1

        start_index = min(max(start_index, 0), len(offset_map) - 1)
        end_index = min(max(end_index, 0), len(offset_map) - 1)

        return offset_map[start_index]["page"], offset_map[end_index]["page"]

    def extract_sections(self, full_text: str) -> list[dict]:
        """Detect section headings across the whole document and build one entry per section."""

        matches = list(self.heading_pattern.finditer(full_text))
        sections = []

        if not matches:

            if full_text:
                sections.append(
                    {
                        "section": "Unknown",
                        "start": 0,
                        "end": len(full_text),
                        "text": full_text.strip(),
                    }
                )

            return sections

        if matches[0].start() > 0:

            sections.append(
                {
                    "section": "Unknown",
                    "start": 0,
                    "end": matches[0].start(),
                    "text": full_text[0:matches[0].start()].strip(),
                }
            )

        for i, match in enumerate(matches):

            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)

            sections.append(
                {
                    "section": match.group(),
                    "start": start,
                    "end": end,
                    "text": full_text[start:end].strip(),
                }
            )

        return sections

    def split_large_sections(self, section: dict, full_text: str) -> list[dict]:
        """Split an oversized section into sub-chunks, each with its own offsets."""

        pieces = self.text_splitter.split_text(section["text"])

        sub_chunks = []
        search_from = section["start"]

        for piece in pieces:

            local_start = full_text.find(piece, search_from)

            if local_start == -1:
                local_start = search_from

            local_end = local_start + len(piece)

            sub_chunks.append(
                {
                    "section": section["section"],
                    "start": local_start,
                    "end": local_end,
                    "text": piece,
                }
            )

            search_from = local_start + 1

        return sub_chunks

    def create_chunks(self, pages: list[dict], document: str = "unknown") -> list[dict]:

        if not pages:
            return []

        offset_map = self.build_page_offset_map(pages)
        full_text = self.concatenate_document(pages)
        sections = self.extract_sections(full_text)

        chunks = []
        chunk_id = 1

        for section in sections:

            if not section["text"]:
                continue

            if len(section["text"]) > self.chunk_size:
                pieces = self.split_large_sections(section, full_text)
            else:
                pieces = [section]

            for piece in pieces:

                page_start, page_end = self.get_pages_from_offsets(
                    piece["start"], piece["end"], offset_map
                )

                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "section": piece["section"],
                        "content": piece["text"],
                        "text": piece["text"],
                        "page_start": page_start,
                        "page_end": page_end,
                        "page": page_start,
                        "document": document,
                    }
                )

                chunk_id += 1

        return chunks
