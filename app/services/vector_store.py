import chromadb

from app.core.config import settings


class VectorStore:

    def __init__(self):

        self.client = chromadb.PersistentClient(
            path=settings.CHROMA_PATH
        )

        self.collection = self.client.get_or_create_collection(
            name=settings.COLLECTION_NAME
        )

    def clear_collection(self):
        """
        Delete all existing vectors.
        Since this project supports only one PDF,
        we clear the collection before inserting new data.
        """

        ids = self.collection.get()["ids"]

        if ids:
            self.collection.delete(ids=ids)

    def add_documents(self, chunks: list[dict]):
        """
        Store chunks and embeddings in ChromaDB.
        """

        ids = []
        documents = []
        embeddings = []
        metadatas = []

        for chunk in chunks:

            ids.append(str(chunk["chunk_id"]))

            documents.append(chunk["text"])

            embeddings.append(chunk["embedding"])

            metadatas.append(
                {
                    "page": chunk["page"],
                    "section": chunk["section"]
                }
            )

        self.collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas
        )