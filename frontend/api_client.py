"""Thin HTTP client for the PharmaTech FastAPI backend. The UI talks only to this."""

import os
from datetime import date
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PharmaTechAPI:
    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("PHARMATECH_API_URL", DEFAULT_API_URL)).rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = httpx.request(
                method, f"{self.base_url}{path}", headers=headers, timeout=15.0, **kwargs
            )
        except httpx.HTTPError:
            raise ApiError(0, "Não foi possível conectar ao servidor PharmaTech.") from None
        if response.is_success:
            return response.json()
        raise ApiError(response.status_code, _error_detail(response))

    def login(self, login: str, password: str) -> str:
        data = {"username": login, "password": password}
        return self._request("POST", "/auth/login", data=data)["access_token"]

    def me(self) -> dict:
        return self._request("GET", "/auth/me")

    def create_patient(self, full_name: str, date_of_birth: date, cpf: str) -> dict:
        payload = {"full_name": full_name, "date_of_birth": date_of_birth.isoformat(), "cpf": cpf}
        return self._request("POST", "/patients", json=payload)

    def search_patients(self, query: str) -> list[dict]:
        return self._request("POST", "/patients/search", json={"query": query})

    def get_patient(self, patient_id: int) -> dict:
        return self._request("GET", f"/patients/{patient_id}")


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str):
        return detail
    if response.status_code == 422:
        return "Dados inválidos. Verifique os campos informados."
    return "Erro inesperado no servidor."
