"""Visual identity: Boston at night. One warm streetlight glow on a dark city; everything else quiet.

Tokens are shared by the Streamlit app (CSS injected) and the HTML brief.
"""
from __future__ import annotations

import html
import re

NIGHT, SURFACE, LINE = "#0E1013", "#16191E", "#272B31"
INK, MUTED = "#ECEEF1", "#8D949D"
AMBER, HALO, GOOD = "#FFB547", "#BFD9FF", "#7FD9A8"
FONT_URL = "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap"

GLOW_CSS = f"""
.pulse-hero{{position:relative;text-align:center;padding:72px 16px 40px;overflow:hidden;isolation:isolate}}
.pulse-hero .light{{position:absolute;left:50%;top:58px;width:120px;height:120px;transform:translateX(-50%);
  border-radius:50%;z-index:-1;
  background:radial-gradient(circle,#fffaf0 0%,#ffd48a 18%,rgba(255,181,71,.55) 34%,rgba(255,181,71,.12) 56%,rgba(255,181,71,0) 72%)}}
.pulse-hero .cone{{position:absolute;left:50%;top:110px;width:760px;height:560px;transform:translateX(-50%);z-index:-2;
  clip-path:polygon(46% 0,54% 0,100% 100%,0 100%);
  background:linear-gradient(180deg,rgba(255,196,110,.20) 0%,rgba(255,181,71,.07) 34%,rgba(255,181,71,0) 62%);filter:blur(18px)}}
.pulse-hero .halo{{position:absolute;left:50%;top:-140px;width:900px;height:620px;transform:translateX(-50%);z-index:-2;
  background:radial-gradient(ellipse at center,rgba(191,217,255,.10) 0%,rgba(191,217,255,.04) 38%,rgba(14,16,19,0) 68%)}}
.pulse-hero .ring{{position:absolute;left:50%;top:118px;width:150px;height:150px;margin:-75px 0 0 -75px;border-radius:50%;
  border:1px solid rgba(255,181,71,.55);z-index:-1;animation:ping 4.5s cubic-bezier(.2,.6,.3,1) infinite}}
@keyframes ping{{0%{{transform:scale(.6);opacity:.9}}100%{{transform:scale(4.2);opacity:0}}}}
@media (prefers-reduced-motion:reduce){{.pulse-hero .ring{{animation:none;opacity:0}}}}
.pulse-pill{{display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border:1px solid {LINE};border-radius:999px;
  background:rgba(22,25,30,.75);color:{MUTED};font-size:13px;font-weight:500;margin-top:118px}}
.pulse-pill i{{width:7px;height:7px;border-radius:50%;background:{AMBER};box-shadow:0 0 10px {AMBER};display:inline-block}}
.pulse-hero h1{{font-family:Manrope,sans-serif;font-weight:800;letter-spacing:-.035em;line-height:1.02;
  font-size:clamp(36px,6.4vw,68px);color:{INK};margin:18px auto 14px;max-width:30ch;text-wrap:balance}}
.pulse-hero p.sub{{color:{MUTED};font-size:clamp(15px,1.6vw,18px);max-width:52ch;margin:0 auto;line-height:1.55;text-wrap:pretty}}
.pulse-stats{{display:flex;justify-content:center;flex-wrap:wrap;gap:12px 40px;margin-top:30px;color:{MUTED};font-size:14px}}
.pulse-stats b{{display:block;color:{INK};font-size:22px;font-weight:700;letter-spacing:-.02em}}
"""


def hero(title: str, sub: str, pill: str, stats: list[tuple[str, str]]) -> str:
    s = "".join(f"<div><b>{html.escape(v)}</b>{html.escape(k)}</div>" for k, v in stats)
    return (f'<section class="pulse-hero"><div class="halo"></div><div class="cone"></div><div class="light"></div><div class="ring"></div>'
            f'<span class="pulse-pill"><i></i>{html.escape(pill)}</span>'
            f"<h1>{'<br>'.join(html.escape(t) for t in title.split(chr(10)))}</h1><p class=\"sub\">{html.escape(sub)}</p>"
            f'<div class="pulse-stats">{s}</div></section>')


STREAMLIT_CSS = f"""
<link rel="stylesheet" href="{FONT_URL}">
<style>
html, body, [class*="css"], .stApp, button, input, textarea, select {{font-family: Manrope, sans-serif !important}}
.stApp {{background: {NIGHT}}}
[data-testid="stHeader"] {{background: transparent}}
[data-testid="stToolbar"], [data-testid="stDecoration"], footer {{visibility: hidden}}
.block-container {{padding-top: 0 !important; max-width: 1180px}}
{GLOW_CSS}
h2, h3, [data-testid="stHeading"] h2, [data-testid="stHeading"] h3 {{letter-spacing: -.02em; font-weight: 700 !important}}
/* tabs as a floating pill bar, like a site nav */
[data-testid="stTabs"] [role="tablist"] {{gap: 4px; background: {SURFACE}; border: 1px solid {LINE};
  border-radius: 999px; padding: 5px; width: fit-content; margin: 0 auto 28px}}
[data-testid="stTabs"] [role="tab"] {{border-radius: 999px; padding: 8px 18px; height: auto; color: {MUTED}}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{background: {INK}; color: {NIGHT}}}
[data-testid="stTabs"] [role="tab"] p {{font-weight: 600}}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {{display: none}}
/* buttons */
.stButton button, .stLinkButton a, .stDownloadButton button, [data-testid="stFormSubmitButton"] button {{
  border-radius: 999px !important; border: 1px solid {LINE} !important; background: {SURFACE} !important;
  color: {INK} !important; font-weight: 600 !important; padding: 8px 20px !important}}
.stButton button:hover, .stLinkButton a:hover, .stDownloadButton button:hover,
[data-testid="stFormSubmitButton"] button:hover {{border-color: {AMBER} !important; color: {AMBER} !important}}
.stLinkButton a[kind="primary"], .stLinkButton a[data-testid="stBaseLinkButton-primary"] {{
  background: {AMBER} !important; color: {NIGHT} !important; border-color: {AMBER} !important}}
:focus-visible {{outline: 2px solid {AMBER} !important; outline-offset: 2px}}
/* upload: the call to action */
[data-testid="stFileUploaderDropzone"] {{background: radial-gradient(ellipse at top, rgba(255,181,71,.07), {SURFACE} 70%);
  border: 1px dashed rgba(255,181,71,.45); border-radius: 22px; padding: 28px}}
/* panels */
[data-testid="stExpander"] details, [data-testid="stForm"] {{background: {SURFACE}; border: 1px solid {LINE}; border-radius: 18px}}
[data-testid="stMetric"] {{background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px; padding: 14px 16px}}
[data-testid="stMetricValue"] {{font-weight: 700; letter-spacing: -.02em}}
[data-testid="stMetricLabel"] p {{color: {MUTED}}}
[data-testid="stAlert"] {{border-radius: 16px}}
[data-testid="stImage"] img {{border-radius: 18px}}
[data-testid="stCode"] pre {{border-radius: 14px; border: 1px solid {LINE}}}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{border-radius: 12px !important}}
.pulse-section {{color: {MUTED}; margin: -6px 0 18px}}
</style>
"""

def _compact(css: str) -> str:
    """Streamlit renders st.markdown as Markdown: a blank line ends the HTML block and the rest of the
    stylesheet shows up as page text. Drop comments and blank lines so it stays one HTML block."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return "\n".join(line for line in css.splitlines() if line.strip())


STREAMLIT_CSS = _compact(STREAMLIT_CSS)

STREAMLIT_TOML = f"""[theme]
base = "dark"
primaryColor = "{AMBER}"
backgroundColor = "{NIGHT}"
secondaryBackgroundColor = "{SURFACE}"
textColor = "{INK}"
"""
