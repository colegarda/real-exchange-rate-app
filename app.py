"""
Real Exchange Rate Explorer
---------------------------
Pulls monthly CPI and exchange-rate data from the IMF SDMX 3.0 API and computes
the bilateral real exchange rate between any two IMF economies (incl. the Euro area).

Definitions
    S(t)   = units of Country 2 currency per 1 unit of Country 1 currency
    RER(t) = S(t) * P1(t) / P2(t)
    RER*   = mean of RER(t) over valid months in the selected sample
    Real Index(t) = 100 * RER(t) / RER*

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import math
import os
import re
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------
IMF_BASE = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"
CPI_FLOW = "CPI"
ER_FLOW = "ER"
START_PERIOD = pd.Period("1976-01", freq="M")
EURO_AREA = "G163"

# "+" = latest stable version, "~" = latest version of the dataflow (SDMX 3.0 wildcards)
VERSIONS = ("+", "~")

ACCEPT_CSV = "application/vnd.sdmx.data+csv;version=2.0.0"
ACCEPT_JSON = "application/vnd.sdmx.data+json;version=2.0.0"

# Optional: some IMF API tiers require a subscription key.
IMF_KEY = os.getenv("IMF_SUBSCRIPTION_KEY", "").strip()

# Vibrant palette (readable on dark background)
C_REAL = "#22D3EE"     # cyan
C_NOMINAL = "#F472B6"  # pink
C_BASE = "#FACC15"     # yellow (RER* = 100 line)
C_ACCENT = "#A78BFA"   # violet

# --------------------------------------------------------------------------------------
# Page setup and styling
# --------------------------------------------------------------------------------------
st.set_page_config(page_title="Real Exchange Rate Explorer", page_icon="💱", layout="wide")

st.markdown(
    f"""
    <style>
    .stApp {{
        background: radial-gradient(1200px 600px at 10% -10%, #1e1b4b 0%, transparent 60%),
                    radial-gradient(900px 500px at 110% 10%, #0c4a6e 0%, transparent 55%),
                    #0b1020;
        color: #E5E7EB;
    }}
    h1, h2, h3, h4 {{ color: #F9FAFB; letter-spacing: -0.02em; }}
    .hero {{
        font-size: 2.4rem; font-weight: 800; line-height: 1.1;
        background: linear-gradient(90deg, {C_REAL}, {C_ACCENT}, {C_NOMINAL});
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }}
    .sub {{ color: #9CA3AF; font-size: 1.0rem; margin-bottom: 1.2rem; }}
    .card {{
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px; padding: 16px 18px; height: 100%;
    }}
    .card .label {{ color: #9CA3AF; font-size: 0.78rem; text-transform: uppercase; letter-spacing: .08em; }}
    .card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 4px; }}
    .card .hint  {{ color: #9CA3AF; font-size: 0.8rem; margin-top: 2px; }}
    .formula {{
        background: rgba(167,139,250,0.08); border-left: 3px solid {C_ACCENT};
        padding: 10px 14px; border-radius: 8px; color: #D1D5DB; font-size: 0.92rem;
    }}
    div[data-testid="stSidebar"] {{ background: #0f172a; }}
    .stDownloadButton button, .stButton button {{
        border-radius: 999px; font-weight: 600;
        background: linear-gradient(90deg, {C_REAL}, {C_ACCENT}); color: #0b1020; border: 0;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------------------
# Country list
# --------------------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def country_options() -> dict[str, str]:
    """Return {display label: ISO3 / IMF code}. Euro area first, then ISO countries."""
    opts: dict[str, str] = {"Euro area (G163)": EURO_AREA}
    extra = {"XKX": "Kosovo"}  # IMF reports Kosovo under XKX, not in ISO 3166
    countries = {c.alpha_3: getattr(c, "common_name", None) or c.name for c in pycountry.countries}
    countries.update(extra)
    for iso, name in sorted(countries.items(), key=lambda kv: kv[1]):
        opts[f"{name} ({iso})"] = iso
    return opts


@st.cache_data(show_spinner=False)
def currency_label(iso: str) -> str:
    if iso == EURO_AREA:
        return "EUR"
    special = {"XKX": "EUR"}
    if iso in special:
        return special[iso]
    try:
        country = pycountry.countries.get(alpha_3=iso)
        if country is None:
            return iso
        # pycountry has no country->currency map; use a small lookup for common ones,
        # otherwise fall back to "<ISO3> currency".
        common = {
            "USA": "USD", "GBR": "GBP", "JPN": "JPY", "CHN": "CNY", "CAN": "CAD", "AUS": "AUD",
            "CHE": "CHF", "IND": "INR", "BRA": "BRL", "MEX": "MXN", "KOR": "KRW", "RUS": "RUB",
            "ZAF": "ZAR", "TUR": "TRY", "SWE": "SEK", "NOR": "NOK", "DNK": "DKK", "NZL": "NZD",
            "SGP": "SGD", "HKG": "HKD", "ARG": "ARS", "CHL": "CLP", "COL": "COP", "PER": "PEN",
            "IDN": "IDR", "THA": "THB", "MYS": "MYR", "PHL": "PHP", "POL": "PLN", "CZE": "CZK",
            "HUN": "HUF", "ISR": "ILS", "SAU": "SAR", "ARE": "AED", "EGY": "EGP", "NGA": "NGN",
            "KEN": "KES", "PAK": "PKR", "VNM": "VND", "ISL": "ISK", "ROU": "RON",
        }
        euro_members = {
            "AUT", "BEL", "CYP", "EST", "FIN", "FRA", "DEU", "GRC", "IRL", "ITA", "LVA",
            "LTU", "LUX", "MLT", "NLD", "PRT", "SVK", "SVN", "ESP", "HRV", "BGR",
        }
        if iso in common:
            return common[iso]
        if iso in euro_members:
            return "EUR (national currency pre-euro)"
        return f"{iso} currency"
    except Exception:
        return iso


# --------------------------------------------------------------------------------------
# IMF SDMX 3.0 fetching
# --------------------------------------------------------------------------------------
_PERIOD_RE = re.compile(r"^(\d{4})-?M?(\d{1,2})$")


def _to_period(raw: str) -> pd.Period | None:
    m = _PERIOD_RE.match(str(raw).strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return pd.Period(year=year, month=month, freq="M")


def _parse_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    cols = {c.upper(): c for c in df.columns}
    if "TIME_PERIOD" not in cols or "OBS_VALUE" not in cols:
        return pd.Series(dtype=float)
    periods = df[cols["TIME_PERIOD"]].map(_to_period)
    values = pd.to_numeric(df[cols["OBS_VALUE"]], errors="coerce")
    s = pd.Series(values.values, index=periods.values).dropna()
    s = s[s.index.notna()]
    return s


def _parse_json(payload: dict) -> pd.Series:
    data = payload.get("data", payload)
    structures = data.get("structures") or [data.get("structure", {})]
    obs_dims = structures[0].get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), obs_dims[0] if obs_dims else None)
    if time_dim is None:
        return pd.Series(dtype=float)
    time_vals = [v.get("id") or v.get("value") for v in time_dim.get("values", [])]
    out: dict[pd.Period, float] = {}
    for ds in data.get("dataSets", []):
        for series in ds.get("series", {}).values():
            for idx, obs in series.get("observations", {}).items():
                p = _to_period(time_vals[int(idx)])
                if p is None or not obs or obs[0] is None:
                    continue
                try:
                    out[p] = float(obs[0])
                except (TypeError, ValueError):
                    continue
    return pd.Series(out, dtype=float)


def _headers(accept: str) -> dict[str, str]:
    h = {"Accept": accept, "User-Agent": "rer-explorer/1.0"}
    if IMF_KEY:
        h["Ocp-Apim-Subscription-Key"] = IMF_KEY
    return h


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_series(flow: str, key: str) -> tuple[pd.Series, str]:
    """Fetch one monthly IMF series. Returns (series indexed by monthly Period, status note)."""
    notes: list[str] = []
    for version in VERSIONS:
        url = f"{IMF_BASE}/data/dataflow/{AGENCY}/{flow}/{version}/{key}"
        for accept, kind in ((ACCEPT_CSV, "csv"), (ACCEPT_JSON, "json")):
            try:
                r = requests.get(url, headers=_headers(accept), timeout=45)
            except requests.RequestException as exc:
                notes.append(f"{flow}/{key}: network error ({exc.__class__.__name__})")
                continue
            if r.status_code == 404:
                notes.append(f"{flow}/{key}: no data (404)")
                break  # no point trying another format for this version
            if r.status_code != 200 or not r.text.strip():
                notes.append(f"{flow}/{key}: HTTP {r.status_code}")
                continue
            try:
                s = _parse_csv(r.text) if kind == "csv" else _parse_json(r.json())
            except Exception as exc:  # malformed / unexpected format
                notes.append(f"{flow}/{key}: parse error ({exc.__class__.__name__})")
                continue
            if not s.empty:
                s = s[~s.index.duplicated(keep="last")].sort_index()
                s = s[s.index >= START_PERIOD]
                s = s[s > 0]
                return s, "ok"
    return pd.Series(dtype=float), "; ".join(dict.fromkeys(notes)) or "no data"


def get_cpi(iso: str) -> tuple[pd.Series, str]:
    return fetch_series(CPI_FLOW, f"{iso}.CPI._T.IX.M")


def get_lcu_per_usd(iso: str, index_hint: pd.Index | None) -> tuple[pd.Series, str]:
    """Local-currency units per USD (period average)."""
    if iso == "USA":
        idx = index_hint if index_hint is not None and len(index_hint) else pd.period_range(
            START_PERIOD, pd.Period(date.today(), freq="M"), freq="M"
        )
        return pd.Series(1.0, index=idx), "USD (= 1 by definition)"
    s, note = fetch_series(ER_FLOW, f"{iso}.XDC_USD.PA_RT.M")
    if not s.empty:
        return s, note
    # Fallback: some IMF releases code the series as USD per national currency.
    s2, note2 = fetch_series(ER_FLOW, f"{iso}.USD_XDC.PA_RT.M")
    if not s2.empty:
        return 1.0 / s2, "ok (inverted from USD_XDC)"
    return pd.Series(dtype=float), f"{note} | {note2}"


# --------------------------------------------------------------------------------------
# Core calculation
# --------------------------------------------------------------------------------------
def build_panel(cpi1: pd.Series, cpi2: pd.Series, e1: pd.Series, e2: pd.Series) -> pd.DataFrame:
    """Full monthly panel from 1976-01 with S(t) and RER(t). NaN where inputs are missing."""
    all_idx = cpi1.index.union(cpi2.index).union(e1.index).union(e2.index)
    if len(all_idx) == 0:
        return pd.DataFrame()
    idx = pd.period_range(max(START_PERIOD, all_idx.min()), all_idx.max(), freq="M")
    df = pd.DataFrame(index=idx)
    df["LCU1_per_USD"] = e1.reindex(idx)
    df["LCU2_per_USD"] = e2.reindex(idx)
    df["P1_CPI"] = cpi1.reindex(idx)
    df["P2_CPI"] = cpi2.reindex(idx)
    # S(t) = (C2 per USD) / (C1 per USD) = units of C2 per 1 unit of C1
    df["S_nominal"] = df["LCU2_per_USD"] / df["LCU1_per_USD"]
    df["RER"] = df["S_nominal"] * df["P1_CPI"] / df["P2_CPI"]
    df["valid"] = df["RER"].notna() & (df["RER"] > 0)
    return df


def sample_stats(panel: pd.DataFrame, start: pd.Period, end: pd.Period) -> tuple[pd.DataFrame, float]:
    sub = panel.loc[start:end].copy()
    rer_star = float(sub.loc[sub["valid"], "RER"].mean()) if sub["valid"].any() else float("nan")
    sub["RER_star"] = rer_star
    sub["Real_Index"] = 100.0 * sub["RER"] / rer_star
    return sub, rer_star


def fmt(x: float, digits: int = 4) -> str:
    if x is None or pd.isna(x):
        return "—"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:,.{digits}f}"
    return f"{x:.{digits + 2}g}"


def card(label: str, value: str, hint: str = "", color: str = "#F9FAFB") -> str:
    return (
        f'<div class="card"><div class="label">{label}</div>'
        f'<div class="value" style="color:{color}">{value}</div>'
        f'<div class="hint">{hint}</div></div>'
    )


# --------------------------------------------------------------------------------------
# Sidebar controls
# --------------------------------------------------------------------------------------
opts = country_options()
labels = list(opts.keys())
default_1 = next(l for l in labels if opts[l] == "USA")
default_2 = next(l for l in labels if opts[l] == "JPN")
st.session_state.setdefault("c1", default_1)
st.session_state.setdefault("c2", default_2)


def _swap():
    st.session_state["c1"], st.session_state["c2"] = st.session_state["c2"], st.session_state["c1"]


with st.sidebar:
    st.markdown("### 🌍 Country pair")
    st.selectbox("Country 1 (base currency)", labels, key="c1")
    st.selectbox("Country 2 (quote currency)", labels, key="c2")
    st.button("⇅  Swap countries", on_click=_swap, width="stretch")
    st.markdown("---")
    st.markdown("### 📈 Chart")
    view = st.segmented_control("Series", ["Real", "Nominal", "Both"], default="Both")
    view = view or "Both"
    log_nominal = st.toggle("Log scale for nominal S(t)", value=False)
    show_base = st.toggle("Show RER* = 100 line", value=True)
    st.markdown("---")
    if st.button("↻  Refresh IMF data", width="stretch"):
        fetch_series.clear()
    st.caption("Source: IMF SDMX 3.0 API (CPI & ER dataflows). Cached for 24h.")

iso1, iso2 = opts[st.session_state["c1"]], opts[st.session_state["c2"]]
cur1, cur2 = currency_label(iso1), currency_label(iso2)
name1 = st.session_state["c1"].rsplit(" (", 1)[0]
name2 = st.session_state["c2"].rsplit(" (", 1)[0]


def poss(name: str) -> str:
    """Possessive form: "Japan's", "United States'"."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"



# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------
st.markdown('<div class="hero">Real Exchange Rate Explorer</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="sub">{name1} → {name2} · monthly IMF CPI and exchange rates since January 1976</div>',
    unsafe_allow_html=True,
)

if iso1 == iso2:
    st.warning("Pick two different countries to compute a bilateral real exchange rate.")
    st.stop()

# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
with st.spinner("Fetching IMF data…"):
    cpi1, n_cpi1 = get_cpi(iso1)
    cpi2, n_cpi2 = get_cpi(iso2)
    e1, n_e1 = get_lcu_per_usd(iso1, cpi1.index)
    e2, n_e2 = get_lcu_per_usd(iso2, cpi2.index)

problems = []
for lbl, s, note in (
    (f"CPI · {name1}", cpi1, n_cpi1),
    (f"CPI · {name2}", cpi2, n_cpi2),
    (f"Exchange rate · {name1}", e1, n_e1),
    (f"Exchange rate · {name2}", e2, n_e2),
):
    if s.empty:
        problems.append(f"**{lbl}** — {note}")

if problems:
    st.error("Some required IMF series could not be retrieved:\n\n- " + "\n- ".join(problems))
    st.info(
        "IMF coverage varies by country and period. Try another pair, press *Refresh IMF data*, "
        "or set the `IMF_SUBSCRIPTION_KEY` environment variable if your API tier requires one."
    )
    st.stop()

panel = build_panel(cpi1, cpi2, e1, e2)
valid_months = panel.index[panel["valid"]]
if len(valid_months) == 0:
    st.error("The CPI and exchange-rate series for this pair never overlap, so no RER can be computed.")
    st.stop()

first_valid, last_valid = valid_months.min(), valid_months.max()
month_range = list(pd.period_range(first_valid, last_valid, freq="M"))

with st.expander("Data coverage", expanded=False):
    cov = pd.DataFrame(
        {
            "Series": [f"CPI {name1}", f"CPI {name2}", f"{cur1} per USD", f"{cur2} per USD"],
            "First": [s.index.min().strftime("%b %Y") for s in (cpi1, cpi2, e1, e2)],
            "Last": [s.index.max().strftime("%b %Y") for s in (cpi1, cpi2, e1, e2)],
            "Observations": [len(s) for s in (cpi1, cpi2, e1, e2)],
        }
    )
    st.dataframe(cov, hide_index=True, width="stretch")
    gaps = int((~panel.loc[first_valid:last_valid, "valid"]).sum())
    st.caption(
        f"Computable RER from {first_valid.strftime('%b %Y')} to {last_valid.strftime('%b %Y')}. "
        f"{gaps} month(s) inside that window are missing at least one input and are excluded."
    )

# --------------------------------------------------------------------------------------
# Sample period slider
# --------------------------------------------------------------------------------------
start, end = st.select_slider(
    "🗓️  Sample period (RER* is the average RER over these months)",
    options=month_range,
    value=(month_range[0], month_range[-1]),
    format_func=lambda p: p.strftime("%b %Y"),
)

sample, rer_star = sample_stats(panel, start, end)
n_valid = int(sample["valid"].sum())
if n_valid == 0:
    st.warning("No valid months in the selected window. Widen the sample period.")
    st.stop()

last_row = sample.loc[sample["valid"]].iloc[-1]
last_idx = float(last_row["Real_Index"])
dev = last_idx - 100

# --------------------------------------------------------------------------------------
# KPI cards
# --------------------------------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)
k1.markdown(card("RER*  (sample mean)", fmt(rer_star), f"{n_valid} valid months", C_BASE), unsafe_allow_html=True)
k2.markdown(
    card(
        f"Real index · {last_row.name.strftime('%b %Y')}",
        f"{last_idx:,.1f}",
        f"{dev:+.1f}% vs. sample average",
        C_REAL,
    ),
    unsafe_allow_html=True,
)
k3.markdown(
    card(
        f"Nominal S · {last_row.name.strftime('%b %Y')}",
        fmt(float(last_row["S_nominal"])),
        f"{cur2} per 1 {cur1}",
        C_NOMINAL,
    ),
    unsafe_allow_html=True,
)
direction = (
    f"{name1} real appreciation vs. avg" if dev > 0 else f"{name1} real depreciation vs. avg" if dev < 0 else "At average"
)
k4.markdown(card("Reading", "▲" if dev > 0 else "▼" if dev < 0 else "●", direction, C_ACCENT), unsafe_allow_html=True)

st.write("")

# --------------------------------------------------------------------------------------
# Chart
# --------------------------------------------------------------------------------------
x = sample.index.to_timestamp()
real_name = "Real Index (RER / RER* × 100)"
nom_name = f"Nominal S(t) — {cur2} per {cur1}"

fig = make_subplots(specs=[[{"secondary_y": view == "Both"}]])

if view in ("Real", "Both"):
    fig.add_trace(
        go.Scatter(
            x=x, y=sample["Real_Index"], name=real_name, mode="lines",
            line=dict(color=C_REAL, width=2.6), connectgaps=False,
            hovertemplate="%{x|%b %Y}<br>Real index: %{y:.2f}<extra></extra>",
        ),
        secondary_y=False,
    )
    if show_base:
        fig.add_hline(y=100, line=dict(color=C_BASE, width=1.4, dash="dash"),
                      annotation_text="RER* = 100", annotation_font_color=C_BASE)

if view in ("Nominal", "Both"):
    fig.add_trace(
        go.Scatter(
            x=x, y=sample["S_nominal"], name=nom_name, mode="lines",
            line=dict(color=C_NOMINAL, width=2.2), connectgaps=False,
            hovertemplate="%{x|%b %Y}<br>S: %{y:,.6g}<extra></extra>",
        ),
        secondary_y=(view == "Both"),
    )

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(255,255,255,0.02)",
    height=520,
    margin=dict(l=10, r=10, t=30, b=10),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=12)),
    font=dict(family="Inter, system-ui, sans-serif", color="#E5E7EB"),
)
fig.update_xaxes(showgrid=False, rangeslider=dict(visible=True, thickness=0.06))
grid = "rgba(255,255,255,0.07)"

if view == "Real":
    fig.update_yaxes(title_text="Real index (sample avg = 100)", gridcolor=grid, title_font_color=C_REAL)
elif view == "Nominal":
    fig.update_yaxes(title_text=f"{cur2} per 1 {cur1}", gridcolor=grid, title_font_color=C_NOMINAL,
                     type="log" if log_nominal else "linear")
else:
    fig.update_yaxes(title_text="Real index (sample avg = 100)", gridcolor=grid,
                     title_font_color=C_REAL, tickfont_color=C_REAL, secondary_y=False)
    fig.update_yaxes(title_text=f"Nominal: {cur2} per 1 {cur1}", showgrid=False,
                     title_font_color=C_NOMINAL, tickfont_color=C_NOMINAL,
                     type="log" if log_nominal else "linear", secondary_y=True)

st.plotly_chart(fig, width="stretch")

st.markdown(
    f'<div class="formula"><b>S(t)</b> = {cur2} per 1 {cur1} = (IMF {cur2}/USD) ÷ (IMF {cur1}/USD) &nbsp;·&nbsp; '
    f'<b>RER(t)</b> = S(t) × P<sub>1</sub>(t) / P<sub>2</sub>(t) &nbsp;·&nbsp; '
    f'<b>Real Index</b> = 100 × RER(t) / RER*. A rising index means {poss(name1)} goods are becoming '
    f'more expensive relative to {poss(name2)} (real appreciation of {cur1}).</div>',
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------------------
# "What does this mean?" — intuition panel
# --------------------------------------------------------------------------------------
st.write("")
st.markdown("### 🧠 What does this mean?")
tab_plain, tab_drivers, tab_play = st.tabs(["💬 Plain English", "🧩 What drove it", "🎛️ Play with it"])

ratio = last_idx / 100.0
month_lbl = last_row.name.strftime("%b %Y")
period_lbl = f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"


def _bar_layout(fig_: go.Figure, height: int) -> go.Figure:
    fig_.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=height, margin=dict(l=10, r=100, t=10, b=10), showlegend=False,
        font=dict(family="Inter, system-ui, sans-serif", color="#E5E7EB", size=13),
    )
    fig_.update_traces(cliponaxis=False)
    fig_.update_xaxes(gridcolor="rgba(255,255,255,0.07)", zeroline=True, zerolinecolor="rgba(255,255,255,0.3)")
    return fig_


with tab_plain:
    left, right = st.columns([3, 2])
    with left:
        if dev > 5:
            verdict = (f"**{name2} looks cheap to people from {name1}**, and {poss(name1)} goods look expensive "
                       f"to people from {name2}. {poss(name1)} exporters have a harder time competing on price.")
        elif dev < -5:
            verdict = (f"**{name1} looks cheap to people from {name2}**, and {poss(name2)} goods look expensive "
                       f"to people from {name1}. {poss(name1)} exporters have a price edge.")
        else:
            verdict = "Relative prices are **close to their average** for this period: neither side looks unusually cheap."
        st.markdown(
            f"""
Imagine a **shopping basket** of everyday goods. Pick the basket so that, on average over
{period_lbl}, one {name1} basket was worth exactly one {name2} basket.

In **{month_lbl}**, a shopper who swaps {cur1} into {cur2} can buy
**{ratio:.2f} {name2} baskets** with the money for one {name1} basket. Going the other way,
a {name2} shopper gets only **{1 / ratio:.2f}** of a {name1} basket.

{verdict}

**The rule of thumb:** the real index rises when {cur1} gets stronger *or* when prices in {name1}
rise faster than in {name2}. Either way, {name1} becomes more expensive relative to {name2}.
"""
        )
    with right:
        meter = go.Figure(
            go.Bar(
                y=["Sample average", month_lbl], x=[1.0, ratio], orientation="h",
                marker_color=[C_BASE, C_REAL],
                text=[f"1.00 basket", f"{ratio:.2f} baskets"], textposition="outside",
                hovertemplate="%{x:.2f} baskets<extra></extra>",
            )
        )
        _bar_layout(meter, 220)
        meter.update_xaxes(range=[0, max(1.0, ratio) * 1.35], title_text=f"{name2} baskets per 1 {name1} basket")
        st.plotly_chart(meter, width="stretch")

with tab_drivers:
    valid_rows = sample.loc[sample["valid"]]
    f_row, l_row = valid_rows.iloc[0], valid_rows.iloc[-1]
    d_s = 100 * math.log(l_row["S_nominal"] / f_row["S_nominal"])
    d_p1 = 100 * math.log(l_row["P1_CPI"] / f_row["P1_CPI"])
    d_p2 = -100 * math.log(l_row["P2_CPI"] / f_row["P2_CPI"])
    total = d_s + d_p1 + d_p2
    f_lbl, l_lbl = f_row.name.strftime("%b %Y"), l_row.name.strftime("%b %Y")

    st.markdown(
        f"Between **{f_lbl}** and **{l_lbl}**, the real exchange rate moved **{total:+.0f} log points** "
        f"(about {100 * (math.exp(total / 100) - 1):+.0f}%). Because RER = S × P1 / P2, that change splits "
        f"exactly into three pieces:"
    )
    labels_ = [
        f"{cur1} vs {cur2} exchange rate",
        f"{name1} inflation (pushes RER up)",
        f"{name2} inflation (pushes RER down)",
        "Total change in RER",
    ]
    vals_ = [d_s, d_p1, d_p2, total]
    colors_ = [C_NOMINAL, C_REAL, C_ACCENT, C_BASE]
    drv = go.Figure(
        go.Bar(
            y=labels_[::-1], x=vals_[::-1], orientation="h", marker_color=colors_[::-1],
            text=[f"{v:+.0f}" for v in vals_[::-1]], textposition="outside",
            hovertemplate="%{y}: %{x:+.1f} log points<extra></extra>",
        )
    )
    _bar_layout(drv, 280)
    span = max(abs(v) for v in vals_) * 1.3 or 1
    drv.update_xaxes(range=[-span, span], title_text="Log points (≈ % for small changes)")
    st.plotly_chart(drv, width="stretch")

    biggest = max(range(3), key=lambda i: abs(vals_[i]))
    st.caption(
        f"The biggest driver was **{labels_[biggest].split(' (')[0]}**. If the exchange-rate bar exactly "
        f"cancels the two inflation bars, purchasing power parity held and the real rate didn't move."
    )

with tab_play:
    st.markdown(
        f"Starting from today's real index of **{last_idx:.1f}**, see what happens over the next year if…"
    )
    p1, p2, p3 = st.columns(3)
    move = p1.slider(f"{cur1} changes vs {cur2} (%)", -30, 30, 0, 1,
                     help=f"Positive = {cur1} buys more {cur2} (nominal appreciation)")
    infl1 = p2.slider(f"{name1} inflation (%)", 0.0, 20.0, 3.0, 0.5)
    infl2 = p3.slider(f"{name2} inflation (%)", 0.0, 20.0, 3.0, 0.5)

    new_idx = last_idx * (1 + move / 100) * (1 + infl1 / 100) / (1 + infl2 / 100)
    chg = 100 * (new_idx / last_idx - 1)
    ppp_move = 100 * ((1 + infl2 / 100) / (1 + infl1 / 100) - 1)

    r1, r2, r3 = st.columns(3)
    r1.markdown(card("New real index", f"{new_idx:,.1f}", f"was {last_idx:,.1f}", C_REAL), unsafe_allow_html=True)
    r2.markdown(
        card("Real change", f"{chg:+.1f}%",
             f"{name1} gets {'more' if chg > 0 else 'less' if chg < 0 else 'no more or less'} expensive",
             C_NOMINAL if chg >= 0 else C_ACCENT),
        unsafe_allow_html=True,
    )
    r3.markdown(
        card("Move that keeps RER flat", f"{ppp_move:+.1f}%", f"{cur1} vs {cur2}: the PPP benchmark", C_BASE),
        unsafe_allow_html=True,
    )
    st.write("")
    st.caption(
        f"Try it: set both inflation rates equal with no currency move, and nothing changes. Then raise "
        f"{poss(name1)} inflation. {name1} gets more expensive unless {cur1} falls by about the inflation gap. "
        f"That offset is the idea behind purchasing power parity."
    )

# --------------------------------------------------------------------------------------
# Table + CSV
# --------------------------------------------------------------------------------------
st.write("")
out = sample.copy()
out.insert(0, "month", out.index.strftime("%Y-%m"))
out.insert(1, "country1", iso1)
out.insert(2, "country2", iso2)
out = out.rename(
    columns={
        "LCU1_per_USD": f"{iso1}_LCU_per_USD",
        "LCU2_per_USD": f"{iso2}_LCU_per_USD",
        "P1_CPI": f"P1_CPI_{iso1}",
        "P2_CPI": f"P2_CPI_{iso2}",
        "S_nominal": f"S_{iso2}_per_{iso1}",
        "RER": "RER",
        "valid": "valid_month",
        "RER_star": "RER_star_sample_mean",
        "Real_Index": "Real_Index",
    }
)
out = out.reset_index(drop=True)

c_left, c_right = st.columns([3, 1])
with c_left:
    st.markdown(f"#### Observations · {start.strftime('%b %Y')} – {end.strftime('%b %Y')}")
with c_right:
    st.download_button(
        "⬇  Download CSV",
        data=out.to_csv(index=False).encode("utf-8"),
        file_name=f"RER_{iso1}_{iso2}_{start.strftime('%Y%m')}_{end.strftime('%Y%m')}.csv",
        mime="text/csv",
        width="stretch",
    )
st.dataframe(out, hide_index=True, width="stretch", height=320)
