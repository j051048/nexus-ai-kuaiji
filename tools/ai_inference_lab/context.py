"""Compatibility imports for the single runtime/offline ContextPack compiler."""
import sys

try:
    from app.intelligence import context_pack as _implementation
except ModuleNotFoundError as error:
    if error.name != "app":
        raise
    # Repository-root CLI use does not add nexus_backend to sys.path. The
    # deployed backend never takes this compatibility path or imports tools.
    from nexus_backend.app.intelligence import context_pack as _implementation

# A root-only tool may later add the backend import path (for example a test
# runner). Keep the same dataclasses rather than loading one file twice.
sys.modules.setdefault("app.intelligence.context_pack", _implementation)
sys.modules.setdefault("nexus_backend.app.intelligence.context_pack", _implementation)

CONTEXT_VERSION = _implementation.CONTEXT_VERSION
TASKS = _implementation.TASKS
BudgetExceeded = _implementation.BudgetExceeded
ContextError = _implementation.ContextError
ContextPack = _implementation.ContextPack
Evidence = _implementation.Evidence
Fact = _implementation.Fact
HistoryItem = _implementation.HistoryItem
SourceEvidence = _implementation.SourceEvidence
canonical = _implementation.canonical
compile_context_pack = _implementation.compile_context_pack
estimate_tokens = _implementation.estimate_tokens
fingerprint = _implementation.fingerprint
load_context_pack = _implementation.load_context_pack

__all__ = [
    "CONTEXT_VERSION", "TASKS", "BudgetExceeded", "ContextError", "ContextPack",
    "Evidence", "Fact", "HistoryItem", "SourceEvidence", "canonical",
    "compile_context_pack", "estimate_tokens", "fingerprint", "load_context_pack",
]
