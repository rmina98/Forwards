"""Precios en vivo desde la API de 1816 (misma lógica que app_BE.py).

La clave se lee de st.secrets["API_1816_KEY"] (local: .streamlit/secrets.toml;
Streamlit Cloud: Settings → Secrets) o de la variable de entorno API_1816_KEY.
Nunca la subas al repo.

Los tickers se piden en grupos. Si un grupo falla (p. ej. la API no reconoce algún
ticker o límite de pedidos), se reintenta con pausas y partiendo el grupo.
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


def _token(api_key: str) -> str:
    auth = requests.post(URL_AUTH, json={"apiKey": api_key, "module": "mercado"}, timeout=10)
    auth.raise_for_status()
    return auth.json()["token"]


def _pedir(token: str, tickers: list[str]) -> dict[str, float]:
    params = {"tickers": [t.strip().lower() for t in tickers], "campos": ["precioDirty"]}
    r = requests.get(URL_INDICADORES, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=15)
    if r.status_code >= 400:
        raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}", response=r)
    data = r.json()
    precios = {}
    if isinstance(data, dict) and "instrumentos" in data:
        for ticker, valores in data["instrumentos"].items():
            if isinstance(valores, dict) and valores.get("precioDirty") is not None:
                precios[ticker.strip().upper()] = float(valores["precioDirty"])
    return precios


def _pedir_con_reintentos(token: str, tickers: list[str], intentos: int = 3) -> dict[str, float]:
    espera = 0.6
    for k in range(intentos):
        try:
            return _pedir(token, tickers)
        except requests.HTTPError:
            if k == intentos - 1:
                raise
            time.sleep(espera)
            espera *= 2
    return {}


def _pedir_partiendo(token: str, tickers: list[str], precios: dict, rechazados: list,
                     errores: list) -> None:
    """Pide el grupo; si falla, lo parte en dos (menos pedidos que ir de a uno)."""
    try:
        precios.update(_pedir_con_reintentos(token, tickers))
    except requests.HTTPError as e:
        if len(tickers) == 1:
            rechazados.append(tickers[0].upper())
            errores.append(str(e))
            return
        mitad = len(tickers) // 2
        _pedir_partiendo(token, tickers[:mitad], precios, rechazados, errores)
        _pedir_partiendo(token, tickers[mitad:], precios, rechazados, errores)


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    """Devuelve {TICKER: precioDirty}. Lanza excepción solo si no trae ningún precio.

    Los tickers que la API rechazó quedan en get_live_prices.rechazados y el
    mensaje de error de 1816 en get_live_prices.errores.
    """
    api_key = get_api_key()
    if not api_key:
        raise API1816NoConfigurada("Falta API_1816_KEY (en st.secrets o variable de entorno).")
    token = _token(api_key)

    precios: dict[str, float] = {}
    rechazados: list[str] = []
    errores: list[str] = []
    for i in range(0, len(tickers), TAMANO_GRUPO):
        _pedir_partiendo(token, tickers[i:i + TAMANO_GRUPO], precios, rechazados, errores)
        time.sleep(0.3)

    get_live_prices.rechazados = rechazados
    get_live_prices.errores = list(dict.fromkeys(errores))[:3]  # mensajes distintos
    if not precios:
        raise ValueError(f"La API no devolvió precios. Error: {errores[:1]}")
    return precios


get_live_prices.rechazados = []
get_live_prices.errores = []
