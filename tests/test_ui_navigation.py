"""The sidebar menu shown to each role (the API enforces the same rules separately)."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "frontend" / "app.py")
sys.path.insert(0, str(Path(APP).parent))
import api_client  # noqa: E402  (the same module app.py imports)


@pytest.fixture(autouse=True)
def _identity_unchanged(monkeypatch):
    """The app re-reads the logged-in user on every run; here it answers with the session's own user."""
    import streamlit as st

    monkeypatch.setattr(api_client.PharmaTechAPI, "me", lambda self: dict(st.session_state["pharmacist"]))


def _menu_for(user: dict) -> list[str]:
    os.environ["PHARMATECH_API_URL"] = "http://127.0.0.1:9"  # nothing listens: data calls fail fast
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["token"] = "test-token"
    at.session_state["pharmacist"] = {"id": 1, "full_name": "Teste", "crf": "CRF/RJ 1", **user}
    at.run()
    assert not at.exception, at.exception
    return list(at.radio(key="nav").options), at


def test_administrator_menu_shows_only_administration():
    options, at = _menu_for({"is_admin": True, "clinical_access": False})
    assert options == ["Administração · Usuários", "Administração · Estabelecimentos"]  # no Controle de Qualidade
    assert any(b.label == "Sair" for b in at.button)
    assert "search_query" not in [w.key for w in at.text_input]  # no patient search on the admin screen


def test_pharmacist_menu_shows_only_clinical_pages():
    options, _ = _menu_for({"is_admin": False, "clinical_access": True})
    assert options == ["Buscar paciente", "Cadastrar paciente", "Controle de Qualidade"]
    assert not any(o.startswith("Administração") for o in options)
