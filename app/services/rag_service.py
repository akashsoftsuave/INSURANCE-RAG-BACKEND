from app.services.retrieval_service import RetrievalService
from app.services.llm_service import LLMService


class RAGService:

    def __init__(self):

        self.retriever = RetrievalService()
        self.llm = LLMService()

    def ask(self, question: str):

        results = self.retriever.retrieve(question)

        documents = results["documents"][0]
        metadata = results["metadatas"][0]

        context = "\n\n".join(documents)

        answer = self.llm.generate_answer(
            question=question,
            context=context
        )

        sources = []
        for meta in metadata:
            source = {"page": meta["page"]}
            if meta.get("section") and meta["section"] != "Unknown":
                source["title"] = meta["section"]
            sources.append(source)

        return {
            "answer": answer,
            "sources": sources
        }