from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GROQ_API_KEY: str
    # Optional second key already present in .env. Never the default;
    # selected explicitly with `--api-key secondary` when the primary
    # key's daily token allowance is exhausted.
    GROQ_API_KEY_OLD: str | None = None
    MODEL_NAME: str
    EMBEDDING_MODEL: str
    CHROMA_PATH: str
    COLLECTION_NAME: str
    TOP_K: int = 3
    TRACE_LOG_PATH: str = "traces/traces.jsonl"

    # Week 11 — prompt versioning + canary. The stable version serves every
    # request except the canary bucket; PROMPT_CANARY_PCT percent of users
    # (bucketed by a stable hash of user_id, so one adjuster always sees the
    # same version) get PROMPT_CANARY_VERSION. Rollback is config-only:
    # PROMPT_CANARY_PCT=0. Promotion is config-only: PROMPT_STABLE_VERSION=<canary>.
    # Current state: the drill fix (insurance-qa-v4) is in canary on 10% of
    # adjusters; v2 stays stable until the canary gates in
    # week11_observability/deliverables/canary_rollback.md pass.
    PROMPT_STABLE_VERSION: str = "insurance-qa-v2"
    PROMPT_CANARY_VERSION: str | None = "insurance-qa-v4"
    PROMPT_CANARY_PCT: int = 10

    # Week 11 — the structured, per-request observability log (one JSON line
    # per request, redacted on write). Separate from TRACE_LOG_PATH, which
    # holds the frozen Week-5/6 evaluation traces.
    REQUEST_LOG_PATH: str = "week11_observability/logs/requests.jsonl"
    # "week11-log-v1" = the schema as first built in Week 11.
    # "week11-log-v2" = v1 + the output index (coverage_verdict,
    # exclusion_context_ids, contradiction_flag) added after the drill.
    REQUEST_LOG_SCHEMA: str = "week11-log-v2"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()
