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
 
        # New retrieval format: list of {id, document, metadata, score}
        documents = [r["document"] for r in results]
        metadatas = [r["metadata"] for r in results]
 
        context = "\n\n".join(documents)
 
        answer = self.llm.generate_answer(
            question=question,
            context=context
        )

        sources = []
        seen = set()
        for meta in metadatas:
            page = meta.get("page", 1)
            section = meta.get("section")
            title = section if section and section != "Unknown" else None
            key = (page, title)
            if key not in seen:
                seen.add(key)
                source = {"page": page}
                if title:
                    source["title"] = title
                sources.append(source)

        return {
            "answer": answer,
            "sources": sources
        }