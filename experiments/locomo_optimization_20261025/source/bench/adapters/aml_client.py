"""HTTP client for a contestant's AML Add/Search endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from benchmark_data.aml_contract import (
    AML_HTTP_TIMEOUT_SEC,
    auth_headers,
    check_add_response,
    check_search_response,
)


class AmlContractError(RuntimeError):
    pass


class AmlClient:
    def __init__(
        self,
        *,
        add_url: str,
        search_url: str,
        api_key: str = "",
        auth: str = "x-api-key",
        health_url: str = "",
        timeout_sec: float = AML_HTTP_TIMEOUT_SEC,
    ) -> None:
        self.add_url = add_url
        self.search_url = search_url
        self.health_url = health_url
        self.timeout_sec = timeout_sec
        self._headers = auth_headers(api_key, auth)

    def health(self) -> int | None:
        if not self.health_url:
            return None
        response = httpx.get(self.health_url, timeout=30.0, follow_redirects=True)
        if not 200 <= response.status_code < 300:
            raise AmlContractError(f"Health returned HTTP {response.status_code}")
        return response.status_code

    def add(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = self._post(self.add_url, body)
        error = check_add_response(body, payload)
        if error:
            raise AmlContractError(error)
        return payload

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = self._post(self.search_url, body)
        error = check_search_response(payload, top_k=int(body["top_k"]))
        if error:
            raise AmlContractError(error)
        return payload

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if not url:
            raise AmlContractError("Add/Search URL is empty. Set AML_ADD_URL and AML_SEARCH_URL.")
        response = httpx.post(
            url,
            json=body,
            headers=self._headers,
            timeout=self.timeout_sec,
            follow_redirects=True,
        )
        if response.status_code != 200:
            raise AmlContractError(f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AmlContractError("Response is not JSON") from exc
        if not isinstance(payload, dict):
            raise AmlContractError("Response JSON must be an object")
        return payload
