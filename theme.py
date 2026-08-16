"""Dark stylesheet mirroring the macOS TapLock popover.

Near-black card on a transparent window, one blue accent, muted grey labels and
hairline dividers. Applied once on the QApplication; widgets opt in by
`objectName`. Avoid inline styles except dynamic colour swatches.
"""

# -- palette (inlined in the QSS below) ------------------------------------
# card #1C1C1E  raised #2C2C2E  divider #38383A
# text #F2F2F7  secondary #8E8E93  faint #636366
# accent #0A84FF  danger #FF453A  warning #FF9F0A

STYLESHEET = """
* {
    font-family: "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif;
}

QWidget#card {
    background-color: #1C1C1E;
    border: 1px solid #2C2C2E;
    border-radius: 12px;
}

/* No font-size here on purpose: a size in this rule outranks setFont() on the
   widget, which is how the big monospaced countdown and slash are set. Roles
   that need a specific size declare it below. */
QLabel {
    color: #F2F2F7;
    background: transparent;
}
QLabel#secondary { color: #8E8E93; font-size: 11px; }
QLabel#hint { color: #636366; font-size: 11px; }

/* Hairline separators between popover sections. */
QFrame#divider {
    background-color: #38383A;
    border: none;
    max-height: 1px;
    min-height: 1px;
}

/* Full-width tinted action, matching the macOS "start" button. */
QPushButton#primary {
    background-color: rgba(10, 132, 255, 0.12);
    color: #0A84FF;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#primary:hover { background-color: rgba(10, 132, 255, 0.20); }
QPushButton#primary:disabled { background-color: rgba(120, 120, 128, 0.12); color: #636366; }

/* Borderless footer rows ("quit taplock", "settings"). */
QPushButton#plain {
    background: transparent;
    border: none;
    color: #8E8E93;
    font-size: 11px;
    padding: 8px 0;
}
QPushButton#plain:hover { color: #F2F2F7; }

QLabel#error { color: #FF453A; font-size: 10px; }

/* Interval / break entry. Point sizes are set in code -- QSS cannot express the
   monospaced ultralight face the macOS popover uses. */
QLineEdit#duration {
    background: transparent;
    border: none;
    color: #F2F2F7;
    selection-background-color: #0A84FF;
    selection-color: #FFFFFF;
}
QLabel#slash { color: #48484A; }

/* s / m / h segmented picker. */
QPushButton#unit {
    background: transparent;
    border: none;
    border-radius: 5px;
    color: #636366;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 0;
    min-width: 26px;
}
QPushButton#unit:hover { color: #F2F2F7; }
QPushButton#unit:checked { background-color: rgba(10, 132, 255, 0.16); color: #0A84FF; }

QPushButton#preset {
    background: transparent;
    border: none;
    color: #8E8E93;
    font-size: 12px;
    padding: 6px 0;
}
QPushButton#preset:hover { color: #F2F2F7; }

QPushButton#danger, QPushButton#warning {
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#danger { background-color: rgba(255, 69, 58, 0.12); color: #FF453A; }
QPushButton#danger:hover { background-color: rgba(255, 69, 58, 0.20); }
QPushButton#warning { background-color: rgba(255, 159, 10, 0.12); color: #FF9F0A; }
QPushButton#warning:hover { background-color: rgba(255, 159, 10, 0.20); }

QMenu {
    background-color: #1C1C1E;
    border: 1px solid #38383A;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 16px;
    border-radius: 6px;
    color: #F2F2F7;
    font-size: 12px;
}
QMenu::item:selected { background-color: #2C2C2E; }
QMenu::separator { height: 1px; background: #38383A; margin: 4px 8px; }
"""
