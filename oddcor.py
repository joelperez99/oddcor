# -*- coding: utf-8 -*-
# Corners Finder — SportMonks con Dropdowns
# ----------------------------------------
# Filtra partidos por línea de corners y momios Over/Under usando SportMonks

import io
import time
from typing import Any, Dict, Iterable, List
from datetime import date as ddate

import pandas as pd
import requests
import streamlit as st

API_FOOTBALL_BASE = "https://api.sportmonks.com/v3/football"
MARKET_ID_ALTERNATIVE_CORNERS = 69

# --------------------------- UI ---------------------------
st.set_page_config(page_title="Corners Finder — SportMonks", page_icon="⚽", layout="wide")
st.title("⚽ Corners Finder — SportMonks (Totales de corners)")

with st.sidebar:
    st.header("🔑 API Token")
    api_token = st.text_input("API token (SportMonks)", type="password")

    if api_token:
        # Obtener ligas y bookmakers
        @st.cache_data(ttl=3600)
        def get_leagues(token):
            url = f"{API_FOOTBALL_BASE}/leagues"
            r = requests.get(url, params={"api_token": token}, timeout=20)
            data = r.json().get("data", [])
            return {f"{l['name']} (ID {l['id']})": l["id"] for l in data if "name" in l}

        @st.cache_data(ttl=3600)
        def get_bookmakers(token):
            url = f"{API_FOOTBALL_BASE}/bookmakers"
            r = requests.get(url, params={"api_token": token}, timeout=20)
            data = r.json().get("data", [])
            return {f"{b['name']} (ID {b['id']})": b["id"] for b in data if "name" in b}

        leagues_dict = get_leagues(api_token)
        bookmakers_dict = get_bookmakers(api_token)

        st.subheader("🌍 Ligas (opcional)")
        selected_leagues = st.multiselect(
            "Selecciona Ligas",
            list(leagues_dict.keys()),
        )
        leagues_csv = ",".join(str(leagues_dict[l]) for l in selected_leagues)

        st.subheader("🏦 Bookmakers (opcional)")
        selected_bookmakers = st.multiselect(
            "Selecciona Casas de Apuesta",
            list(bookmakers_dict.keys()),
        )
        bookmakers_csv = ",".join(str(bookmakers_dict[b]) for b in selected_bookmakers)

    st.header("📅 Parámetros de búsqueda")
    the_day: ddate = st.date_input("Fecha (UTC)", value=ddate.today())

    corners_line = st.number_input("Línea de corners (8, 8.5, 9…)", min_value=0.0, step=0.5, value=8.0, format="%.2f")
    over_min = st.number_input("Momio mínimo Over (≥)", min_value=0.0, step=0.05, value=2.0, format="%.2f")
    under_min = st.number_input("Momio mínimo Under (≥)", min_value=0.0, step=0.05, value=2.0, format="%.2f")

    fetch_btn = st.button("🔎 Buscar")

# --------------------------- API helper ---------------------------
def _get(url: str, params: Dict[str, Any]):
    r = requests.get(url, params=params, timeout=30)
    return r.json()

def fetch_fixtures(api_token, day, leagues_csv, bookmakers_csv):
    url = f"{API_FOOTBALL_BASE}/fixtures/date/{day.isoformat()}"
    filters = f"markets:{MARKET_ID_ALTERNATIVE_CORNERS}"
    if leagues_csv:
        filters += f",fixtureLeagues:{leagues_csv}"
    if bookmakers_csv:
        filters += f",bookmakers:{bookmakers_csv}"

    params = {"api_token": api_token, "include": "odds,participants", "filters": filters}
    data = _get(url, params)
    return data.get("data", []) if isinstance(data, dict) else []

def get_match_name(fx):
    parts = fx.get("participants", {}).get("data", [])
    names = [p.get("name") for p in parts if isinstance(p, dict)]
    return f"{names[0]} vs {names[1]}" if len(names) >= 2 else fx.get("name")

# --------------------------- Main ---------------------------
if fetch_btn:
    if not api_token:
        st.error("❌ Ingresa tu API token.")
        st.stop()

    st.info(f"Buscando partidos del **{the_day}** con línea **{corners_line}**…")

    fixtures = fetch_fixtures(api_token, the_day, leagues_csv, bookmakers_csv)

    rows = []
    for fx in fixtures:
        name = get_match_name(fx)
        start = fx.get("starting_at")
        odds = fx.get("odds", {}).get("data", [])

        for o in odds:
            if o.get("market_id") != MARKET_ID_ALTERNATIVE_CORNERS:
                continue

            total = o.get("total")
            price = o.get("value")

            if total is None or price is None:
                continue

            try:
                total = float(total); price = float(price)
            except:
                continue

            rows.append({
                "fixture_id": fx.get("id"),
                "match": name,
                "starting_at": start,
                "bookmaker_id": o.get("bookmaker_id"),
                "label": o.get("label") or o.get("name"),
                "total": total,
                "price": price,
            })

    if not rows:
        st.warning("⚠️ No hay mercados de corners ese día.")
        st.stop()

    df = pd.DataFrame(rows)

    df_line = df[df["total"] == float(corners_line)]
    if df_line.empty:
        st.warning(f"No existe línea **{corners_line}** en esos partidos.")
        st.stop()

    pivot = (
        df_line.pivot_table(
            index=["fixture_id", "match", "starting_at", "bookmaker_id", "total"],
            columns="label",
            values="price",
            aggfunc="first",
        ).reset_index()
    )

    if "Over" not in pivot: pivot["Over"] = None
    if "Under" not in pivot: pivot["Under"] = None

    filtered = pivot[
        (pivot["Over"] >= over_min)
        & (pivot["Under"] >= under_min)
    ]

    if filtered.empty:
        st.warning("🚫 No hay partidos que cumplan la condición de momios.")
        st.stop()

    st.success(f"✅ Encontrados {len(filtered)} resultados.")
    st.dataframe(filtered, use_container_width=True)

    # Descargar Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="corners")
    st.download_button("⬇️ Descargar Excel", output.getvalue(), "corners.xlsx")

