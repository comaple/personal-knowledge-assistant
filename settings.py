"""Explicit application settings; keys stay on the server."""

import os

ENV_KEYS = {
    "LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "DSH_MODEL",
    "DSH_BASE_URL",
    "RAG_ENABLED",
    "RAG_LLM_BASE_URL",
    "RAG_LLM_MODEL",
    "RAG_LLM_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "EMBEDDING_API_KEY",
}


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in ENV_KEYS:
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
