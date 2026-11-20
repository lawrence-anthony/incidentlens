from __future__ import annotations

import os
from typing import Any

from .blobs import S3Blobs
from .inference import AkamaiLLM
from .store import PgStore


def production_dependencies() -> dict[str, Any]:
    """Build the real services from the environment; a missing setting leaves that service unset."""
    llm = None
    if all(os.environ.get(n) for n in ("AKAMAI_BASE_URL", "AKAMAI_API_KEY", "CHAT_MODEL")):
        from openai import OpenAI
        client = OpenAI(base_url=os.environ["AKAMAI_BASE_URL"], api_key=os.environ["AKAMAI_API_KEY"],
                        timeout=120, max_retries=1)
        llm = AkamaiLLM(client, os.environ["CHAT_MODEL"])
    store = PgStore() if os.environ.get("PGHOST") else None
    blobs = None
    if all(os.environ.get(n) for n in ("OBJ_ENDPOINT", "OBJ_BUCKET", "OBJ_ACCESS_KEY", "OBJ_SECRET_KEY")):
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "s3",
            endpoint_url=os.environ["OBJ_ENDPOINT"],
            aws_access_key_id=os.environ["OBJ_ACCESS_KEY"],
            aws_secret_access_key=os.environ["OBJ_SECRET_KEY"],
            # Akamai Object Storage rejects the checksums newer boto3 sends by default.
            config=Config(signature_version="s3v4", request_checksum_calculation="when_required",
                          connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}),
        )
        blobs = S3Blobs(client, os.environ["OBJ_BUCKET"])
    return {"store": store, "blobs": blobs, "llm": llm}
