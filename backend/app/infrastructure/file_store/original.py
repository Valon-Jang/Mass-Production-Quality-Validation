"""Project-isolated, content-addressed storage for original OQC workbooks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import BinaryIO, Final
from uuid import uuid4
from xml.etree import ElementTree

from app.domain.source_file import SourceFileReceipt
from app.infrastructure.file_store.errors import (
    SourceChangedDuringIngestError,
    SourceFileValidationError,
    StoredSourceIntegrityError,
    StoredSourceNotFoundError,
)

_CHUNK_BYTES: Final = 1024 * 1024
_MAX_CONTENT_TYPES_BYTES: Final = 2 * 1024 * 1024
_PROJECT_KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")
_CONTENT_TYPES_NAMESPACE: Final = "http://schemas.openxmlformats.org/package/2006/content-types"
_CONTENT_TYPES_ROOT_TAG: Final = f"{{{_CONTENT_TYPES_NAMESPACE}}}Types"
_CONTENT_TYPES_DEFAULT_TAG: Final = f"{{{_CONTENT_TYPES_NAMESPACE}}}Default"
_CONTENT_TYPES_OVERRIDE_TAG: Final = f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"
_WORKBOOK_PART_NAME: Final = "/xl/workbook.xml"

XLSX_MIME: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MIME: Final = "application/vnd.ms-excel.sheet.macroEnabled.12"

_MIME_BY_EXTENSION: Final = {
    ".xlsx": XLSX_MIME,
    ".xlsm": XLSM_MIME,
}
_WORKBOOK_CONTENT_TYPE_BY_EXTENSION: Final = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
}


class OriginalFileStore:
    """Preserve validated workbook bytes without interpreting workbook data.

    ``max_bytes`` is deliberately required: a deployment must choose an upload
    limit rather than silently inheriting an unbounded default.  The class never
    changes source permissions and only opens source and stored blobs in binary
    read mode.  Immutability is enforced by the API and by content hashes.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()

    def preserve(
        self,
        *,
        project_key: str,
        source: Path,
        declared_mime_type: str,
        model_candidates: Sequence[str] = (),
        lot_candidates: Sequence[str] = (),
        receipt_id: str | None = None,
        received_at: datetime | None = None,
    ) -> SourceFileReceipt:
        """Validate and atomically preserve one original workbook.

        Raw preservation is deliberately independent of workbook scanning.  A
        caller may retain this receipt even when a later scanner or mapper fails.
        """

        safe_project_key = self._validate_project_key(project_key)
        source_path = source.resolve()
        extension = source.suffix.lower()
        normalized_declared_mime = self._validate_declared_mime(
            extension=extension,
            declared_mime_type=declared_mime_type,
        )
        if not source_path.is_file():
            raise SourceFileValidationError("SOURCE_NOT_FILE", "source must be a regular file")

        before_digest, before_size = self._hash_source(source_path)
        detected_mime = self._detect_ooxml_mime(source_path, extension=extension)
        normalized_models = self._normalize_candidates(model_candidates, field="model_candidates")
        normalized_lots = self._normalize_candidates(lot_candidates, field="lot_candidates")
        reserved_receipt_id = self._validate_reserved_receipt_id(receipt_id)
        reserved_received_at = self._validate_reserved_received_at(received_at)

        with self._lock:
            return self._preserve_validated(
                project_key=safe_project_key,
                source=source_path,
                original_filename=source.name,
                extension=extension,
                declared_mime_type=normalized_declared_mime,
                detected_mime_type=detected_mime,
                before_digest=before_digest,
                before_size=before_size,
                model_candidates=normalized_models,
                lot_candidates=normalized_lots,
                receipt_id=reserved_receipt_id,
                received_at=reserved_received_at,
            )

    @contextmanager
    def open_source(self, receipt: SourceFileReceipt) -> Iterator[BinaryIO]:
        """Open a stored original as a read-only binary stream."""

        path = self._resolve_blob_path(
            project_key=receipt.project_key,
            digest=receipt.content_sha256,
            extension=receipt.canonical_extension,
        )
        if not path.is_file():
            raise StoredSourceNotFoundError(f"stored source {receipt.blob_id!r} was not found")
        actual_digest, actual_size = self._hash_unbounded(path)
        if actual_digest != receipt.content_sha256 or actual_size != receipt.size_bytes:
            raise StoredSourceIntegrityError("stored source no longer matches its receipt")
        with path.open("rb") as stream:
            yield stream

    def list_receipts(
        self, *, project_key: str, content_sha256: str
    ) -> tuple[SourceFileReceipt, ...]:
        """Return every receipt for the same project-local content blob."""

        safe_project_key = self._validate_project_key(project_key)
        safe_digest = self._validate_digest(content_sha256)
        receipt_directory = self._receipt_shard(safe_project_key, safe_digest)
        if not receipt_directory.is_dir():
            return ()
        receipts = [
            receipt
            for path in receipt_directory.glob("*.json")
            if (receipt := self._receipt_from_json(path)).content_sha256 == safe_digest
            and receipt.project_key == safe_project_key
        ]
        return tuple(sorted(receipts, key=lambda item: (item.received_at, item.receipt_id)))

    def resolve_receipt(
        self,
        *,
        project_key: str,
        receipt_id: str,
        content_sha256: str,
    ) -> SourceFileReceipt:
        """Resolve one exact project-local receipt without exposing storage paths.

        The content digest is deliberately part of the lookup contract.  This
        keeps receipt replay bounded to one content-addressed shard and makes a
        wrong project, receipt, or digest indistinguishable to public callers.
        """

        if not receipt_id or receipt_id != receipt_id.strip() or len(receipt_id) > 128:
            raise StoredSourceNotFoundError("stored source receipt was not found")
        try:
            matches = tuple(
                receipt
                for receipt in self.list_receipts(
                    project_key=project_key,
                    content_sha256=content_sha256,
                )
                if receipt.receipt_id == receipt_id
            )
        except SourceFileValidationError as error:
            raise StoredSourceNotFoundError("stored source receipt was not found") from error
        if len(matches) != 1:
            raise StoredSourceNotFoundError("stored source receipt was not found")
        return matches[0]

    def _preserve_validated(
        self,
        *,
        project_key: str,
        source: Path,
        original_filename: str,
        extension: str,
        declared_mime_type: str,
        detected_mime_type: str,
        before_digest: str,
        before_size: int,
        model_candidates: tuple[str, ...],
        lot_candidates: tuple[str, ...],
        receipt_id: str | None,
        received_at: datetime | None,
    ) -> SourceFileReceipt:
        blob_path = self._resolve_blob_path(
            project_key=project_key,
            digest=before_digest,
            extension=extension,
        )
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_temp = blob_path.parent / f".{uuid4().hex}.tmp"
        receipt_temp: Path | None = None
        created_blob = False

        try:
            copied_digest, copied_size = self._copy_source_to_temp(source, blob_temp)
            after_digest, after_size = self._hash_source(source)
            if not (
                before_digest == copied_digest == after_digest
                and before_size == copied_size == after_size
            ):
                raise SourceChangedDuringIngestError(
                    "source bytes changed while the immutable copy was being acquired"
                )

            temp_digest, temp_size = self._hash_unbounded(blob_temp)
            if temp_digest != before_digest or temp_size != before_size:
                raise StoredSourceIntegrityError("temporary copy failed its content hash check")

            if blob_path.exists():
                stored_digest, stored_size = self._hash_unbounded(blob_path)
                if stored_digest != before_digest or stored_size != before_size:
                    raise StoredSourceIntegrityError(
                        "an existing content-addressed blob failed its integrity check"
                    )
                blob_temp.unlink()
            else:
                os.replace(blob_temp, blob_path)
                created_blob = True

            effective_received_at = received_at or self._clock()
            if effective_received_at.tzinfo is None or effective_received_at.utcoffset() is None:
                raise ValueError("clock must return a timezone-aware datetime")
            receipt = SourceFileReceipt(
                receipt_id=receipt_id or uuid4().hex,
                project_key=project_key,
                blob_id=f"sha256:{before_digest}",
                content_sha256=before_digest,
                received_at=effective_received_at.astimezone(UTC),
                original_filename=original_filename,
                model_candidates=model_candidates,
                lot_candidates=lot_candidates,
                declared_mime_type=declared_mime_type,
                detected_mime_type=detected_mime_type,
                canonical_extension=extension,
                size_bytes=before_size,
            )
            receipt_directory = self._receipt_shard(project_key, before_digest)
            receipt_directory.mkdir(parents=True, exist_ok=True)
            receipt_path = self._resolve_within_root(
                receipt_directory / f"{receipt.receipt_id}.json"
            )
            if receipt_path.exists():
                existing = self._receipt_from_json(receipt_path)
                if existing != receipt:
                    raise StoredSourceIntegrityError(
                        "reserved receipt identity already has different immutable evidence"
                    )
                return existing
            receipt_temp = receipt_directory / f".{receipt.receipt_id}.{uuid4().hex}.tmp"
            self._write_receipt_temp(receipt_temp, receipt)
            os.replace(receipt_temp, receipt_path)
            receipt_temp = None
            return receipt
        except Exception:
            self._unlink_if_present(blob_temp)
            if receipt_temp is not None:
                self._unlink_if_present(receipt_temp)
            if created_blob and not self._has_receipt(project_key, before_digest):
                self._unlink_if_present(blob_path)
            raise

    def _hash_source(self, path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > self._max_bytes:
                    raise SourceFileValidationError(
                        "SOURCE_TOO_LARGE",
                        f"source exceeds the configured {self._max_bytes}-byte limit",
                    )
                digest.update(chunk)
        return digest.hexdigest(), size

    def _copy_source_to_temp(self, source: Path, destination: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
                while chunk := source_stream.read(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise SourceFileValidationError(
                            "SOURCE_TOO_LARGE",
                            f"source exceeds the configured {self._max_bytes}-byte limit",
                        )
                    destination_stream.write(chunk)
                    digest.update(chunk)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
        except Exception:
            self._unlink_if_present(destination)
            raise
        return digest.hexdigest(), size

    @staticmethod
    def _hash_unbounded(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    def _detect_ooxml_mime(self, source: Path, *, extension: str) -> str:
        expected_content_type = _WORKBOOK_CONTENT_TYPE_BY_EXTENSION[extension]
        try:
            with zipfile.ZipFile(source, "r") as archive:
                names = archive.namelist()
                if names.count("[Content_Types].xml") != 1 or names.count("xl/workbook.xml") != 1:
                    raise SourceFileValidationError(
                        "INVALID_OOXML_PACKAGE",
                        "workbook OOXML parts are missing or duplicated",
                    )
                content_types_info = archive.getinfo("[Content_Types].xml")
                if content_types_info.flag_bits & 0x1:
                    raise SourceFileValidationError(
                        "ENCRYPTED_OOXML_UNSUPPORTED",
                        "encrypted workbook packages are not accepted",
                    )
                if content_types_info.file_size > _MAX_CONTENT_TYPES_BYTES:
                    raise SourceFileValidationError(
                        "INVALID_OOXML_PACKAGE",
                        "OOXML content type metadata is unexpectedly large",
                    )
                with archive.open(content_types_info, "r") as content_types_stream:
                    content_types_xml = content_types_stream.read(_MAX_CONTENT_TYPES_BYTES + 1)
                if len(content_types_xml) > _MAX_CONTENT_TYPES_BYTES:
                    raise SourceFileValidationError(
                        "INVALID_OOXML_PACKAGE",
                        "OOXML content type metadata exceeds its safety limit",
                    )
        except SourceFileValidationError:
            raise
        except (OSError, KeyError, zipfile.BadZipFile, RuntimeError) as error:
            raise SourceFileValidationError(
                "INVALID_OOXML_PACKAGE", "source is not a readable OOXML workbook"
            ) from error

        try:
            root = ElementTree.fromstring(content_types_xml)
        except ElementTree.ParseError as error:
            raise SourceFileValidationError(
                "INVALID_OOXML_PACKAGE", "OOXML content type metadata is malformed"
            ) from error

        effective_content_type = self._effective_workbook_content_type(root)
        if (
            effective_content_type is None
            or effective_content_type.casefold() != expected_content_type.casefold()
        ):
            raise SourceFileValidationError(
                "OOXML_TYPE_MISMATCH",
                "file extension and OOXML workbook content type do not match",
            )
        return _MIME_BY_EXTENSION[extension]

    @staticmethod
    def _effective_workbook_content_type(root: ElementTree.Element) -> str | None:
        if root.tag != _CONTENT_TYPES_ROOT_TAG:
            raise SourceFileValidationError(
                "INVALID_OOXML_PACKAGE",
                "OOXML content type metadata has an invalid root element",
            )

        defaults: dict[str, str] = {}
        overrides: dict[str, str] = {}
        for element in root:
            if element.tag == _CONTENT_TYPES_DEFAULT_TAG:
                extension = element.attrib.get("Extension")
                content_type = element.attrib.get("ContentType")
                if not extension or extension != extension.strip() or not content_type:
                    raise SourceFileValidationError(
                        "INVALID_OOXML_PACKAGE",
                        "OOXML Default content type declaration is incomplete",
                    )
                normalized_extension = extension.casefold()
                if normalized_extension in defaults:
                    raise SourceFileValidationError(
                        "AMBIGUOUS_OOXML_CONTENT_TYPE",
                        "OOXML package has duplicate Default declarations",
                    )
                defaults[normalized_extension] = content_type
            elif element.tag == _CONTENT_TYPES_OVERRIDE_TAG:
                part_name = element.attrib.get("PartName")
                content_type = element.attrib.get("ContentType")
                if (
                    not part_name
                    or part_name != part_name.strip()
                    or not part_name.startswith("/")
                    or not content_type
                ):
                    raise SourceFileValidationError(
                        "INVALID_OOXML_PACKAGE",
                        "OOXML Override content type declaration is incomplete",
                    )
                if part_name in overrides:
                    raise SourceFileValidationError(
                        "AMBIGUOUS_OOXML_CONTENT_TYPE",
                        "OOXML package has duplicate Override declarations",
                    )
                overrides[part_name] = content_type

        exact_override = overrides.get(_WORKBOOK_PART_NAME)
        if exact_override is not None:
            return exact_override
        return defaults.get("xml")

    @staticmethod
    def _validate_declared_mime(*, extension: str, declared_mime_type: str) -> str:
        if extension not in _MIME_BY_EXTENSION:
            raise SourceFileValidationError(
                "UNSUPPORTED_EXTENSION", "only .xlsx and .xlsm workbooks are accepted"
            )
        if not isinstance(declared_mime_type, str) or not declared_mime_type.strip():
            raise SourceFileValidationError(
                "DECLARED_MIME_REQUIRED", "a declared MIME type is required"
            )
        declared = declared_mime_type.strip()
        normalized = declared.split(";", maxsplit=1)[0].strip().lower()
        if normalized != _MIME_BY_EXTENSION[extension].lower():
            raise SourceFileValidationError(
                "DECLARED_MIME_MISMATCH",
                "declared MIME type does not match the workbook extension",
            )
        return declared

    @staticmethod
    def _normalize_candidates(values: Sequence[str], *, field: str) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise SourceFileValidationError(
                    "INVALID_METADATA", f"{field} entries must be strings"
                )
            candidate = value.strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return tuple(normalized)

    @staticmethod
    def _validate_project_key(project_key: str) -> str:
        if not isinstance(project_key, str) or _PROJECT_KEY_PATTERN.fullmatch(project_key) is None:
            raise SourceFileValidationError(
                "INVALID_PROJECT_KEY",
                "project_key must be 1-64 safe ASCII letters, digits, dots, dashes, or underscores",
            )
        if project_key in {".", ".."}:
            raise SourceFileValidationError(
                "INVALID_PROJECT_KEY", "relative project keys are invalid"
            )
        return project_key

    @staticmethod
    def _validate_digest(digest: str) -> str:
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise SourceFileValidationError("INVALID_BLOB_ID", "invalid SHA-256 blob identifier")
        return digest

    @staticmethod
    def _validate_reserved_receipt_id(receipt_id: str | None) -> str | None:
        if receipt_id is None:
            return None
        if not isinstance(receipt_id, str) or re.fullmatch(r"[0-9a-f]{32}", receipt_id) is None:
            raise SourceFileValidationError(
                "INVALID_RECEIPT_ID",
                "reserved receipt_id must be 32 lowercase hexadecimal characters",
            )
        return receipt_id

    @staticmethod
    def _validate_reserved_received_at(received_at: datetime | None) -> datetime | None:
        if received_at is None:
            return None
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise SourceFileValidationError(
                "INVALID_RECEIVED_AT", "reserved received_at must be timezone-aware"
            )
        return received_at.astimezone(UTC)

    def _resolve_blob_path(self, *, project_key: str, digest: str, extension: str) -> Path:
        safe_project_key = self._validate_project_key(project_key)
        safe_digest = self._validate_digest(digest)
        if extension not in _MIME_BY_EXTENSION:
            raise SourceFileValidationError("UNSUPPORTED_EXTENSION", "invalid blob extension")
        return self._resolve_within_root(
            self._project_directory(safe_project_key)
            / "blobs"
            / safe_digest[:2]
            / f"{safe_digest}{extension}"
        )

    def _resolve_within_root(self, candidate: Path) -> Path:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as error:
            raise SourceFileValidationError(
                "PATH_TRAVERSAL_REJECTED", "resolved storage path leaves the configured root"
            ) from error
        return resolved

    @staticmethod
    def _write_receipt_temp(path: Path, receipt: SourceFileReceipt) -> None:
        payload = asdict(receipt)
        payload["received_at"] = receipt.received_at.isoformat().replace("+00:00", "Z")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            OriginalFileStore._unlink_if_present(path)
            raise

    @staticmethod
    def _receipt_from_json(path: Path) -> SourceFileReceipt:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        received_at = datetime.fromisoformat(str(payload["received_at"]).replace("Z", "+00:00"))
        return SourceFileReceipt(
            receipt_id=str(payload["receipt_id"]),
            project_key=str(payload["project_key"]),
            blob_id=str(payload["blob_id"]),
            content_sha256=str(payload["content_sha256"]),
            received_at=received_at,
            original_filename=str(payload["original_filename"]),
            model_candidates=tuple(str(item) for item in payload["model_candidates"]),
            lot_candidates=tuple(str(item) for item in payload["lot_candidates"]),
            declared_mime_type=str(payload["declared_mime_type"]),
            detected_mime_type=str(payload["detected_mime_type"]),
            canonical_extension=str(payload["canonical_extension"]),
            size_bytes=int(payload["size_bytes"]),
        )

    def _has_receipt(self, project_key: str, digest: str) -> bool:
        return bool(self.list_receipts(project_key=project_key, content_sha256=digest))

    def _receipt_shard(self, project_key: str, digest: str) -> Path:
        return self._resolve_within_root(
            self._project_directory(project_key) / "receipts" / digest[:2]
        )

    def _project_directory(self, project_key: str) -> Path:
        safe_project_key = self._validate_project_key(project_key)
        project_token = hashlib.sha256(safe_project_key.encode("ascii")).hexdigest()
        return self._resolve_within_root(
            self._root / "projects" / project_token[:2] / project_token
        )

    @staticmethod
    def _unlink_if_present(path: Path) -> None:
        path.unlink(missing_ok=True)
