from __future__ import annotations

from praxis_core.schemas.payloads import PAYLOAD_MODELS
from praxis_core.schemas.task_types import TaskType


def test_payload_registry_covers_all_task_types() -> None:
    task_values = {t.value for t in TaskType}
    payload_keys = set(PAYLOAD_MODELS.keys())
    missing = sorted(task_values - payload_keys)
    extra = sorted(payload_keys - task_values)
    assert missing == [], f"Missing payload models for task types: {missing}"
    assert extra == [], f"Payload model keys with no TaskType: {extra}"
