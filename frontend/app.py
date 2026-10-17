"""PharmaTech — Streamlit UI. Run from the project root:

    streamlit run frontend/app.py
"""

import html
import re
import sys
from datetime import date, datetime, timezone
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
NAV_USERS = "Administração · Usuários"
NAV_ESTABLISHMENT = "Administração · Estabelecimentos"
ADMIN_NAV = (NAV_USERS, NAV_ESTABLISHMENT)
ROLE_LABELS = {"administrator": "Administrador", "pharmacist": "Farmacêutico(a)"}
UFS = ("AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
       "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO")

st.set_page_config(page_title=f"{APP_NAME} · PAFarC", page_icon=str(PHARMATECH_LOGO), layout="wide")

st.markdown(
    """
    <style>
    h1, h2, h3 { color: #F7EFE3; letter-spacing: 0.02em; }
    [data-testid="stSidebar"] { border-right: 1px solid #4A4138; }
    [data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"] { border-color: #554A40; }
    .pt-label { color: #BBAE9E; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .pt-value { color: #F3ECE2; font-size: 1.15rem; margin-bottom: 0.8rem; }
    .pt-muted { color: #BBAE9E; }
    /* Dark text on the champagne primary buttons (white-on-silver was unreadable). */
    button[kind^="primary"], button[kind^="primary"] p { color: #2B2622 !important; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- helpers -----------------------------------------------------------------

def api() -> PharmaTechAPI:
    return PharmaTechAPI(token=st.session_state.get("token"))


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def md(text: str) -> str:
    """Escape user-entered text before it is placed inside Markdown."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


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
    # Clear the menu selection so both menu items respond to a click while a
    # patient is open (a radio only reacts when its selection changes).
    st.session_state["reset_nav"] = True


def close_patient() -> None:
    st.session_state.pop("patient_id", None)
    close_consultation()


def open_consultation(consultation_id: int) -> None:
    st.session_state["consultation_id"] = consultation_id


def soap_has_unsaved_changes() -> bool:
    saved = st.session_state.get("soap_saved")
    if not saved or saved["cid"] != st.session_state.get("consultation_id"):
        return False
    cid = saved["cid"]
    return any(
        str(st.session_state.get(f"soap_{cid}_{key}", value)).strip() != value.strip()
        for key, value in saved["values"].items()
    )


def may_leave_consultation() -> bool:
    """False (and a warning on the next render) the first time the user tries to
    leave with unsaved SOAP text; True on a second attempt, i.e. discard."""
    if not soap_has_unsaved_changes() or st.session_state.pop("unsaved_warned", False):
        return True
    st.session_state["unsaved_warned"] = True
    return False


def leave_consultation() -> None:
    if may_leave_consultation():
        close_consultation()


def on_nav_change() -> None:
    if may_leave_consultation():
        close_patient()


def close_consultation() -> None:
    st.session_state.pop("consultation_id", None)
    st.session_state.pop("soap_saved", None)
    st.session_state.pop("unsaved_warned", None)
    # Generated PDFs live only in session memory; drop them when leaving the consultation.
    for key in [k for k in st.session_state if str(k).startswith("doc_result_")]:
        del st.session_state[key]


def field(label: str, value: str) -> None:
    st.markdown(
        f'<div class="pt-label">{html.escape(label)}</div>'
        f'<div class="pt-value">{html.escape(value)}</div>',
        unsafe_allow_html=True,
    )


def unsaved_page_guard(active: bool) -> None:
    """Ask the browser to confirm before closing/reloading the tab while SOAP has unsaved text."""
    flag = "true" if active else "false"
    st.html(
        f"<script>window.__ptUnsaved = {flag};"
        "if (!window.__ptGuard) {window.__ptGuard = true;"
        "window.addEventListener('beforeunload', function (e) {"
        "if (window.__ptUnsaved) {e.preventDefault(); e.returnValue = '';}});}</script>",
        unsafe_allow_javascript=True,
    )


def install_app_metadata() -> None:
    """Installable web-app (PWA) metadata: adds the manifest, theme colour and icon to <head>.
    Nothing is cached: there is no service worker and no offline storage."""
    st.html(
        "<script>(function () {var h = document.head;"
        "if (h.querySelector('link[rel=manifest]')) return;"
        "function add(tag, attrs) {var e = document.createElement(tag);"
        "for (var k in attrs) e.setAttribute(k, attrs[k]); h.appendChild(e);}"
        "add('link', {rel: 'manifest', href: 'app/static/manifest.json'});"
        "add('meta', {name: 'theme-color', content: '#2B2622'});"
        "add('meta', {name: 'mobile-web-app-capable', content: 'yes'});"
        "add('meta', {name: 'apple-mobile-web-app-capable', content: 'yes'});"
        "add('meta', {name: 'apple-mobile-web-app-title', content: 'PharmaTech'});"
        "add('link', {rel: 'apple-touch-icon', href: 'app/static/apple-touch-icon.png'});"
        "})();</script>",
        unsafe_allow_javascript=True,
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
                messages = {
                    401: "Login ou senha inválidos.",
                    403: "Conta inativa. Procure a coordenação.",
                    429: "Muitas tentativas de login. Aguarde 15 minutos e tente novamente.",
                }
                st.error(messages.get(exc.status_code, exc.detail))
                return
            st.session_state["token"] = token
            st.session_state["pharmacist"] = pharmacist
            st.rerun()


def sidebar() -> None:
    pharmacist = st.session_state["pharmacist"]
    if st.session_state.pop("reset_nav", False):
        st.session_state["nav"] = None
    with st.sidebar:
        st.image(str(PAFARC_LOGO), width="stretch")
        st.markdown(f"**{md(pharmacist['full_name'])}**  \n{md(pharmacist['crf'])}")
        st.divider()
        st.radio("Menu", menu_options(), key="nav", on_change=on_nav_change)
        st.divider()
        if st.button("Sair", key="logout", width="stretch"):
            if may_leave_consultation():
                logout("Sessão encerrada.")
            st.rerun()


def menu_options() -> list[str]:
    """Clinical entries for clinical users, administration entries for administrators only.
    (The API enforces the same rules; this only decides what is shown.)"""
    user = st.session_state["pharmacist"]
    options = [NAV_SEARCH, NAV_REGISTER] if user.get("clinical_access", True) else []
    if user.get("is_admin"):
        options += list(ADMIN_NAV)
    return options


def bump_admin_forms() -> None:
    """New widget keys after a successful change, so the browser shows fresh forms."""
    st.session_state["admin_gen"] = st.session_state.get("admin_gen", 0) + 1


def establishment_label(est: dict) -> str:
    return f"{est['name']} (CNPJ {est['cnpj']})" if est.get("cnpj") else est["name"]


def users_view() -> None:
    st.header("Usuários")
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    try:
        users = api().list_users()
        establishments = api().list_establishments()
    except ApiError as exc:
        show_api_error(exc)
        return
    est_by_id = {e["id"]: e for e in establishments}
    gen = st.session_state.get("admin_gen", 0)
    st.dataframe(
        [{"Nome": u["full_name"], "CRF": u["crf"] or "—", "Login": u["login"],
          "Perfil": ROLE_LABELS.get(u["role"], u["role"]),
          "Estabelecimento": u["establishment_name"] or "—",
          "Situação": "Ativo" if u["active"] else "Bloqueado"} for u in users],
        hide_index=True, width="stretch",
    )

    st.subheader("Novo usuário")
    # Outside the form so the fields below follow the chosen profile.
    role = st.radio("Perfil do novo usuário", list(ROLE_LABELS), index=1, format_func=ROLE_LABELS.get,
                    horizontal=True, key=f"un_role_{gen}")
    is_pharmacist = role == "pharmacist"
    with st.form(f"user_new_{gen}"):
        name = st.text_input("Nome completo", key=f"un_name_{gen}")
        crf = uf = est = None
        if is_pharmacist:
            c1, c2 = st.columns([3, 1])
            crf = c1.text_input("CRF (número)", key=f"un_crf_{gen}")
            uf = c2.selectbox("UF", UFS, index=None, placeholder="UF", key=f"un_uf_{gen}")
            est = st.selectbox("Estabelecimento", list(est_by_id), index=None, placeholder="Selecione o estabelecimento",
                               format_func=lambda i: establishment_label(est_by_id[i]), key=f"un_est_{gen}")
        login = st.text_input("Login / e-mail", key=f"un_login_{gen}")
        p1, p2 = st.columns(2)
        password = p1.text_input("Senha", type="password", key=f"un_pw_{gen}")
        confirm = p2.text_input("Confirmar senha", type="password", key=f"un_pw2_{gen}")
        submitted = st.form_submit_button("Criar usuário", type="primary")
    if submitted:
        if not all((name.strip(), login.strip(), password)) or (
                is_pharmacist and not ((crf or "").strip() and uf and est is not None)):
            st.warning("Preencha nome, login e senha" + (", CRF, UF e estabelecimento." if is_pharmacist else "."))
        elif password != confirm:
            st.warning("As senhas não conferem.")
        else:
            payload = {"full_name": name, "login": login, "password": password, "role": role}
            if is_pharmacist:
                payload.update({"crf": crf, "crf_uf": uf, "establishment_id": est})
            try:
                api().create_user(payload)
            except ApiError as exc:
                show_api_error(exc)
            else:
                st.session_state["flash"] = "Usuário criado."
                bump_admin_forms()
                st.rerun()

    st.subheader("Editar usuário")
    by_id = {u["id"]: u for u in users}
    selected = st.selectbox("Usuário", list(by_id), index=None, placeholder="Selecione um usuário",
                            format_func=lambda i: f"{by_id[i]['full_name']} ({by_id[i]['login']})",
                            key=f"user_select_{gen}")
    if selected is not None:
        edit_user_panel(by_id[selected], gen, est_by_id)


def edit_user_panel(user: dict, gen: int, est_by_id: dict) -> None:
    uid = user["id"]
    is_self = uid == st.session_state["pharmacist"]["id"]
    role = st.radio("Perfil", list(ROLE_LABELS), index=list(ROLE_LABELS).index(user["role"]),
                    format_func=ROLE_LABELS.get, horizontal=True, key=f"ue_role_{uid}_{gen}", disabled=is_self)
    is_pharmacist = role == "pharmacist"
    with st.form(f"user_edit_{uid}_{gen}"):
        name = st.text_input("Nome completo", value=user["full_name"], key=f"ue_name_{uid}_{gen}")
        crf = uf = est = None
        if is_pharmacist:
            c1, c2 = st.columns([3, 1])
            crf = c1.text_input("CRF (número)", value=user["crf_number"], key=f"ue_crf_{uid}_{gen}")
            uf_index = UFS.index(user["crf_uf"]) if user["crf_uf"] in UFS else None
            uf = c2.selectbox("UF", UFS, index=uf_index, placeholder="UF", key=f"ue_uf_{uid}_{gen}")
            est_ids = list(est_by_id)
            est_index = est_ids.index(user["establishment_id"]) if user["establishment_id"] in est_ids else None
            est = st.selectbox("Estabelecimento", est_ids, index=est_index, placeholder="Selecione o estabelecimento",
                               format_func=lambda i: establishment_label(est_by_id[i]), key=f"ue_est_{uid}_{gen}")
        login = st.text_input("Login / e-mail", value=user["login"], key=f"ue_login_{uid}_{gen}")
        save = st.form_submit_button("Salvar alterações", type="primary")
    if save:
        changes = {}
        if name.strip() != user["full_name"]:
            changes["full_name"] = name
        if role != user["role"]:
            changes["role"] = role
        if is_pharmacist:
            if not (crf or "").strip() or not uf or est is None:
                st.warning("Informe o número do CRF, a UF e o estabelecimento.")
                return
            # CRF rewritten only if changed (or needed because the profile changes)
            if crf.strip() != user["crf_number"] or uf != user["crf_uf"] or (role != user["role"] and not user["crf"]):
                changes.update({"crf": crf, "crf_uf": uf})
            if est != user["establishment_id"]:
                changes["establishment_id"] = est
        if login.strip().lower() != user["login"]:
            changes["login"] = login
        if not changes:
            st.info("Nenhuma alteração.")
        else:
            try:
                api().update_user(uid, changes)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state["flash"] = "Usuário atualizado."
            bump_admin_forms()
            st.rerun()

    status_col, password_col = st.columns(2)
    with status_col:
        st.markdown(f"**Situação:** {'Ativo' if user['active'] else 'Bloqueado'}")
        if is_self:
            st.caption("Você não pode bloquear o seu próprio usuário.")
        else:
            label = "Bloquear usuário" if user["active"] else "Reativar usuário"
            if st.button(label, key=f"ue_toggle_{uid}_{gen}"):
                try:
                    api().set_user_active(uid, not user["active"])
                except ApiError as exc:
                    show_api_error(exc)
                    return
                st.session_state["flash"] = "Usuário bloqueado." if user["active"] else "Usuário reativado."
                bump_admin_forms()
                st.rerun()
    with password_col:
        with st.form(f"user_pw_{uid}_{gen}"):
            new_pw = st.text_input("Nova senha", type="password", key=f"ue_pw_{uid}_{gen}")
            confirm = st.text_input("Confirmar nova senha", type="password", key=f"ue_pw2_{uid}_{gen}")
            reset = st.form_submit_button("Redefinir senha")
        if reset:
            if not new_pw or new_pw != confirm:
                st.warning("As senhas não conferem.")
                return
            try:
                api().reset_password(uid, new_pw)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state["flash"] = "Senha redefinida."
            bump_admin_forms()
            st.rerun()


def establishments_view() -> None:
    st.header("Estabelecimentos")
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    try:
        establishments = api().list_establishments()
    except ApiError as exc:
        show_api_error(exc)
        return
    gen = st.session_state.get("admin_gen", 0)
    st.dataframe(
        [{"Nome": e["name"], "CNPJ": e["cnpj"] or "—", "Endereço": e["address"] or "—",
          "Telefone": e["phone"] or "—"} for e in establishments],
        hide_index=True, width="stretch",
    )
    st.caption("O estabelecimento vinculado a cada farmacêutico aparece no cabeçalho dos novos documentos "
               "emitidos por ele. Documentos já emitidos não são alterados.")

    st.subheader("Novo estabelecimento")
    with st.form(f"est_new_{gen}"):
        name = st.text_input("Nome do estabelecimento", key=f"en_name_{gen}")
        cnpj = st.text_input("CNPJ", placeholder="00.000.000/0000-00", key=f"en_cnpj_{gen}")
        address = st.text_input("Endereço completo", key=f"en_address_{gen}")
        phone = st.text_input("Telefone", key=f"en_phone_{gen}")
        submitted = st.form_submit_button("Criar estabelecimento", type="primary")
    if submitted:
        if not name.strip():
            st.warning("Informe o nome do estabelecimento.")
        else:
            try:
                api().create_establishment({"name": name, "cnpj": cnpj, "address": address, "phone": phone})
            except ApiError as exc:
                show_api_error(exc)
            else:
                st.session_state["flash"] = "Estabelecimento criado."
                bump_admin_forms()
                st.rerun()

    st.subheader("Editar estabelecimento")
    by_id = {e["id"]: e for e in establishments}
    selected = st.selectbox("Estabelecimento", list(by_id), index=None, placeholder="Selecione um estabelecimento",
                            format_func=lambda i: establishment_label(by_id[i]), key=f"est_select_{gen}")
    if selected is None:
        return
    current = by_id[selected]
    with st.form(f"est_edit_{selected}_{gen}"):
        values = {
            "name": st.text_input("Nome do estabelecimento", value=current["name"], key=f"ee_name_{selected}_{gen}"),
            "cnpj": st.text_input("CNPJ", value=current["cnpj"], key=f"ee_cnpj_{selected}_{gen}"),
            "address": st.text_input("Endereço completo", value=current["address"], key=f"ee_address_{selected}_{gen}"),
            "phone": st.text_input("Telefone", value=current["phone"], key=f"ee_phone_{selected}_{gen}"),
        }
        save = st.form_submit_button("Salvar alterações", type="primary")
    if save:
        changes = {k: v for k, v in values.items() if v.strip() != (current[k] or "")}
        if not values["name"].strip():
            st.warning("Informe o nome do estabelecimento.")
        elif not changes:
            st.info("Nenhuma alteração.")
        else:
            try:
                api().update_establishment(selected, changes)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state["flash"] = "Estabelecimento atualizado."
            bump_admin_forms()
            st.rerun()


def search_view() -> None:
    st.header("Buscar paciente")
    with st.form("search_form"):
        query = st.text_input("Nome ou CPF", key="search_query")
        submitted = st.form_submit_button("Buscar", type="primary")
    st.caption(
        "Digite o nome (ou o início de cada nome, com pelo menos 3 letras — ex.: \"maria sant\") "
        "ou o CPF completo. Maiúsculas e acentos não fazem diferença."
    )
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
                f"**{md(patient['full_name'])}**  \n"
                f"Nascimento: {format_date(patient['date_of_birth'])} · CPF: {md(mask_cpf(patient['cpf']))}"
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
    st.header(md(patient["full_name"]))
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
                f"{md(pharmacist['full_name'])} · {md(pharmacist['crf'])}"
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

    st.button("← Voltar ao paciente", key="back_consultation", on_click=leave_consultation)
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    if st.session_state.get("unsaved_warned"):
        st.warning(
            "Há alterações não salvas no SOAP. Clique em “Salvar SOAP” para mantê-las, "
            "ou repita a ação (Voltar, menu ou Sair) para descartá-las."
        )
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

    def exams_after_objective() -> None:
        exams_section(patient_id, consultation, editable)
        previous_exams_section(patient_id, consultation["id"])

    soap_section(patient_id, consultation, editable, exams_after_objective)
    documents_section(patient_id, patient, consultation, editable)


def soap_section(patient_id: int, consultation: dict, editable: bool, after_objective) -> None:
    """SOAP fields; `after_objective` renders what goes between Objetivo and Avaliação."""
    cid = consultation["id"]
    soap = consultation["soap"] or {}
    upper, lower = SOAP_SECTIONS[:2], SOAP_SECTIONS[2:]  # (Subjetivo, Objetivo) / (Avaliação, Plano)
    with st.container(border=True):
        st.subheader("SOAP")
        if not editable:
            for key, label in upper:
                st.text_area(label, value=soap.get(key) or "—", disabled=True, height=120, key=f"soap_ro_{cid}_{key}")
            after_objective()
            for key, label in lower:
                st.text_area(label, value=soap.get(key) or "—", disabled=True, height=120, key=f"soap_ro_{cid}_{key}")
            return
        # Not inside st.form, so edits reach the session as soon as a field loses
        # focus; that lets the app tell saved from unsaved text.
        saved = {key: soap.get(key) or "" for key, _ in SOAP_SECTIONS}
        st.session_state["soap_saved"] = {"cid": cid, "values": saved}
        values = {
            key: st.text_area(label, value=saved[key], height=140, key=f"soap_{cid}_{key}")
            for key, label in upper
        }
        after_objective()
        values.update({
            key: st.text_area(label, value=saved[key], height=140, key=f"soap_{cid}_{key}")
            for key, label in lower
        })
        save_col, state_col = st.columns([1, 4])
        if save_col.button("Salvar SOAP", type="primary", key=f"soap_save_{cid}"):
            try:
                api().save_soap(patient_id, cid, values)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state.pop("unsaved_warned", None)
            st.session_state["flash"] = "SOAP salvo com sucesso."
            st.rerun()
        if soap_has_unsaved_changes():
            state_col.markdown(":orange[● Alterações não salvas]")
            unsaved_page_guard(True)
        else:
            if consultation["soap"] is not None:
                state_col.markdown(":gray[✓ SOAP salvo]")
            unsaved_page_guard(False)


def exams_section(patient_id: int, consultation: dict, editable: bool) -> None:
    cid = consultation["id"]
    exams = consultation["exam_results"]
    with st.container(border=True):
        st.subheader("Resultados de exames deste atendimento")
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

        # The generation number is part of every exam widget key: bumping it after a
        # successful add/edit/delete gives the browser fresh (empty/unselected) widgets.
        gen = exam_widgets_generation(cid)
        st.markdown("**Adicionar resultado**")
        with st.form(f"exam_add_{cid}_{gen}"):
            new_exam = exam_inputs(f"exam_new_{cid}_{gen}", {})
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
                key=f"exam_select_{cid}_{gen}",
            )
            if selected is not None:
                edit_exam_form(patient_id, cid, by_id[selected], gen)


def previous_exams_section(patient_id: int, cid: int) -> None:
    """Read-only exam results of this patient's earlier consultations, for comparison."""
    try:
        groups = api().previous_exams(patient_id, cid)
    except ApiError as exc:
        show_api_error(exc)
        return
    with st.container(border=True):
        st.subheader("Exames anteriores")
        if not groups:
            st.markdown('<p class="pt-muted">Nenhum exame em atendimentos anteriores.</p>', unsafe_allow_html=True)
            return
        st.caption("Somente leitura: resultados registrados em atendimentos anteriores deste paciente.")
        for index, group in enumerate(groups):
            pharmacist = group["pharmacist"]
            title = (f"{format_date(group['consultation_date'])} · {pharmacist['full_name']} · "
                     f"{pharmacist['crf']} · {len(group['exam_results'])} exame(s)")
            with st.expander(title, expanded=index == 0):
                st.dataframe(
                    [{label: exam[key] for key, label in EXAM_COLUMNS} for exam in group["exam_results"]],
                    hide_index=True,
                    width="stretch",
                )


def exam_widgets_generation(cid: int) -> int:
    return st.session_state.setdefault(f"exam_gen_{cid}", 0)


def reset_exam_widgets(cid: int) -> None:
    st.session_state[f"exam_gen_{cid}"] = exam_widgets_generation(cid) + 1


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


def edit_exam_form(patient_id: int, cid: int, exam: dict, gen: int) -> None:
    with st.form(f"exam_edit_{cid}_{exam['id']}_{gen}"):
        values = exam_inputs(f"exam_edit_{exam['id']}_{gen}", exam)
        save_col, delete_col, confirm_col = st.columns([1, 1, 2])
        save = save_col.form_submit_button("Salvar alterações", type="primary")
        delete = delete_col.form_submit_button("Excluir exame")
        confirmed = confirm_col.checkbox("Confirmar exclusão", key=f"exam_delete_confirm_{exam['id']}_{gen}")
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
        reset_exam_widgets(cid)
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
    reset_exam_widgets(cid)  # only after a successful save, so failed input is kept
    st.session_state["flash"] = message
    st.rerun()


DOC_PRESCRIPTION = "prescription"
DOC_EXAM_REQUEST = "exam-request"
DOC_REFERRAL = "referral"
DOC_DSF = "dsf"
DOCUMENT_LABELS = {
    DOC_PRESCRIPTION: "Prescrição / Plano de Cuidado",
    DOC_EXAM_REQUEST: "Solicitação de Exames",
    DOC_REFERRAL: "Encaminhamento / Interconsulta",
    DOC_DSF: "Declaração de Serviço Farmacêutico (DSF)",
}
ISSUED_LABELS = {
    "prescription": "Prescrição / Plano de Cuidado",
    "exam_request": "Solicitação de Exames Laboratoriais",
    "referral": "Encaminhamento / Interconsulta",
    "dsf": "Declaração de Serviço Farmacêutico (DSF)",
}
REPRINT_FILE_PREFIXES = {
    "prescription": "prescricao-plano-de-cuidado",
    "exam_request": "solicitacao-de-exames",
    "referral": "encaminhamento-interconsulta",
    "dsf": "declaracao-servico-farmaceutico",
}
MAX_PRESCRIPTION_ITEMS = 15


def format_timestamp(iso: str) -> str:
    value = datetime.fromisoformat(iso)
    if value.tzinfo is None:  # stored as UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d/%m/%Y %H:%M")


def documents_section(patient_id: int, patient: dict, consultation: dict, editable: bool) -> None:
    cid = consultation["id"]
    with st.container(border=True):
        st.subheader("Documentos")
        issued = consultation.get("documents", [])
        if issued:
            st.markdown("**Documentos emitidos**")
            token = st.session_state.get("token")
            for doc in reversed(issued):
                left, right = st.columns([5, 1])
                left.markdown(
                    f"{ISSUED_LABELS.get(doc['document_type'], doc['document_type'])} · "
                    f"{format_timestamp(doc['created_at'])} · {md(doc['pharmacist']['full_name'])}"
                )
                if doc["reprintable"]:
                    prefix = REPRINT_FILE_PREFIXES.get(doc["document_type"], "documento")
                    right.download_button(
                        "Reimprimir",
                        # Fetched only when clicked: the stored PDF exactly as issued.
                        data=lambda doc_id=doc["id"]: PharmaTechAPI(token=token).reprint_document(
                            patient_id, cid, doc_id),
                        file_name=f"{prefix}-atendimento-{cid}-{doc['id']}.pdf",
                        mime="application/pdf",
                        on_click="ignore",
                        key=f"reprint_{doc['id']}",
                    )
                else:
                    right.caption("Não disponível")
        if not editable:
            if not issued:
                st.markdown('<p class="pt-muted">Nenhum documento emitido.</p>', unsafe_allow_html=True)
            return

        kind = st.radio(
            "Tipo de documento",
            list(DOCUMENT_LABELS),
            format_func=DOCUMENT_LABELS.get,
            horizontal=True,
            key=f"doc_type_{cid}",
        )
        pharmacist = st.session_state["pharmacist"]
        st.caption(
            f"Paciente: {md(patient['full_name'])} · CPF {format_cpf(patient['cpf'])} — "
            f"Farmacêutico(a): {md(pharmacist['full_name'])} · {md(pharmacist['crf'])} "
            "(preenchidos automaticamente no documento)"
        )
        if kind == DOC_PRESCRIPTION:
            payload = prescription_form(cid)
        elif kind == DOC_EXAM_REQUEST:
            payload = exam_request_form(cid, consultation)
        elif kind == DOC_DSF:
            payload = dsf_form(cid, consultation)
        else:
            payload = referral_form(cid)

        if payload is not None:
            try:
                filename, data = api().generate_document(patient_id, cid, kind, payload)
            except ApiError as exc:
                show_api_error(exc)
                return
            st.session_state[f"doc_result_{cid}"] = {"kind": kind, "filename": filename, "data": data}
            st.session_state["flash"] = f"{DOCUMENT_LABELS[kind]}: PDF gerado."
            st.rerun()

        result = st.session_state.get(f"doc_result_{cid}")
        if result and result["kind"] == kind:
            st.download_button(
                "Baixar PDF",
                data=result["data"],
                file_name=result["filename"],
                mime="application/pdf",
                type="primary",
                on_click="ignore",
                key=f"doc_download_{cid}",
            )


def prescription_form(cid: int) -> dict | None:
    count = st.number_input(
        "Número de itens", min_value=1, max_value=MAX_PRESCRIPTION_ITEMS, value=1, step=1, key=f"rx_count_{cid}"
    )
    with st.form(f"rx_form_{cid}"):
        items = []
        for i in range(int(count)):
            with st.container(border=True):
                st.markdown(f"**Item {i + 1}**")
                item = {"medication": st.text_input("Medicamento / cuidado", key=f"rx_{cid}_{i}_medication")}
                col1, col2 = st.columns(2)
                item["dosage"] = col1.text_input("Dosagem", key=f"rx_{cid}_{i}_dosage")
                item["route"] = col2.text_input("Via", key=f"rx_{cid}_{i}_route")
                col3, col4 = st.columns(2)
                item["posology"] = col3.text_input("Posologia", key=f"rx_{cid}_{i}_posology")
                item["duration"] = col4.text_input("Tempo de tratamento", key=f"rx_{cid}_{i}_duration")
                item["guidance"] = st.text_area(
                    "Orientações farmacêuticas / estilo de vida", height=90, key=f"rx_{cid}_{i}_guidance"
                )
                items.append(item)
        submitted = st.form_submit_button("Gerar PDF", type="primary")
    if not submitted:
        return None
    if any(not item["medication"].strip() for item in items):
        st.warning("Informe o medicamento ou cuidado em todos os itens.")
        return None
    return {"items": items}


def exam_request_form(cid: int, consultation: dict) -> dict | None:
    recorded = list(dict.fromkeys(exam["exam_name"] for exam in consultation["exam_results"]))
    with st.form(f"exam_request_form_{cid}"):
        selected = st.multiselect(
            "Exames registrados neste atendimento",
            recorded,
            placeholder="Selecione (opcional)",
            key=f"er_{cid}_selected",
        ) if recorded else []
        others = st.text_area("Exames solicitados (um por linha)", height=110, key=f"er_{cid}_others")
        justification = st.text_area("Justificativa clínica", height=110, key=f"er_{cid}_justification")
        context = st.text_area(
            "Contexto do acompanhamento farmacoterapêutico (opcional)", height=90, key=f"er_{cid}_context"
        )
        submitted = st.form_submit_button("Gerar PDF", type="primary")
    if not submitted:
        return None
    exams = list(dict.fromkeys(selected + [line.strip() for line in others.splitlines() if line.strip()]))
    if not exams or not justification.strip():
        st.warning("Informe ao menos um exame e a justificativa clínica.")
        return None
    return {"exams": exams, "clinical_justification": justification, "follow_up_context": context}


def referral_form(cid: int) -> dict | None:
    with st.form(f"referral_form_{cid}"):
        destination = st.text_input("Profissional / equipe de destino", key=f"ref_{cid}_destination")
        summary = st.text_area("Resumo do caso", height=130, key=f"ref_{cid}_summary")
        prm = st.text_area("PRM identificado", height=90, key=f"ref_{cid}_prm")
        conduct = st.text_area("Conduta sugerida", height=90, key=f"ref_{cid}_conduct")
        submitted = st.form_submit_button("Gerar PDF", type="primary")
    if not submitted:
        return None
    if not all(v.strip() for v in (destination, summary, prm, conduct)):
        st.warning("Preencha destino, resumo do caso, PRM e conduta sugerida.")
        return None
    return {"destination": destination, "case_summary": summary, "prm": prm, "suggested_conduct": conduct}


def dsf_form(cid: int, consultation: dict) -> dict | None:
    results = consultation["exam_results"]
    if not results:
        st.info("Para emitir a DSF, registre primeiro os resultados de exame neste atendimento "
                "(seção Resultados de exames deste atendimento).")
        return None
    st.markdown("**Resultados que serão incluídos na declaração**")
    st.dataframe(
        [{"Exame": e["exam_name"], "Resultado": e["result"], "Unidade": e["unit"] or "—",
          "Valor de referência": e["reference_range"] or "—", "Observações": e["notes"] or "—"} for e in results],
        hide_index=True, width="stretch",
    )
    st.caption("Os resultados vêm do atendimento. Para corrigir algum, altere-o na seção "
               "Resultados de exames deste atendimento.")
    with st.form(f"dsf_form_{cid}"):
        manufacturer = st.text_input("Fabricante do teste", max_chars=200, key=f"dsf_{cid}_manufacturer")
        c1, c2 = st.columns(2)
        lot = c1.text_input("Número do lote", max_chars=100, key=f"dsf_{cid}_lot")
        expiry = c2.date_input("Data de validade", value=None, format="DD/MM/YYYY",
                               min_value=date(2000, 1, 1), max_value=date(2100, 12, 31), key=f"dsf_{cid}_expiry")
        submitted = st.form_submit_button("Gerar PDF", type="primary")
    if not submitted:
        return None
    if not manufacturer.strip() or not lot.strip() or expiry is None:
        st.warning("Informe o fabricante do teste, o número do lote e a data de validade.")
        return None
    return {"manufacturer": manufacturer, "lot": lot, "expiry_date": expiry.isoformat()}


# --- main --------------------------------------------------------------------

install_app_metadata()

if "token" not in st.session_state:
    login_view()
else:
    sidebar()
    st.markdown(f"## {APP_NAME}")
    st.caption(ESTABLISHMENT)
    user = st.session_state["pharmacist"]
    nav = st.session_state.get("nav")
    clinical = user.get("clinical_access", True)
    if user.get("is_admin") and (nav in ADMIN_NAV or not clinical):
        if nav == NAV_ESTABLISHMENT:
            establishments_view()
        else:
            users_view()
    elif not clinical:
        st.info("Seu perfil não tem acesso às funções clínicas.")
    elif "patient_id" in st.session_state and "consultation_id" in st.session_state:
        consultation_view(st.session_state["patient_id"], st.session_state["consultation_id"])
    elif "patient_id" in st.session_state:
        profile_view(st.session_state["patient_id"])
    elif nav == NAV_REGISTER:
        register_view()
    else:
        search_view()
