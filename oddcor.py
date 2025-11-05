# Streamlit — Corners Finder con autodetección de endpoint (RapidAPI betodds)
# ----------------------------------------------------------------------------
# Requisitos: streamlit, requests, pandas, openpyxl
# - Evita 404 probando múltiples rutas comunes: /{provider}/upcoming, /inplay, /prematch, etc.
# - Permite escribir una "Ruta personalizada" por si tu plan/proveedor usa un path distinto.
# - Filtra por mercados/selecciones que contengan "corner" (si el feed los publica).

import io
import re
import json
from datetime import datetime, date
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st

# ===================== CONFIG =====================
st.set_page_config(page_title="Corners Finder — RapidAPI betodds", page_icon="⚽", layout="wide")
DEFAULT_HOST = "football-betting-odds1.p.rapidapi.com"
TIMEOUT = 20

# ===================== SIDEBAR =====================
st.sidebar.title("⚙️ Config - RapidAPI (betodds)")
rapidapi_key = st.sidebar.text_input("X-RapidAPI-Key", type="password")
rapidapi_host = st.sidebar.text_input("X-RapidAPI-Host", value=DEFAULT_HOST)
provider = st.sidebar.text_input("Provider (ej. bet365, pinnacle, bwin)", value="bet365")

endpoint_mode = st.sidebar.selectbox(
    "Modo de endpoint",
    ["Autodetectar", "Elegir de lista", "Ruta personalizada"],
    index=0
)

preset_endpoint = st.sidebar.selectbox(
    "Si eliges 'Elegir de lista', selecciona:",
    [
        "{provider}/upcoming",
        "{provider}/inplay",
        "{provider}/prematch",
        "{provider}/fixtures",
        "{provider}/events",
        "{provider}/live",
        "{provider}/live/inplay",
        "{provider}/live/upcoming",
        "{provider}/pre-match",
        "{provider}/odds",  # a veces listado general de odds
    ],
    index=0
)

custom_path = st.sidebar.text_input(
    "Ruta personalizada (sin https://HOST/). Ej.: bet365/upcoming",
    value=""
)

league_filter = st.sidebar.text_input("Filtrar por liga (contiene)", value="")
team_filter = st.sidebar.text_input("Filtrar por equipo (contiene)", value="")
demo_mode = st.sidebar.toggle("Modo demo (sin API)")
st.sidebar.caption("Usa el Playground de RapidAPI para confirmar la ruta exacta si tu plan la expone.")

# ===================== UI PRINCIPAL =====================
st.title("⚽ Buscador de mercados de corners — RapidAPI (betodds)")
sel_date = st.date_input("Fecha de referencia (solo UI)", value=date.today())
min_line = st.number_input("Filtro: 'Local arriba de…' (heurístico numérico en selección)", 0.0, 15.0, step=0.5, value=2.0)
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

def compose_candidates() -> List[str]:
    """
    Construye una lista de posibles rutas a probar.
    """
    base = provider.strip().strip("/")
    candidates = [
        f"{base}/upcoming",
        f"{base}/inplay",
        f"{base}/prematch",
        f"{base}/fixtures",
        f"{base}/events",
        f"{base}/live",
        f"{base}/live/inplay",
        f"{base}/live/upcoming",
        f"{base}/pre-match",
        f"{base}/odds",
    ]
    return candidates

def pick_endpoint_path() -> Tuple[str, List[str]]:
    """
    Devuelve (path_seleccionado, lista_de_paths_probados).
    """
    if endpoint_mode == "Ruta personalizada":
        path = custom_path.strip().strip("/")
        return path, [path] if path else []
    elif endpoint_mode == "Elegir de lista":
        path = preset_endpoint.format(provider=provider).strip().strip("/")
        return path, [path]
    else:  # Autodetectar
        cands = compose_candidates()
        return "", cands

def probe_first_working_path(paths: List[str]) -> Tuple[str, Dict[str, Any]]:
    """
    Prueba una lista de rutas y devuelve la primera que responda 200 con un JSON usable.
    Regresa (path_ok, payload_json). Si ninguna sirve, path_ok = "".
    """
    for path in paths:
        url = f"https://{rapidapi_host}/{path}"
        try:
            r = requests.get(url, headers=headers(), timeout=TIMEOUT)
            if r.status_code == 200:
                # Intentamos parsear JSON
                j = r.json()
                return path, j
        except Exception:
            pass
    return "", {}

def normalize_matches(data: Any) -> List[Dict[str, Any]]:
    # Intenta devolver lista de partidos desde un JSON variable
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("matches", "events", "fixtures", "data", "result"):
            if isinstance(data.get(k), list):
                return data[k]
        # Si luce como un partido suelto:
        if any(k in data for k in ("homeTeam", "home_team", "home")) and any(k in data for k in ("awayTeam", "away_team", "away")):
            return [data]
    return []

def guess_odds_block(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Devuelve lista de bloques de odds si vienen incrustadas en el match.
    """
    for k in ("odds", "markets", "bookmakers"):
        if isinstance(match.get(k), list):
            return match[k]
    return []

def try_fetch_odds_by_id(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Si el match no trae odds, intenta rutas por id.
    """
    match_id = (match.get("id") or match.get("matchId") or match.get("fixtureId") or match.get("eventId"))
    if not match_id:
        return []
    id_paths = [
        f"{provider.strip().strip('/')}/odds/{match_id}",
        f"{provider.strip().strip('/')}/prematch/odds/{match_id}",
        f"{provider.strip().strip('/')}/live/odds/{match_id}",
    ]
    for p in id_paths:
        url = f"https://{rapidapi_host}/{p}"
        try:
            r = requests.get(url, headers=headers(), timeout=TIMEOUT)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, list):
                    return j
                for k in ("odds", "markets", "bookmakers", "data", "result"):
                    if isinstance(j, dict) and isinstance(j.get(k), list):
                        return j[k]
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
        if k in src and src[k] not in (None, ""):
            return src[k]
    return default

def parse_dt(s: str) -> str:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s

def extract_corner_rows(match: Dict[str, Any], odds_block: List[Dict[str, Any]], min_line: float) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    home = pick_first("homeTeam", "home_team", "home", src=match, default="")
    away = pick_first("awayTeam", "away_team", "away", src=match, default="")
    league = pick_first("league", "competition", "tournament", src=match, default="")
    match_id = pick_first("id", "matchId", "fixtureId", "eventId", src=match, default="")
    start_time = parse_dt(pick_first("startTime", "commence_time", "start_time", "kickoff", src=match, default=""))

    def has_corners(text: str) -> bool:
        return "corner" in (text or "").lower()

    candidates = odds_block if isinstance(odds_block, list) else []
    for book in candidates:
        book_name = pick_first("book", "bookmaker", "title", "name", src=book, default="")
        markets = []
        if "markets" in book and isinstance(book["markets"], list):
            markets = book["markets"]
        else:
            # a veces el propio 'book' es el market
            if any(k in book for k in ("key", "market", "name", "type")):
                markets = [book]

        for m in markets:
            m_name = pick_first("key", "market", "name", "type", src=m, default="")
            outs = []
            for k in ("selections", "outcomes", "bets", "options"):
                if isinstance(m.get(k), list):
                    outs = m[k]
                    break
            for o in outs:
                sel_name = pick_first("name", "label", "selection", "outcome", src=o, default="")
                if not (has_corners(m_name) or has_corners(sel_name)):
                    continue

                odds_val = None
                for ok in ("odds", "price", "decimal", "value", "coeff"):
                    if ok in o and isinstance(o[ok], (int, float)):
                        odds_val = float(o[ok])
                        break

                # Heurística: si la selección tiene un número, exigir >= min_line
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
                    "match_id": match_id,
                    "start_time": start_time,
                    "league": league,
                    "home": home,
                    "away": away,
                    "book": book_name,
                    "market": m_name,
                    "selection": sel_name,
                    "odds": odds_val
                })
    return rows

# ===================== ACTION =====================
if btn_search:
    try:
        if demo_mode:
            path_used = "(demo)"
            raw_payload = DEMO_MATCHES
        else:
            need_key()
            chosen_path, to_probe = pick_endpoint_path()

            if endpoint_mode == "Autodetectar":
                path_used, raw_payload = probe_first_working_path(to_probe)
                if not path_used:
                    raise RuntimeError(
                        "No se encontró un endpoint válido. Prueba 'Ruta personalizada' "
                        "o confirma el path exacto en el Playground de RapidAPI."
                    )
            else:
                # Usar el seleccionado/escrito y hacer un GET
                path_used = chosen_path
                if not path_used:
                    raise ValueError("Ruta personalizada vacía.")
                url = f"https://{rapidapi_host}/{path_used}"
                r = requests.get(url, headers=headers(), timeout=TIMEOUT)
                r.raise_for_status()
                raw_payload = r.json()

        matches = normalize_matches(raw_payload)

        # Filtros
        if league_filter:
            matches = [m for m in matches if league_filter.lower() in to_str(m.get("league") or m.get("competition") or "").lower()]
        if team_filter:
            def t_contains(m):
                h = to_str(m.get("homeTeam") or m.get("home_team") or m.get("home") or "")
                a = to_str(m.get("awayTeam") or m.get("away_team") or m.get("away") or "")
                return team_filter.lower() in h.lower() or team_filter.lower() in a.lower()
            matches = [m for m in matches if t_contains(m)]

        st.success(f"Endpoint usado: {path_used}")
        st.write(f"Partidos obtenidos: {len(matches)}")

        progress = st.progress(0)
        all_rows: List[Dict[str, Any]] = []

        for idx, match in enumerate(matches):
            progress.progress((idx + 1) / max(1, len(matches)))

            if demo_mode:
                odds_block = DEMO_ODDS
            else:
                odds_block = guess_odds_block(match)
                if not odds_block:
                    odds_block = try_fetch_odds_by_id(match)

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
