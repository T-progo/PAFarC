"""PharmaTech — Streamlit UI. Run from the project root:

    streamlit run frontend/app.py
"""

import html
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_client import ApiError, PharmaTechAPI  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PHARMATECH_LOGO = ROOT / "IMG_1485.jpeg"
PAFARC_LOGO = ROOT / "IMG_1486.jpeg"
APP_NAME = "PharmaTech"
ESTABLISHMENT = "PAFarC — Programa de Aperfeiçoamento em Farmácia Clínica"

NAV_SEARCH = "Buscar paciente"
NAV_REGISTER = "Cadastrar paciente"

st.set_page_config(page_title=f"{APP_NAME} · PAFarC", page_icon=str(PHARMATECH_LOGO), layout="wide")

st.markdown(
    """
    <style>
    h1, h2, h3 { color: #E6E6E6; letter-spacing: 0.02em; }
    [data-testid="stSidebar"] { border-right: 1px solid #2E2E2E; }
    [data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"] { border-color: #3A3A3A; }
    .pt-label { color: #9E9E9E; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .pt-value { color: #F2F2F2; font-size: 1.15rem; margin-bottom: 0.8rem; }
    .pt-muted { color: #9E9E9E; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- helpers -----------------------------------------------------------------

def api() -> PharmaTechAPI:
    return PharmaTechAPI(token=st.session_state.get("token"))


def format_cpf(digits: str) -> str:
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def mask_cpf(digits: str) -> str:
    return f"***.{digits[3:6]}.{digits[6:9]}-**"


def format_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def logout(message: str | None = None) -> None:
    st.session_state.clear()
    if message:
        st.session_state["flash"] = message
    st.rerun()


def show_api_error(exc: ApiError) -> None:
    if exc.status_code == 401:
        logout("Sessão expirada. Entre novamente.")
    st.error(exc.detail)


def open_patient(patient_id: int) -> None:
    st.session_state["patient_id"] = patient_id


def close_patient() -> None:
    st.session_state.pop("patient_id", None)
    close_consultation()


def open_consultation(consultation_id: int) -> None:
    st.session_state["consultation_id"] = consultation_id


def close_consultation() -> None:
    st.session_state.pop("consultation_id", None)


def field(label: str, value: str) -> None:
    st.markdown(
        f'<div class="pt-label">{html.escape(label)}</div>'
        f'<div class="pt-value">{html.escape(value)}</div>',
        unsafe_allow_html=True,
    )


# --- views -------------------------------------------------------------------

def login_view() -> None:
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.image(str(PHARMATECH_LOGO), width="stretch")
        st.markdown(f'<p class="pt-muted" style="text-align:center">{ESTABLISHMENT}</p>', unsafe_allow_html=True)
        if flash := st.session_state.pop("flash", None):
            st.info(flash)
        with st.form("login_form"):
            login = st.text_input("Login", key="login_user")
            password = st.text_input("Senha", type="password", key="login_password")
            submitted = st.form_submit_button("Entrar", type="primary", width="stretch")
        if submitted:
            if not login or not password:
                st.error("Informe login e senha.")
                return
            try:
                token = PharmaTechAPI().login(login, password)
                pharmacist = PharmaTechAPI(token=token).me()
            except ApiError as exc:
                messages = {401: "Login ou senha inválidos.", 403: "Conta inativa. Procure a coordenação."}
                st.error(messages.get(exc.status_code, exc.detail))
                return
            st.session_state["token"] = token
            st.session_state["pharmacist"] = pharmacist
            st.rerun()


def sidebar() -> None:
    pharmacist = st.session_state["pharmacist"]
    with st.sidebar:
        st.image(str(PAFARC_LOGO), width="stretch")
        st.markdown(f"**{pharmacist['full_name']}**  \n{pharmacist['crf']}")
        st.divider()
        st.radio("Menu", [NAV_SEARCH, NAV_REGISTER], key="nav", on_change=close_patient)
        st.divider()
        if st.button("Sair", key="logout", width="stretch"):
            logout("Sessão encerrada.")


def search_view() -> None:
    st.header("Buscar paciente")
    with st.form("search_form"):
        query = st.text_input("Nome completo ou CPF", key="search_query")
        submitted = st.form_submit_button("Buscar", type="primary")
    st.caption("A busca por nome considera o nome completo (sem diferenciar maiúsculas ou acentos).")
    if submitted:
        if not query.strip():
            st.warning("Informe o nome completo ou o CPF.")
            return
        try:
            st.session_state["search_results"] = api().search_patients(query)
        except ApiError as exc:
            st.session_state.pop("search_results", None)
            show_api_error(exc)
            return

    results = st.session_state.get("search_results")
    if results is None:
        return
    if not results:
        st.info("Nenhum paciente encontrado.")
        return
    st.markdown(f"**{len(results)} paciente(s) encontrado(s)**")
    for patient in results:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.markdown(
                f"**{patient['full_name']}**  \n"
                f"Nascimento: {format_date(patient['date_of_birth'])} · CPF: {mask_cpf(patient['cpf'])}"
            )
            right.button("Abrir", key=f"open_{patient['id']}", on_click=open_patient, args=(patient["id"],))


def register_view() -> None:
    st.header("Cadastrar paciente")
    with st.form("register_form"):
        full_name = st.text_input("Nome completo", key="reg_name")
        date_of_birth = st.date_input(
            "Data de nascimento",
            value=None,
            min_value=date(1900, 1, 1),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="reg_dob",
        )
        cpf = st.text_input("CPF", placeholder="000.000.000-00", key="reg_cpf")
        submitted = st.form_submit_button("Cadastrar", type="primary")
    if submitted:
        if not full_name.strip() or date_of_birth is None or not cpf.strip():
            st.warning("Preencha nome completo, data de nascimento e CPF.")
            return
        try:
            patient = api().create_patient(full_name, date_of_birth, cpf)
        except ApiError as exc:
            show_api_error(exc)
            return
        st.session_state["flash"] = "Paciente cadastrado com sucesso."
        open_patient(patient["id"])
        st.rerun()


def profile_view(patient_id: int) -> None:
    try:
        patient = api().get_patient(patient_id)
    except ApiError as exc:
        close_patient()
        show_api_error(exc)
        return
    st.button("← Voltar", key="back", on_click=close_patient)
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    st.header(patient["full_name"])
    with st.container(border=True):
        st.subheader("Dados do paciente")
        col1, col2, col3 = st.columns(3)
        with col1:
            field("Nome completo", patient["full_name"])
        with col2:
            field("Data de nascimento", format_date(patient["date_of_birth"]))
        with col3:
            field("CPF", format_cpf(patient["cpf"]))
    consultations_section(patient_id)


def consultations_section(patient_id: int) -> None:
    with st.container(border=True):
        title, action = st.columns([4, 1])
        title.subheader("Atendimentos")
        if action.button("Novo atendimento", key="new_consultation", type="primary", width="stretch"):
            try:
                consultation = api().create_consultation(patient_id)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state["flash"] = "Atendimento iniciado."
            open_consultation(consultation["id"])
            st.rerun()
        try:
            history = api().list_consultations(patient_id)
        except ApiError as exc:
            show_api_error(exc)
            return
        if not history:
            st.markdown('<p class="pt-muted">Nenhum atendimento registrado.</p>', unsafe_allow_html=True)
            return
        for consultation in history:
            pharmacist = consultation["pharmacist"]
            left, right = st.columns([5, 1])
            left.markdown(
                f"**{format_date(consultation['consultation_date'])}** · "
                f"{pharmacist['full_name']} · {pharmacist['crf']}"
            )
            right.button(
                "Abrir",
                key=f"open_consultation_{consultation['id']}",
                on_click=open_consultation,
                args=(consultation["id"],),
            )


SOAP_SECTIONS = [
    ("subjective", "Subjetivo"),
    ("objective", "Objetivo"),
    ("assessment", "Avaliação"),
    ("plan", "Plano"),
]
EXAM_COLUMNS = [
    ("exam_name", "Exame"),
    ("result", "Resultado"),
    ("unit", "Unidade"),
    ("reference_range", "Valor/Intervalo de Referência"),
    ("notes", "Observações"),
]


def consultation_view(patient_id: int, consultation_id: int) -> None:
    try:
        patient = api().get_patient(patient_id)
        consultation = api().get_consultation(patient_id, consultation_id)
    except ApiError as exc:
        close_consultation()
        show_api_error(exc)
        return
    pharmacist = consultation["pharmacist"]
    editable = pharmacist["id"] == st.session_state["pharmacist"]["id"]

    st.button("← Voltar ao paciente", key="back_consultation", on_click=close_consultation)
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    st.header(f"Atendimento de {format_date(consultation['consultation_date'])}")
    with st.container(border=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            field("Paciente", patient["full_name"])
        with col2:
            field("Farmacêutico(a)", pharmacist["full_name"])
        with col3:
            field("CRF", pharmacist["crf"])
    if not editable:
        st.info("Somente leitura: este atendimento foi registrado por outro farmacêutico.")

    soap_section(patient_id, consultation, editable)
    exams_section(patient_id, consultation, editable)


def soap_section(patient_id: int, consultation: dict, editable: bool) -> None:
    cid = consultation["id"]
    soap = consultation["soap"] or {}
    with st.container(border=True):
        st.subheader("SOAP")
        if not editable:
            for key, label in SOAP_SECTIONS:
                st.text_area(label, value=soap.get(key) or "—", disabled=True, height=120, key=f"soap_ro_{cid}_{key}")
            return
        with st.form(f"soap_form_{cid}"):
            values = {
                key: st.text_area(label, value=soap.get(key, ""), height=140, key=f"soap_{cid}_{key}")
                for key, label in SOAP_SECTIONS
            }
            submitted = st.form_submit_button("Salvar SOAP", type="primary")
        if submitted:
            try:
                api().save_soap(patient_id, cid, values)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state["flash"] = "SOAP salvo com sucesso."
            st.rerun()


def exams_section(patient_id: int, consultation: dict, editable: bool) -> None:
    cid = consultation["id"]
    exams = consultation["exam_results"]
    with st.container(border=True):
        st.subheader("Resultados de exames")
        if exams:
            st.dataframe(
                [{label: exam[key] for key, label in EXAM_COLUMNS} for exam in exams],
                hide_index=True,
                width="stretch",
            )
        else:
            st.markdown('<p class="pt-muted">Nenhum resultado registrado.</p>', unsafe_allow_html=True)
        if not editable:
            return

        st.markdown("**Adicionar resultado**")
        with st.form(f"exam_add_{cid}"):
            new_exam = exam_inputs(f"exam_new_{cid}", {})
            submitted = st.form_submit_button("Adicionar exame", type="primary")
        if submitted:
            save_exam(patient_id, cid, None, new_exam, "Exame adicionado.")

        if exams:
            st.markdown("**Editar ou excluir resultado**")
            by_id = {exam["id"]: exam for exam in exams}
            selected = st.selectbox(
                "Resultado",
                list(by_id),
                index=None,
                format_func=lambda i: f"{by_id[i]['exam_name']} — {by_id[i]['result']} {by_id[i]['unit']}".strip(),
                placeholder="Selecione um resultado",
                key=f"exam_select_{cid}",
            )
            if selected is not None:
                edit_exam_form(patient_id, cid, by_id[selected])


def exam_inputs(prefix: str, exam: dict) -> dict:
    col1, col2, col3 = st.columns([3, 2, 1])
    values = {
        "exam_name": col1.text_input("Exame", value=exam.get("exam_name", ""), key=f"{prefix}_name"),
        "result": col2.text_input("Resultado", value=exam.get("result", ""), key=f"{prefix}_result"),
        "unit": col3.text_input("Unidade", value=exam.get("unit", ""), key=f"{prefix}_unit"),
    }
    values["reference_range"] = st.text_input(
        "Valor/Intervalo de Referência", value=exam.get("reference_range", ""), key=f"{prefix}_reference"
    )
    values["notes"] = st.text_input("Observações", value=exam.get("notes", ""), key=f"{prefix}_notes")
    return values


def edit_exam_form(patient_id: int, cid: int, exam: dict) -> None:
    with st.form(f"exam_edit_{cid}_{exam['id']}"):
        values = exam_inputs(f"exam_edit_{exam['id']}", exam)
        save_col, delete_col, confirm_col = st.columns([1, 1, 2])
        save = save_col.form_submit_button("Salvar alterações", type="primary")
        delete = delete_col.form_submit_button("Excluir exame")
        confirmed = confirm_col.checkbox("Confirmar exclusão", key=f"exam_delete_confirm_{exam['id']}")
    if save:
        save_exam(patient_id, cid, exam["id"], values, "Resultado atualizado.")
    elif delete:
        if not confirmed:
            st.warning("Marque “Confirmar exclusão” para excluir o resultado.")
            return
        try:
            api().delete_exam(patient_id, cid, exam["id"])
        except ApiError as exc:
            show_api_error(exc)
            return
        del st.session_state[f"exam_select_{cid}"]
        st.session_state["flash"] = "Resultado excluído."
        st.rerun()


def save_exam(patient_id: int, cid: int, exam_id: int | None, values: dict, message: str) -> None:
    if not values["exam_name"].strip() or not values["result"].strip():
        st.warning("Informe ao menos o nome do exame e o resultado.")
        return
    try:
        if exam_id is None:
            api().add_exam(patient_id, cid, values)
        else:
            api().update_exam(patient_id, cid, exam_id, values)
    except ApiError as exc:
        show_api_error(exc)
        return
    if exam_id is None:  # clear the add form only after a successful save
        for key in [k for k in st.session_state if str(k).startswith(f"exam_new_{cid}_")]:
            del st.session_state[key]
    else:
        del st.session_state[f"exam_select_{cid}"]
    st.session_state["flash"] = message
    st.rerun()


# --- main --------------------------------------------------------------------

if "token" not in st.session_state:
    login_view()
else:
    sidebar()
    st.markdown(f"## {APP_NAME}")
    st.caption(ESTABLISHMENT)
    if "patient_id" in st.session_state and "consultation_id" in st.session_state:
        consultation_view(st.session_state["patient_id"], st.session_state["consultation_id"])
    elif "patient_id" in st.session_state:
        profile_view(st.session_state["patient_id"])
    elif st.session_state.get("nav") == NAV_REGISTER:
        register_view()
    else:
        search_view()
