"""Visual identity: the City of Boston's civic design language, kept minimal.

Palette and typefaces follow Boston.gov's published brand: Charles Blue, Optimistic Blue and
Freedom Trail Red on Snow White; Montserrat for headings, Lora for reading text. No logo or seal is
used and every page says the project is independent, so it can't be mistaken for an official City site.

Tokens are shared by the Streamlit app (CSS injected) and the HTML brief.
"""
from __future__ import annotations

import html
import re

CHARLES = "#091F2F"     # Charles Blue: headings, primary buttons, header bar
OPTIMISTIC = "#288BE4"  # Optimistic Blue: links, focus, active tab
RED = "#FB4D42"         # Freedom Trail Red: the one most important action, and overdue
SNOW = "#FFFFFF"        # Snow White: page
MIST = "#F3F5F7"        # quiet panels
RULE = "#D8DDE3"        # hairlines
INK, MUTED = "#091F2F", "#58616B"
# Kept for code that refers to the earlier names
NIGHT, SURFACE, LINE, AMBER, HALO, GOOD = SNOW, MIST, RULE, RED, OPTIMISTIC, "#2E7D4F"

FONT_URL = ("https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,500;0,600;1,400"
            "&family=Montserrat:wght@500;600;700;800&display=swap")
HEAD = "Montserrat, 'Helvetica Neue', Arial, sans-serif"
BODY = "Lora, Georgia, 'Times New Roman', serif"

BASE_CSS = f"""
.civic-bar{{background:{CHARLES};color:#fff;display:flex;justify-content:space-between;align-items:center;
  gap:16px;flex-wrap:wrap;padding:12px 24px;font-family:{HEAD};font-size:13px}}
.civic-bar b{{font-weight:800;font-size:16px;letter-spacing:.01em}}
.civic-bar span{{opacity:.8}}
.civic-hero{{max-width:1080px;margin:0 auto;padding:56px 24px 36px;border-bottom:1px solid {RULE}}}
.civic-hero .rule{{width:56px;height:4px;background:{RED};margin-bottom:22px}}
.civic-hero h1{{font-family:{HEAD};font-weight:800;color:{CHARLES};letter-spacing:-.015em;line-height:1.08;
  font-size:clamp(32px,5vw,54px);margin:0 0 16px;max-width:26ch;text-wrap:balance}}
.civic-hero p.sub{{font-family:{BODY};color:{MUTED};font-size:clamp(17px,1.7vw,20px);line-height:1.6;
  max-width:58ch;margin:0;text-wrap:pretty}}
.civic-hero .note{{font-family:{HEAD};font-size:13px;color:{MUTED};margin:22px 0 0}}
.civic-hero .note i{{display:inline-block;width:8px;height:8px;border-radius:50%;background:{OPTIMISTIC};margin-right:8px}}
.civic-stats{{display:flex;flex-wrap:wrap;margin-top:34px;border-top:1px solid {RULE}}}
.civic-stats div{{flex:1 1 180px;padding:18px 24px 4px 0;font-family:{BODY};color:{MUTED};font-size:15px}}
.civic-stats div+div{{border-left:1px solid {RULE};padding-left:24px}}
.civic-stats b{{display:block;font-family:{HEAD};color:{CHARLES};font-size:30px;font-weight:700;
  letter-spacing:-.01em;line-height:1.2}}
@media (max-width:640px){{.civic-stats div+div{{border-left:0;padding-left:0}}}}
"""


def hero(title: str, sub: str, pill: str, stats: list[tuple[str, str]]) -> str:
    s = "".join(f"<div><b>{html.escape(v)}</b>{html.escape(k)}</div>" for k, v in stats)
    lines = "<br>".join(html.escape(t) for t in title.split("\n"))
    return (f'<div class="civic-bar"><b>311 Pulse</b><span>Independent project using City of Boston open data. '
            f'Not affiliated with the City.</span></div>'
            f'<section class="civic-hero"><div class="rule"></div><h1>{lines}</h1>'
            f'<p class="sub">{html.escape(sub)}</p><p class="note"><i></i>{html.escape(pill)}</p>'
            + (f'<div class="civic-stats">{s}</div>' if stats else "") + "</section>")


STREAMLIT_CSS = f"""
<link rel="stylesheet" href="{FONT_URL}">
<style>
.stApp {{background:{SNOW}}}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, [data-testid="stMarkdownContainer"] {{font-family:{BODY}}}
.stApp h1, .stApp h2, .stApp h3, .stApp h4, button, [data-testid="stMetricValue"], [data-testid="stMetricLabel"],
[role="tab"] p, .stApp [data-testid="stExpander"] summary p, [data-baseweb="select"] {{font-family:{HEAD} !important}}
[data-testid="stHeader"] {{background:transparent}}
[data-testid="stToolbar"], [data-testid="stDecoration"], footer {{visibility:hidden}}
.block-container {{padding-top:0 !important; max-width:1128px}}
{BASE_CSS}
.civic-bar {{margin:0 -24px}}
.civic-hero {{padding-left:0; padding-right:0}}
h2, h3 {{color:{CHARLES} !important; font-weight:700 !important; letter-spacing:-.01em}}
[data-testid="stTabs"] [role="tablist"] {{gap:28px; border-bottom:1px solid {RULE}; margin:8px 0 26px}}
[data-testid="stTabs"] [role="tab"] {{padding:14px 0; height:auto; color:{MUTED}; background:transparent}}
[data-testid="stTabs"] [role="tab"] p {{font-weight:700; font-size:15px}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{color:{CHARLES}}}
[data-baseweb="tab-highlight"] {{background:{OPTIMISTIC} !important; height:3px !important}}
[data-baseweb="tab-border"] {{display:none}}
.stButton button, .stLinkButton a, .stDownloadButton button, [data-testid="stFormSubmitButton"] button {{
  border-radius:2px !important; border:2px solid {CHARLES} !important; background:{SNOW} !important;
  color:{CHARLES} !important; font-weight:700 !important; padding:8px 20px !important; font-family:{HEAD} !important}}
.stButton button:hover, .stLinkButton a:hover, .stDownloadButton button:hover,
[data-testid="stFormSubmitButton"] button:hover {{background:{CHARLES} !important; color:#fff !important}}
.stLinkButton a[kind="primary"], .stLinkButton a[data-testid="stBaseLinkButton-primary"] {{
  background:{RED} !important; border-color:{RED} !important; color:#fff !important}}
:focus-visible {{outline:3px solid {OPTIMISTIC} !important; outline-offset:2px}}
.stApp a {{color:{OPTIMISTIC}}}
[data-testid="stFileUploaderDropzone"] {{background:{MIST}; border:2px dashed {CHARLES}40; border-radius:4px; padding:30px}}
[data-testid="stExpander"] details, [data-testid="stForm"] {{background:{SNOW}; border:1px solid {RULE}; border-radius:4px}}
[data-testid="stMetric"] {{border-left:3px solid {CHARLES}; padding:4px 0 4px 14px}}
[data-testid="stMetricValue"] {{color:{CHARLES}; font-weight:700}}
[data-testid="stMetricLabel"] p {{color:{MUTED}; font-family:{HEAD}}}
[data-testid="stAlert"] {{border-radius:4px}}
[data-testid="stImage"] img {{border-radius:4px}}
[data-testid="stCode"] pre {{border-radius:4px; border:1px solid {RULE}; background:{MIST}}}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{border-radius:2px !important}}
.civic-section {{color:{MUTED}; font-size:17px; margin:0 0 18px}}
</style>
"""


def _compact(css: str) -> str:
    """Streamlit renders st.markdown as Markdown: a blank line ends the HTML block and the rest of the
    stylesheet shows up as page text. Drop comments and blank lines so it stays one HTML block."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return "\n".join(line for line in css.splitlines() if line.strip())


STREAMLIT_CSS = _compact(STREAMLIT_CSS)
GLOW_CSS = BASE_CSS  # name used by older callers

STREAMLIT_TOML = f"""[theme]
base = "light"
primaryColor = "{OPTIMISTIC}"
backgroundColor = "{SNOW}"
secondaryBackgroundColor = "{MIST}"
textColor = "{INK}"
"""
