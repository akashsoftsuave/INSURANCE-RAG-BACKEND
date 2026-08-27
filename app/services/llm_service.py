from groq import Groq

from app.core.config import settings


class LLMService:

    def __init__(self):

        print(f"[LLM][Init] Groq client | model={settings.MODEL_NAME}")
        self.client = Groq(
            api_key=settings.GROQ_API_KEY
        )

    def generate_answer(
        self,
        question: str,
        context: str
    ) -> str:

        prompt = f"""
You are an Insurance Assistant.

Rules:

1. Answer ONLY from the provided context.

2. If the answer is not available,
reply exactly:

"I couldn't find this information in the provided documents."

3. Do not use outside knowledge.

Context:

{context}

Question:

{question}
"""

        print(f"[LLM] Requesting answer | model={settings.MODEL_NAME} "
              f"context_chars={len(context)} prompt_chars={len(prompt)}")

        response = self.client.chat.completions.create(
            model=settings.MODEL_NAME,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "You answer only using provided documents."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        return response.choices[0].message.content