"""
Audio storage abstraction.

  AUDIO_STORAGE_PROVIDER=local  — write to local disk (default, dev)
  AUDIO_STORAGE_PROVIDER=s3     — upload to S3-compatible bucket (production)

S3 env vars (all required when provider=s3):
  S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY
Optional:
  S3_ENDPOINT_URL    — custom endpoint (Cloudflare R2, DigitalOcean Spaces, MinIO)
  S3_PUBLIC_BASE_URL — CDN or R2 public domain to use as URL prefix instead of default
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AudioStorageResult:
    storage_path: str        # local absolute path or S3 object key
    audio_url: str | None    # publicly accessible URL; None for local storage


class AudioStorageProvider(ABC):
    @abstractmethod
    def store(self, audio_bytes: bytes, filename: str) -> AudioStorageResult:
        """Persist audio bytes and return the storage result."""


# ── Local ──────────────────────────────────────────────────────────────────────

class LocalAudioStorage(AudioStorageProvider):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def store(self, audio_bytes: bytes, filename: str) -> AudioStorageResult:
        os.makedirs(self._base_dir, exist_ok=True)
        path = os.path.join(self._base_dir, filename)
        with open(path, "wb") as f:
            f.write(audio_bytes)
        logger.info("audio_storage local: saved  path=%s  bytes=%d", path, len(audio_bytes))
        return AudioStorageResult(storage_path=path, audio_url=None)


# ── S3 ─────────────────────────────────────────────────────────────────────────

class S3AudioStorage(AudioStorageProvider):
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str | None = None,
        public_base_url: str | None = None,
        key_prefix: str = "bulletins",
    ) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "boto3 is required for S3 storage — run: poetry add boto3"
            )
        self._bucket = bucket
        self._key_prefix = key_prefix
        self._public_base_url = public_base_url
        self._endpoint_url = endpoint_url
        self._region = region
        self._client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url or None,
        )

    def store(self, audio_bytes: bytes, filename: str) -> AudioStorageResult:
        key = f"{self._key_prefix}/{filename}" if self._key_prefix else filename
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=audio_bytes,
            ContentType="audio/mpeg",
        )
        audio_url = self._build_url(key)
        logger.info(
            "audio_storage s3: uploaded  bucket=%s  key=%s  bytes=%d  url=%s",
            self._bucket, key, len(audio_bytes), audio_url,
        )
        return AudioStorageResult(storage_path=key, audio_url=audio_url)

    def _build_url(self, key: str) -> str:
        if self._public_base_url:
            return f"{self._public_base_url.rstrip('/')}/{key}"
        if self._endpoint_url:
            # S3-compatible: endpoint/{bucket}/{key}
            return f"{self._endpoint_url.rstrip('/')}/{self._bucket}/{key}"
        # Standard AWS virtual-hosted style
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"


# ── Factory ────────────────────────────────────────────────────────────────────

_provider: AudioStorageProvider | None = None


def get_audio_storage_provider() -> AudioStorageProvider:
    """Return the singleton storage provider (lazy-initialized from settings)."""
    global _provider
    if _provider is None:
        _provider = _build_provider()
    return _provider


def _build_provider() -> AudioStorageProvider:
    from core.platform.config.settings import settings

    mode = settings.audio_storage_provider.lower()

    if mode == "local":
        base_dir = os.path.join(os.getcwd(), settings.audio_local_dir, "bulletins")
        return LocalAudioStorage(base_dir=base_dir)

    if mode == "s3":
        missing = [
            name for name, val in [
                ("S3_BUCKET", settings.s3_bucket),
                ("S3_ACCESS_KEY_ID", settings.s3_access_key_id),
                ("S3_SECRET_ACCESS_KEY", settings.s3_secret_access_key),
            ] if not val
        ]
        if missing:
            raise RuntimeError(
                f"AUDIO_STORAGE_PROVIDER=s3 but required env vars are missing: {', '.join(missing)}"
            )
        return S3AudioStorage(
            bucket=settings.s3_bucket,  # type: ignore[arg-type]
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,  # type: ignore[arg-type]
            secret_access_key=settings.s3_secret_access_key,  # type: ignore[arg-type]
            endpoint_url=settings.s3_endpoint_url,
            public_base_url=settings.s3_public_base_url,
        )

    raise ValueError(f"Unknown AUDIO_STORAGE_PROVIDER={mode!r} — must be 'local' or 's3'")
