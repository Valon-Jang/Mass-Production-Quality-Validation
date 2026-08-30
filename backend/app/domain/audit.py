"""Validated input for append-only audit records."""

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.identity import Actor


@dataclass(frozen=True, slots=True)
class AuditChange:
    actor: Actor
    action: str
    target_type: str
    reason: str
    target_id: str | None = None
    before_state: Mapping[str, object] | None = None
    after_state: Mapping[str, object] | None = None
    requirement_id: str | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("action", "target_type", "reason"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
