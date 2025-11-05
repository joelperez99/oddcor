# -*- coding: utf-8 -*-
# Corners Finder — SportMonks (Totales de corners con dropdowns)
# --------------------------------------------------------------
# Reqs: streamlit, requests, pandas, openpyxl

import io
from typing import Any, Dict, List
from datetime import date as ddate

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.sportmonks.com/v3/football"
MARKET_ID_ALTERNATIVE_CORNERS = 69  # Alternative Corners

st.set_page_config(page_title="Corners Finder — SportMonks", page_icon="⚽", layout="wide")
st.title("⚽ Corners Finder — SportMonks (Totales de corners)")

# ============================ Helpers ============================
def api_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        raise RuntimeError(f"Respuesta no JSON para {path}")

@st.cache_data(ttl=3600, show_spinner=False)
def get_leagues(token: str) -> Dict[str, int]:
    """Devuelve { 'Liga (ID n)': id }."""
    data = api_get("/leagues", {"api_token": token}).get("data", [])
    return {f"{l.get('name','Liga')} (ID {l['id']})": l["id"] for l in data if isinstance(l, dict) and "id" in l}

def fixtures_with_odds(
    token: str,
    day: ddate,
    leagues_csv: str,
    bookmakers_csv: str
) -> List[Dict[str, Any]]:
    """
    Fixtures del día con odds de 'Alternative Corners' y datos del bookmaker.
    include=odds,participants,odds.bookmaker para tener nombre del bookmaker.
    filters=markets:69 (+ opcional fixtureLeagues:{ids}, bookmakers:{ids})
    """
    filters = f"markets:{MARKET_ID_ALTERNATIVE_CORNERS}"
    if leagues_csv:
        filters += f",fixtureLeagues:{leagues_csv}"
    if bookmakers_csv:
        filters += f",bookmakers:{bookmakers_csv}"

    params = {
        "api_token": token,
        "include": "odds,participants,odds.bookmaker",
        "filters": filters,
    }
    resp = api_get(f"/fixtures/date/{day.isoformat()}", params)
    return resp.get("data", []) if isinstance(resp, dict) else []

def fx_name(fx: Dict[str, Any]) -> str:
    parts = fx.get("participants", {}).get("data", [])
    names = [p.get("name") for p in parts if isinstance(p, dict)]
    return f"{names[0]} vs {names[1]}" if len(names) >= 2 else (fx.get("name") or f"Fixture {fx.get('id')}")

# ============================ Sidebar ============================
with st.sidebar:
    st.header("🔑 API Token")
    token = st.text_input("SportMonks API token", type="password")

    st.header("🌍 Ligas (opcional)")
    if token:
        leagues_dict = get_leagues(token)
        sel_leagues = st.multiselect(
            "Selecciona Ligas",
            list(leagues_dict.keys()),
        )
        leagues_csv = ",".join(str(leagues_dict[k]) for k in sel_leagues)
    else:
        leagues_csv = ""
        st.info("Ingresa el token para cargar el listado de ligas.")

    st.header("🏦 Bookmakers (opcional)")
    # Se llenará después de la primera búsqueda exitosa
    if "available_bookies" not in st.session_state:
        st.session_state.available_bookies = {}  # { 'Nombre (ID n)': id }

    if st.session_state.available_bookies:
        sel_bookies = st.multiselect(
            "Selecciona Casas de Apuesta (de los partidos encontrados)",
            list(st.session_state.available_bookies.keys()),
        )
        bookmakers_csv = ",".join(str(st.session_state.available_bookies[n]) for n in sel_bookies)
    else:
        bookmakers_csv = ""
        st.info("Se poblará tras la primera búsqueda. Luego podrás filtrar por casas específicas.")

    st.header("📅 Parámetros de búsqueda")
    the_day: ddate = st.date_input("Fecha (UTC)", value=ddate.today())

    st.header("🎯 Filtro — Totales de corners")
    corners_line = st.number_input("Línea (8, 8.5, 9…)", min_value=0.0, value=8.0, step=0.5, format="%.2f")
    over_min = st.number_input("Momio mínimo Over (≥)", min_value=0.0, value=2.0, step=0.05, format="%.2f")
    under_min = st.number_input("Momio mínimo Under (≥)", min_value=0.0, value=2.0, step=0.05, format="%.2f")

    fetch_btn = st.button("🔎 Buscar")

# ============================ Main ============================
if fetch_btn:
    if not token:
        st.error("Falta API token.")
        st.stop()

    st.write(f"Buscando fixtures del **{the_day.isoformat()}** con mercado **Alternative Corners (ID {MARKET_ID_ALTERNATIVE_CORNERS})**…")

    try:
        fixtures = fixtures_with_odds(token, the_day, leagues_csv, bookmakers_csv)
    except Exception as e:
        st.error(f"No pude obtener fixtures: {e}")
        st.stop()

    if not fixtures:
        st.warning("No se encontraron fixtures (o tu plan no incluye odds para esas ligas/fecha).")
        st.stop()

    # Construir mapa dinámico de bookmakers (según lo que venga en fixtures)
    bookies_found = {}
    rows = []

    for fx in fixtures:
        name = fx_name(fx)
        start = fx.get("starting_at")
        fx_id = fx.get("id")
        odds_list = fx.get("odds", {}).get("data", [])

        for o in odds_list:
            if o.get("market_id") != MARKET_ID_ALTERNATIVE_CORNERS:
                continue

            # Nombre del bookmaker si viene incluido
            bk_id = o.get("bookmaker_id")
            bk_name = None
            if isinstance(o.get("bookmaker"), dict):
                bk_data = o["bookmaker"].get("data")
                if isinstance(bk_data, dict):
                    bk_name = bk_data.get("name")

            if not bk_name:
                bk_name = f"Bookmaker ID {bk_id}" if bk_id else "Bookmaker"

            # Registrar en el mapa para el dropdown dinámico
            if bk_id:
                bookies_found[f"{bk_name} (ID {bk_id})"] = bk_id

            # Extraer total/price + etiqueta Over/Under
            total = o.get("total")
            price = o.get("value")
            label = o.get("label") or o.get("name")

            if total is None or price is None or label not in ("Over", "Under"):
                continue

            try:
                total = float(total); price = float(price)
            except Exception:
                continue

            rows.append({
                "fixture_id": fx_id,
                "match": name,
                "starting_at": start,
                "bookmaker_id": bk_id,
                "bookmaker_name": bk_name,
                "label": label,      # Over / Under
                "total": total,      # línea
                "price": price,      # momio
            })

    # Actualiza el dropdown dinámico de bookies
    st.session_state.available_bookies = bookies_found

    if not rows:
        st.warning("No recibí cuotas de 'Alternative Corners' para esa fecha.")
        st.stop()

    df = pd.DataFrame(rows)

    # Filtrar por línea elegida
    target = float(corners_line)
    df_line = df[df["total"] == target].copy()
    if df_line.empty:
        st.warning(f"No hay cuotas para la línea **{target}**.")
        st.stop()

    # Pivotar Over/Under por fixture + bookmaker
    pivot = (
        df_line.pivot_table(
            index=["fixture_id", "match", "starting_at", "bookmaker_id", "bookmaker_name", "total"],
            columns="label",
            values="price",
            aggfunc="first",
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )

    # Asegurar columnas
    if "Over" not in pivot.columns: pivot["Over"] = pd.NA
    if "Under" not in pivot.columns: pivot["Under"] = pd.NA

    # Filtro por umbrales
    filtered = pivot[
        (pd.to_numeric(pivot["Over"], errors="coerce") >= over_min) &
        (pd.to_numeric(pivot["Under"], errors="coerce") >= under_min)
    ].copy()

    if filtered.empty:
        st.warning(f"No hay partidos con **línea {target}** donde Over ≥ {over_min} y Under ≥ {under_min}.")
        st.stop()

    # Orden: por fecha y luego por el mayor de Over/Under
    filtered["max_price"] = filtered[["Over", "Under"]].max(axis=1, numeric_only=True)
    filtered = filtered.sort_values(by=["starting_at", "max_price"], ascending=[True, False])

    st.subheader("Resultados — Totales de corners (Alternative Corners)")
    st.caption(f"Línea **{target}** — Over ≥ **{over_min}**, Under ≥ **{under_min}**.")
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    # Descargar Excel
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="corners_totales")
    st.download_button("⬇️ Descargar Excel", out.getvalue(),
                       file_name=f"sportmonks_corners_L{str(target).replace('.','_')}_{the_day.isoformat()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    c1, c2, c3 = st.columns(3)
    c1.metric("Filas", f"{len(filtered):,}")
    c2.metric("Bookmakers únicos", filtered["bookmaker_id"].nunique())
    c3.metric("Eventos únicos", filtered["fixture_id"].nunique())
