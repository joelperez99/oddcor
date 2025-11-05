# Streamlit — Sportradar Corners Finder (versión final limpia)
# -----------------------------------------------------------
# Requisitos: streamlit, requests, pandas, openpyxl
# Funciones:
#  - UI para ingresar API Key en la barra lateral (o usar modo DEMO)
#  - Selección de fecha y filtro "Local arriba de N" (línea de corners)
#  - Ping de conexión
#  - Descarga a Excel
#  - Manejo robusto de errores y tiempos de espera

import io
import re
from datetime import date

import pandas as pd
import requests
import streamlit as st

# ===================== CONFIG BÁSICA =====================
st.set_page_config(page_title="Corners Finder — Sportradar", page_icon="⚽", layout="wide")

BASE_URL = "https://api.sportradar.com"
SPORT_ID_SOCCER = 1  # Soccer
DEFAULT_ACCESS = "trial"  # o "production"
DEFAULT_LANG = "en"       # o "es"
TIMEOUT = 10               # segundos para requests

# ===================== SIDEBAR =====================
st.sidebar.title("⚙️ Config")
st.sidebar.caption("Usa Modo Demo para probar la UI sin tocar la API.")

api_key = st.sidebar.text_input("Sportradar API Key", type="password")
access_level = st.sidebar.selectbox("Access level", ["trial", "production"], index=0)
language = st.sidebar.selectbox("Idioma del feed", ["en", "es"], index=0)
demo_mode = st.sidebar.toggle("Modo demo (sin llamadas a API)", value=False)

log_box = st.sidebar.container()

# ===================== UTILIDADES =====================

def require_api_key() -> None:
    """Detiene la app si no hay API key (salvo modo demo)."""
    if demo_mode:
        return
    if not api_key:
        st.warning("⚠️ Ingresa tu Sportradar API Key en la barra lateral o activa Modo demo.")
        st.stop()


def api_get(path: str, params: dict | None = None) -> dict:
    if params is None:
        params = {}
    params["api_key"] = api_key
    url = f"{BASE_URL}{path}"
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def sr_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")

@st.cache_data(show_spinner=False, ttl=300)
def list_daily_events(day: date, lang: str, access: str) -> list[dict]:
    """Daily Schedules (prematch) para Soccer.
    Devuelve lista de dicts con sport_event_id, start_time, status, competitors, tournament.
    """
    path = f"/oddscomparison-prematch/{access}/v2/{lang}/sports/{SPORT_ID_SOCCER}/schedules/{sr_date(day)}/schedules.json"
    data = api_get(path)
    schedules = data.get("schedules", [])
    out: list[dict] = []
    for item in schedules:
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

@st.cache_data(show_spinner=False, ttl=300)
def fetch_event_markets(event_id: str, lang: str, access: str) -> dict:
    """Sport Event Markets (prematch) para un evento."""
    path = f"/oddscomparison-prematch/{access}/v2/{lang}/sport_events/{event_id}/sport_event_markets.json"
    return api_get(path)


def team_names(competitors: list[dict]) -> tuple[str, str]:
    home = away = ""
    for c in competitors or []:
        q = c.get("qualifier")
        if q == "home":
            home = c.get("name", "")
        elif q == "away":
            away = c.get("name", "")
    return home, away

# patrones para detectar mercados/outcomes
CORNER_PAT = re.compile(r"corner", re.IGNORECASE)
OVER_PAT = re.compile(r"over", re.IGNORECASE)


def rows_from_markets(markets_payload: dict, min_home_line: float, allowed_books: set[str] | None) -> list[dict]:
    rows: list[dict] = []
    se = markets_payload.get("sport_event", {})
    competitors = se.get("competitors", [])
    home, away = team_names(competitors)

    markets = markets_payload.get("markets", []) or []
    for m in markets:
        m_name = (m.get("name") or "").strip()
        if not CORNER_PAT.search(m_name):
            continue

        for book in m.get("books", []) or []:
            book_name = (book.get("name") or "").strip()
            if allowed_books and book_name not in allowed_books:
                continue

            for outcome in book.get("outcomes", []) or []:
                # odds
                odds_dec = outcome.get("odds_decimal")
                try:
                    odds_dec = float(odds_dec) if odds_dec is not None else None
                except Exception:
                    odds_dec = None

                # handicap (línea)
                hcap_raw = outcome.get("handicap")
                try:
                    hcap = float(hcap_raw) if hcap_raw not in (None, "") else None
                except Exception:
                    hcap = None

                field_id = outcome.get("field_id")  # 1=home
                oname = (outcome.get("name") or "").strip()

                # Regla "Local arriba de N":
                # (A) Mercado de totales corners del local + outcome Over + handicap >= N
                cond_A = (
                    hcap is not None and hcap >= min_home_line and OVER_PAT.search(oname)
                    and re.search(r"home|local", m_name, re.IGNORECASE) is not None
                )
                # (B) Mercado de hándicap de corners con field_id == 1 (home) y handicap >= N
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

# ===================== UI PRINCIPAL =====================
st.title("⚽ Buscador de partidos con mercados de corners (Prematch)")

col_a, col_b, col_c = st.columns([1, 1, 2])
with col_a:
    sel_date = st.date_input("Fecha", value=date.today(), format="YYYY-MM-DD")
with col_b:
    min_local = st.number_input("Filtro: Local arriba de… (línea de corners)", min_value=0.0, step=0.5, value=2.0)
with col_c:
    st.caption("Opcional: limitar por casa(s) de apuestas — separa por comas")
    books_text = st.text_input("Books permitidos", value="")

col_btn1, col_btn2 = st.columns([1, 1])
with col_btn1:
    btn_search = st.button("🔍 Buscar partidos con corners")
with col_btn2:
    btn_ping = st.button("🧪 Probar conexión (ping)")

if btn_ping:
    if demo_mode:
        st.info("En modo demo no se llama a la API; desactívalo para probar credenciales.")
    else:
        try:
            require_api_key()
            _ = list_daily_events(sel_date, language, access_level)
            st.success("Conexión OK y credenciales válidas.")
            log_box.write(f"Ping OK para {sr_date(sel_date)}.")
        except requests.HTTPError as e:
            st.error(f"HTTPError: {e}")
        except Exception as e:
            st.error(f"Error: {e}")

if btn_search:
    try:
        # Preparar filtro de books
        allowed_books: set[str] | None = None
        if books_text.strip():
            allowed_books = {b.strip() for b in books_text.split(',') if b.strip()}

        # Conseguir eventos
        if demo_mode:
            events = [
                {
                    "sport_event_id": "demo:1",
                    "start_time": f"{sr_date(sel_date)}T18:00:00Z",
                    "status": "not_started",
                    "competitors": [
                        {"name": "Equipo A", "qualifier": "home"},
                        {"name": "Equipo B", "qualifier": "away"}
                    ],
                    "tournament": "Demo League",
                }
            ]
        else:
            require_api_key()
            events = list_daily_events(sel_date, language, access_level)

        st.write(f"Eventos encontrados: {len(events)}")

        progress = st.progress(0)
        all_rows: list[dict] = []

        for idx, ev in enumerate(events):
            progress.progress((idx + 1) / max(1, len(events)))
            ev_id = ev.get("sport_event_id")
            if not ev_id:
                continue

            if demo_mode:
                payload = {
                    "sport_event": {
                        "id": ev_id,
                        "start_time": f"{sr_date(sel_date)}T18:00:00Z",
                        "tournament": {"name": "Demo League"},
                        "competitors": [
                            {"name": "Equipo A", "qualifier": "home"},
                            {"name": "Equipo B", "qualifier": "away"}
                        ],
                    },
                    "markets": [
                        {
                            "name": "Total Home Corners",
                            "books": [
                                {
                                    "name": "DemoBook",
                                    "outcomes": [
                                        {"name": "Over", "handicap": 2.0, "odds_decimal": 1.9, "field_id": 1}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            else:
                try:
                    payload = fetch_event_markets(ev_id, language, access_level)
                except requests.HTTPError:
                    # Si no hay permisos o mercados aún, seguimos con el siguiente evento
                    continue

            rows = rows_from_markets(payload, min_home_line=min_local, allowed_books=allowed_books)
            all_rows.extend(rows)

        if all_rows:
            df = pd.DataFrame(all_rows)
            st.dataframe(df, use_container_width=True)

            out_name = f"corners_{sr_date(sel_date)}_minHome{min_local}.xlsx"
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

    except requests.HTTPError as e:
        st.error(f"HTTPError: {e}")
    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)
