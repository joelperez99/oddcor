# Streamlit — Corners Finder usando The Odds API (v4)
# ----------------------------------------------------
# Requisitos: streamlit, requests, pandas, openpyxl
# API docs: https://the-odds-api.com/liveapi/guides/v4/
# NOTA: The Odds API NO tiene mercados específicos de corners.
# Sin embargo, vamos a construir un filtrador similar:
#  - Buscar partidos de soccer
#  - Obtener mercados disponibles
#  - Filtrar apuestas donde en el nombre del mercado aparezcan "corner"
#    (si el sportsbook que TheOddsAPI agregue en el futuro lo soporta)
#  - Si no hay mercados de corners, la app lo indicará

import io
from datetime import date
import pandas as pd
import requests
import streamlit as st

# ===================== CONFIG =====================
st.set_page_config(page_title="Corners Finder — The Odds API", page_icon="⚽", layout="wide")

BASE_URL = "https://api.the-odds-api.com/v4/sports"
SPORT_KEY = "soccer"  # categoría principal
TIMEOUT = 10

# ===================== SIDEBAR =====================
st.sidebar.title("⚙️ Config - The Odds API")
api_key = st.sidebar.text_input("The Odds API Key", type="password")
regions = st.sidebar.text_input("Regiones (ej: us,eu,uk)", value="us,uk,eu")
markets = st.sidebar.text_input("Mercados (ej: h2h,spreads,totals)", value="h2h,totals,spreads")
odds_format = st.sidebar.selectbox("Formato odds", ["decimal", "american"], index=0)
demo_mode = st.sidebar.toggle("Modo demo (sin API)")
log = st.sidebar.container()

# ===================== HELPERS =====================
def require_api_key():
    if demo_mode:
        return
    if not api_key:
        st.warning("⚠️ Ingresa tu The Odds API Key en la barra lateral o usa Modo Demo.")
        st.stop()


def fetch_soccer_events() -> list:
    url = f"{BASE_URL}/{SPORT_KEY}/events"
    params = {"api_key": api_key}
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_odds_for_event(event_id: str) -> list:
    url = f"{BASE_URL}/{SPORT_KEY}/events/{event_id}/odds"
    params = {
        "api_key": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format
    }
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

# ===================== LOGIC =====================
def extract_corner_markets(event, odds_payload, min_line):
    rows = []
    home = event.get("home_team", "")
    away = event.get("away_team", "")

    # The Odds API odds struct = sportsbooks -> markets -> outcomes
    for book in odds_payload or []:
        book_name = book.get("bookmaker_key") or book.get("bookmaker") or ""
        for market in book.get("markets", []):
            m_name = market.get("key", "")
            # Buscamos la palabra "corner" — actualmente muchos books no la incluyen
            if "corner" not in m_name.lower():
                continue
            for outcome in market.get("outcomes", []):
                odds_value = outcome.get("price") or outcome.get("odds")
                rows.append({
                    "event_id": event.get("id"),
                    "start_time": event.get("commence_time"),
                    "home": home,
                    "away": away,
                    "book": book_name,
                    "market": m_name,
                    "outcome": outcome.get("name"),
                    "odds": odds_value,
                    "note": "Filtrado por palabra 'corner' en market"
                })

    return rows

# ===================== UI =====================
st.title("⚽ Buscador de mercados de corners — The Odds API")

sel_date = st.date_input("Fecha (sólo referencia UI)", value=date.today())
min_line = st.number_input("Filtro: Local arriba de… (visual, no exacto en esta API)", 0.0, 10.0, step=0.5, value=2.0)

btn_search = st.button("🔍 Buscar eventos de soccer y odds")

# ===================== DEMO =====================
DEMO_EVENTS = [
    {
        "id": "demo1",
        "home_team": "Barcelona",
        "away_team": "Real Madrid",
        "commence_time": "2025-11-05T18:00:00Z"
    }
]

DEMO_ODDS = [
    {
        "bookmaker_key": "demo-book",
        "markets": [
            {
                "key": "total_corners_home",
                "outcomes": [
                    {"name": "Over 2.0", "price": 1.9},
                    {"name": "Under 2.0", "price": 1.8}
                ]
            }
        ]
    }
]

# ===================== ACTION =====================
if btn_search:
    try:
        if demo_mode:
            events = DEMO_EVENTS
        else:
            require_api_key()
            events = fetch_soccer_events()

        st.write(f"Eventos obtenidos: {len(events)}")

        progress = st.progress(0)
        all_rows = []

        for idx, ev in enumerate(events):
            progress.progress((idx + 1) / max(1, len(events)))
            ev_id = ev.get("id")
            if not ev_id:
                continue

            if demo_mode:
                odds_data = DEMO_ODDS
            else:
                try:
                    odds_data = fetch_odds_for_event(ev_id)
                except Exception:
                    continue

            rows = extract_corner_markets(ev, odds_data, min_line)
            all_rows.extend(rows)

        if not all_rows:
            st.warning("⚠️ No se encontraron mercados que contengan la palabra 'corner'.\n*The Odds API normalmente no ofrece mercados de corners.*")
        else:
            df = pd.DataFrame(all_rows)
            st.dataframe(df, use_container_width=True)

            out_name = f"corners_oddsapi_{sel_date}.xlsx"
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
            st.download_button(
                f"📥 Descargar Excel — {out_name}",
                data=bio.getvalue(),
                file_name=out_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)
