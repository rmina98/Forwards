"""Precios en vivo desde la API de 1816 (misma lógica que app_BE.py).

La clave se lee de st.secrets["API_1816_KEY"] (local: .streamlit/secrets.toml;
Streamlit Cloud: Settings → Secrets) o de la variable de entorno API_1816_KEY.
Nunca la subas al repo.
"""
from __future__ import annotations

import os

import requests
import streamlit as st

URL_AUTH = "https://api.1816.com.ar/v1/auth/token"
URL_INDICADORES = "https://api.1816.com.ar/v1/mercado/indicadores"


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


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    """Devuelve {TICKER: precioDirty}. Lanza excepción si algo falla."""
    api_key = get_api_key()
    if not api_key:
        raise API1816NoConfigurada("Falta API_1816_KEY (en st.secrets o variable de entorno).")

    auth = requests.post(URL_AUTH, json={"apiKey": api_key, "module": "mercado"}, timeout=10)
    auth.raise_for_status()
    token = auth.json()["token"]

    params = {"tickers": [t.strip().lower() for t in tickers], "campos": ["precioDirty"]}
    r = requests.get(URL_INDICADORES, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    precios = {}
    if isinstance(data, dict) and "instrumentos" in data:
        for ticker, valores in data["instrumentos"].items():
            if isinstance(valores, dict) and valores.get("precioDirty") is not None:
                precios[ticker.strip().upper()] = float(valores["precioDirty"])
    if not precios:
        raise ValueError("La API respondió sin precioDirty para ningún ticker.")
    return precios