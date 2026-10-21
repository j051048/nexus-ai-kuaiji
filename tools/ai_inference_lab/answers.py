"""Compatibility exports for the single runtime/offline fact validation kernel."""
import sys

try:
    from app.intelligence import fact_validation as _implementation
except ModuleNotFoundError as error:
    if error.name != "app":
        raise
    from nexus_backend.app.intelligence import fact_validation as _implementation

sys.modules.setdefault("app.intelligence.fact_validation", _implementation)
sys.modules.setdefault("nexus_backend.app.intelligence.fact_validation", _implementation)

ANSWER_VERSION = _implementation.ANSWER_VERSION
SUPPORTED_VERSIONS = _implementation.SUPPORTED_VERSIONS
Status = _implementation.Status
ClaimKind = _implementation.ClaimKind
AnswerError = _implementation.AnswerError
Claim = _implementation.Claim
EvidenceFact = _implementation.EvidenceFact
CalculationRule = _implementation.CalculationRule
Suggestion = _implementation.Suggestion
validate_bound_claims = _implementation.validate_bound_claims
validate_suggestion = _implementation.validate_suggestion
load_suggestion = _implementation.load_suggestion
to_offline_handoff = _implementation.to_offline_handoff

__all__ = [
    "ANSWER_VERSION", "SUPPORTED_VERSIONS", "Status", "ClaimKind", "AnswerError",
    "Claim", "EvidenceFact", "CalculationRule", "Suggestion", "validate_bound_claims",
    "validate_suggestion", "load_suggestion", "to_offline_handoff",
]
