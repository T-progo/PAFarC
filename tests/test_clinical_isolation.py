"""Complete clinical isolation between establishments: every patient, consultation (with its SOAP and
exam results) and document belongs to one establishment; pharmacists only ever reach their current
establishment's records (anything else answers 404, revealing nothing). Fictitious data only."""
import io
import sqlite3
from datetime import date

import pytest
from pypdf import PdfReader
from sqlalchemy import text

from backend import crypto as crypto_module
from backend.auth import create_pharmacist
from backend.database import get_engine
from backend.models import ROLE_ADMINISTRATOR
from backend.patients import create_patient

PASSWORD = "senha-segura-1"
CPF = "52998224725"
CPF_ONLY_B = "11144477735"
SOAP = {"subjective": "S isolamento", "objective": "O isolamento", "assessment": "A isolamento", "plan": "P isolamento"}
EXAM = {"exam_name": "Glicemia capilar", "result": "98", "unit": "mg/dL", "reference_range": "70-99", "notes": "",
        "manufacturer": "Fabricante A", "lot": "LOTE-A", "expiry_date": "2027-03-31"}
DOCS = {
    "prescription": {"items": [{"medication": "Item de teste"}]},
    "exam-request": {"exams": ["Ureia"], "clinical_justification": "Justificativa de teste."},
    "referral": {"destination": "Nefrologia", "case_summary": "Resumo.", "prm": "PRM.", "suggested_conduct": "Conduta."},
    "dsf": {},
}


def pdf_text(data):
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)


def _headers(client, login):
    token = client.post("/auth/login", data={"username": login, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def iso(client, db):
    create_pharmacist(db, full_name="Admin Iso", crf="", login="admin@iso.test", password=PASSWORD, role=ROLE_ADMINISTRATOR)
    admin = _headers(client, "admin@iso.test")
    est_a = client.post("/admin/establishments", headers=admin, json={"name": "Farmácia Alfa Isolada",
                                                                      "cnpj": "11.222.333/0001-81"}).json()
    est_b = client.post("/admin/establishments", headers=admin, json={"name": "Drogaria Beta Isolada"}).json()
    ids = {}
    for name, login, est in (("Farm A Um", "a1@iso.test", est_a), ("Farm A Dois", "a2@iso.test", est_a),
                             ("Farm B Um", "b1@iso.test", est_b)):
        ids[login] = client.post("/admin/users", headers=admin, json={
            "full_name": name, "crf": "16906", "crf_uf": "RJ", "login": login, "password": PASSWORD,
            "establishment_id": est["id"]}).json()["id"]
    env = {"c": client, "admin": admin, "A": est_a["id"], "B": est_b["id"], "ids": ids,
           "a1": _headers(client, "a1@iso.test"), "a2": _headers(client, "a2@iso.test"), "b1": _headers(client, "b1@iso.test")}

    def move(login, establishment):
        r = client.patch(f"/admin/users/{ids[login]}", headers=admin, json={"establishment_id": env[establishment]})
        assert r.status_code == 200, r.text
    env["move"] = move
    return env


def new_patient(env, h, name="Paciente Comum Isolado", cpf=CPF, dob="1970-01-15"):
    r = env["c"].post("/patients", headers=h, json={"full_name": name, "date_of_birth": dob, "cpf": cpf})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def search(env, h, query):
    r = env["c"].post("/patients/search", headers=h, json={"query": query})
    assert r.status_code == 200, r.text
    return [p["id"] for p in r.json()]


def full_record(env, h, pid):
    """Consultation with SOAP, an exam (with kit) and all four documents; returns ids and the PDFs."""
    c = env["c"]
    cid = c.post(f"/patients/{pid}/consultations", headers=h, json={}).json()["id"]
    base = f"/patients/{pid}/consultations/{cid}"
    assert c.put(f"{base}/soap", headers=h, json=SOAP).status_code == 200
    exam_id = c.post(f"{base}/exams", headers=h, json=EXAM).json()["id"]
    pdfs = {}
    for kind, payload in DOCS.items():
        r = c.post(f"{base}/documents/{kind}", headers=h, json=payload)
        assert r.status_code == 200, (kind, r.text)
        pdfs[kind] = r.content
    docs = c.get(base, headers=h).json()["documents"]
    return {"pid": pid, "cid": cid, "base": base, "exam": exam_id, "docs": [d["id"] for d in docs], "pdfs": pdfs}


def clinical_snapshot():
    with get_engine().connect() as conn:
        return {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY id")).fetchall()
                for t in ("patients", "patient_name_tokens", "consultations", "soap_records", "exam_results",
                          "generated_documents")}


def all_routes(base, pid, exam_id, doc_id):
    """(method, url, json) of every clinical route for one record."""
    return [
        ("GET", f"/patients/{pid}", None),
        ("GET", f"/patients/{pid}/consultations", None),
        ("POST", f"/patients/{pid}/consultations", {}),
        ("GET", base, None),
        ("GET", f"{base}/previous-exams", None),
        ("POST", f"{base}/finalize", None),
        ("PUT", f"{base}/soap", SOAP),
        ("POST", f"{base}/exams", EXAM),
        ("PUT", f"{base}/exams/{exam_id}", EXAM),
        ("DELETE", f"{base}/exams/{exam_id}", None),
        ("GET", f"{base}/documents/{doc_id}", None),
    ] + [("POST", f"{base}/documents/{kind}", payload) for kind, payload in DOCS.items()]


# --- A. patients: same CPF per establishment, no existence leakage ----------------------------

def test_same_cpf_once_per_establishment(iso):
    pa = new_patient(iso, iso["a1"])
    pb = new_patient(iso, iso["b1"])  # same CPF and name, other establishment: allowed
    assert pa != pb
    for h in (iso["a1"], iso["a2"], iso["b1"]):
        dup = iso["c"].post("/patients", headers=h, json={"full_name": "Outro Nome", "date_of_birth": "1980-01-01", "cpf": CPF})
        assert dup.status_code == 409 and dup.json()["detail"] == "Já existe um paciente cadastrado com este CPF."
    assert search(iso, iso["a1"], CPF) == [pa] and search(iso, iso["a2"], CPF) == [pa]
    assert search(iso, iso["b1"], CPF) == [pb]
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT establishment_id, cpf_index, cpf_encrypted FROM patients ORDER BY id")).fetchall()
    assert [r[0] for r in rows] == [iso["A"], iso["B"]] and rows[0][1] == rows[1][1]  # same digest, two records
    assert CPF not in repr(rows) and rows[0][2] != rows[1][2]  # CPF stays encrypted (independent ciphertexts)


def test_cpf_of_another_establishment_looks_absent(iso):
    new_patient(iso, iso["b1"], name="Somente Beta", cpf=CPF_ONLY_B)
    absent = "39053344705"  # valid CPF registered nowhere
    assert search(iso, iso["a1"], CPF_ONLY_B) == search(iso, iso["a1"], absent) == []
    # registering it in A behaves exactly as for a CPF that exists nowhere
    assert new_patient(iso, iso["a1"], name="Somente Alfa", cpf=CPF_ONLY_B)


def test_direct_patient_id_of_another_establishment_is_404(iso):
    pa = new_patient(iso, iso["a1"])
    pb = new_patient(iso, iso["b1"])
    c = iso["c"]
    assert c.get(f"/patients/{pa}", headers=iso["b1"]).status_code == 404
    assert c.get(f"/patients/{pb}", headers=iso["a1"]).status_code == 404
    assert c.get(f"/patients/{pa}", headers=iso["b1"]).json()["detail"] == \
        c.get("/patients/999999", headers=iso["b1"]).json()["detail"] == "Paciente não encontrado."
    assert c.get(f"/patients/{pa}", headers=iso["a2"]).status_code == 200


# --- B. search -------------------------------------------------------------------------------

def test_name_search_is_isolated(iso):
    a = new_patient(iso, iso["a1"], name="Maria José da Silva Ávila", cpf=CPF)
    b = new_patient(iso, iso["b1"], name="MARIA JOSE DA SILVA AVILA", cpf=CPF)
    b2 = new_patient(iso, iso["b1"], name="Mariana Josefina Souza", cpf=CPF_ONLY_B)
    for query in ("maria", "MARIA", "José", "jose silva", "Avi", "ávila maria", "mar jos sil"):
        assert search(iso, iso["a1"], query) == [a], query
        assert set(search(iso, iso["b1"], query)) <= {b, b2} and b in search(iso, iso["b1"], query), query
    assert search(iso, iso["a1"], "mariana") == [] and search(iso, iso["b1"], "mariana") == [b2]
    assert search(iso, iso["a1"], "souza") == []


def test_other_establishment_cannot_fill_the_result_limit_and_is_never_decrypted(iso, db, monkeypatch):
    from backend.patients import SEARCH_LIMIT

    cpfs = iter(["%09d" % n for n in range(100000001, 100009999)])

    def valid_cpf():
        while True:
            base = next(cpfs)
            digits = [int(x) for x in base]
            for length in (9, 10):
                digits.append((sum(d * w for d, w in zip(digits, range(length + 1, 1, -1))) * 10 % 11) % 10)
            if len(set(digits)) > 1:
                return "".join(map(str, digits))
    for n in range(SEARCH_LIMIT + 5):
        create_patient(db, establishment_id=iso["B"], full_name=f"Limite Beta {n}", date_of_birth=date(1970, 1, 1),
                       cpf=valid_cpf())
    mine = {create_patient(db, establishment_id=iso["A"], full_name=f"Limite Alfa {n}",
                           date_of_birth=date(1970, 1, 1), cpf=valid_cpf()).id for n in range(2)}
    decrypts = []
    real = crypto_module.CryptoService.decrypt
    monkeypatch.setattr(crypto_module.CryptoService, "decrypt", lambda self, token: decrypts.append(1) or real(self, token))
    assert set(search(iso, iso["a1"], "limite")) == mine
    assert len(decrypts) <= 3 * len(mine)  # only the returned patients' fields are decrypted
    assert len(search(iso, iso["b1"], "limite")) == SEARCH_LIMIT


# --- C/D/E/G. every clinical route is isolated -------------------------------------------------

def test_every_route_of_another_establishment_answers_404_and_writes_nothing(iso):
    rec_a = full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    rec_b = full_record(iso, iso["b1"], new_patient(iso, iso["b1"]))
    before = clinical_snapshot()
    c = iso["c"]
    for rec, h in ((rec_a, iso["b1"]), (rec_b, iso["a1"]), (rec_b, iso["a2"])):
        for method, url, body in all_routes(rec["base"], rec["pid"], rec["exam"], rec["docs"][0]):
            r = c.request(method, url, headers=h, json=body)
            assert r.status_code == 404, (method, url, r.status_code)
    # mixing an own patient with another establishment's consultation / document ids
    mixed = f"/patients/{rec_b['pid']}/consultations/{rec_a['cid']}"
    assert c.get(mixed, headers=iso["b1"]).status_code == 404
    assert c.get(f"{rec_b['base']}/documents/{rec_a['docs'][0]}", headers=iso["b1"]).status_code == 404
    assert clinical_snapshot() == before
    # the owners still have everything, byte for byte
    for rec, h in ((rec_a, iso["a1"]), (rec_b, iso["b1"])):
        stored = [c.get(f"{rec['base']}/documents/{d}", headers=h).content for d in rec["docs"]]
        assert stored == list(rec["pdfs"].values())


def test_histories_and_previous_exams_never_cross(iso):
    pa = new_patient(iso, iso["a1"])
    pb = new_patient(iso, iso["b1"])
    ra1 = full_record(iso, iso["a1"], pa)
    rb1 = full_record(iso, iso["b1"], pb)
    ra2 = iso["c"].post(f"/patients/{pa}/consultations", headers=iso["a1"], json={}).json()["id"]
    rb2 = iso["c"].post(f"/patients/{pb}/consultations", headers=iso["b1"], json={}).json()["id"]
    c = iso["c"]
    assert [x["id"] for x in c.get(f"/patients/{pa}/consultations", headers=iso["a1"]).json()] == [ra2, ra1["cid"]]
    assert [x["id"] for x in c.get(f"/patients/{pb}/consultations", headers=iso["b1"]).json()] == [rb2, rb1["cid"]]
    prev_a = c.get(f"/patients/{pa}/consultations/{ra2}/previous-exams", headers=iso["a1"]).json()
    prev_b = c.get(f"/patients/{pb}/consultations/{rb2}/previous-exams", headers=iso["b1"]).json()
    assert [g["consultation_id"] for g in prev_a] == [ra1["cid"]] and [g["consultation_id"] for g in prev_b] == [rb1["cid"]]


def test_new_consultation_closes_only_the_same_establishment_record(iso):
    pa = new_patient(iso, iso["a1"])
    pb = new_patient(iso, iso["b1"])
    ca = iso["c"].post(f"/patients/{pa}/consultations", headers=iso["a1"], json={}).json()["id"]
    iso["c"].post(f"/patients/{pb}/consultations", headers=iso["b1"], json={})
    iso["c"].post(f"/patients/{pb}/consultations", headers=iso["b1"], json={})
    assert iso["c"].get(f"/patients/{pa}/consultations/{ca}", headers=iso["a1"]).json()["closed_at"] is None


def test_same_establishment_rules_are_unchanged(iso):
    rec = full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    c, base = iso["c"], rec["base"]
    # colleague in the same establishment: reads and reprints, cannot change or issue
    assert c.get(base, headers=iso["a2"]).status_code == 200
    assert c.get(f"{base}/documents/{rec['docs'][0]}", headers=iso["a2"]).content == rec["pdfs"]["prescription"]
    for method, url, body in [("PUT", f"{base}/soap", SOAP), ("POST", f"{base}/exams", EXAM),
                              ("PUT", f"{base}/exams/{rec['exam']}", EXAM), ("DELETE", f"{base}/exams/{rec['exam']}", None),
                              ("POST", f"{base}/finalize", None), ("POST", f"{base}/documents/referral", DOCS["referral"])]:
        assert c.request(method, url, headers=iso["a2"], json=body).status_code == 403, url
    # owner of a closed consultation: 409 for clinical changes
    assert c.post(f"{base}/finalize", headers=iso["a1"]).status_code == 200
    assert c.put(f"{base}/soap", headers=iso["a1"], json=SOAP).status_code == 409
    assert c.post(f"{base}/exams", headers=iso["a1"], json=EXAM).status_code == 409


# --- documents ---------------------------------------------------------------------------------

def test_documents_owned_by_and_headed_with_the_consultation_establishment(iso):
    rec_a = full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    rec_b = full_record(iso, iso["b1"], new_patient(iso, iso["b1"]))
    for kind in DOCS:
        ta, tb = pdf_text(rec_a["pdfs"][kind]), pdf_text(rec_b["pdfs"][kind])
        assert "Farmácia Alfa Isolada" in ta and "Drogaria Beta Isolada" not in ta, kind
        assert "Drogaria Beta Isolada" in tb and "Farmácia Alfa Isolada" not in tb, kind
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT d.establishment_id, c.establishment_id FROM generated_documents d "
                                 "JOIN consultations c ON c.id = d.consultation_id")).fetchall()
    assert len(rows) == 8 and all(d == c for d, c in rows)
    assert sorted(r[0] for r in rows) == [iso["A"]] * 4 + [iso["B"]] * 4


# --- transfer of a pharmacist ------------------------------------------------------------------

def test_transfer_changes_access_not_ownership(iso):
    c = iso["c"]
    rec = full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    qc = c.post("/quality-controls", headers=iso["a1"], json={"control_type": "CIQ"}).json()["id"]
    before = clinical_snapshot()
    iso["move"]("a1@iso.test", "B")
    assert search(iso, iso["a1"], CPF) == [] and search(iso, iso["a1"], "comum") == []
    for method, url, body in all_routes(rec["base"], rec["pid"], rec["exam"], rec["docs"][0]):
        assert c.request(method, url, headers=iso["a1"], json=body).status_code == 404, url
    assert c.get(f"/quality-controls/{qc}", headers=iso["a1"]).status_code == 404  # QC: unchanged rule
    assert clinical_snapshot() == before  # historical ownership never moves
    # the colleague who stayed keeps everything
    assert c.get(rec["base"], headers=iso["a2"]).status_code == 200
    assert c.get(f"{rec['base']}/documents/{rec['docs'][0]}", headers=iso["a2"]).content == rec["pdfs"]["prescription"]
    # new work of the moved pharmacist belongs to B; same CPF registers independently there
    pb = new_patient(iso, iso["a1"])
    cb = c.post(f"/patients/{pb}/consultations", headers=iso["a1"], json={}).json()["id"]
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT p.establishment_id, c.establishment_id FROM consultations c JOIN patients p "
                                 "ON p.id = c.patient_id WHERE c.id = :i"), {"i": cb}).fetchone() == (iso["B"], iso["B"])
    assert search(iso, iso["b1"], CPF) == [pb]


def test_kit_suggestion_only_from_the_current_establishment(iso):
    c = iso["c"]
    kit = lambda h: c.post("/exam-kit-suggestion", headers=h, json={"exam_name": "glicemia CAPILAR"}).json()
    full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    assert kit(iso["a1"]) == {"manufacturer": "Fabricante A", "lot": "LOTE-A", "expiry_date": "2027-03-31"}
    assert kit(iso["a2"]) == {"manufacturer": None, "lot": None, "expiry_date": None}  # not broadened
    iso["move"]("a1@iso.test", "B")
    assert kit(iso["a1"]) == {"manufacturer": None, "lot": None, "expiry_date": None}
    pb = new_patient(iso, iso["a1"])
    cb = c.post(f"/patients/{pb}/consultations", headers=iso["a1"], json={}).json()["id"]
    c.post(f"/patients/{pb}/consultations/{cb}/exams", headers=iso["a1"],
           json={**EXAM, "result": "123", "manufacturer": "Fabricante B", "lot": "LOTE-B", "expiry_date": "2028-01-31"})
    assert kit(iso["a1"]) == {"manufacturer": "Fabricante B", "lot": "LOTE-B", "expiry_date": "2028-01-31"}


# --- administrator / pharmacist without establishment -------------------------------------------

def test_administrator_has_no_clinical_access(iso):
    rec = full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    c, admin = iso["c"], iso["admin"]
    assert c.post("/patients/search", headers=admin, json={"query": CPF}).status_code == 403
    assert c.post("/patients", headers=admin, json={"full_name": "X Y", "date_of_birth": "1970-01-01", "cpf": CPF_ONLY_B}).status_code == 403
    assert c.post("/exam-kit-suggestion", headers=admin, json={"exam_name": "x"}).status_code == 403
    for method, url, body in all_routes(rec["base"], rec["pid"], rec["exam"], rec["docs"][0]):
        assert c.request(method, url, headers=admin, json=body).status_code == 403, url
    assert c.post("/patients/search", json={"query": CPF}).status_code == 401


def test_pharmacist_without_establishment_has_no_clinical_data(iso, db):
    new_patient(iso, iso["a1"])
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE pharmacists SET establishment_id = NULL WHERE id = :i"), {"i": iso["ids"]["a2@iso.test"]})
    for method, url, body in [("POST", "/patients/search", {"query": CPF}), ("POST", "/exam-kit-suggestion", {"exam_name": "x"}),
                              ("POST", "/patients", {"full_name": "X Y", "date_of_birth": "1970-01-01", "cpf": CPF_ONLY_B})]:
        r = iso["c"].request(method, url, headers=iso["a2"], json=body)
        assert r.status_code == 409 and "não está vinculado a um estabelecimento" in r.json()["detail"], url


# --- database guards (bypassing the application) -------------------------------------------------

def test_database_guards_reject_ownership_changes(iso):
    rec_a = full_record(iso, iso["a1"], new_patient(iso, iso["a1"]))
    pb = new_patient(iso, iso["b1"])
    raw = get_engine().raw_connection()
    attempts = [
        ("UPDATE patients SET establishment_id = ? WHERE id = ?", (iso["B"], rec_a["pid"])),
        ("UPDATE consultations SET establishment_id = ? WHERE id = ?", (iso["B"], rec_a["cid"])),
        ("UPDATE consultations SET patient_id = ? WHERE id = ?", (pb, rec_a["cid"])),  # to another establishment's patient
        ("INSERT INTO consultations (patient_id, pharmacist_id, establishment_id, consultation_date, created_at, updated_at) "
         "VALUES (?, 1, ?, '2026-01-01', '2026-01-01', '2026-01-01')", (pb, iso["A"])),
        ("INSERT INTO consultations (patient_id, pharmacist_id, establishment_id, consultation_date, created_at, updated_at) "
         "VALUES (?, 1, NULL, '2026-01-01', '2026-01-01', '2026-01-01')", (pb,)),
        ("UPDATE generated_documents SET establishment_id = ? WHERE id = ?", (iso["B"], rec_a["docs"][0])),
        ("INSERT INTO generated_documents (consultation_id, pharmacist_id, establishment_id, document_type, created_at) "
         "VALUES (?, 1, ?, 'referral', '2026-01-01')", (rec_a["cid"], iso["B"])),
        ("INSERT INTO generated_documents (consultation_id, pharmacist_id, establishment_id, document_type, created_at) "
         "VALUES (?, 1, NULL, 'referral', '2026-01-01')", (rec_a["cid"],)),
        ("INSERT INTO patients (establishment_id, full_name_encrypted, full_name_index, date_of_birth_encrypted, "
         "cpf_encrypted, cpf_index, created_at, updated_at) VALUES (NULL, 'x', 'x', 'x', 'x', 'x', '2026-01-01', '2026-01-01')", ()),
    ]
    before = clinical_snapshot()
    try:
        for sql, args in attempts:
            with pytest.raises(sqlite3.DatabaseError):
                raw.execute(sql, args)
            raw.rollback()
    finally:
        raw.close()
    assert clinical_snapshot() == before


# --- route audit: every clinical route is accounted for -------------------------------------------

AUDITED_CLINICAL_ROUTES = {  # all establishment-scoped; administrators 403; unauthenticated 401
    ("POST", "/patients"), ("POST", "/patients/search"), ("GET", "/patients/{patient_id}"),
    ("GET", "/patients/{patient_id}/consultations"), ("POST", "/patients/{patient_id}/consultations"),
    ("GET", "/patients/{patient_id}/consultations/{consultation_id}"),
    ("POST", "/patients/{patient_id}/consultations/{consultation_id}/finalize"),
    ("GET", "/patients/{patient_id}/consultations/{consultation_id}/previous-exams"),
    ("PUT", "/patients/{patient_id}/consultations/{consultation_id}/soap"),
    ("POST", "/patients/{patient_id}/consultations/{consultation_id}/exams"),
    ("PUT", "/patients/{patient_id}/consultations/{consultation_id}/exams/{exam_id}"),
    ("DELETE", "/patients/{patient_id}/consultations/{consultation_id}/exams/{exam_id}"),
    ("POST", "/exam-kit-suggestion"),
    ("GET", "/patients/{patient_id}/consultations/{consultation_id}/documents/{document_id}"),
    ("POST", "/patients/{patient_id}/consultations/{consultation_id}/documents/prescription"),
    ("POST", "/patients/{patient_id}/consultations/{consultation_id}/documents/exam-request"),
    ("POST", "/patients/{patient_id}/consultations/{consultation_id}/documents/referral"),
    ("POST", "/patients/{patient_id}/consultations/{consultation_id}/documents/dsf"),
}


def test_every_clinical_route_is_audited():
    """A new clinical route must be added here (and to the isolation tests) on purpose."""
    from backend.main import app

    clinical = {(method.upper(), path) for path, ops in app.openapi()["paths"].items()
                if path.startswith(("/patients", "/exam-kit-suggestion")) for method in ops}
    assert clinical == AUDITED_CLINICAL_ROUTES
    # each one is exercised against another establishment / an administrator above
    exercised = {(m, u.replace("{patient_id}", "P")) for m, u, _ in all_routes("/patients/P/consultations/C", "P", "E", "D")}
    templated = {(m, p.replace("{patient_id}", "P").replace("{consultation_id}", "C").replace("{exam_id}", "E")
                  .replace("{document_id}", "D")) for m, p in AUDITED_CLINICAL_ROUTES}
    assert templated - exercised == {("POST", "/patients"), ("POST", "/patients/search"), ("POST", "/exam-kit-suggestion")}
