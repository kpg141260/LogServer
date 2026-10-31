__VERSION__ = "0.1.0.8"
# ruff: noqa: RUF100, UP025, UP004, I001, F401
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


def logColors(dark):
    return {
        "ip": "#00b800" if dark else "#006200",
        "time": "#8441c6" if dark else "#572b82",
        "number": "#00aaff" if dark else "#004162",
        "path": "#00ddff" if dark else "#004550"
    }


def _refreshPalette(app):
    mode = app._appearance_mode
    if mode == "system":
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Unknown:
            app.setPalette(app._system_palette)
            return
        mode = "dark" if scheme == Qt.ColorScheme.Dark else "light"
    dark = mode == "dark"
    palette = QPalette(app.style().standardPalette())
    text, window, base = ("#eeeeee", "#353535", "#313131") if dark else ("#202020", "#efefef", "#e8e8e8")
    colors = {
        "Window": window, "WindowText": text, "Base": base,
        "AlternateBase": window, "Text": text, "Button": window,
        "ButtonText": text, "ToolTipBase": base, "ToolTipText": text,
        "Highlight": "#1766bd", "HighlightedText": "#ffffff",
        "Link": "#6277ff" if dark else "#37448f",
    }
    for role, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#999999" if dark else "#777777"))
    app.setPalette(palette)


def applyAppearance(settings):
    app = QApplication.instance()
    if not hasattr(app, "_system_palette"):
        app._system_palette = QPalette(app.palette())
        app.styleHints().colorSchemeChanged.connect(lambda scheme: _refreshPalette(app))
    app._appearance_mode = settings.appearance
    if not hasattr(app, "_default_widget_style"):
        app._default_widget_style = app.style().objectName()
    style = settings.widget_style or app._default_widget_style
    if style.lower() != app.style().objectName().lower():
        app.setStyle(style)
    schemes = Qt.ColorScheme
    app.styleHints().setColorScheme({"system": schemes.Unknown, "dark": schemes.Dark,
                                    "light": schemes.Light}[settings.appearance])
    _refreshPalette(app)
    if not hasattr(app, "_default_interface_font"):
        app._default_interface_font = QFont(app.font())
    font = QFont(app._default_interface_font)
    if settings.interface_font:
        font.fromString(settings.interface_font)
    app.setFont(font)
