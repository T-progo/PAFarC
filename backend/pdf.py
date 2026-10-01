"""Shared PDF service for PharmaTech documents.

Centralizes page setup, fonts (Unicode), the PAFarC header, footer with page
numbers, patient/pharmacist identification, safe multiline text and the
date/location + signature block. Output is produced in memory as bytes.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from backend.config import get_settings

APP_NAME = "PharmaTech"
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
LOGO_PATH = Path(__file__).resolve().parent.parent / "IMG_1486.jpeg"  # PAFarC logo, used as-is
FONT = "DejaVu"

# Black / graphite / silver identity. The header band matches the logo's own
# near-black background so the JPEG sits in it without a visible box.
BAND = (10, 10, 10)
WHITE = (255, 255, 255)
SILVER = (192, 192, 192)
GRAPHITE = (45, 45, 48)
TEXT = (25, 25, 25)
MUTED = (110, 110, 110)
RULE = (175, 175, 175)

MARGIN = 18
BAND_HEIGHT = 34
LOGO_ASPECT = 1156 / 912  # width / height of IMG_1486.jpeg
FOOTER_SPACE = 22
SIGNATURE_BLOCK_HEIGHT = 48
LONG_LABEL = 50  # mm; longer labels go on their own line
MONTHS = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class Establishment:
    name: str
    address: str = ""
    cnpj: str = ""
    phone: str = ""
    city: str = ""

    @classmethod
    def from_settings(cls) -> "Establishment":
        s = get_settings()
        return cls(
            name=s.establishment_name.strip(),
            address=s.establishment_address.strip(),
            cnpj=s.establishment_cnpj.strip(),
            phone=s.establishment_phone.strip(),
            city=s.establishment_city.strip(),
        )


@dataclass(frozen=True)
class DocumentContext:
    establishment: Establishment
    patient_name: str
    patient_date_of_birth: date
    patient_cpf: str
    pharmacist_name: str
    pharmacist_crf: str
    consultation_date: date
    issued_at: datetime


def clean_text(value: str) -> str:
    """Normalize newlines/tabs and drop control characters that break PDF text."""
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return _CONTROL_CHARS.sub("", value).strip()


def format_cpf(digits: str) -> str:
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}" if len(digits) == 11 else digits


def crf_label(crf: str) -> str:
    """'CRF-SP 12345' stays as-is; a bare '12345' becomes 'CRF 12345'."""
    crf = crf.strip()
    return crf if crf.upper().startswith("CRF") else f"CRF {crf}"


def format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def long_date(value: date) -> str:
    return f"{value.day} de {MONTHS[value.month - 1]} de {value.year}"


class DocumentPDF(FPDF):
    def __init__(self, title: str, context: DocumentContext) -> None:
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self.doc_title = title
        self.context = context
        self.add_font(FONT, "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=FOOTER_SPACE)
        # Metadata deliberately excludes patient data.
        self.set_title(title)
        self.set_creator(APP_NAME)
        self.set_producer(APP_NAME)
        self.set_creation_date(context.issued_at)
        self.add_page()
        self._document_heading()
        self._identification()

    # --- page furniture ----------------------------------------------------

    def header(self) -> None:
        est = self.context.establishment
        if self.page_no() == 1:
            self.set_fill_color(*BAND)
            self.rect(0, 0, self.w, BAND_HEIGHT, style="F")
            logo_w = BAND_HEIGHT * LOGO_ASPECT
            self.image(str(LOGO_PATH), x=MARGIN - 6, y=0, h=BAND_HEIGHT)
            text_x = MARGIN - 6 + logo_w + 4
            text_w = self.w - text_x - MARGIN - 30
            self.set_xy(text_x, 9)
            self.set_text_color(*WHITE)
            self.set_font(FONT, "B", 11)
            self.multi_cell(text_w, 5.2, est.name, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            contact = [v for v in (
                est.address,
                " · ".join(p for p in (f"CNPJ {est.cnpj}" if est.cnpj else "",
                                       f"Tel. {est.phone}" if est.phone else "") if p),
            ) if v]
            self.set_text_color(*SILVER)
            self.set_font(FONT, "", 8)
            for line in contact:
                self.set_x(text_x)
                self.multi_cell(text_w, 4, line, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_xy(self.w - MARGIN - 30, 13)
            self.set_font(FONT, "B", 12)
            self.cell(30, 6, APP_NAME, align="R")
            self.set_y(BAND_HEIGHT + 8)
        else:
            self.set_y(9)
            self.set_text_color(*MUTED)
            self.set_font(FONT, "", 8)
            half = self.epw / 2 - 2
            self.cell(half, 5, self._fit(f"{APP_NAME} · Paciente: {self.context.patient_name}", half))
            self.set_x(MARGIN + half + 4)
            self.cell(half, 5, self._fit(f"{self.doc_title} (continuação)", half), align="R")
            self.set_draw_color(*RULE)
            self.line(MARGIN, 15.5, self.w - MARGIN, 15.5)
            self.set_y(21)
        self.set_text_color(*TEXT)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_draw_color(*RULE)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.ln(1.5)
        self.set_font(FONT, "", 7.5)
        self.set_text_color(*MUTED)
        issued = self.context.issued_at.strftime("%d/%m/%Y %H:%M")
        self.cell(0, 4, f"{APP_NAME} · documento emitido em {issued}", align="L")
        self.set_x(MARGIN)
        self.cell(0, 4, f"Página {self.page_no()} de {{nb}}", align="R")

    def _fit(self, text: str, width: float) -> str:
        """Shorten single-line text with an ellipsis so it never overflows `width`."""
        if self.get_string_width(text) <= width:
            return text
        while text and self.get_string_width(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    # --- fixed blocks ------------------------------------------------------

    def _document_heading(self) -> None:
        self.set_font(FONT, "B", 15)
        self.set_text_color(*GRAPHITE)
        self.multi_cell(0, 8, self.doc_title, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*SILVER)
        self.set_line_width(0.6)
        y = self.get_y() + 1.5
        self.line(self.w / 2 - 25, y, self.w / 2 + 25, y)
        self.set_line_width(0.2)
        self.set_y(y + 5)
        self.set_text_color(*TEXT)

    def _identification(self) -> None:
        ctx = self.context
        gap = 6
        col_w = (self.epw - gap) / 2
        top = self.get_y()
        columns = [
            ("PACIENTE", ctx.patient_name, [
                f"Data de nascimento: {format_date(ctx.patient_date_of_birth)}",
                f"CPF: {format_cpf(ctx.patient_cpf)}",
            ]),
            ("FARMACÊUTICO(A) RESPONSÁVEL", ctx.pharmacist_name, [
                crf_label(ctx.pharmacist_crf),
                f"Atendimento: {format_date(ctx.consultation_date)}",
            ]),
        ]
        bottoms = []
        for i, (label, name, lines) in enumerate(columns):
            x = MARGIN + i * (col_w + gap) + 3
            self.set_xy(x, top + 3)
            self.set_font(FONT, "B", 7.5)
            self.set_text_color(*MUTED)
            self.cell(col_w - 6, 4, label, new_x=XPos.LEFT, new_y=YPos.NEXT)
            self.set_font(FONT, "B", 10.5)
            self.set_text_color(*TEXT)
            self.multi_cell(col_w - 6, 5, clean_text(name), align="L", new_x=XPos.LEFT, new_y=YPos.NEXT)
            self.set_font(FONT, "", 9)
            for line in lines:
                self.multi_cell(col_w - 6, 4.6, clean_text(line), align="L", new_x=XPos.LEFT, new_y=YPos.NEXT)
            bottoms.append(self.get_y())
        height = max(bottoms) - top + 3
        self.set_draw_color(*RULE)
        for i in range(2):
            self.rect(MARGIN + i * (col_w + gap), top, col_w, height)
        self.set_xy(MARGIN, top + height + 7)

    # --- body helpers ------------------------------------------------------

    def section(self, label: str) -> None:
        if self.get_y() > self.page_break_trigger - 20:  # keep headings with their content
            self.add_page()
        self.ln(2)
        self.set_font(FONT, "B", 10)
        self.set_text_color(*GRAPHITE)
        self.cell(0, 6, label.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*SILVER)
        self.line(MARGIN, self.get_y(), MARGIN + 30, self.get_y())
        self.ln(2.5)
        self.set_text_color(*TEXT)

    def paragraph(self, text: str, size: float = 10) -> None:
        text = clean_text(text)
        if not text:
            return
        self.set_font(FONT, "", size)
        self.set_text_color(*TEXT)
        self.multi_cell(0, 5.2, text, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1.5)

    def labeled_line(self, label: str, value: str, indent: float = 0) -> None:
        value = clean_text(value)
        if not value:
            return
        self.set_x(MARGIN + indent)
        self.set_font(FONT, "B", 9.5)
        label_w = self.get_string_width(f"{label}: ") + 0.5
        if label_w > LONG_LABEL:
            self.cell(0, 5.2, f"{label}:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_x(MARGIN + indent)
            self.set_font(FONT, "", 9.5)
            self.multi_cell(0, 5.2, value, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            return
        self.cell(label_w, 5.2, f"{label}: ")
        self.set_font(FONT, "", 9.5)
        self.multi_cell(0, 5.2, value, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def numbered_heading(self, number: int, text: str, bold: bool = True) -> None:
        if self.get_y() > self.page_break_trigger - 18:
            self.add_page()
        self.set_font(FONT, "B" if bold else "", 10.5 if bold else 10)
        self.set_text_color(*TEXT)
        self.multi_cell(0, 5.6, f"{number}. {clean_text(text)}", align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def closing(self) -> None:
        """Date/location line and signature/stamp area, kept together on one page."""
        ctx = self.context
        if self.get_y() + SIGNATURE_BLOCK_HEIGHT > self.page_break_trigger:
            self.add_page()
        self.ln(8)
        city = ctx.establishment.city
        when = long_date(ctx.issued_at.date())
        self.set_font(FONT, "", 10)
        self.cell(0, 5, f"{city}, {when}." if city else f"Data: {when}.", align="R",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(18)
        line_w = 80
        x = (self.w - line_w) / 2
        self.set_draw_color(*TEXT)
        self.line(x, self.get_y(), x + line_w, self.get_y())
        self.ln(1.5)
        self.set_font(FONT, "B", 10)
        self.cell(0, 5, clean_text(ctx.pharmacist_name), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font(FONT, "", 9)
        self.cell(0, 4.6, f"Farmacêutico(a) · {crf_label(ctx.pharmacist_crf)}", align="C",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*MUTED)
        self.set_font(FONT, "", 8)
        self.cell(0, 4.6, "Assinatura e carimbo", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*TEXT)

    def to_bytes(self) -> bytes:
        return bytes(self.output())
