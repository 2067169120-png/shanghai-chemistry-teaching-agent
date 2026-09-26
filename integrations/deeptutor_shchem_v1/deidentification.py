from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

DEIDENTIFICATION_CONTRACT = "deeptutor_image_sanitization_v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalDeidentifier:
    """Fail-closed fallback; image privacy processing belongs to student_learning_v1."""

    def process(
        self,
        *,
        original_path: Path,
        derived_root: Path,
        student_id: str,
        upload_id: str,
        mime_type: str,
    ) -> dict[str, Any]:
        del derived_root, mime_type
        try:
            profile_id = str(uuid.UUID(student_id))
        except ValueError:
            profile_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"shchem-profile:{student_id}")
            )
        media_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"shchem:{student_id}:{upload_id}")
        )
        return {
            "contract_version": DEIDENTIFICATION_CONTRACT,
            "state": "blocked_uncertain",
            "egress_allowed": False,
            "allowed_consumers": [],
            "raw_local_save_allowed": True,
            "raw_model_access_allowed": False,
            "checks": {
                name: {
                    "status": "blocked_uncertain",
                    "backend": None,
                    "reason": "student_media_privacy_contract_unavailable",
                }
                for name in (
                    "sensitive_metadata",
                    "identity_text",
                    "qr_code",
                    "barcode",
                    "face",
                )
            },
            "redaction_summary": {},
            "reasons": ["student_media_privacy_contract_unavailable"],
            "processing_location": "local_only",
            "machine_status": "machine_only_not_human_reviewed",
            "human_reviewed": False,
            "media_id": media_id,
            "profile_id": profile_id,
            "raw_sha256": _sha256_file(original_path),
            "sanitized_sha256": None,
            "claim_scope": "local_private_input_only",
        }
