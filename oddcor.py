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

# (set_page_config movido arriba)
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

col_btn1, col_btn2 = st.columns([1,1])
with col_btn1:
    do_search = st.button("🔍 Buscar partidos con corners")
with col_btn2:
    do_ping = st.button("🧪 Probar conexión (ping)")

if do_ping:
    try:
        # endpoint muy liviano: status o schedules del día
        test_date = date.today()
        _ = list_daily_events(test_date, lang)
        st.success("Conexión OK y credenciales válidas.")
        LOG.write(f"Ping OK para {test_date}.")
    except Exception as e:
        st.error(f"Ping falló: {e}")
        LOG.write(f"Ping error: {e}")

if do_search:
    try:
        if DEMO_MODE:
                payload = {
                    "sport_event": {"id": ev_id, "start_time": f"{sr_date(fecha)}T18:00:00Z", "tournament": {"name": "Demo League"},
                                     "competitors": [{"name": "Equipo A", "qualifier": "home"}, {"name": "Equipo B", "qualifier": "away"}]},
                    "markets": [
                        {"name": "Total Home Corners",
                         "books": [{"name": "DemoBook", "outcomes": [
                             {"name": "Over", "handicap": 2.0, "odds_decimal": 1.9, "field_id": 1}
                         ]}]}
                    ]
                }
            else:
                payload = fetch_event_markets(ev_id, lang)(ev_id, lang)
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
