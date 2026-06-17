"""
Backward-compatible shim for older imports.

The project now uses LM Studio's OpenAI-compatible `/v1` endpoints as the
canonical local-model integration. Keep this module as a thin alias so older
imports do not break while the codebase migrates to `minirag.llm.lmstudio`.
"""

from minirag.llm.lmstudio import (
    lmstudio_embed as ollama_embed,
    lmstudio_model_complete as ollama_model_complete,
)

__all__ = ["ollama_embed", "ollama_model_complete"]
