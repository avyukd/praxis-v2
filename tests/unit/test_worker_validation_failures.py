from __future__ import annotations

from praxis_core.schemas.artifacts import ValidationMalformed, ValidationResult
from services.dispatcher.worker import validation_failure_reason


def test_validation_failure_reason_prefers_malformed_details() -> None:
    validation = ValidationResult(
        malformed=[
            ValidationMalformed(
                path="/tmp/company/dives/capital-allocation.md",
                reason="frontmatter missing ticker",
            )
        ]
    )

    reason = validation_failure_reason(validation)

    assert reason.startswith("artifacts malformed:")
    assert "capital-allocation.md" in reason
    assert "frontmatter missing ticker" in reason


def test_validation_failure_reason_uses_missing_paths_when_present() -> None:
    validation = ValidationResult(missing=["/tmp/company/dives/capital-allocation.md"])

    assert (
        validation_failure_reason(validation)
        == "artifacts missing: ['/tmp/company/dives/capital-allocation.md']"
    )
