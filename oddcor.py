# Corners Finder — Totales de Corners (The Odds API v4)
# -----------------------------------------------------
# Reqs: streamlit, requests, pandas, openpyxl
# Funcionalidad:
#  - Indicas línea de corners (ej. 8, 8.5, 9)
#  - Umbral de momio mínimo para Over y Under
#  - Filtra partidos cuyo mercado alternate_totals_corners tenga esa línea
#    y cumpla ambos umbrales
#  - Descarga a Excel (tabla pivotada con Over/Under)
#
# Notas:
#  - Los mercados de corners vienen en odds por EVENTO:
#      /v4/sports/{sport}/events/{eventId}/odds?markets=alternate_totals_corners
#  - Fechas deben ir en formato sin microsegundos: YYYY-MM-DDTHH:MM:SSZ

import io
import time
from typing import Iterable, List, Dict, Any
from datetime import datetime, date, time as dtime, timezone

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.the-odds-api.com/v4"

# ===================== UI BASE =====================
st.set_page_config(page_title="Corners — Totales (The Odds API)", page_icon="⚽", layout="wide")
st.title("⚽ Corners Finder — Totales de Corners (The Odds API v4)")

with st.sidebar:
    st.header("🔑 Configuración API")
    api_key = st.text_input("API key (The Odds API)", type="password")
    odds_format = st.selectbox("Formato de momios", ["decimal", "american"], index=0)
    regions = st.multiselect("Regiones (bookmakers)", ["uk", "eu", "us", "us2", "au"], default=["uk", "eu"])
    the_day = st.date_input("Fecha (UTC)", value=date.today(), help="Ventana 00:00:00–23:59:59 UTC")

    st.header("🎯 Filtro — Totales de corners")
    corners_line = st.number_input("Línea de corners (ej. 8, 8.5, 9)", min_value=0.0, step=0.5, value=8.0, format="%.2f")
    over_min = st.number_input("Momio mínimo Over (≥)", min_value=0.0, step=0.05, value=2.0, format="%.2f")
    under_min = st.number_input("Momio mínimo Under (≥)", min_value=0.0, step=0.05, value=2.0, format="%.2f")

    fetch_btn = st.button("🔎 Buscar partidos que cumplan")

# ===================== Helpers =====================
def _headers() -> Dict[str, str]:
    return {"Accept": "application/json"}

def _get(url: str, params: Dict[str, Any], retries: int = 2, backoff: float = 0.7) -> Any:
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
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

@st.cache_data(ttl=1200, show_spinner=False)
def list_soccer_sports(api_key: str) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/sports"
    data = _get(url, {"apiKey": api_key, "all": "true"})
    soccer = [s for s in data if "Soccer" in s.get("group", "")]
    soccer_sorted = sorted(soccer, key=lambda x: (not x.get("active", False), x.get("title", "")))
    return soccer_sorted

@st.cache_data(ttl=600, show_spinner=False)
def list_events_for_sport(api_key: str, sport_key: str, commence_from_iso: str, commence_to_iso: str) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/sports/{sport_key}/events"
    params = {"apiKey": api_key, "commenceTimeFrom": commence_from_iso, "commenceTimeTo": commence_to_iso}
    data = _get(url, params)
    return data if isinstance(data, list) else []

def fetch_event_totals_corners(api_key: str, sport_key: str, event_id: str, regions_csv: str, odds_format: str) -> Any:
    url = f"{API_BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions_csv,
        "markets": "alternate_totals_corners",
        "oddsFormat": odds_format,
        "dateFormat": "iso",
    }
    try:
        return _get(url, params)
    except Exception:
        return []

def iter_bookmakers(resp: Any) -> Iterable[Dict[str, Any]]:
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

def flatten_totals_rows(event: Dict[str, Any], bookmaker: Dict[str, Any], market: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    outcomes = market.get("outcomes") if isinstance(market, dict) else None
    if not isinstance(outcomes, list):
        return rows
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        rows.append({
            "event_id": event.get("id"),
            "commence_time": event.get("commence_time"),
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "bookmaker": bookmaker.get("title"),
            "market_key": market.get("key"),
            "outcome_name": o.get("name"),   # "Over" o "Under"
            "point": o.get("point"),         # línea (ej. 8, 8.5, 9)
            "price": o.get("price"),
            "last_update": bookmaker.get("last_update"),
        })
    return rows

# ===================== Ligas / selección =====================
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

# ===================== Búsqueda principal =====================
if fetch_btn:
    if not api_key:
        st.error("Falta API key.")
        st.stop()
    if not regions:
        st.error("Selecciona al menos una región.")
        st.stop()

    regions_csv = ",".join(regions)
    start_dt = datetime.combine(the_day, dtime(0, 0, 0, tzinfo=timezone.utc))
    end_dt   = datetime.combine(the_day, dtime(23, 59, 59, tzinfo=timezone.utc))
    start_iso = iso_utc(start_dt)
    end_iso   = iso_utc(end_dt)

    st.write(f"🔍 Buscando *totales de corners* entre **{start_iso}** y **{end_iso}** en {', '.join(chosen_sports)} …")

    all_rows: List[Dict[str, Any]] = []
    total_events = 0
    events_with_totals = 0

    for sport in chosen_sports:
        try:
            events = list_events_for_sport(api_key, sport, start_iso, end_iso)
        except Exception as e:
            st.warning(f"[{sport}] No pude obtener eventos: {e}")
            events = []

        for ev in events:
            total_events += 1
            resp = fetch_event_totals_corners(api_key, sport, ev.get("id"), regions_csv, odds_format)
            had_totals = False
            for bk in iter_bookmakers(resp):
                markets = bk.get("markets") if isinstance(bk, dict) else None
                if not isinstance(markets, list):
                    continue
                for m in markets:
                    if not isinstance(m, dict):
                        continue
                    if m.get("key") == "alternate_totals_corners":
                        rows = flatten_totals_rows(ev, bk, m)
                        if rows:
                            had_totals = True
                            all_rows.extend(rows)
            if had_totals:
                events_with_totals += 1

    st.write(f"Eventos inspeccionados: **{total_events}** — con totales de corners: **{events_with_totals}**")

    if not all_rows:
        st.warning("No se encontraron **totales de corners** para la selección. Prueba otras ligas/regiones u otra fecha.")
        st.stop()

    df = pd.DataFrame(all_rows)
    # Asegurar tipos numéricos
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    # --- Filtro por LÍNEA exacta + umbrales Over/Under ---
    target = float(corners_line)

    # Nos quedamos con esa línea exacta
    df_line = df[(df["market_key"] == "alternate_totals_corners") & (df["point"] == target)].copy()

    if df_line.empty:
        st.warning(f"No hay cuotas para la **línea {target}** en los mercados de totales de corners.")
        st.stop()

    # Pivotar Over/Under por evento-bookmaker (una fila por combinacion)
    pivot = (
        df_line.pivot_table(
            index=["event_id", "commence_time", "home_team", "away_team", "bookmaker", "point"],
            columns="outcome_name",
            values="price",
            aggfunc="first"
        )
        .reset_index()
    )

    # Normalizar nombres de columnas (por si falta alguno)
    cols = list(pivot.columns)
    if "Over" not in cols: pivot["Over"] = pd.NA
    if "Under" not in cols: pivot["Under"] = pd.NA

    # Filtro por umbrales
    filtered = pivot[
        (pd.to_numeric(pivot["Over"], errors="coerce") >= over_min) &
        (pd.to_numeric(pivot["Under"], errors="coerce") >= under_min)
    ].copy()

    if filtered.empty:
        st.warning(f"No hay partidos con **línea {target}** donde Over ≥ {over_min} y Under ≥ {under_min}.")
        st.stop()

    # Ordenar por fecha y, opcional, por el mayor de Over/Under descendente
    filtered["max_price"] = filtered[["Over", "Under"]].max(axis=1, numeric_only=True)
    filtered = filtered.sort_values(by=["commence_time", "max_price"], ascending=[True, False])

    st.subheader("Resultados — Totales de corners")
    st.caption(f"Mostrando línea **{target}** con Over ≥ **{over_min}** y Under ≥ **{under_min}**.")
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    # Descarga a Excel
    file_name = f"corners_totales_L{str(target).replace('.','_')}_{the_day.isoformat()}.xlsx"
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            filtered.to_excel(writer, index=False, sheet_name="totales_corners")
        st.download_button(
            "⬇️ Descargar Excel",
            data=buffer.getvalue(),
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # Métricas rápidas
    c1, c2, c3 = st.columns(3)
    c1.metric("Partidos (filas)", f"{len(filtered):,}")
    c2.metric("Bookmakers únicos", filtered["bookmaker"].nunique())
    c3.metric("Eventos únicos", filtered["event_id"].nunique())
