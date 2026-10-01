"""PharmaTech — Streamlit UI. Run from the project root:

    streamlit run frontend/app.py
"""

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


def field(label: str, value: str) -> None:
    st.markdown(
        f'<div class="pt-label">{label}</div><div class="pt-value">{value}</div>',
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
    with st.container(border=True):
        st.subheader("Atendimentos")
        st.markdown('<p class="pt-muted">Funcionalidade ainda não disponível.</p>', unsafe_allow_html=True)


# --- main --------------------------------------------------------------------

if "token" not in st.session_state:
    login_view()
else:
    sidebar()
    st.markdown(f"## {APP_NAME}")
    st.caption(ESTABLISHMENT)
    if "patient_id" in st.session_state:
        profile_view(st.session_state["patient_id"])
    elif st.session_state.get("nav") == NAV_REGISTER:
        register_view()
    else:
        search_view()
