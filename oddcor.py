# Streamlit — Corners Finder usando RapidAPI (Football Betting Odds - betodds)
# ---------------------------------------------------------------------------
# Requisitos: streamlit, requests, pandas, openpyxl
# Docs/Playground: RapidAPI "football-betting-odds1" (betodds)
# - Endpoints vistos en el Playground: /{provider}/live/inplaying y /{provider}/live/upcoming
#   (usamos estos y hacemos parsing robusto del JSON para detectar "corner"). 
# NOTA: Si el proveedor no publica mercados de corners, verás una advertencia.

import io
import re
import json
from datetime import datetime, date
from typing import Any, Dict, List

import pandas as pd
import requests
import streamlit as st

# ===================== CONFIG =====================
st.set_page_config(page_title="Corners Finder — RapidAPI betodds", page_icon="⚽", layout="wide")

DEFAULT_HOST = "football-betting-odds1.p.rapidapi.com"  # Host de RapidAPI para este proveedor
TIMEOUT = 15

# ===================== SIDEBAR =====================
st.sidebar.title("⚙️ Config - RapidAPI (betodds)")
rapidapi_key = st.sidebar.text_input("X-RapidAPI-Key", type="password")
rapidapi_host = st.sidebar.text_input("X-RapidAPI-Host", value=DEFAULT_HOST)

# El API organiza rutas tipo /{provider}/live/inplaying y /{provider}/live/upcoming.
# Dejamos el provider configurable (ejemplos a probar: bet365, pinnacle, bwin, williamhill, etc. si el feed los soporta)
provider = st.sidebar.text_input("Provider (segmento de ruta)", value="bet365")

mode = st.sidebar.selectbox("Tipo de feed", ["upcoming", "inplaying"], index=0)
league_filter = st.sidebar.text_input("Filtrar por liga (contiene)", value="")
team_filter = st.sidebar.text_input("Filtrar por equipo (contiene)", value="")
demo_mode = st.sidebar.toggle("Modo demo (sin API)")

st.sidebar.caption(
    "Tip: Si recibes 403/404, revisa tu suscripción en RapidAPI, el provider, y que uses la cabecera Host correcta."
)

# ===================== UI PRINCIPAL =====================
st.title("⚽ Buscador de mercados de corners — RapidAPI (betodds)")
sel_date = st.date_input("Fecha de referencia (solo UI)", value=date.today())
min_line = st.number_input("Filtro: 'Local arriba de…' (numérico detectado en selección)", 0.0, 15.0, step=0.5, value=2.0)
btn_search = st.button("🔍 Buscar partidos y odds")

# ===================== DEMO DATA =====================
DEMO_MATCHES = [
    {
        "id": "demo-001",
        "startTime": "2025-11-05T18:00:00Z",
        "homeTeam": "Barcelona",
        "awayTeam": "Real Madrid",
        "league": "LaLiga"
    }
]

DEMO_ODDS = [
    {
        "book": "demo-book",
        "markets": [
            {
                "name": "total_corners_home",
                "selections": [
                    {"name": "Over 2.0", "odds": 1.90},
                    {"name": "Under 2.0", "odds": 1.80}
                ]
            },
            {
                "name": "corners_team_home",
                "selections": [
                    {"name": "Barcelona Over 2.5", "odds": 2.05}
                ]
            }
        ]
    }
]

# ===================== HELPERS =====================
def need_key():
    if demo_mode:
        return
    if not rapidapi_key:
        st.warning("⚠️ Ingresa tu X-RapidAPI-Key en la barra lateral o activa Modo Demo.")
        st.stop()

def headers() -> Dict[str, str]:
    return {
        "X-RapidAPI-Key": rapidapi_key or "",
        "X-RapidAPI-Host": rapidapi_host or DEFAULT_HOST,
        "accept": "application/json",
    }

def get_url() -> str:
    # p.ej. https://football-betting-odds1.p.rapidapi.com/bet365/live/upcoming
    path = "live/inplaying" if mode == "inplaying" else "live/upcoming"
    return f"https://{rapidapi_host}/{provider}/{path}"

def fetch_matches() -> List[Dict[str, Any]]:
    url = get_url()
    r = requests.get(url, headers=headers(), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    # El esquema varía; intentamos normalizar algunas claves comunes
    # Buscamos lista de partidos en data o en alguna clave (fixtures, events, matches).
    if isinstance(data, list):
        return data
    for key in ("matches", "events", "fixtures", "data", "result"):
        if isinstance(data, dict) and key in data and isinstance(data[key], list):
            return data[key]
    # Si llega un objeto por partido (raro), lo envolvemos:
    if isinstance(data, dict):
        return [data]
    return []

def fetch_odds_for_match(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Algunas implementaciones exponen odds dentro del mismo objeto de match.
    En otras, necesitas llamar otro endpoint con el id del fixture. 
    Para generalidad:
      1) Si el match ya trae 'odds' o 'markets', úsalo.
      2) Si vemos un id razonable, intentamos un segundo endpoint común:
         /{provider}/odds/{match_id}  (heurística)
    """
    # 1) ¿Ya trae odds?
    for k in ("odds", "markets", "bookmakers"):
        if k in match and isinstance(match[k], list):
            return match[k]

    # 2) Intento heurístico por id
    match_id = (
        match.get("id")
        or match.get("matchId")
        or match.get("fixtureId")
        or match.get("eventId")
    )
    if match_id:
        # Probaremos algunas rutas conocidas. Si falla, devolvemos [] sin romper.
        candidate_paths = [
            f"https://{rapidapi_host}/{provider}/odds/{match_id}",
            f"https://{rapidapi_host}/{provider}/prematch/odds/{match_id}",
            f"https://{rapidapi_host}/{provider}/live/odds/{match_id}",
        ]
        for url in candidate_paths:
            try:
                r = requests.get(url, headers=headers(), timeout=TIMEOUT)
                if r.status_code == 200:
                    j = r.json()
                    if isinstance(j, list):
                        return j
                    for k in ("odds", "markets", "bookmakers", "data", "result"):
                        if isinstance(j, dict) and k in j:
                            val = j[k]
                            if isinstance(val, list):
                                return val
            except Exception:
                pass

    return []

def to_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return ""

def pick_first(*keys, src: Dict[str, Any], default="") -> Any:
    for k in keys:
        if k in src and src[k]:
            return src[k]
    return default

def parse_dt(s: str) -> str:
    # Devuelve ISO legible si se puede
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return s

def extract_corner_rows(match: Dict[str, Any], odds_block: List[Dict[str, Any]], min_line: float) -> List[Dict[str, Any]]:
    """
    Recorre estructura genérica:
    - Soporta claves comunes: book / bookmaker / key / market / name / selections / outcomes / odds / price
    - Selección si el nombre del mercado contiene "corner".
    - Filtro "Local arriba de …": intenta detectar un número en la selección y lo compara con min_line.
      (Es heurstico: si la selección contiene "Over 2.5", detectamos 2.5).
    """
    rows = []

    home = pick_first("homeTeam", "home_team", "home", src=match, default="")
    away = pick_first("awayTeam", "away_team", "away", src=match, default="")
    league = pick_first("league", "competition", "tournament", src=match, default="")

    def has_corners(text: str) -> bool:
        return "corner" in (text or "").lower()

    # Algunas respuestas usan "bookmakers" -> [{"title":..., "markets":[...]}]
    # Otras usan lista plana de "markets".
    candidates = odds_block if isinstance(odds_block, list) else []

    for book in candidates:
        book_name = pick_first("book", "bookmaker", "title", "name", src=book, default="")
        markets = []
        if "markets" in book and isinstance(book["markets"], list):
            markets = book["markets"]
        else:
            # a veces el propio 'book' ya es un market (si la API no separa por book)
            if any(k in book for k in ("key", "market", "name", "type")):
                markets = [book]

        for m in markets:
            m_name = pick_first("key", "market", "name", "type", src=m, default="")
            if not has_corners(m_name):
                # Quizá el key no dice corner pero las selecciones sí
                # Entonces miramos igual las selecciones y solo guardamos lo que diga corner.
                pass

            # outcomes/selections
            outs = []
            for k in ("selections", "outcomes", "bets", "options"):
                if k in m and isinstance(m[k], list):
                    outs = m[k]
                    break

            for o in outs:
                sel_name = pick_first("name", "label", "selection", "outcome", src=o, default="")
                # ¿Alguna parte menciona corners?
                if not (has_corners(m_name) or has_corners(sel_name)):
                    continue

                # odds/price
                odds_val = None
                for ok in ("odds", "price", "decimal", "value", "coeff"):
                    if ok in o and isinstance(o[ok], (int, float)):
                        odds_val = float(o[ok])
                        break

                # Heurística para "Local arriba de …": buscamos el primer número tipo 2 o 2.5
                # dentro del nombre de la selección. Si existe y >= min_line, lo retenemos.
                numbers = re.findall(r"\d+(?:\.\d+)?", to_str(sel_name))
                pass_min = True
                if numbers:
                    try:
                        first_num = float(numbers[0])
                        pass_min = first_num >= float(min_line)
                    except Exception:
                        pass_min = True

                if not pass_min:
                    continue

                rows.append({
                    "match_id": pick_first("id", "matchId", "fixtureId", "eventId", src=match, default=""),
                    "start_time": parse_dt(pick_first("startTime", "commence_time", "start_time", "kickoff", src=match, default="")),
                    "league": league,
                    "home": home, "away": away,
                    "book": book_name,
                    "market": m_name,
                    "selection": sel_name,
                    "odds": odds_val,
                })

    return rows

# ===================== ACTION =====================
if btn_search:
    try:
        if demo_mode:
            matches = DEMO_MATCHES
        else:
            need_key()
            matches = fetch_matches()

        # Filtros por liga/equipo (contiene)
        if league_filter:
            matches = [m for m in matches if league_filter.lower() in to_str(m.get("league") or m.get("competition") or "").lower()]
        if team_filter:
            def t_contains(m):
                h = to_str(m.get("homeTeam") or m.get("home_team") or m.get("home") or "")
                a = to_str(m.get("awayTeam") or m.get("away_team") or m.get("away") or "")
                return team_filter.lower() in h.lower() or team_filter.lower() in a.lower()
            matches = [m for m in matches if t_contains(m)]

        st.write(f"Partidos obtenidos: {len(matches)}")

        progress = st.progress(0)
        all_rows: List[Dict[str, Any]] = []

        for idx, match in enumerate(matches):
            progress.progress((idx + 1) / max(1, len(matches)))

            if demo_mode:
                odds_block = DEMO_ODDS
            else:
                odds_block = fetch_odds_for_match(match)

            rows = extract_corner_rows(match, odds_block, min_line=min_line)
            all_rows.extend(rows)

        if not all_rows:
            st.warning(
                "⚠️ No se detectaron mercados/selecciones con la palabra 'corner'. "
                "Es posible que este feed no publique corners para estos eventos."
            )
        else:
            df = pd.DataFrame(all_rows)
            st.dataframe(df, use_container_width=True)

            out_name = f"corners_rapidapi_betodds_{sel_date}.xlsx"
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
            st.download_button(
                f"📥 Descargar Excel — {out_name}",
                data=bio.getvalue(),
                file_name=out_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except requests.HTTPError as http_err:
        st.error(f"HTTPError: {http_err}")
        try:
            st.code(http_err.response.text)
        except Exception:
            pass
    except Exception as e:
        st.error(f"Ocurrió un error: {e}")
        st.exception(e)

