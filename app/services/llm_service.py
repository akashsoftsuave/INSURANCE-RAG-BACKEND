from groq import Groq

from app.core.config import settings

# Bump this whenever the prompt template below changes — trace records store
# it so a replayed trace can be told apart from one built on an older prompt.
PROMPT_VERSION = "insurance-qa-v1"
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

        return f"""
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

    def generate_answer(self, question: str, context: str) -> str:
        """Back-compat string-returning form, used by eval/run_eval.py."""

        return self.generate(question, context)["raw_output"]