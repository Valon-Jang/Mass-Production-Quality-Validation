"""Errors raised by the local original-file store."""

from __future__ import annotations


class OriginalFileStoreError(Exception):
    """Base error for original-file persistence."""


class SourceFileValidationError(OriginalFileStoreError):
    """The supplied source did not satisfy the explicit ingestion policy."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SourceChangedDuringIngestError(OriginalFileStoreError):
    """The source bytes changed while the immutable copy was being acquired."""


class StoredSourceIntegrityError(OriginalFileStoreError):
    """A content-addressed blob does not match its identifier."""


class StoredSourceNotFoundError(OriginalFileStoreError):
    """A requested original-file blob does not exist."""
