from langchain_text_splitters import RecursiveCharacterTextSplitter


class ChunkService:
    """
    Service responsible for splitting extracted PDF text into chunks.
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[
                "\n\n",
                "\n",
                ". ",
                " ",
                ""
            ]
        )

    def create_chunks(self, pages: list[dict]) -> list[dict]:

        chunks = []

        chunk_id = 1

        for page in pages:

            page_chunks = self.text_splitter.split_text(page["text"])

            for chunk in page_chunks:

                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "page": page["page"],
                        "text": chunk
                    }
                )

                chunk_id += 1

        return chunks