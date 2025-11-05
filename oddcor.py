# -*- coding: utf-8 -*-
# Corners Finder — SportMonks (Totales de Corners por línea)
# ----------------------------------------------------------
# Reqs: streamlit, requests, pandas, openpyxl
#
# ¿Qué hace?
# - Pides una FECHA (UTC), una LÍNEA de corners (ej. 8 / 8.5 / 9),
#   y momios mínimos para Over y Under.
# - Consulta fixtures del día en SportMonks y trae odds de "Alternative Corners" (market_id=69).
# - Filtra partidos donde exista esa LÍNEA y cumplan Over ≥ X y Under ≥ Y.
# - Muestra tabla pivotada (Over / Under por bookmaker) y permite descargar a Excel.
#
# Docs clave:
# - GET fixtures by date: /v3/football/fixtures/date/{date}
# - include=odds y filtros de mercados: &include=odds&filters=markets:69
# - Mercado "Alternative Corners": id=69
#
# Autor: tú + ChatGPT :)

import io
import time
from typing import Any, Dict, Iterable, List
from datetime import date as ddate

import pandas as pd
import requests
import streamlit as st

API_FOOTBALL_BASE = "https://api.sportmonks.com/v3/football"
MARKET_ID_ALTERNATIVE_CORNERS = 69  # "Alternative Corners"

# --------------------------- UI ---------------------------
st.set_page_config(page_title="Corners Finder — SportMonks", page_icon="⚽", layout="wide")
st.title("⚽ Corners Finder — SportMonks (Totales de corners)")

with st.sidebar:
    st.header("🔑 Configuración API")
    api_token = st.text_input("API token (SportMonks)", type="password")

    st.header("📅 Fecha & Filtros")
    the_day: ddate = st.date_input("Fecha (UTC)", value=ddate.today(), help="Ventana del día entero en UTC")

    corners_line = st.number_input("Línea de corners (ej. 8, 8.5, 9)", min_value=0.0, step=0.5, value=8.0, format="%.2f")
    over_min = st.number_input("Momio mínimo Over (≥)", min_value=0.0, step=0.05, value=2.0, format="%.2f")
    under_min = st.number_input("Momio mínimo Under (≥)", min_value=0.0, step=0.05, value=2.0, format="%.2f")

    # Opcional: limitar por ligas (IDs separados por coma). Si lo dejas vacío, trae todas.
    leagues_csv = st.text_input("Ligas (IDs separadas por coma) — opcional", value="")
    # Opcional: limitar bookies por IDs
    bookmakers_csv = st.text_input("Bookmakers (IDs separadas por coma) — opcional", value="")

    fetch_btn = st.button("🔎 Buscar partidos que cumplan")

# --------------------------- Helpers ---------------------------
def _get(url: str, params: Dict[str, Any], retries: int = 2, backoff: float = 0.7) -> Any:
    """GET con reintentos básicos para 429/5xx."""
    for i in range(retries + 1):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                raise RuntimeError(f"Respuesta no JSON en {url}")
        if r.status_code in (429, 500, 502, 503, 504) and i < retries:
            time.sleep(backoff * (2 ** i))
            continue
        raise RuntimeError(f"{r.status_code} {r.text}")

def fetch_fixtures_with_corners(
    api_token: str,
    day: ddate,
    leagues_csv: str = "",
    bookmakers_csv: str = "",
) -> List[Dict[str, Any]]:
    """
    Trae fixtures del día con odds incluidas y filtradas al mercado 'Alternative Corners' (id=69).
    Usa: GET /v3/football/fixtures/date/{date}?include=odds&filters=markets:69
    Opcional: &filters=fixtureLeagues:{ids} y &filters=bookmakers:{ids}
    """
    url = f"{API_FOOTBALL_BASE}/fixtures/date/{day.isoformat()}"
    params = {
        "api_token": api_token,
        "include": "odds,participants",  # participants para mostrar nombres si se requieren
        # Filtros de odds:
        "filters": f"markets:{MARKET_ID_ALTERNATIVE_CORNERS}",
    }

    # Agregar filtros opcionales de ligas y bookmakers
    extra_filters = []
    if leagues_csv.strip():
        extra_filters.append(f"fixtureLeagues:{leagues_csv.strip()}")
    if bookmakers_csv.strip():
        extra_filters.append(f"bookmakers:{bookmakers_csv.strip()}")

    if extra_filters:
        if params["filters"]:
            params["filters"] = params["filters"] + "," + ",".join(extra_filters)
        else:
            params["filters"] = ",".join(extra_filters)

    data = _get(url, params)
    return data.get("data", []) if isinstance(data, dict) else []

def participants_to_vs_name(fx: Dict[str, Any]) -> str:
    """Intenta devolver 'Team A vs Team B' desde participants; si no, usa fx['name']."""
    try:
        parts = fx.get("participants", {}).get("data", [])
        names = [p.get("name") for p in parts if isinstance(p, dict)]
        if len(names) >= 2:
            return f"{names[0]} vs {names[1]}"
    except Exception:
        pass
    return fx.get("name") or f"Fixture {fx.get('id')}"

# --------------------------- Main ---------------------------
if fetch_btn:
    if not api_token:
        st.error("Falta API token.")
        st.stop()

    st.write(
        f"🔍 Buscando **totales de corners** (market_id {MARKET_ID_ALTERNATIVE_CORNERS}) "
        f"para **{the_day.isoformat()}**…"
    )

    try:
        fixtures = fetch_fixtures_with_corners(api_token, the_day, leagues_csv, bookmakers_csv)
    except Exception as e:
        st.error(f"No pude obtener fixtures: {e}")
        st.stop()

    if not fixtures:
        st.warning("No se encontraron fixtures para esa fecha (o tu plan no incluye odds).")
        st.stop()

    rows: List[Dict[str, Any]] = []
    for fx in fixtures:
        fx_id = fx.get("id")
        fx_name = participants_to_vs_name(fx)
        start_at = fx.get("starting_at")
        odds = fx.get("odds", {}).get("data", []) if isinstance(fx.get("odds"), dict) else []

        # Cada 'odd' es un registro del mercado con campos: market_id, bookmaker_id, label/name (Over/Under), value, total, etc.
        for odd in odds:
            if odd.get("market_id") != MARKET_ID_ALTERNATIVE_CORNERS:
                continue

            # Convertir tipos
            total = None
            try:
                total = float(odd.get("total")) if odd.get("total") is not None else None
            except Exception:
                total = None

            price = None
            try:
                price = float(odd.get("value"))
            except Exception:
                price = None

            if total is None or price is None:
                continue

            rows.append(
                {
                    "fixture_id": fx_id,
                    "match": fx_name,
                    "starting_at": start_at,
                    "bookmaker_id": odd.get("bookmaker_id"),
                    "market_id": odd.get("market_id"),
                    "label": odd.get("label") or odd.get("name"),  # "Over" / "Under"
                    "total": total,  # línea
                    "price": price,  # momio
                }
            )

    if not rows:
        st.warning("No recibí cuotas de corners en 'Alternative Corners' para esa fecha.")
        st.stop()

    df = pd.DataFrame(rows)

    # Filtrar por la línea elegida
    target_line = float(corners_line)
    df_line = df[df["total"] == target_line].copy()

    if df_line.empty:
        st.warning(f"No hay cuotas para la **línea {target_line}** en 'Alternative Corners'.")
        st.stop()

    # Pivotar Over/Under por fixture+bookmaker
    pivot = (
        df_line.pivot_table(
            index=["fixture_id", "match", "starting_at", "bookmaker_id", "total"],
            columns="label",
            values="price",
            aggfunc="first",
        )
        .reset_index()
    )

    # Asegurar columnas Over/Under
    if "Over" not in pivot.columns:
        pivot["Over"] = pd.NA
    if "Under" not in pivot.columns:
        pivot["Under"] = pd.NA

    # Filtros de momio
    filtered = pivot[
        (pd.to_numeric(pivot["Over"], errors="coerce") >= float(over_min))
        & (pd.to_numeric(pivot["Under"], errors="coerce") >= float(under_min))
    ].copy()

    if filtered.empty:
        st.warning(f"No hay partidos con **línea {target_line}** donde Over ≥ {over_min} y Under ≥ {under_min}.")
        st.stop()

    # Orden sugerida: por fecha y por el mayor de Over/Under desc
    filtered["max_price"] = filtered[["Over", "Under"]].max(axis=1, numeric_only=True)
    filtered = filtered.sort_values(by=["starting_at", "max_price"], ascending=[True, False])

    st.subheader("Resultados — Totales de corners (Alternative Corners)")
    st.caption(
        f"Línea **{target_line}** — Over ≥ **{over_min}**, Under ≥ **{under_min}**."
        + ("  (Filtrado por ligas: " + leagues_csv + ")" if leagues_csv.strip() else "")
        + ("  (Bookmakers: " + bookmakers_csv + ")" if bookmakers_csv.strip() else "")
    )
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    # Descarga Excel
    file_name = f"smonks_corners_L{str(target_line).replace('.','_')}_{the_day.isoformat()}.xlsx"
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            filtered.to_excel(writer, index=False, sheet_name="corners_alt")
        st.download_button(
            "⬇️ Descargar Excel",
            data=buffer.getvalue(),
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # Métricas
    c1, c2, c3 = st.columns(3)
    c1.metric("Filas", f"{len(filtered):,}")
    c2.metric("Bookmakers únicos", filtered["bookmaker_id"].nunique())
    c3.metric("Eventos únicos", filtered["fixture_id"].nunique())
