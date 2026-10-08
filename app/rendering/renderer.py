"""Pure PDF certificate rendering function using ReportLab."""

from dataclasses import dataclass
from datetime import date
import io
import logging
from pathlib import Path
from typing import Union

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.rendering import template

logger = logging.getLogger(__name__)

# Register Unicode Font with Fallback
FONT_FAMILY = template.FONT_NAME_FALLBACK
FONT_FAMILY_BOLD = template.FONT_NAME_BOLD_FALLBACK

_FONT_PATH = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"
if _FONT_PATH.exists():
    try:
        pdfmetrics.registerFont(TTFont(template.FONT_NAME_UNICODE, str(_FONT_PATH)))
        FONT_FAMILY = template.FONT_NAME_UNICODE
        FONT_FAMILY_BOLD = template.FONT_NAME_UNICODE
        logger.info("Successfully registered Unicode font %s", template.FONT_NAME_UNICODE)
    except Exception as exc:
        logger.warning(
            "Failed to register Unicode font '%s' (%s); falling back to %s",
            _FONT_PATH,
            exc,
            template.FONT_NAME_FALLBACK,
        )
else:
    logger.warning(
        "Font file not found at %s; falling back to %s",
        _FONT_PATH,
        template.FONT_NAME_FALLBACK,
    )


@dataclass(frozen=True)
class CertificateData:
    """Pure data structure required to render a certificate."""

    issuer_name: str
    title: str
    recipient_name: str
    course_name: str
    issue_date: Union[date, str]
    certificate_number: str


def _fit_text(
    text: str,
    font_name: str,
    max_size: float,
    min_size: float,
    max_width: float,
) -> tuple[str, float]:
    """Calculate the optimal font size and truncate with ellipsis if necessary."""
    size = max_size
    while size >= min_size:
        width = pdfmetrics.stringWidth(text, font_name, size)
        if width <= max_width:
            return text, size
        size -= 1.0

    # Truncate with ellipsis if it still exceeds max_width at min_size
    ellipsis = "..."
    truncated = text
    while truncated and pdfmetrics.stringWidth(truncated + ellipsis, font_name, min_size) > max_width:
        truncated = truncated[:-1]
    return (truncated + ellipsis) if truncated else ellipsis, min_size


def render_certificate(data: CertificateData) -> bytes:
    """Render a single certificate PDF document into in-memory bytes."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(template.PAGE_WIDTH, template.PAGE_HEIGHT))
    center_x = template.PAGE_WIDTH / 2.0

    # 1. Background Fill
    c.setFillColor(template.COLOR_BACKGROUND)
    c.rect(0, 0, template.PAGE_WIDTH, template.PAGE_HEIGHT, fill=True, stroke=False)

    # 2. Decorative Double Borders
    c.setStrokeColor(template.COLOR_BORDER_OUTER)
    c.setLineWidth(template.BORDER_OUTER_WIDTH)
    c.rect(
        template.OUTER_MARGIN,
        template.OUTER_MARGIN,
        template.PAGE_WIDTH - (2 * template.OUTER_MARGIN),
        template.PAGE_HEIGHT - (2 * template.OUTER_MARGIN),
        fill=False,
        stroke=True,
    )

    c.setStrokeColor(template.COLOR_BORDER_INNER)
    c.setLineWidth(template.BORDER_INNER_WIDTH)
    c.rect(
        template.INNER_MARGIN,
        template.INNER_MARGIN,
        template.PAGE_WIDTH - (2 * template.INNER_MARGIN),
        template.PAGE_HEIGHT - (2 * template.INNER_MARGIN),
        fill=False,
        stroke=True,
    )

    # 3. Issuer Name
    c.setFont(FONT_FAMILY_BOLD, template.FONT_SIZE_ISSUER)
    c.setFillColor(template.COLOR_MUTED)
    issuer_display = data.issuer_name.upper()
    c.drawCentredString(center_x, template.Y_ISSUER, issuer_display)

    # 4. Certificate Title
    c.setFont(FONT_FAMILY_BOLD, template.FONT_SIZE_TITLE)
    c.setFillColor(template.COLOR_PRIMARY)
    c.drawCentredString(center_x, template.Y_TITLE, data.title)

    # 5. Accent Divider Bar under Title
    c.setStrokeColor(template.COLOR_ACCENT)
    c.setLineWidth(2.5)
    divider_half_len = 55.0
    c.line(
        center_x - divider_half_len,
        template.Y_DECORATIVE_LINE,
        center_x + divider_half_len,
        template.Y_DECORATIVE_LINE,
    )

    # 6. Certification Preamble
    c.setFont(FONT_FAMILY, template.FONT_SIZE_SUBTITLE)
    c.setFillColor(template.COLOR_SECONDARY)
    c.drawCentredString(center_x, template.Y_CERTIFY_TEXT, "This is to certify that")

    # 7. Recipient Name (with shrink-to-fit & ellipsis truncation)
    name_text, name_font_size = _fit_text(
        data.recipient_name,
        font_name=FONT_FAMILY_BOLD,
        max_size=template.FONT_SIZE_RECIPIENT_MAX,
        min_size=template.FONT_SIZE_RECIPIENT_MIN,
        max_width=template.MAX_RECIPIENT_WIDTH,
    )
    c.setFont(FONT_FAMILY_BOLD, name_font_size)
    c.setFillColor(template.COLOR_PRIMARY)
    c.drawCentredString(center_x, template.Y_RECIPIENT_NAME, name_text)

    # Subtle Underline for Recipient Name
    name_width = pdfmetrics.stringWidth(name_text, FONT_FAMILY_BOLD, name_font_size)
    underline_half_len = min(name_width / 2.0 + 20.0, template.MAX_RECIPIENT_WIDTH / 2.0)
    c.setStrokeColor(template.COLOR_ACCENT)
    c.setLineWidth(1.0)
    c.line(
        center_x - underline_half_len,
        template.Y_NAME_UNDERLINE,
        center_x + underline_half_len,
        template.Y_NAME_UNDERLINE,
    )

    # 8. Completion Clause
    c.setFont(FONT_FAMILY, template.FONT_SIZE_SUBTITLE)
    c.setFillColor(template.COLOR_SECONDARY)
    c.drawCentredString(center_x, template.Y_COMPLETED_TEXT, "has successfully completed")

    # 9. Course Name
    course_text, course_size = _fit_text(
        data.course_name,
        font_name=FONT_FAMILY_BOLD,
        max_size=template.FONT_SIZE_COURSE,
        min_size=14.0,
        max_width=template.MAX_COURSE_WIDTH,
    )
    c.setFont(FONT_FAMILY_BOLD, course_size)
    c.setFillColor(template.COLOR_PRIMARY)
    c.drawCentredString(center_x, template.Y_COURSE_NAME, course_text)

    # 10. Footer Section: Issue Date (Left) & Certificate Number (Right)
    left_x = template.INNER_MARGIN + 80.0
    right_x = template.PAGE_WIDTH - template.INNER_MARGIN - 80.0

    # Footer divider rule
    c.setStrokeColor(template.COLOR_DIVIDER)
    c.setLineWidth(0.75)
    c.line(
        template.INNER_MARGIN + 40.0,
        template.Y_FOOTER_DIVIDER,
        template.PAGE_WIDTH - template.INNER_MARGIN - 40.0,
        template.Y_FOOTER_DIVIDER,
    )

    # Date column
    c.setFont(FONT_FAMILY, template.FONT_SIZE_METADATA_LABEL)
    c.setFillColor(template.COLOR_MUTED)
    c.drawCentredString(left_x, template.Y_FOOTER_LABELS, "ISSUE DATE")

    date_str = data.issue_date.isoformat() if isinstance(data.issue_date, date) else str(data.issue_date)
    c.setFont(FONT_FAMILY_BOLD, template.FONT_SIZE_METADATA_VALUE)
    c.setFillColor(template.COLOR_SECONDARY)
    c.drawCentredString(left_x, template.Y_FOOTER_VALUES, date_str)

    # Certificate Number column
    c.setFont(FONT_FAMILY, template.FONT_SIZE_METADATA_LABEL)
    c.setFillColor(template.COLOR_MUTED)
    c.drawCentredString(right_x, template.Y_FOOTER_LABELS, "CERTIFICATE ID")

    c.setFont(FONT_FAMILY_BOLD, template.FONT_SIZE_METADATA_VALUE)
    c.setFillColor(template.COLOR_SECONDARY)
    c.drawCentredString(right_x, template.Y_FOOTER_VALUES, data.certificate_number)

    c.showPage()
    c.save()

    return buffer.getvalue()
