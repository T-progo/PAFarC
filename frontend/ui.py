"""Visual foundation of the Streamlit UI: global CSS, page header, status badges, empty states and
label/value fields. Presentation only: no API calls, no session state, no business rules.

Every helper that builds HTML escapes its text with html.escape, so names, establishment names or
free text typed by users are always shown as text, never interpreted as markup. Status badges use
fixed labels from STATUS (never user text).

CSS targets only stable hooks: data-testid attributes, our own `st-key-<key>` container classes and
the `pt-*` classes created here (never Streamlit's generated class names).
"""

import html

import streamlit as st

CSS = """
:root {
  --bg: #121417; --surface: #181B1F; --surface-nested: #20252B; --surface-selected: #1F2A36;
  --border: #2E343B; --separator: #262B31;
  --text: #E6E8EB; --text-muted: #A1A8B0; --text-subtle: #8B939C; --accent: #6CA6DE; --on-accent: #0E1013;
  --success: #4FBF85; --warning: #E3A443; --danger: #EC7A6C;
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --radius-sm: 4px; --radius-md: 6px; --radius-lg: 8px;
}

/* Page frame: compact top chrome, 32px sides on desktop, 16px on phones. On phones Streamlit's header
   (sidebar toggle) becomes an opaque 53px bar, so the top padding there must clear it. */
[data-testid="stMainBlockContainer"] { padding: var(--space-5) var(--space-6) var(--space-6); }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding: 48px var(--space-4) var(--space-5); }
}

/* Typography */
[data-testid="stMain"] h3 { font-size: 17px; font-weight: 600; letter-spacing: 0; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { font-size: 13px; color: var(--text-muted); }
[data-testid="stWidgetLabel"] p { font-size: 13px; font-weight: 500; }

/* Page header */
.pt-page-header { margin: 0 0 var(--space-1); }
.pt-eyebrow { color: var(--text-subtle); font-size: 12px; font-weight: 600; letter-spacing: .04em;
  text-transform: uppercase; margin-bottom: 2px; overflow-wrap: anywhere; }
.pt-page-title { color: var(--text); font-size: 22px; font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
.pt-page-meta { color: var(--text-muted); font-size: 13px; margin-top: 2px; font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere; }

/* Label / value fields */
.pt-label { color: var(--text-subtle); font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
.pt-value { color: var(--text); font-size: 15px; margin-bottom: var(--space-2); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere; }
.pt-muted { color: var(--text-muted); }

/* Empty state: compact, no illustration */
.pt-empty { border: 1px dashed var(--border); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4);
  background: var(--surface); }
.pt-empty-title { color: var(--text); font-size: 14px; font-weight: 600; }
.pt-empty-text { color: var(--text-muted); font-size: 13px; margin-top: 2px; }

/* Cards (st.container(border=True)) and forms */
[data-testid="stForm"] { border-color: var(--border); border-radius: var(--radius-md); }
[data-testid="stMain"] hr { margin: var(--space-3) 0; }

/* Sidebar */
[data-testid="stSidebar"] { border-right: 1px solid var(--separator); }
[data-testid="stSidebar"] [data-testid="stImage"] img { border-radius: var(--radius-md); }
[data-testid="stSidebar"] hr { margin: var(--space-3) 0; border-color: var(--separator); }
.pt-brand { color: var(--text-subtle); font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
  margin-bottom: var(--space-2); }
.pt-identity-name { color: var(--text); font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.pt-identity-meta { color: var(--text-muted); font-size: 13px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.pt-identity-org { color: var(--text); font-size: 13px; margin-top: var(--space-2); padding: 6px var(--space-2);
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-nested);
  overflow-wrap: anywhere; }
.pt-identity-org-label { display: block; color: var(--text-subtle); font-size: 12px; font-weight: 600;
  letter-spacing: .04em; text-transform: uppercase; }

/* Navigation: the real st.radio (key "nav"), styled as a list. The radio circle stays visible, so the
   selected item is shown by shape and weight, not by colour alone; keyboard behaviour is native. */
.st-key-nav [data-testid="stWidgetLabel"] p { color: var(--text-subtle); font-size: 12px; font-weight: 600;
  letter-spacing: .04em; text-transform: uppercase; }
.st-key-nav, .st-key-nav [data-testid="stRadio"] { width: 100%; }
.st-key-nav [data-testid="stRadioGroup"] { gap: 2px; width: 100%; }
.st-key-nav [data-testid="stRadioGroup"] > div { width: 100%; }
.st-key-nav [data-testid="stRadioOption"] { width: 100%; margin: 0; padding: 7px var(--space-2); min-height: 36px;
  border-radius: var(--radius-sm); border-left: 2px solid transparent; align-items: center; box-sizing: border-box; }
.st-key-nav [data-testid="stRadioOption"]:hover { background: var(--surface-nested); }
.st-key-nav [data-testid="stRadioOption"]:has(input:checked) { background: var(--surface-selected);
  border-left-color: var(--accent); }
.st-key-nav [data-testid="stRadioOption"]:has(input:checked) p { font-weight: 600; color: var(--text); }
.st-key-nav [data-testid="stRadioOption"]:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 1px; }

/* Buttons: dark text on the steel-blue primary (contrast 7.3:1); visible keyboard focus everywhere. */
[data-testid^="stBaseButton-primary"], [data-testid^="stBaseButton-primary"] p { color: var(--on-accent) !important;
  font-weight: 600; }
[data-testid^="stBaseButton"]:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 640px) {
  [data-testid^="stBaseButton"] { min-height: 40px; }
  .st-key-nav [data-testid="stRadioOption"] { min-height: 40px; }
}

/* "Cancelar lançamento": secondary button with a muted danger outline. */
.st-key-qc_cancel_actions button, .st-key-qc_cancel_confirm_actions button { border-color: var(--danger); }
.st-key-qc_cancel_actions button p, .st-key-qc_cancel_confirm_actions button p { color: var(--danger); }
"""


def apply_ui() -> None:
    """Inject the global CSS (a style-only st.html takes no space on the page)."""
    st.html(f"<style>{CSS}</style>")


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def page_header_html(title: str, eyebrow: str | None = None, meta: str | None = None) -> str:
    parts = ['<div class="pt-page-header">']
    if eyebrow:
        parts.append(f'<div class="pt-eyebrow">{_esc(eyebrow)}</div>')
    parts.append(f'<div class="pt-page-title" role="heading" aria-level="1">{_esc(title)}</div>')
    if meta:
        parts.append(f'<div class="pt-page-meta">{_esc(meta)}</div>')
    parts.append("</div>")
    return "".join(parts)


def page_header(title: str, eyebrow: str | None = None, meta: str | None = None, status: str | None = None) -> None:
    """Compact page context: optional eyebrow (section/context), title, optional metadata line and an
    optional status badge (a STATUS key)."""
    st.markdown(page_header_html(title, eyebrow, meta), unsafe_allow_html=True)
    if status:
        status_badge(status)


# Fixed vocabulary: key -> (label, colour, icon). Labels repeat the wording the app already used.
STATUS = {
    "consultation_open": ("Em andamento", "blue", ":material/edit_note:"),
    "consultation_closed": ("Encerrado", "gray", ":material/lock:"),
    "qc_draft": ("Em preenchimento", "gray", ":material/edit:"),
    "qc_finalized": ("Finalizado", "blue", ":material/task_alt:"),
    "qc_cancelled": ("Cancelado", "red", ":material/block:"),
    "qc_approved": ("Aprovado / Adequado", "green", ":material/check_circle:"),
    "qc_rejected": ("Rejeitado / Inadequado", "red", ":material/cancel:"),
    "qc_not_evaluated": ("Status não informado", "gray", ":material/help:"),
    "account_active": ("Ativo", "green", ":material/check_circle:"),
    "account_blocked": ("Bloqueado", "red", ":material/block:"),
    "role_administrator": ("Administrador", "violet", ":material/admin_panel_settings:"),
    "role_pharmacist": ("Farmacêutico(a)", "gray", ":material/medication:"),
}


def consultation_status(consultation: dict) -> str:
    return "consultation_closed" if consultation.get("closed_at") else "consultation_open"


def qc_lifecycle_status(record: dict) -> str:
    """Lifecycle (Em preenchimento / Finalizado / Cancelado): a different question from the evaluation."""
    if record.get("cancelled_at"):
        return "qc_cancelled"
    return "qc_finalized" if record.get("finalized_at") else "qc_draft"


def qc_evaluation_status(record: dict) -> str:
    return {"APPROVED": "qc_approved", "REJECTED": "qc_rejected"}.get(record.get("evaluation_status"), "qc_not_evaluated")


def account_status(user: dict) -> str:
    return "account_active" if user.get("active") else "account_blocked"


def badge_md(status: str) -> str:
    """Inline Markdown badge (icon + text), e.g. inside a list row; same rendering as st.badge."""
    label, color, icon = STATUS[status]
    return f":{color}-badge[{icon} {label}]"


def status_badge(status: str) -> None:
    label, color, icon = STATUS[status]
    st.badge(label, icon=icon, color=color)


def empty_state_html(title: str, text: str | None = None) -> str:
    body = f'<div class="pt-empty-text">{_esc(text)}</div>' if text else ""
    return f'<div class="pt-empty" role="status"><div class="pt-empty-title">{_esc(title)}</div>{body}</div>'


def empty_state(title: str, text: str | None = None) -> None:
    """Compact "nothing here yet" box: a short title and an optional one-line explanation."""
    st.markdown(empty_state_html(title, text), unsafe_allow_html=True)


def field_html(label: str, value: str) -> str:
    return f'<div class="pt-label">{_esc(label)}</div><div class="pt-value">{_esc(value)}</div>'


def field(label: str, value: str) -> None:
    st.markdown(field_html(label, value), unsafe_allow_html=True)


def identity_html(user: dict) -> str:
    """Sidebar identity block: name, CRF (pharmacists) or role (administrators), current establishment."""
    parts = [f'<div class="pt-identity-name">{_esc(user.get("full_name", ""))}</div>']
    if user.get("is_admin"):
        parts.append('<div class="pt-identity-meta">Administrador</div>')
    else:
        if user.get("crf"):
            parts.append(f'<div class="pt-identity-meta">{_esc(user["crf"])}</div>')
        org = user.get("establishment_name") or "Sem estabelecimento vinculado"
        parts.append(f'<div class="pt-identity-org"><span class="pt-identity-org-label">Estabelecimento</span>{_esc(org)}</div>')
    return "".join(parts)
