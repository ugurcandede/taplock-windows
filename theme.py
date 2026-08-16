"""Stylesheet for the panel, the tray menu and the statistics window.

Two palettes over one template, picked from the Windows app theme. Note that
this is the *app* theme (`AppsUseLightTheme`, which Qt reports as
`styleHints().colorScheme()`), not the taskbar theme that `icon.py` reads --
Windows tracks them separately and a light taskbar with dark apps is a normal
setup.

Applied once on the QApplication; widgets opt in by `objectName`. Avoid inline
styles except dynamic colour swatches. Do not put a `font-size` in the bare
`QLabel` rule: it would outrank `setFont()` on the widget, which is how the big
monospaced countdown and slash are sized.
"""

from string import Template

DARK = {
    "bg": "#1C1C1E",
    "border": "#2C2C2E",
    "divider": "#38383A",
    "text": "#F2F2F7",
    "secondary": "#8E8E93",
    "faint": "#636366",
    "slash": "#48484A",
    "accent": "#0A84FF",
    "accent_soft": "rgba(10, 132, 255, 0.12)",
    "accent_soft_hover": "rgba(10, 132, 255, 0.20)",
    "accent_check": "rgba(10, 132, 255, 0.16)",
    "disabled_bg": "rgba(120, 120, 128, 0.12)",
    "danger": "#FF453A",
    "danger_soft": "rgba(255, 69, 58, 0.12)",
    "danger_soft_hover": "rgba(255, 69, 58, 0.20)",
    "warning": "#FF9F0A",
    "warning_soft": "rgba(255, 159, 10, 0.12)",
    "warning_soft_hover": "rgba(255, 159, 10, 0.20)",
    "menu_hover": "#2C2C2E",
}

# Accents follow Fluent rather than Apple here: the Apple blue, red and orange
# are tuned for dark backgrounds and drop under any sensible contrast ratio on
# white -- the same trap the tray green fell into.
LIGHT = {
    "bg": "#FFFFFF",
    "border": "#E5E5E7",
    "divider": "#E5E5E7",
    "text": "#1B1B1F",
    "secondary": "#605E5C",
    "faint": "#8A8886",
    "slash": "#C8C6C4",
    "accent": "#0067C0",
    "accent_soft": "rgba(0, 103, 192, 0.10)",
    "accent_soft_hover": "rgba(0, 103, 192, 0.18)",
    "accent_check": "rgba(0, 103, 192, 0.14)",
    "disabled_bg": "rgba(0, 0, 0, 0.06)",
    "danger": "#C42B1C",
    "danger_soft": "rgba(196, 43, 28, 0.10)",
    "danger_soft_hover": "rgba(196, 43, 28, 0.18)",
    "warning": "#9D5D00",
    "warning_soft": "rgba(157, 93, 0, 0.10)",
    "warning_soft_hover": "rgba(157, 93, 0, 0.18)",
    "menu_hover": "#F0F0F0",
}

# `string.Template` rather than str.format: QSS is full of braces.
_TEMPLATE = Template("""
/* Plain Segoe UI everywhere, not the Variable Display face. Display is the
   optical size meant for large text: at the 10-13px this UI is mostly made of
   it thins out and stops reading. */
* {
    font-family: "Segoe UI", system-ui, sans-serif;
    font-weight: 600;
}

QWidget#card {
    background-color: $bg;
    border: 1px solid $border;
    border-radius: 12px;
}

QLabel {
    color: $text;
    background: transparent;
}
QLabel#secondary { color: $secondary; font-size: 11px; }
QLabel#hint { color: $faint; font-size: 11px; }
QLabel#error { color: $danger; font-size: 10px; }
QLabel#slash { color: $slash; }

/* Hairline separators between popover sections. */
QFrame#divider {
    background-color: $divider;
    border: none;
    max-height: 1px;
    min-height: 1px;
}

/* Interval / break entry. Point sizes are set in code -- QSS cannot express the
   monospaced ultralight face the macOS popover uses. */
QLineEdit#duration {
    background: transparent;
    border: none;
    color: $text;
    selection-background-color: $accent;
    selection-color: #FFFFFF;
}

/* s / m / h segmented picker. */
QPushButton#unit {
    background: transparent;
    border: none;
    border-radius: 5px;
    color: $faint;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 0;
    min-width: 26px;
}
QPushButton#unit:hover { color: $text; }
QPushButton#unit:checked { background-color: $accent_check; color: $accent; }

QPushButton#preset {
    background: transparent;
    border: none;
    color: $secondary;
    font-size: 12px;
    padding: 6px 0;
}
QPushButton#preset:hover { color: $text; }

/* Full-width tinted action, matching the macOS "start" button. */
QPushButton#primary {
    background-color: $accent_soft;
    color: $accent;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#primary:hover { background-color: $accent_soft_hover; }
QPushButton#primary:disabled { background-color: $disabled_bg; color: $faint; }

QPushButton#danger, QPushButton#warning {
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#danger { background-color: $danger_soft; color: $danger; }
QPushButton#danger:hover { background-color: $danger_soft_hover; }
QPushButton#warning { background-color: $warning_soft; color: $warning; }
QPushButton#warning:hover { background-color: $warning_soft_hover; }

/* Borderless footer rows ("quit taplock", "settings"). */
QPushButton#plain {
    background: transparent;
    border: none;
    color: $secondary;
    font-size: 11px;
    padding: 8px 0;
}
QPushButton#plain:hover { color: $text; }

QComboBox {
    background-color: $accent_soft;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    color: $text;
    font-size: 12px;
    min-width: 88px;
}
QComboBox:hover { background-color: $accent_soft_hover; }
QComboBox::drop-down { border: none; width: 16px; }
QComboBox QAbstractItemView {
    background-color: $bg;
    border: 1px solid $divider;
    border-radius: 6px;
    color: $text;
    padding: 4px;
    selection-background-color: $menu_hover;
    outline: none;
}

QLabel#about {
    color: $faint;
    font-size: 11px;
}
/* Anchors are not styled here: Qt draws rich-text links from the text
   document's own anchor styling, which a QLabel does not expose, so the rule
   would silently do nothing. panel.py sets them inline instead. */

/* The tray context menu picks this up too. */
QMenu {
    background-color: $bg;
    border: 1px solid $divider;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 16px;
    border-radius: 6px;
    color: $text;
    font-size: 12px;
}
QMenu::item:selected { background-color: $menu_hover; }
QMenu::separator { height: 1px; background: $divider; margin: 4px 8px; }
""")


def stylesheet(dark: bool) -> str:
    return _TEMPLATE.substitute(DARK if dark else LIGHT)
