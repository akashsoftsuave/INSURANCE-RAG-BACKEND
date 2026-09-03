from groq import Groq

from app.core.config import settings

# Bump this whenever the prompt template below changes — trace records store
# it so a replayed trace can be told apart from one built on an older prompt.
PROMPT_VERSION = "insurance-qa-v2"
TEMPERATURE = 0
SYSTEM_MESSAGE = "You answer only using provided documents."


class LLMService:

    def __init__(self):

        print(f"[LLM][Init] Groq client | model={settings.MODEL_NAME}")
        self.client = Groq(
            api_key=settings.GROQ_API_KEY
        )

    @staticmethod
    def build_prompt(question: str, context: str) -> str:
        """Pure template render — used both for live calls and for replaying
        a trace from its stored (question, context, prompt_version)."""

        return f"""You are an Insurance Assistant.

Rules:

1. Answer the user's question using all relevant information in the provided context.

2. The question may contain multiple requested fields.
   Check each requested field independently.

3. Check ALL provided context chunks before deciding that information is unavailable.

4. Do not say information is unavailable unless you have verified that the provided context contains no information for that specific field.

5. If some fields are available and others are unavailable:
   - answer the available fields
   - explicitly identify only the unavailable fields.

6. Do not use outside knowledge.

7. Do not infer or invent information that is not explicitly stated in the context.

8. If relevant information is spread across multiple context chunks, combine it into one answer.

9. Preserve the exact names, dates, amounts, percentages, limits, and policy terms stated in the context.

Context:

{context}

Question:

{question}
"""

    def generate(self, question: str, context: str) -> dict:
        """Returns the raw output plus everything a trace needs to reproduce
        this call later: prompt_version, model name, temperature."""

        prompt = self.build_prompt(question, context)

        print(f"[LLM] Requesting answer | model={settings.MODEL_NAME} "
              f"context_chars={len(context)} prompt_chars={len(prompt)}")

        response = self.client.chat.completions.create(
            model=settings.MODEL_NAME,
            temperature=TEMPERATURE,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_MESSAGE
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        return {
            "raw_output": response.choices[0].message.content,
            "prompt_version": PROMPT_VERSION,
            "model": settings.MODEL_NAME,
            "temperature": TEMPERATURE,
        }