from minirag.llm.lmstudio import lmstudio_embed, lmstudio_model_complete

# Backward-compatible aliases for older call sites.
ollama_embed = lmstudio_embed
ollama_model_complete = lmstudio_model_complete

__all__ = [
    "lmstudio_embed",
    "lmstudio_model_complete",
    "ollama_embed",
    "ollama_model_complete",
]
