"""Precios en vivo desde la API de 1816 (misma lógica que app_BE.py).

La clave se lee de st.secrets["API_1816_KEY"] (local: .streamlit/secrets.toml;
Streamlit Cloud: Settings → Secrets) o de la variable de entorno API_1816_KEY.
Nunca la subas al repo.

Los tickers se piden en grupos. Si un grupo falla (p. ej. la API no reconoce algún
ticker), se reintenta de a uno y se saltean los que dan error.
"""
from __future__ import annotations

import os

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


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    """Devuelve {TICKER: precioDirty}. Lanza excepción solo si no trae ningún precio.

    Los tickers que la API rechazó quedan en get_live_prices.rechazados (lista).
    """
    api_key = get_api_key()
    if not api_key:
        raise API1816NoConfigurada("Falta API_1816_KEY (en st.secrets o variable de entorno).")
    token = _token(api_key)

    precios: dict[str, float] = {}
    rechazados: list[str] = []
    ultimo_error = None
    for i in range(0, len(tickers), TAMANO_GRUPO):
        grupo = tickers[i:i + TAMANO_GRUPO]
        try:
            precios.update(_pedir(token, grupo))
        except requests.HTTPError as e:
            ultimo_error = e
            for t in grupo:  # de a uno, para aislar el ticker problemático
                try:
                    precios.update(_pedir(token, [t]))
                except requests.HTTPError as e1:
                    ultimo_error = e1
                    rechazados.append(t.upper())

    get_live_prices.rechazados = rechazados
    if not precios:
        raise ValueError(f"La API no devolvió precios. Último error: {ultimo_error}")
    return precios


get_live_prices.rechazados = []
