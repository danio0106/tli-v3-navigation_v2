import customtkinter as ctk


COLORS = {
    "bg_dark": "#0D1117",
    "bg_medium": "#161B22",
    "bg_light": "#21262D",
    "bg_card": "#1C2128",
    "border": "#30363D",
    "text_primary": "#E6EDF3",
    "text_secondary": "#8B949E",
    "text_muted": "#6E7681",
    "accent_blue": "#58A6FF",
    "accent_green": "#3FB950",
    "accent_red": "#F85149",
    "accent_orange": "#D29922",
    "accent_purple": "#BC8CFF",
    "accent_cyan": "#39D2C0",
    "button_bg": "#21262D",
    "button_hover": "#30363D",
    "button_active": "#58A6FF",
    "entry_bg": "#0D1117",
    "entry_border": "#30363D",
    "scrollbar": "#30363D",
    "success": "#3FB950",
    "warning": "#D29922",
    "error": "#F85149",
    "info": "#58A6FF",
}

FONTS = {
    "heading": ("Segoe UI", 15, "bold"),
    "subheading": ("Segoe UI", 12, "bold"),
    "body": ("Segoe UI", 12),
    "small": ("Segoe UI", 10),
    "mono": ("Consolas", 11),
    "mono_small": ("Consolas", 10),
    "button": ("Segoe UI", 11, "bold"),
    "status": ("Segoe UI", 11),
}


def apply_theme():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def create_card_frame(parent, **kwargs) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent,
        fg_color=COLORS["bg_card"],
        corner_radius=8,
        border_width=1,
        border_color=COLORS["border"],
        **kwargs,
    )


def create_accent_button(parent, text, command=None, color="accent_blue", **kwargs) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        fg_color=COLORS[color],
        hover_color=COLORS.get(f"{color}_hover", COLORS["button_hover"]),
        text_color=COLORS["text_primary"],
        font=FONTS["button"],
        corner_radius=6,
        height=30,
        **kwargs,
    )


def create_label(parent, text, font_key="body", color_key="text_primary", **kwargs) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=text,
        font=FONTS[font_key],
        text_color=COLORS[color_key],
        **kwargs,
    )


def create_entry(parent, placeholder="", **kwargs) -> ctk.CTkEntry:
    return ctk.CTkEntry(
        parent,
        placeholder_text=placeholder,
        fg_color=COLORS["entry_bg"],
        border_color=COLORS["entry_border"],
        text_color=COLORS["text_primary"],
        font=FONTS["mono"],
        corner_radius=6,
        height=32,
        **kwargs,
    )
