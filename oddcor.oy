# Streamlit — Sportradar: Buscador de partidos con apuestas de **corners**
# Requisitos: streamlit, requests, pandas, openpyxl
# \n# ✔ Selecciona fecha
# ✔ Filtra mercados de corners (pre‑match) y muestra sólo los que cumplan "Local arriba de N"
# ✔ Descarga a Excel
# \n# Cómo ejecutar localmente:
#   1) pip install streamlit requests pandas openpyxl
#   2) Exporta variables de entorno (o ponlas en st.secrets):
#       SPORTRADAR_API_KEY="TU_API_KEY"
#       SPORTRADAR_ACCESS_LEVEL="trial"   # o "production"
#       SPORTRADAR_LANGUAGE="en"          # "en", "es", etc.
#   3) streamlit run streamlit_corners_finder.py
# \n# Nota importante: Este ejemplo usa el producto Odds Comparison Prematch v2.
# Endpoints de referencia (documentación):
# - Daily Schedules (prematch): /oddscomparison-prematch/{access_level}/v2/{lang}/sports/{sport_id}/schedules/{date}/schedules.{format}
# - Sport Event Markets (prematch): /oddscomparison-prematch/{access_level}/v2/{lang}/sport_events/{sport_event_id}/sport_event_markets.{format}
# 
# "Corners": En el feed de mercados, el nombre del mercado (market.name) puede contener la palabra "corner" para variantes
# como total corners, team (home/away) total corners, asian handicap corners, etc. Este script filtra por ese criterio
# (insensible a mayúsculas). Además, intenta interpretar la restricción "Local arriba de N" de dos formas:
#   A) Mercados de totales de corners por equipo (home) con outcome "Over" y handicap >= N
#   B) Mercados de hándicap de corners donde field_id==1 (home) y handicap >= N
# 
# Según el book/competencia, los mercados pueden variar en nombre y estructura; el script es tolerante y mantendrá registros
# que encajen con cualquiera de las dos lógicas (A o B). Si quieres acotar por casa de apuestas, usa el multiselect del UI.

import os
import re
import io
import json
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st

# ===================== CONFIGURACIÓN =====================
SPORT_ID_SOCCER = 1  # según docs, Soccer = 1
DEFAULT_LANG = os.getenv("SPORTRADAR_LANGUAGE", "en")
ACCESS_LEVEL = os.getenv("SPORTRADAR_ACCESS_LEVEL", "trial")
# Eliminamos lectura directa para usar UI input
API_KEY = None  # será asignado desde UI("SPORTRADAR_API_KEY", "")
BASE = "https://api.sportradar.com"

# ===================== HELPERS =====================

def get_secret(key: str, fallback: str = "") -> str:
    # Ya no usamos secretos; se reemplaza por UI API_KEY
    if key == "SPORTRADAR_API_KEY":
        return API_KEY or fallback
    return fallback(key: str, fallback: str = "") -> str:
    # Permite leer de st.secrets o de env
    try:
        return st.secrets.get(key, os.getenv(key, fallback))
    except Exception:
        return os.getenv(key, fallback)


def api_get(path: str, params: dict | None = None):
    if params is None:
        params = {}
    api_key = get_secret("SPORTRADAR_API_KEY", API_KEY)
    if not api_key:
        raise RuntimeError("Falta SPORTRADAR_API_KEY (en st.secrets o variables de entorno).")
    params["api_key"] = api_key
    url = f"{BASE}{path}"
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def sr_date(d: date) -> str:
    # Formato YYYY-MM-DD
    return d.strftime("%Y-%m-%d")


def list_daily_events(date_obj: date, lang: str) -> list[dict]:
    """Obtiene eventos con odds disponibles para una fecha (prematch)."""
    path = f"/oddscomparison-prematch/{ACCESS_LEVEL}/v2/{lang}/sports/{SPORT_ID_SOCCER}/schedules/{sr_date(date_obj)}/schedules.json"
    data = api_get(path)
    # La estructura típica incluye una lista de sport_events
    events = data.get("schedules", [])
    # Normalizamos a una lista de diccionarios con sport_event y quizá markets_count
    out = []
    for item in events:
        se = item.get("sport_event", {})
        if not se:
            continue
        out.append({
            "sport_event_id": se.get("id"),
            "start_time": se.get("start_time"),
            "status": se.get("status"),
            "competitors": se.get("competitors", []),
            "tournament": (se.get("tournament") or {}).get("name"),
        })
    return out


def team_names_from_competitors(competitors: list[dict]) -> tuple[str, str]:
    home = away = ""
    for c in competitors or []:
        q = c.get("qualifier")
        if q == "home":
            home = c.get("name", "")
        elif q == "away":
            away = c.get("name", "")
    return home, away


def fetch_event_markets(event_id: str, lang: str) -> dict:
    path = f"/oddscomparison-prematch/{ACCESS_LEVEL}/v2/{lang}/sport_events/{event_id}/sport_event_markets.json"
    return api_get(path)


CORNER_PAT = re.compile(r"corner", re.IGNORECASE)
OVER_PAT = re.compile(r"over", re.IGNORECASE)
UNDER_PAT = re.compile(r"under", re.IGNORECASE)


def rows_from_markets(event: dict, markets_payload: dict, min_home_line: float, allowed_books: set[str] | None) -> list[dict]:
    rows = []
    se = markets_payload.get("sport_event", {})
    competitors = se.get("competitors", [])
    home, away = team_names_from_competitors(competitors)

    for m in markets_payload.get("markets", []) or []:
        m_name = (m.get("name") or "").strip()
        if not CORNER_PAT.search(m_name):
            continue  # sólo mercados que contengan "corner" en el nombre

        for book in m.get("books", []) or []:
            book_name = (book.get("name") or "").strip()
            if allowed_books and book_name not in allowed_books:
                continue

            for outcome in book.get("outcomes", []) or []:
                odds_dec = outcome.get("odds_decimal")
                try:
                    odds_dec = float(odds_dec) if odds_dec is not None else None
                except Exception:
                    odds_dec = None

                # Campo handicap (puede ser línea de corners)
                hcap_raw = outcome.get("handicap")
                try:
                    hcap = float(hcap_raw) if hcap_raw is not None and hcap_raw != "" else None
                except Exception:
                    hcap = None

                field_id = outcome.get("field_id")  # 1=home, 2=away, 3=home in 1x2?, etc.
                oname = (outcome.get("name") or "").strip()

                # --- LÓGICA DE FILTRO "Local arriba de N" ---
                # (A) Mercados tipo totales (Over/Under) de corners para el HOME
                cond_A = False
                if hcap is not None and hcap >= min_home_line and OVER_PAT.search(oname):
                    # Si el mercado es de equipo local, asumimos home total corners
                    # Heurística: el nombre del mercado incluye "home" o "team home" o similar
                    if re.search(r"home|local|team\s*A|equipo\s*local", m_name, re.IGNORECASE):
                        cond_A = True

                # (B) Mercados tipo handicap de corners donde field_id==1 (home) y handicap >= N
                cond_B = (hcap is not None and hcap >= min_home_line and str(field_id) == "1")

                if cond_A or cond_B:
                    rows.append({
                        "sport_event_id": se.get("id"),
                        "start_time": se.get("start_time"),
                        "tournament": (se.get("tournament") or {}).get("name"),
                        "home": home,
                        "away": away,
                        "market_name": m_name,
                        "book": book_name,
                        "outcome_name": oname,
                        "handicap": hcap,
                        "odds_decimal": odds_dec,
                    })
    return rows

# ===================== UI =====================
st.set_page_config(page_title="Corners Finder — Sportradar", page_icon="⚽", layout="wide")
st.title("⚽ Buscador de partidos con mercados de corners (Sportradar — Prematch)")

# ==== API Key en UI ====
api_key_input = st.text_input("Ingresa tu Sportradar API Key", type="password")
if not api_key_input:
    st.warning("⚠️ Ingresa tu API Key para continuar")
    st.stop()
API_KEY = api_key_input("⚽ Buscador de partidos con mercados de corners (Sportradar — Prematch)")

col_a, col_b, col_c = st.columns([1,1,2])
with col_a:
    fecha = st.date_input("Fecha", value=date.today(), format="YYYY-MM-DD")
with col_b:
    min_local = st.number_input("Filtro: Local arriba de… (línea de corners)", min_value=0.0, step=0.5, value=2.0)
with col_c:
    st.caption("Opcional: limita por casa(s) de apuestas (deja vacío para todas)")
    books_filter_text = st.text_input("Books permitidos (separados por coma)", value="")

lang = get_secret("SPORTRADAR_LANGUAGE", DEFAULT_LANG)

st.markdown(
    """
    **Notas**
    - Necesitas una API Key válida de Sportradar (Media APIs). Colócala en *st.secrets* como `SPORTRADAR_API_KEY`.
    - Este script usa *Odds Comparison Prematch v2*. Primero descarga el **Daily Schedule** del deporte **Soccer (ID=1)** para la fecha seleccionada; luego llama **Sport Event Markets** por cada evento y filtra los mercados cuyo nombre contenga "corner".
    - El filtro **Local arriba de N** se cumple si: (A) el mercado parece ser *total corners del local* y el outcome es **Over** con *handicap* ≥ N; o (B) en un mercado de hándicap de corners el *field_id* del outcome es **1 (home)** y *handicap* ≥ N.
    """
)

if st.button("Buscar partidos con corners"):
    try:
        events = list_daily_events(fecha, lang)
        st.write(f"Eventos encontrados: {len(events)}")
        all_rows: list[dict] = []

        # Preparar filtro de casas de apuestas
        allowed_books: set[str] | None = None
        if books_filter_text.strip():
            allowed_books = {b.strip() for b in books_filter_text.split(",") if b.strip()}

        progress = st.progress(0)
        for idx, ev in enumerate(events):
            progress.progress((idx + 1) / max(1, len(events)))
            ev_id = ev["sport_event_id"]
            if not ev_id:
                continue
            try:
                payload = fetch_event_markets(ev_id, lang)
            except requests.HTTPError as http_err:
                # Si el endpoint no tiene mercados aún o no hay permisos, seguimos
                continue
            rows = rows_from_markets(ev, payload, min_home_line=min_local, allowed_books=allowed_books)
            all_rows.extend(rows)

        if all_rows:
            df = pd.DataFrame(all_rows)
            st.dataframe(df, use_container_width=True)

            # Descarga Excel
            out_name = f"corners_{sr_date(fecha)}_minHome{min_local}.xlsx"
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="corners", index=False)
            st.download_button(
                label=f"Descargar Excel — {out_name}",
                data=bio.getvalue(),
                file_name=out_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.info("No se encontraron mercados de corners que cumplan el filtro para esa fecha.")

    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)
