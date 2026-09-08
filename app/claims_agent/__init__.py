"""Week 7 — claims triage: agent vs fixed-workflow race.

New module, additive to the existing Week 4-6 RAG app. Reuses the project's
existing Groq client/config (app.core.config.settings) so the model,
credentials, and provider are identical to the rest of the app. Nothing in
app/services or app/api is modified by this module.
"""
