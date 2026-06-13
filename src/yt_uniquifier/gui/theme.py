"""QSS theme tokens + builder. Dark default; light + system future-ready."""

from __future__ import annotations

from typing import Literal

ThemeName = Literal["dark", "light", "system"]


DARK_TOKENS = {
    "bg":             "#1f1f2b",
    "bg_alt":         "#2a2a37",
    "bg_deep":        "#16161e",
    "fg":             "#e2e2e8",
    "fg_dim":         "#9b9ba8",
    "accent":         "#3b6ea8",
    "accent_hover":   "#4f87c4",
    "accent_warm":    "#d18b3b",
    "danger":         "#a83b3b",
    "danger_hover":   "#c44f4f",
    "success":        "#3ba85c",
    "warning":        "#d1a93b",
    "border":         "#444",
    # Semantic tokens for status badges + KPI pills (R1/E4 — was hard-coded
    # in widgets/preflight_panel.py and widgets/kpi_pills.py, leaking across
    # theme switches). bg + fg are paired so a token swap repaints both at once.
    "badge_fail_bg":  "#a83b3b",
    "badge_fail_fg":  "#ffffff",
    "badge_warn_bg":  "#d1a93b",
    "badge_warn_fg":  "#16161e",
    "badge_ok_bg":    "#3ba85c",
    "badge_ok_fg":    "#ffffff",
    "kpi_red":        "#a83b3b",
    "kpi_yellow":     "#d1a93b",
    "kpi_green":      "#3ba85c",
    "kpi_neutral":    "#9b9ba8",
    "kpi_fg":         "#ffffff",
}

LIGHT_TOKENS = {
    "bg":             "#f5f5f8",
    "bg_alt":         "#ffffff",
    "bg_deep":        "#eaeaef",
    "fg":             "#1f1f2b",
    "fg_dim":         "#6a6a78",
    "accent":         "#2b5e98",
    "accent_hover":   "#3877b8",
    "accent_warm":    "#c17a2b",
    "danger":         "#a83b3b",
    "danger_hover":   "#c44f4f",
    "success":        "#2c8c4a",
    "warning":        "#b8902f",
    "border":         "#ccc",
    # Light-theme semantic tokens — darker fills for AA contrast against
    # a near-white background; readable fg on each fill.
    "badge_fail_bg":  "#a83b3b",
    "badge_fail_fg":  "#ffffff",
    "badge_warn_bg":  "#b8902f",
    "badge_warn_fg":  "#ffffff",
    "badge_ok_bg":    "#2c8c4a",
    "badge_ok_fg":    "#ffffff",
    "kpi_red":        "#a83b3b",
    "kpi_yellow":     "#b8902f",
    "kpi_green":      "#2c8c4a",
    "kpi_neutral":    "#6a6a78",
    "kpi_fg":         "#ffffff",
}


def tokens_for(theme: str) -> dict[str, str]:
    """Return the token dict for the given theme name.

    Accepts a plain `str` (not just `ThemeName`) so widgets that hold
    `self._theme: str` from `AppState.theme()` can call this without
    casting. Any non-"light" value resolves to dark — same fallback as
    `qss_for`, matching the v0.5 behaviour until explicit OS detection
    lands in v0.6.
    """
    return LIGHT_TOKENS if theme == "light" else DARK_TOKENS


def qss_for(theme: str) -> str:
    """Return the full QSS stylesheet for the given theme.

    `system` falls back to dark (MVP — explicit OS detection is v0.6).
    """
    return _QSS_TEMPLATE.format(**tokens_for(theme))


_QSS_TEMPLATE = """
QWidget {{ background: {bg}; color: {fg}; font-size: 13px; }}

QPushButton {{
    background: {accent}; color: white; padding: 6px 14px;
    border-radius: 4px; border: none;
}}
QPushButton:disabled {{ background: {fg_dim}; color: {bg}; }}
QPushButton:hover:!disabled {{ background: {accent_hover}; }}
QPushButton#cancel {{ background: {danger}; }}
QPushButton#cancel:hover:!disabled {{ background: {danger_hover}; }}
QPushButton#run {{
    background: {accent_warm}; font-weight: bold; padding: 8px 18px;
}}

QListWidget#sidebar {{
    background: {bg_deep}; border: none; padding: 8px 0; font-size: 14px;
}}
QListWidget#sidebar::item {{
    padding: 10px 16px; color: {fg_dim}; border: none;
}}
QListWidget#sidebar::item:selected {{
    background: {accent}; color: white;
}}
QListWidget#sidebar::item:hover:!selected {{
    background: {bg_alt}; color: {fg};
}}

QLabel#path {{
    background: {bg_alt}; padding: 5px 8px; border-radius: 3px;
    color: {fg_dim};
}}
QLabel#status {{ color: {fg_dim}; }}
QLabel#title {{ font-size: 16px; font-weight: bold; color: {fg}; }}

QComboBox {{
    background: {bg_alt}; padding: 4px 8px; border-radius: 3px;
    border: 1px solid {border};
}}
QComboBox::drop-down {{ border: none; }}

QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {bg_alt}; padding: 4px 8px; border-radius: 3px;
    border: 1px solid {border};
}}

QProgressBar {{
    background: {bg_alt}; border-radius: 3px; text-align: center;
    color: white; padding: 1px; height: 16px;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QTextEdit, QPlainTextEdit {{
    background: {bg_deep}; color: {fg};
    font-family: SFMono-Regular, Menlo, monospace; font-size: 11px;
    border: 1px solid {border}; border-radius: 3px;
}}

QTableWidget {{
    background: {bg_alt}; gridline-color: {border};
    border: 1px solid {border};
}}
QHeaderView::section {{
    background: {bg_deep}; color: {fg_dim}; padding: 4px; border: none;
}}

QTabWidget::pane {{ border: 1px solid {border}; }}
QTabBar::tab {{
    background: {bg_alt}; padding: 6px 12px; color: {fg_dim};
    border: 1px solid {border}; border-bottom: none;
}}
QTabBar::tab:selected {{ background: {bg}; color: {fg}; }}

QGroupBox {{
    border: 1px solid {border}; border-radius: 4px;
    margin-top: 10px; padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 8px; padding: 0 4px;
    color: {fg_dim};
}}

QStatusBar {{ background: {bg_deep}; color: {fg_dim}; }}
"""
