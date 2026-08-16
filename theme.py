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

QLabel {
    color: #F2F2F7;
    font-size: 13px;
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
