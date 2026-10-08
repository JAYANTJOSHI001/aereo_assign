"""Layout constants and visual definitions for the certificate template (A4 Landscape)."""

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape

# Page Dimensions (A4 Landscape: 841.89 x 595.28 pt)
PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)

# Color Palette (Elegant navy, deep slate, and warm gold accents)
COLOR_BACKGROUND = HexColor("#FCFDFD")
COLOR_PRIMARY = HexColor("#0F172A")       # Deep slate navy
COLOR_SECONDARY = HexColor("#334155")     # Slate text
COLOR_MUTED = HexColor("#64748B")         # Subdued slate
COLOR_ACCENT = HexColor("#D97706")        # Warm amber / gold
COLOR_BORDER_OUTER = HexColor("#0F172A")  # Slate border
COLOR_BORDER_INNER = HexColor("#B45309")  # Warm gold inner line
COLOR_DIVIDER = HexColor("#E2E8F0")       # Subtle divider grey

# Margins and Borders
OUTER_MARGIN = 36.0                       # Outer boundary
INNER_MARGIN = 46.0                       # Inset for decorative border
BORDER_OUTER_WIDTH = 3.0
BORDER_INNER_WIDTH = 1.0

# Typography: Registered Font Names and Sizes
FONT_NAME_UNICODE = "DejaVuSans"
FONT_NAME_FALLBACK = "Helvetica"
FONT_NAME_BOLD_FALLBACK = "Helvetica-Bold"

FONT_SIZE_ISSUER = 14.0
FONT_SIZE_TITLE = 30.0
FONT_SIZE_SUBTITLE = 13.0
FONT_SIZE_RECIPIENT_MAX = 34.0
FONT_SIZE_RECIPIENT_MIN = 18.0
FONT_SIZE_COURSE = 21.0
FONT_SIZE_METADATA_LABEL = 9.5
FONT_SIZE_METADATA_VALUE = 11.5

# Balanced Vertical Alignment Y-Positions (from bottom of page)
Y_ISSUER = 495.0
Y_TITLE = 445.0
Y_DECORATIVE_LINE = 428.0
Y_CERTIFY_TEXT = 385.0
Y_RECIPIENT_NAME = 325.0
Y_NAME_UNDERLINE = 310.0
Y_COMPLETED_TEXT = 265.0
Y_COURSE_NAME = 220.0
Y_FOOTER_DIVIDER = 140.0
Y_FOOTER_LABELS = 112.0
Y_FOOTER_VALUES = 92.0

# Maximum Width for Dynamic Text
MAX_RECIPIENT_WIDTH = 600.0
MAX_COURSE_WIDTH = 640.0
