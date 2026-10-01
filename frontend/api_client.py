"""Thin HTTP client for the PharmaTech FastAPI backend. The UI talks only to this."""

import os
import re
from datetime import date
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"

# One shared client for the whole UI process: reuses connections and the TLS setup
# instead of rebuilding them on every call (~300 ms each). It holds no per-user
# state: the user's token is sent per request and the API never sets cookies.
_http = httpx.Client(timeout=30.0)


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PharmaTechAPI:
    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("PHARMATECH_API_URL", DEFAULT_API_URL)).rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, raw: bool = False, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = _http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except httpx.HTTPError:
            raise ApiError(0, "Não foi possível conectar ao servidor PharmaTech.") from None
        if raw and response.is_success:
            return response
        if response.status_code == 204:
            return None
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

    def create_consultation(self, patient_id: int) -> dict:
        return self._request("POST", f"/patients/{patient_id}/consultations", json={})

    def list_consultations(self, patient_id: int) -> list[dict]:
        return self._request("GET", f"/patients/{patient_id}/consultations")

    def get_consultation(self, patient_id: int, consultation_id: int) -> dict:
        return self._request("GET", f"/patients/{patient_id}/consultations/{consultation_id}")

    def save_soap(self, patient_id: int, consultation_id: int, soap: dict) -> dict:
        path = f"/patients/{patient_id}/consultations/{consultation_id}/soap"
        return self._request("PUT", path, json=soap)

    def add_exam(self, patient_id: int, consultation_id: int, exam: dict) -> dict:
        path = f"/patients/{patient_id}/consultations/{consultation_id}/exams"
        return self._request("POST", path, json=exam)

    def update_exam(self, patient_id: int, consultation_id: int, exam_id: int, exam: dict) -> dict:
        path = f"/patients/{patient_id}/consultations/{consultation_id}/exams/{exam_id}"
        return self._request("PUT", path, json=exam)

    def delete_exam(self, patient_id: int, consultation_id: int, exam_id: int) -> None:
        path = f"/patients/{patient_id}/consultations/{consultation_id}/exams/{exam_id}"
        self._request("DELETE", path)

    def generate_document(
        self, patient_id: int, consultation_id: int, kind: str, payload: dict
    ) -> tuple[str, bytes]:
        """Returns (filename, pdf_bytes); the PDF is kept in memory only."""
        path = f"/patients/{patient_id}/consultations/{consultation_id}/documents/{kind}"
        response = self._request("POST", path, json=payload, raw=True)
        match = re.search(r'filename="([^"]+)"', response.headers.get("content-disposition", ""))
        return (match.group(1) if match else f"{kind}.pdf"), response.content


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
