"""HTTP-only client for the versioned AWorld Cloud Server API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote

import httpx

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class CloudClientConfig:
    """Connection settings supplied by the CLI or its environment."""

    endpoint: str
    token: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        endpoint = self.endpoint.strip().rstrip("/")
        if not endpoint:
            raise ValueError("cloud endpoint must not be empty")
        parsed = httpx.URL(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("cloud endpoint must be an absolute HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("cloud timeout must be positive")
        object.__setattr__(self, "endpoint", endpoint)

    @property
    def api_root(self) -> str:
        if self.endpoint.endswith("/api/v1/cloud"):
            return self.endpoint
        return f"{self.endpoint}/api/v1/cloud"


class CloudApiError(RuntimeError):
    """Stable client-side representation of a Cloud API or transport failure."""

    def __init__(
        self,
        *,
        status_code: int | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> JsonObject:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "status_code": self.status_code,
            }
        }


class CloudHttpClient:
    """Small async client that never accesses Cloud storage or executors directly."""

    def __init__(
        self,
        config: CloudClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        headers = {"accept": "application/json"}
        if config.token:
            headers["authorization"] = f"Bearer {config.token}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=config.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: JsonObject | None = None,
        params: Mapping[str, str | int | None] | None = None,
    ) -> JsonObject:
        try:
            response = await self._client.request(
                method,
                f"{self._config.api_root}{path}",
                json=json_body,
                params={
                    key: value
                    for key, value in (params or {}).items()
                    if value is not None
                },
            )
        except httpx.HTTPError as exc:
            raise CloudApiError(
                status_code=None,
                code="transport_error",
                message="could not reach AWorld Cloud Server",
                details={"reason": str(exc)},
            ) from exc
        if response.is_error:
            raise self._decode_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudApiError(
                status_code=response.status_code,
                code="invalid_response",
                message="AWorld Cloud Server returned invalid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise CloudApiError(
                status_code=response.status_code,
                code="invalid_response",
                message="AWorld Cloud Server returned a non-object response",
            )
        return payload

    @staticmethod
    def _decode_error(response: httpx.Response) -> CloudApiError:
        code = "http_error"
        message = f"AWorld Cloud Server returned HTTP {response.status_code}"
        details: Mapping[str, Any] = {}
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            if isinstance(error.get("code"), str):
                code = error["code"]
            if isinstance(error.get("message"), str):
                message = error["message"]
            if isinstance(error.get("details"), dict):
                details = error["details"]
        return CloudApiError(
            status_code=response.status_code,
            code=code,
            message=message,
            details=details,
        )

    @staticmethod
    def _id(value: str) -> str:
        return quote(value, safe="")

    async def create_workspace(
        self,
        *,
        name: str,
        profile_name: str,
        idempotency_key: str,
    ) -> JsonObject:
        return await self._request_json(
            "POST",
            "/workspaces",
            json_body={
                "name": name,
                "profile_name": profile_name,
                "idempotency_key": idempotency_key,
            },
        )

    async def list_workspaces(
        self,
        *,
        limit: int = 50,
        page_token: str | None = None,
    ) -> JsonObject:
        return await self._request_json(
            "GET",
            "/workspaces",
            params={"limit": limit, "page_token": page_token},
        )

    async def get_workspace(self, workspace_id: str) -> JsonObject:
        return await self._request_json("GET", f"/workspaces/{self._id(workspace_id)}")

    async def release_workspace(
        self,
        workspace_id: str,
        *,
        idempotency_key: str,
    ) -> JsonObject:
        return await self._request_json(
            "DELETE",
            f"/workspaces/{self._id(workspace_id)}",
            json_body={"idempotency_key": idempotency_key},
        )

    async def submit_run(
        self,
        workspace_id: str,
        *,
        task: str,
        mode: str,
        idempotency_key: str,
        model: str | None = None,
        benchmark: JsonObject | None = None,
    ) -> JsonObject:
        payload: JsonObject = {
            "idempotency_key": idempotency_key,
            "request_schema_version": "aworld.cloud.run-request.v1",
            "mode": mode,
            "task": task,
            "model": model,
            "benchmark": benchmark,
        }
        return await self._request_json(
            "POST",
            f"/workspaces/{self._id(workspace_id)}/runs",
            json_body=payload,
        )

    async def list_runs(
        self,
        *,
        limit: int = 50,
        page_token: str | None = None,
        workspace_id: str | None = None,
        state: str | None = None,
    ) -> JsonObject:
        return await self._request_json(
            "GET",
            "/runs",
            params={
                "limit": limit,
                "page_token": page_token,
                "workspace_id": workspace_id,
                "state": state,
            },
        )

    async def create_batch(
        self,
        workspace_id: str,
        *,
        name: str,
        runs: list[JsonObject],
        idempotency_key: str,
    ) -> JsonObject:
        return await self._request_json(
            "POST",
            f"/workspaces/{self._id(workspace_id)}/batches",
            json_body={
                "name": name,
                "runs": runs,
                "idempotency_key": idempotency_key,
            },
        )

    async def list_batches(
        self,
        *,
        limit: int = 50,
        page_token: str | None = None,
        workspace_id: str | None = None,
    ) -> JsonObject:
        return await self._request_json(
            "GET",
            "/batches",
            params={
                "limit": limit,
                "page_token": page_token,
                "workspace_id": workspace_id,
            },
        )

    async def get_batch(self, batch_id: str) -> JsonObject:
        return await self._request_json("GET", f"/batches/{self._id(batch_id)}")

    async def cancel_batch(self, batch_id: str, *, idempotency_key: str) -> JsonObject:
        return await self._request_json(
            "POST",
            f"/batches/{self._id(batch_id)}/cancel",
            json_body={"idempotency_key": idempotency_key},
        )

    async def get_run(self, run_id: str) -> JsonObject:
        return await self._request_json("GET", f"/runs/{self._id(run_id)}")

    async def cancel_run(self, run_id: str, *, idempotency_key: str) -> JsonObject:
        return await self._request_json(
            "POST",
            f"/runs/{self._id(run_id)}/cancel",
            json_body={"idempotency_key": idempotency_key},
        )

    async def retry_run(self, run_id: str, *, idempotency_key: str) -> JsonObject:
        return await self._request_json(
            "POST",
            f"/runs/{self._id(run_id)}/retry",
            json_body={"idempotency_key": idempotency_key},
        )

    async def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> JsonObject:
        return await self._request_json(
            "GET",
            f"/runs/{self._id(run_id)}/events",
            params={"after_sequence": after_sequence, "limit": limit},
        )

    async def list_files(self, run_id: str) -> JsonObject:
        return await self._request_json("GET", f"/runs/{self._id(run_id)}/files")

    async def download_file(self, run_id: str, file_id: str) -> bytes:
        try:
            response = await self._client.get(
                f"{self._config.api_root}/runs/{self._id(run_id)}/files/"
                f"{self._id(file_id)}"
            )
        except httpx.HTTPError as exc:
            raise CloudApiError(
                status_code=None,
                code="transport_error",
                message="could not reach AWorld Cloud Server",
                details={"reason": str(exc)},
            ) from exc
        if response.is_error:
            raise self._decode_error(response)
        return response.content
