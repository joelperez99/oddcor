# Corners Finder — The Odds API v4 (Streamlit)
# ---------------------------------------------
# Reqs: streamlit, requests, pandas, openpyxl
# Funciones:
#  - API key The Odds API
#  - Fecha (UTC), regiones y ligas de soccer
#  - Busca eventos del día y trae mercados de corners por evento
#  - Filtro: handicap de corners (Local ≥ X)
#  - Descarga a Excel
#
# Notas del endpoint de evento:
#   /v4/sports/{sport}/events/{eventId}/odds?markets=alternate_totals_corners,alternate_spreads_corners
#   La respuesta puede llegar como:
#     A) lista de bookmakers: [ { "key": "...", "title": "...", "markets": [...] }, ... ]
#     B) objeto con "bookmakers": { "bookmakers": [ { ... }, ... ], ... }
#   Por eso normalizamos antes de iterar.

import io
import json
import time
from typing import Iterable, List, Dict, Any
from datetime import datetime, date, time as dtime, timezone

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.the-odds-api.com/v4"

# ===================== UI BASE =====================
st.set_page_config(page_title="Corners Finder — The Odds API", page_icon="⚽", layout="wide")
st.title("⚽ Corners Finder — The Odds API v4")

with st.sidebar:
    st.header("🔑 Configuración")
    api_key = st.text_input("API key (The Odds API)", type="password")
    odds_format = st.selectbox("Formato de momios", ["decimal", "american"], index=0)
    regions = st.multiselect("Regiones (bookmakers)", ["uk", "eu", "us", "us2", "au"], default=["uk", "eu"])
    the_day = st.date_input(
        "Fecha (UTC)",
        value=date.today(),
        help="Ventana exacta 00:00:00–23:59:59 UTC"
    )
    local_handicap_min = st.number_input("Filtro: Handicap corners Local ≥", min_value=0.0, value=2.0, step=0.5)
    fetch_btn = st.button("🔎 Buscar corners")

# ===================== Helpers =====================
def _headers() -> Dict[str, str]:
    return {"Accept": "application/json"}

def _get(url: str, params: Dict[str, Any], retries: int = 2, backoff: float = 0.7) -> Any:
    """GET con reintentos básicos para 429/5xx; propaga errores legibles."""
    for i in range(retries + 1):
        r = requests.get(url, params=params, headers=_headers(), timeout=30)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                raise RuntimeError(f"Respuesta no JSON en {url}")
        if r.status_code in (429, 500, 502, 503, 504) and i < retries:
            time.sleep(backoff * (2 ** i))
            continue
        raise RuntimeError(f"{r.status_code} {r.text}")

def iso_utc(dt: datetime) -> str:
    """Formatea a ISO Z sin microsegundos (YYYY-MM-DDTHH:MM:SSZ)."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

@st.cache_data(ttl=1200, show_spinner=False)
def list_soccer_sports(api_key: str) -> List[Dict[str, Any]]:
    """Lista ligas de Soccer, activas primero."""
    url = f"{API_BASE}/sports"
    data = _get(url, {"apiKey": api_key, "all": "true"})
    soccer = [s for s in data if "Soccer" in s.get("group", "")]
    soccer_sorted = sorted(soccer, key=lambda x: (not x.get("active", False), x.get("title", "")))
    return soccer_sorted

@st.cache_data(ttl=600, show_spinner=False)
def list_events_for_sport(api_key: str, sport_key: str, commence_from_iso: str, commence_to_iso: str) -> List[Dict[str, Any]]:
    """Eventos del deporte/competición entre dos tiempos (no cuenta a cuota)."""
    url = f"{API_BASE}/sports/{sport_key}/events"
    params = {
        "apiKey": api_key,
        "commenceTimeFrom": commence_from_iso,
        "commenceTimeTo": commence_to_iso,
    }
    data = _get(url, params)
    # Siempre devolver lista
    return data if isinstance(data, list) else []

def fetch_event_corners(api_key: str, sport_key: str, event_id: str, regions_csv: str, odds_format: str) -> Any:
    """Mercados ADICIONALES del evento (corners)."""
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
    except Exception:
        # Hay eventos sin corners/mercados alternos -> regresa vacío
        return []

def iter_bookmakers(resp: Any) -> Iterable[Dict[str, Any]]:
    """
    Normaliza la respuesta del endpoint de evento a un iterable de 'bookmaker' dicts.
    Formas esperadas:
      - Lista: [ {bookmaker}, {bookmaker}, ... ]
      - Objeto: { "bookmakers": [ {bookmaker}, ... ], ... }
    """
    if isinstance(resp, list):
        for bk in resp:
            if isinstance(bk, dict):
                yield bk
    elif isinstance(resp, dict):
        bks = resp.get("bookmakers") or resp.get("bookmaker") or []
        if isinstance(bks, list):
            for bk in bks:
                if isinstance(bk, dict):
                    yield bk

def flatten_corners_rows(event: Dict[str, Any], bookmaker: Dict[str, Any], market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convierte un market de corners en filas planas (una por outcome)."""
    rows = []
    ev_id = event.get("id")
    commence = event.get("commence_time")
    home = event.get("home_team")
    away = event.get("away_team")
    market_key = market.get("key")
    outcomes = market.get("outcomes") if isinstance(market, dict) else None
    if not isinstance(outcomes, list):
        return rows
    for outc in outcomes:
        if not isinstance(outc, dict):
            continue
        rows.append({
            "event_id": ev_id,
            "commence_time": commence,
            "home_team": home,
            "away_team": away,
            "bookmaker": bookmaker.get("title"),
            "market_key": market_key,
            "outcome_name": outc.get("name"),
            "point": outc.get("point"),
            "price": outc.get("price"),
            "last_update": bookmaker.get("last_update"),
        })
    return rows

# ===================== Paso 1: ligas =====================
chosen_sports: List[str] = []
if api_key:
    try:
        soccer_list = list_soccer_sports(api_key)
        with st.expander("⚙️ Ligas/competiciones de Soccer (The Odds API)"):
            default_keys = [
                s["key"] for s in soccer_list
                if s.get("active") and any(
                    kw in s.get("title", "").lower()
                    for kw in ["premier", "la liga", "serie a", "bundesliga", "ligue 1", "mls"]
                )
            ]
            options = {f'{s["title"]} — {s["key"]}': s["key"] for s in soccer_list}
            chosen_labels = st.multiselect(
                "Selecciona ligas (puedes dejar vacío y usar 'upcoming')",
                list(options.keys()),
                default=[lbl for lbl in options if options[lbl] in default_keys][:6]
            )
            chosen_sports = [options[lbl] for lbl in chosen_labels]
            use_upcoming = st.checkbox("Usar sport = 'upcoming' (próximos/en vivo)", value=False)
            if use_upcoming or not chosen_sports:
                chosen_sports = ["upcoming"]
    except Exception as e:
        st.warning(f"No pude listar ligas: {e}")
else:
    st.info("Ingresa tu API key para listar ligas y eventos.")

# ===================== Paso 2: búsqueda =====================
if fetch_btn:
    if not api_key:
        st.error("Falta API key.")
        st.stop()
    if not regions:
        st.error("Selecciona al menos una región.")
        st.stop()

    regions_csv = ",".join(regions)

    # Ventana exacta del día seleccionado en UTC (SIN microsegundos)
    start_dt = datetime.combine(the_day, dtime(0, 0, 0, tzinfo=timezone.utc))
    end_dt   = datetime.combine(the_day, dtime(23, 59, 59, tzinfo=timezone.utc))
    start_iso = iso_utc(start_dt)  # e.g., 2025-11-05T00:00:00Z
    end_iso   = iso_utc(end_dt)    # e.g., 2025-11-05T23:59:59Z

    st.write(f"🔍 Buscando eventos entre **{start_iso}** y **{end_iso}** en {', '.join(chosen_sports)} …")

    all_rows: List[Dict[str, Any]] = []
    total_events_checked = 0
    eventos_con_corners = 0

    for sport in chosen_sports:
        # 1) Eventos del día
        try:
            events = list_events_for_sport(api_key, sport, start_iso, end_iso)
        except Exception as e:
            st.warning(f"[{sport}] No pude obtener eventos: {e}")
            events = []

        # 2) Por cada evento: odds de mercados adicionales (corners)
        for ev in events:
            total_events_checked += 1
            resp = fetch_event_corners(api_key, sport, ev.get("id"), regions_csv, odds_format)

            tuvo_corners = False
            for bk in iter_bookmakers(resp):
                markets = bk.get("markets") if isinstance(bk, dict) else None
                if not isinstance(markets, list):
                    continue
                for m in markets:
                    if not isinstance(m, dict):
                        continue
                    mk = m.get("key")
                    if mk in ("alternate_totals_corners", "alternate_spreads_corners"):
                        rows = flatten_corners_rows(ev, bk, m)
                        if rows:
                            tuvo_corners = True
                            all_rows.extend(rows)
            if tuvo_corners:
                eventos_con_corners += 1

    st.write(f"Eventos inspeccionados: **{total_events_checked}** — con corners: **{eventos_con_corners}**")

    if not all_rows:
        st.warning("No se encontraron mercados de *corners* para la selección. "
                   "Prueba otras ligas/regiones u otra fecha.")
        st.stop()

    df = pd.DataFrame(all_rows)

    # Filtro: handicap de corners Local ≥ X (sólo para alternate_spreads_corners)
    mask_local_handicap = (
        (df["market_key"] == "alternate_spreads_corners") &
        (df["outcome_name"] == df["home_team"]) &
        (pd.to_numeric(df["point"], errors="coerce") >= float(local_handicap_min))
    )

    df_filtered = pd.concat(
        [
            df[mask_local_handicap],                                 # handicaps del local que cumplen el umbral
            df[df["market_key"] == "alternate_totals_corners"],      # totales de corners
        ],
        ignore_index=True
    )

    # Ordenar por fecha -> bookmaker -> point (desc si existe)
    if "point" in df_filtered.columns:
        df_filtered = df_filtered.sort_values(
            by=["commence_time", "bookmaker", "point"],
            ascending=[True, True, False]
        )
    else:
        df_filtered = df_filtered.sort_values(by=["commence_time", "bookmaker"])

    st.subheader("Resultados")
    st.caption("Incluye `alternate_spreads_corners` (handicap por equipo) y `alternate_totals_corners` (total corners).")
    st.dataframe(df_filtered, use_container_width=True, hide_index=True)

    # Descarga a Excel
    file_name = f"corners_{the_day.isoformat()}.xlsx"
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_filtered.to_excel(writer, index=False, sheet_name="corners")
        st.download_button(
            "⬇️ Descargar Excel",
            data=buffer.getvalue(),
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # Métricas rápidas
    c1, c2, c3 = st.columns(3)
    c1.metric("Filas", f"{len(df_filtered):,}")
    c2.metric("Bookmakers únicos", df_filtered["bookmaker"].nunique())
    c3.metric("Eventos con corners", df_filtered["event_id"].nunique())
