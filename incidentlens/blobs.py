from __future__ import annotations

from typing import Any, Protocol

PREFIX = "incidentlens/"


def raw_key(incident_id: str, name: str) -> str:
    return f"{PREFIX}incidents/{incident_id}/raw/{name}"


class Blobs(Protocol):
    def ping(self) -> None: ...
    def put(self, key: str, data: bytes, metadata: dict[str, str]) -> None: ...
    def get(self, key: str) -> bytes: ...


class S3Blobs:
    """Private Akamai Object Storage bucket, accessed with boto3."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def ping(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, metadata: dict[str, str]) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType="text/plain; charset=utf-8", Metadata=metadata)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
