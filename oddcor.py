# Corners Finder — The Odds API v4
# Reqs: streamlit, requests, pandas, openpyxl
# Doc: https://the-odds-api.com/liveapi/guides/v4/  (v4)
# Mercados de corners (adicionales): alternate_totals_corners, alternate_spreads_corners

import io
import math
import time
from datetime import datetime, timedelta, date, timezone

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.the-odds-api.com/v4"

st.set_page_config(page_title="Corners Finder — The Odds API", page_icon="⚽", layout="wide")
st.title("⚽ Corners Finder — The Odds API v4")

with st.sidebar:
    st.header("🔑 Configuración")
    api_key = st.text_input("API key (The Odds API)", type="password")
    odds_format = st.selectbox("Formato de momios", ["decimal", "american"], index=0)
    # Regiones soportadas (las más comunes en soccer)
    regions = st.multiselect("Regiones (bookmakers por región)", ["uk", "eu", "us", "us2", "au"], default=["uk","eu"])
    the_day = st.date_input("Fecha (UTC)", value=date.today())
    local_handicap_min = st.number_input("Filtro: Handicap corners Local ≥", min_value=0.0, value=2.0, step=0.5)
    fetch_btn = st.button("Buscar corners")

def _headers():
    return {"Accept": "application/json"}

def _get(url, params, retries=2, backoff=0.6):
    for i in range(retries + 1):
        r = requests.get(url, params=params, headers=_headers(), timeout=25)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and i < retries:
            time.sleep(backoff * (2 ** i))
            continue
        # Propaga error legible
        raise RuntimeError(f"{r.status_code} {r.text}")

@st.cache_data(ttl=1200, show_spinner=False)
def list_soccer_sports(api_key: str):
    """Lista ligas de soccer activas (y también puedes marcar 'all')."""
    url = f"{API_BASE}/sports"
    # all=true para mostrar también fuera de temporada; filtramos por 'group' Soccer
    data = _get(url, {"apiKey": api_key, "all": "true"})
    soccer = [s for s in data if "Soccer" in s.get("group","")]
    # Ordenar por título
    soccer_sorted = sorted(soccer, key=lambda x: (not x.get("active", False), x.get("title","")))
    return soccer_sorted

@st.cache_data(ttl=600, show_spinner=False)
def list_events_for_sport(api_key: str, sport_key: str, date_from_iso: str, date_to_iso: str):
    """Eventos (no incluye momios) — no cuenta a la cuota."""
    url = f"{API_BASE}/sports/{sport_key}/events"
    return _get(url, {"apiKey": api_key, "commenceTimeFrom": date_from_iso, "commenceTimeTo": date_to_iso})

def fetch_event_corners(api_key: str, sport_key: str, event_id: str, regions_csv: str, odds_format: str):
    """Trae mercados ADICIONALES del evento (corners)."""
    url = f"{API_BASE}/sports/{sport_key}/events/{event_id}/odds"
    markets = "alternate_totals_corners,alternate_spreads_corners"
    params = {
        "apiKey": api_key,
        "regions": regions_csv,
        "markets": markets,
        "oddsFormat": odds_format,
        "dateFormat": "iso",
    }
    try:
        return _get(url, params)
    except Exception as e:
        # Si un evento no tiene mercados soportados, devolver vacío.
        return []

def flatten_corners_rows(event, bookie, market):
    """Convierte un market de corners a filas planas."""
    rows = []
    ev_id = event.get("id")
    commence = event.get("commence_time")
    home = event.get("home_team")
    away = event.get("away_team")
    market_key = market.get("key")
    for outc in market.get("outcomes", []):
        name = outc.get("name")
        price = outc.get("price")
        point = outc.get("point")  # puede venir None si no aplica
        rows.append({
            "event_id": ev_id,
            "commence_time": commence,
            "home_team": home,
            "away_team": away,
            "bookmaker": bookie.get("title"),
            "market_key": market_key,
            "outcome_name": name,
            "point": point,
            "price": price,
            "last_update": bookie.get("last_update"),
        })
    return rows

# Paso 1: escoger ligas
if api_key:
    try:
        soccer_list = list_soccer_sports(api_key)
        with st.expander("⚙️ Ligas/competiciones de Soccer (The Odds API)"):
            # Sugerimos algunas típicas primero
            default_keys = [s["key"] for s in soccer_list if s.get("active") and any(
                kw in s.get("title","").lower() for kw in ["premier", "la liga", "serie a", "bundesliga", "ligue 1", "mls"]
            )]
            options = {f'{s["title"]} — {s["key"]}': s["key"] for s in soccer_list}
            chosen_labels = st.multiselect("Selecciona ligas (puedes dejar vacío y usar 'upcoming')",
                                           list(options.keys()),
                                           default=[l for l in options if options[l] in default_keys][:6])
            chosen_sports = [options[l] for l in chosen_labels]
            # Opción universal 'upcoming'
            use_upcoming = st.checkbox("Usar sport = 'upcoming' (muestra en vivo y próximos)", value=False)
            if use_upcoming:
                chosen_sports = ["upcoming"]
    except Exception as e:
        st.warning(f"No pude listar ligas: {e}")
        soccer_list = []
else:
    st.info("Ingresa tu API key para listar ligas y eventos.")

# Paso 2: ejecutar búsqueda
if fetch_btn:
    if not api_key:
        st.error("Falta API key.")
        st.stop()
    if not regions:
        st.error("Selecciona al menos una región.")
        st.stop()

    regions_csv = ",".join(regions)

    # Ventana de la fecha seleccionada en UTC
    start_iso = datetime.combine(the_day, datetime.min.time()).replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")
    end_iso   = (datetime.combine(the_day, datetime.max.time()).replace(tzinfo=timezone.utc)
                 .isoformat().replace("+00:00","Z"))

    # Si no seleccionaron ligas: usar 'upcoming'
    if not 'chosen_sports' in locals() or len(chosen_sports) == 0:
        chosen_sports = ["upcoming"]

    st.write(f"🔍 Buscando eventos entre **{start_iso}** y **{end_iso}** en {', '.join(chosen_sports)} …")

    all_rows = []
    total_events_checked = 0

    for sport in chosen_sports:
        try:
            # Listar eventos del día para la liga
            events = list_events_for_sport(api_key, sport, start_iso, end_iso)
        except Exception as e:
            st.warning(f"[{sport}] No pude obtener eventos: {e}")
            events = []

        # Para cada evento, pedir mercados adicionales (corners)
        for ev in events:
            total_events_checked += 1
            data = fetch_event_corners(api_key, sport, ev["id"], regions_csv, odds_format)
            # data es lista de bookmakers (o [] si no hay)
            for bk in data:
                for m in bk.get("markets", []):
                    if m.get("key") in ("alternate_totals_corners", "alternate_spreads_corners"):
                        all_rows.extend(flatten_corners_rows(ev, bk, m))

    st.write(f"Eventos inspeccionados: **{total_events_checked}**")
    if not all_rows:
        st.warning("No se encontraron mercados de *corners* para la selección. Prueba otras ligas/regiones u otra fecha.")
        st.stop()

    df = pd.DataFrame(all_rows)

    # Filtro: handicap de corners Local ≥ X (aplica a alternate_spreads_corners)
    # Asumimos que en outcomes el 'name' coincide con home_team / away_team y 'point' es el spread.
    mask_local = (
        (df["market_key"] == "alternate_spreads_corners") &
        (df["outcome_name"] == df["home_team"]) &
        (pd.to_numeric(df["point"], errors="coerce") >= float(local_handicap_min))
    )
    df_filtered = pd.concat([df[mask_local], df[df["market_key"] == "alternate_totals_corners"]], ignore_index=True)

    st.subheader("Resultados")
    st.caption("Se muestran mercados `alternate_spreads_corners` (handicap por equipo) y `alternate_totals_corners` (línea total).")

    # Ordenar: fecha, liga implícita por recorrer sports, luego bookmaker, por punto descendente si existe
    if "point" in df_filtered.columns:
        df_filtered = df_filtered.sort_values(by=["commence_time","bookmaker","point"], ascending=[True, True, False])
    else:
        df_filtered = df_filtered.sort_values(by=["commence_time","bookmaker"])

    st.dataframe(df_filtered, use_container_width=True, hide_index=True)

    # Descarga a Excel
    file_name = f"corners_{the_day.isoformat()}.xlsx"
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_filtered.to_excel(writer, index=False, sheet_name="corners")
        st.download_button("⬇️ Descargar Excel", data=buffer.getvalue(), file_name=file_name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # Métricas rápidas
    col1, col2, col3 = st.columns(3)
    col1.metric("Filas", f"{len(df_filtered):,}")
    col2.metric("Bookmakers únicos", df_filtered["bookmaker"].nunique())
    col3.metric("Eventos con corners", df_filtered["event_id"].nunique())
