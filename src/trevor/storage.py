"""S3 storage client abstraction (aioboto3).

Researchers never receive S3 credentials (C-02).
All storage interactions go through this module.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import aioboto3

from trevor.settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def _make_session(settings: Settings) -> aioboto3.Session:
    return aioboto3.Session(
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region,
    )


@asynccontextmanager
async def s3_client(settings: Settings | None = None) -> AsyncGenerator[Any]:
    """Async context manager yielding a boto3-compatible S3 client."""
    if settings is None:
        settings = get_settings()
    session = _make_session(settings)
    kwargs: dict[str, Any] = {}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    async with session.client("s3", **kwargs) as client:
        yield client


async def upload_object(
    *,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str = "application/octet-stream",
    settings: Settings | None = None,
) -> None:
    """Upload bytes to S3."""
    async with s3_client(settings) as client:
        await client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)


async def upload_fileobj(
    *,
    bucket: str,
    key: str,
    fileobj: object,
    content_type: str = "application/octet-stream",
    settings: Settings | None = None,
) -> None:
    """Upload file-like object to S3 (streaming)."""
    async with s3_client(settings) as client:
        await client.upload_fileobj(fileobj, bucket, key, ExtraArgs={"ContentType": content_type})


async def download_object(
    *,
    bucket: str,
    key: str,
    settings: Settings | None = None,
) -> bytes:
    """Download object from S3 and return bytes."""
    async with s3_client(settings) as client:
        response = await client.get_object(Bucket=bucket, Key=key)
        data: bytes = await response["Body"].read()
    return data


async def generate_presigned_get_url(
    *,
    bucket: str,
    key: str,
    expires_in: int = 3600,
    settings: Settings | None = None,
) -> str:
    """Return a pre-signed GET URL. Never exposed to researchers directly."""
    async with s3_client(settings) as client:
        url: str = await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    return url


async def generate_presigned_put_url(
    *,
    bucket: str,
    key: str,
    content_type: str = "application/octet-stream",
    expires_in: int = 3600,
    settings: Settings | None = None,
) -> str:
    """Return a pre-signed PUT URL for external upload to quarantine (ingress)."""
    async with s3_client(settings) as client:
        url: str = await client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )
    return url


async def head_object(
    *,
    bucket: str,
    key: str,
    settings: Settings | None = None,
) -> dict:
    """HEAD object — returns content_length, etag, content_type."""
    async with s3_client(settings) as client:
        response = await client.head_object(Bucket=bucket, Key=key)
    return {
        "content_length": response.get("ContentLength", 0),
        "etag": response.get("ETag", ""),
        "content_type": response.get("ContentType", ""),
    }
