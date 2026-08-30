"""Domain values for an immutable original OQC source file receipt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SourceFileReceipt:
    """Public metadata proving that an original workbook was preserved.

    The receipt intentionally contains only a logical blob identifier.  A local
    absolute path is infrastructure detail and must not cross the public API.
    """

    receipt_id: str
    project_key: str
    blob_id: str
    content_sha256: str
    received_at: datetime
    original_filename: str
    model_candidates: tuple[str, ...]
    lot_candidates: tuple[str, ...]
    declared_mime_type: str
    detected_mime_type: str
    canonical_extension: str
    size_bytes: int
