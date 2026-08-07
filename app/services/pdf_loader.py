import fitz


class PDFLoader:
    """
    Service responsible for reading PDF documents.
    """

    @staticmethod
    def extract_text(file_path: str) -> list[dict]:
        """
        Read a PDF and return page-wise text.
        """

        document = fitz.open(file_path)

        pages = []

        for page_number in range(len(document)):

            page = document.load_page(page_number)

            text = page.get_text("text")

            pages.append(
                {
                    "page": page_number + 1,
                    "text": text.strip()
                }
            )

        document.close()

        return pages