"""Memory candidate extraction.

The pipeline runs all configured extractors against an episode, applies
`trust_gate` and `thresholds`, and returns the survivors. Phase 3 ships a
heuristic extractor (always on) and an Ollama LLM extractor (opt-in).
"""

from agent_memory_lite.extraction.base import Extractor, ExtractorUnavailableError
from agent_memory_lite.extraction.heuristic_extractor import HeuristicExtractor
from agent_memory_lite.extraction.llm_extractor import (
    OllamaExtractor,
    probe_ollama,
)
from agent_memory_lite.extraction.thresholds import meets_thresholds
from agent_memory_lite.extraction.trust_gate import passes_trust_gate

__all__ = [
    "Extractor",
    "ExtractorUnavailableError",
    "HeuristicExtractor",
    "OllamaExtractor",
    "meets_thresholds",
    "passes_trust_gate",
    "probe_ollama",
]
