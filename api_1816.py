"""Precios en vivo desde la API de 1816.

👉 Pegá acá la misma función que usás en los dashboards de CER / TAMAR.
   Lo único que tiene que cumplir es la firma:

       get_live_prices(tickers: list[str]) -> dict[str, float]

   devolviendo {ticker: último precio}. Puede venir cada 1 VN o cada 100 VN:
   la app lo normaliza sola.

La clave se lee de st.secrets["API_1816_KEY"] (en Streamlit Cloud:
Settings → Secrets; en local: .streamlit/secrets.toml). Nunca la subas al repo.
"""
from __future__ import annotations

import streamlit as st


class API1816NoConfigurada(RuntimeError):
    pass


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    key = st.secrets.get("API_1816_KEY") if hasattr(st, "secrets") else None
    if not key:
        raise API1816NoConfigurada("Falta API_1816_KEY en los secrets.")

    # ------------------------------------------------------------------
    # TODO: reemplazar por la llamada real (la de las otras apps). Ejemplo:
    #
    # import requests
    # r = requests.get(URL, headers={"Authorization": f"Bearer {key}"},
    #                  params={"tickers": ",".join(tickers)}, timeout=10)
    # r.raise_for_status()
    # return {row["ticker"]: float(row["last"]) for row in r.json()}
    # ------------------------------------------------------------------
    raise API1816NoConfigurada("Falta implementar la llamada en api_1816.py.")
