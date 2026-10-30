"""Precios en vivo desde la API de 1816 (misma lógica que app_BE.py).

La clave se lee de st.secrets["API_1816_KEY"] (local: .streamlit/secrets.toml;
Streamlit Cloud: Settings → Secrets) o de la variable de entorno API_1816_KEY.
Nunca la subas al repo.

Respeta los límites del plan: 1 pedido por segundo y reusa el token (máx. 50 por día).

Los tickers se piden en grupos. Si un grupo falla (p. ej. la API no reconoce algún
ticker o límite de pedidos), se reintenta con pausas y partiendo el grupo.

Bonos en dólares: 1816 usa el ticker base (AL30) con moneda=mep o moneda=ccl.
La app los nombra AL30D (MEP) y AL30C (cable), como en la hoja Precios.
"""
from __future__ import annotations

import os
import time

import requests
import streamlit as st

URL_AUTH = "https://api.1816.com.ar/v1/auth/token"
URL_INDICADORES = "https://api.1816.com.ar/v1/mercado/indicadores"
TAMANO_GRUPO = 20


class API1816NoConfigurada(RuntimeError):
    pass


def get_api_key() -> str | None:
    try:
        k = st.secrets["API_1816_KEY"]
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("API_1816_KEY")


# Límites del plan de 1816: máx. 1 pedido por segundo y máx. 50 tokens por día.
PAUSA_MIN = 1.05          # segundos entre pedidos (cualquier pedido, incluido el token)
TOKEN_VIGENCIA = 30 * 60  # se reusa el token 30 min; si la API lo rechaza antes, se renueva
_estado = {"ultimo_pedido": 0.0, "token": None, "token_t": 0.0, "tokens_pedidos": 0}


def _esperar_turno() -> None:
    falta = PAUSA_MIN - (time.monotonic() - _estado["ultimo_pedido"])
    if falta > 0:
        time.sleep(falta)
    _estado["ultimo_pedido"] = time.monotonic()


def _token(api_key: str, renovar: bool = False) -> str:
    """Token reutilizado entre consultas (y entre usuarios de la app) para no pasar 50/día."""
    vigente = _estado["token"] and time.monotonic() - _estado["token_t"] < TOKEN_VIGENCIA
    if vigente and not renovar:
        return _estado["token"]
    _esperar_turno()
    auth = requests.post(URL_AUTH, json={"apiKey": api_key, "module": "mercado"}, timeout=10)
    auth.raise_for_status()
    _estado.update(token=auth.json()["token"], token_t=time.monotonic(),
                   tokens_pedidos=_estado["tokens_pedidos"] + 1)
    return _estado["token"]


class TokenVencido(Exception):
    pass


def _pedir(token: str, tickers: list[str], moneda: str | None = None,
           fecha: str | None = None) -> dict[str, float]:
    """Pide precioDirty. Para bonos en dólares se pasa el ticker base (AL30) y moneda mep/ccl.
    Con fecha (AAAA-MM-DD) pide el precio de ese día (parámetro fechaOperacion)."""
    params = {"tickers": [t.strip().lower() for t in tickers], "campos": ["precioDirty"]}
    if moneda:
        params["moneda"] = moneda
    if fecha:
        params["fechaOperacion"] = fecha
    _esperar_turno()
    r = requests.get(URL_INDICADORES, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=15)
    if r.status_code in (401, 403):
        raise TokenVencido(r.text[:200])
    if r.status_code >= 400:
        raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}", response=r)
    data = r.json()
    if isinstance(data, dict) and data.get("fechaOperacion"):
        _estado.setdefault("fechas_respuesta", set()).add(str(data["fechaOperacion"])[:10])
    precios = {}
    if isinstance(data, dict) and "instrumentos" in data:
        for ticker, valores in data["instrumentos"].items():
            if isinstance(valores, dict) and valores.get("precioDirty") is not None:
                precios[ticker.strip().upper()] = float(valores["precioDirty"])
    return precios


def _pedir_con_reintentos(token: str, tickers: list[str], moneda: str | None = None,
                          intentos: int = 3, fecha: str | None = None) -> dict[str, float]:
    espera = 1.5
    for k in range(intentos):
        try:
            return _pedir(token, tickers, moneda, fecha)
        except requests.HTTPError:
            if k == intentos - 1:
                raise
            time.sleep(espera)
            espera *= 2
    return {}


def _pedir_partiendo(token: str, tickers: list[str], precios: dict, rechazados: list,
                     errores: list, moneda: str | None = None, sufijo: str = "",
                     fecha: str | None = None) -> None:
    """Pide el grupo; si falla, lo parte en dos (menos pedidos que ir de a uno).

    Con moneda (mep/ccl) los tickers son base (AL30) y se guardan con sufijo (AL30D / AL30C).
    """
    try:
        res = _pedir_con_reintentos(token, tickers, moneda, fecha=fecha)
        precios.update({k + sufijo: v for k, v in res.items()})
    except requests.HTTPError as e:
        if len(tickers) == 1:
            rechazados.append(tickers[0].upper() + sufijo)
            errores.append(str(e))
            return
        mitad = len(tickers) // 2
        _pedir_partiendo(token, tickers[:mitad], precios, rechazados, errores, moneda, sufijo, fecha)
        _pedir_partiendo(token, tickers[mitad:], precios, rechazados, errores, moneda, sufijo, fecha)


def get_live_prices(tickers: list[str], fecha: str | None = None) -> dict[str, float]:
    """Devuelve {TICKER: precioDirty}. Lanza excepción solo si no trae ningún precio.

    fecha (AAAA-MM-DD, opcional): precios de ese día. get_live_prices.fechas_respuesta
    trae las fechaOperacion que informó 1816, para verificar que respetó el pedido.

    Los tickers que la API rechazó quedan en get_live_prices.rechazados y el
    mensaje de error de 1816 en get_live_prices.errores.
    """
    api_key = get_api_key()
    if not api_key:
        raise API1816NoConfigurada("Falta API_1816_KEY (en st.secrets o variable de entorno).")
    _estado["fechas_respuesta"] = set()
    try:
        precios = _consultar(api_key, tickers, _token(api_key), fecha)
    except TokenVencido:
        precios = _consultar(api_key, tickers, _token(api_key, renovar=True), fecha)
    get_live_prices.fechas_respuesta = sorted(_estado["fechas_respuesta"])
    return precios


def _consultar(api_key: str, tickers: list[str], token: str,
               fecha: str | None = None) -> dict[str, float]:
    precios: dict[str, float] = {}
    rechazados: list[str] = []
    errores: list[str] = []
    # Bonos en dólares: AL30D → AL30 con moneda=mep; AL30C → AL30 con moneda=ccl
    def es_hd(t: str, letra: str) -> bool:
        t = t.strip().upper()
        return len(t) > 2 and t[-1] == letra and t[-2].isdigit()

    lotes = [
        ([t for t in tickers if not (es_hd(t, "D") or es_hd(t, "C"))], None, ""),
        ([t.strip()[:-1] for t in tickers if es_hd(t, "D")], "mep", "D"),
        ([t.strip()[:-1] for t in tickers if es_hd(t, "C")], "ccl", "C"),
    ]
    for lista, moneda, sufijo in lotes:
        for i in range(0, len(lista), TAMANO_GRUPO):
            _pedir_partiendo(token, lista[i:i + TAMANO_GRUPO], precios, rechazados, errores,
                             moneda, sufijo, fecha)

    get_live_prices.rechazados = rechazados
    get_live_prices.errores = list(dict.fromkeys(errores))[:3]  # mensajes distintos
    if not precios:
        raise ValueError(f"La API no devolvió precios. Error: {errores[:1]}")
    return precios


get_live_prices.rechazados = []
get_live_prices.errores = []
get_live_prices.fechas_respuesta = []
