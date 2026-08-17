from app.services.retrieval_service import RetrievalService
from app.services.llm_service import LLMService
from app.services.guardrail_service import GuardrailService


class RAGService:

    def __init__(self):

        self.guardrail = GuardrailService()
        self.retriever = RetrievalService()
        self.llm = LLMService()

    def ask(self, question: str):

        guard = self.guardrail.check(question)

        if not guard["allowed"]:
            return {
                "answer": "I can only answer questions related to your insurance policy documents.",
                "sources": []
            }

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