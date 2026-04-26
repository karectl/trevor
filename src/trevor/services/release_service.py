"""Release service — RO-Crate assembly, S3 upload, pre-signed URL."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.release import ReleaseRecord
from trevor.models.request import (
    AirlockRequest,
    AirlockRequestStatus,
    OutputObject,
    OutputObjectMetadata,
    OutputObjectState,
)
from trevor.models.review import Review
from trevor.models.user import User
from trevor.services import audit_service
from trevor.settings import Settings

logger = logging.getLogger(__name__)


def _build_ro_crate_metadata(
    request: AirlockRequest,
    objects: list[tuple[OutputObject, OutputObjectMetadata | None]],
    reviews: list[Review],
    submitter: User | None,
) -> dict[str, Any]:
    """Build ro-crate-metadata.json content as a dict."""
    context = [
        "https://w3id.org/ro/crate/1.1/context",
        {
            "tre": "https://karectl.example/trevor/context#",
            "tre:statbarn": {"@id": "tre:statbarn"},
            "tre:researcherJustification": {"@id": "tre:researcherJustification"},
            "tre:suppressionNotes": {"@id": "tre:suppressionNotes"},
            "tre:airlockRequestId": {"@id": "tre:airlockRequestId"},
            "tre:agentReviewSummary": {"@id": "tre:agentReviewSummary"},
        },
    ]

    # Root dataset
    root = {
        "@type": "Dataset",
        "@id": "./",
        "name": request.title,
        "description": request.description,
        "tre:airlockRequestId": str(request.id),
        "datePublished": datetime.now(UTC).isoformat(),
        "hasPart": [],
    }

    graph: list[dict[str, Any]] = [
        {
            "@type": "CreativeWork",
            "@id": "ro-crate-metadata.json",
            "about": {"@id": "./"},
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        },
        root,
    ]

    # File entities
    for obj, meta in objects:
        file_path = f"data/{obj.filename}"
        root["hasPart"].append({"@id": file_path})

        file_entity: dict[str, Any] = {
            "@type": "File",
            "@id": file_path,
            "name": obj.filename,
            "contentSize": str(obj.size_bytes),
            "sha256": obj.checksum_sha256,
            "tre:statbarn": obj.statbarn,
        }
        if meta:
            file_entity["description"] = meta.description
            file_entity["tre:researcherJustification"] = meta.researcher_justification
            file_entity["tre:suppressionNotes"] = meta.suppression_notes
        graph.append(file_entity)

    # Person entity for submitter
    if submitter:
        person_id = f"#person-{submitter.id}"
        graph.append({
            "@type": "Person",
            "@id": person_id,
            "name": f"{submitter.given_name} {submitter.family_name}",
            "email": submitter.email,
        })

    # Approval action from reviews
    for review in reviews:
        graph.append({
            "@type": "CreateAction",
            "@id": f"#review-{review.id}",
            "name": f"Review ({review.reviewer_type})",
            "description": review.summary,
            "endTime": review.created_at.isoformat(),
        })

    return {"@context": context, "@graph": graph}


def build_crate_zip(
    metadata_json: dict[str, Any],
    files: list[tuple[str, bytes]],
) -> bytes:
    """Build an in-memory zip of the RO-Crate.

    Args:
        metadata_json: the ro-crate-metadata.json content
        files: list of (filename, content) for data/ directory
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ro-crate-metadata.json",
            json.dumps(metadata_json, indent=2),
        )
        for filename, content in files:
            zf.writestr(f"data/{filename}", content)
    return buf.getvalue()


async def assemble_and_release(
    request_id: uuid.UUID,
    session: AsyncSession,
    settings: Settings,
) -> ReleaseRecord:
    """Assemble RO-Crate and create ReleaseRecord."""
    req = await session.get(AirlockRequest, request_id)
    if req is None:
        msg = f"Request {request_id} not found"
        raise ValueError(msg)

    # Load approved objects (latest version per logical_object_id)
    result = await session.exec(
        select(OutputObject).where(
            OutputObject.request_id == request_id,
            OutputObject.state == OutputObjectState.APPROVED,
        )
    )
    approved_objects = list(result.all())

    # If no approved objects, also include PENDING (dev mode edge case)
    if not approved_objects:
        result = await session.exec(
            select(OutputObject).where(
                OutputObject.request_id == request_id,
                OutputObject.state != OutputObjectState.SUPERSEDED,
            )
        )
        approved_objects = list(result.all())

    # Load metadata for each
    objects_with_meta: list[tuple[OutputObject, OutputObjectMetadata | None]] = []
    for obj in approved_objects:
        meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
        objects_with_meta.append((obj, meta))

    # Load reviews
    review_result = await session.exec(select(Review).where(Review.request_id == request_id))
    reviews = list(review_result.all())

    # Load submitter
    submitter = await session.get(User, req.submitted_by)

    # Build metadata
    metadata_json = _build_ro_crate_metadata(req, objects_with_meta, reviews, submitter)

    # Fetch file contents and verify checksums
    files: list[tuple[str, bytes]] = []
    for obj, _ in objects_with_meta:
        if settings.dev_auth_bypass:
            # Dev mode: use empty content placeholder
            file_content = b""
        else:
            from trevor.storage import download_object

            file_content = await download_object(
                bucket=settings.s3_quarantine_bucket,
                key=obj.storage_key,
                settings=settings,
            )
            # Verify checksum (C-03)
            actual = hashlib.sha256(file_content).hexdigest()
            if actual != obj.checksum_sha256:
                msg = (
                    f"Checksum mismatch for object {obj.id}: "
                    f"expected {obj.checksum_sha256}, got {actual}"
                )
                raise ValueError(msg)

        files.append((obj.filename, file_content))

    # Build zip
    zip_bytes = build_crate_zip(metadata_json, files)
    zip_checksum = hashlib.sha256(zip_bytes).hexdigest()

    storage_key = f"releases/{request_id}/ro-crate-{request_id}.zip"
    presigned_url = ""
    url_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        seconds=settings.presigned_url_ttl
    )

    if not settings.dev_auth_bypass:
        from trevor.storage import generate_presigned_get_url, upload_object

        await upload_object(
            bucket=settings.s3_release_bucket,
            key=storage_key,
            body=zip_bytes,
            content_type="application/zip",
            settings=settings,
        )
        presigned_url = await generate_presigned_get_url(
            bucket=settings.s3_release_bucket,
            key=storage_key,
            expires_in=settings.presigned_url_ttl,
            settings=settings,
        )
    else:
        presigned_url = f"https://dev-placeholder/{storage_key}"

    # Create ReleaseRecord
    record = ReleaseRecord(
        request_id=request_id,
        crate_storage_key=storage_key,
        crate_checksum_sha256=zip_checksum,
        presigned_url=presigned_url,
        url_expires_at=url_expires_at,
        delivered_to=[],
    )
    session.add(record)

    # Transition to RELEASED
    req.status = AirlockRequestStatus.RELEASED
    req.updated_at = datetime.now(UTC).replace(tzinfo=None)
    req.closed_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(req)

    await audit_service.emit(
        session,
        event_type="request.released",
        actor_id="system",
        request_id=request_id,
        payload={
            "release_id": str(record.id),
            "crate_storage_key": storage_key,
            "crate_checksum_sha256": zip_checksum,
        },
    )

    await session.commit()
    await session.refresh(record)
    return record
