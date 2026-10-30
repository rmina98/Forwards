"""Guarda el cierre del día de 1816 en precios_1816.csv.

Lo corre GitHub Actions todos los días hábiles después del cierre (ver
.github/workflows/cierre_diario.yml). También se puede correr a mano:

    set API_1816_KEY=...        (Windows)   /   export API_1816_KEY=...  (Mac/Linux)
    python guardar_cierre.py

Toma las especies del Excel (Lecaps, LECER y Flujo HD con sus D y C), pide los
precios a 1816 y agrega (o reemplaza) la fila de hoy en precios_1816.csv, con el
mismo formato que la hoja Precios: Fecha + una columna por ticker.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import forwards as fw
from api_1816 import get_live_prices

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ARCHIVO = Path(__file__).parent / fw.ARCHIVO_CIERRES


def tickers_del_excel() -> list[str]:
    lecaps = fw.load_lecaps()
    lecer = fw.load_lecer()
    hd = fw.bonos_hd(fw.load_flujos_hd())
    hoy = datetime.now(TZ).date()
    vivos = (lecaps[lecaps.vencimiento >= hoy].ticker.tolist()
             + lecer[lecer.vencimiento >= hoy].ticker.tolist())
    hd_vivos = hd[hd.vencimiento >= hoy].ticker.tolist()
    return vivos + [t + s for t in hd_vivos for s in ("D", "C")]


def main(forzar: bool = False) -> int:
    hoy = datetime.now(TZ).date()
    if not forzar and not fw.es_habil(hoy, fw.load_feriados()):
        print(f"{hoy}: no es día hábil, no se guarda nada.")
        return 0

    tickers = tickers_del_excel()
    precios = get_live_prices(tickers)
    rechazados = get_live_prices.rechazados
    print(f"{hoy}: {len(precios)} precios de {len(tickers)} tickers.")
    if rechazados:
        print("Sin precio (rechazados por 1816):", ", ".join(rechazados))

    fila = pd.DataFrame([precios], index=pd.Index([hoy], name="Fecha"))
    if ARCHIVO.exists():
        previo = pd.read_csv(ARCHIVO, parse_dates=["Fecha"])
        previo["Fecha"] = previo["Fecha"].dt.date
        previo = previo.set_index("Fecha")
        previo = previo.drop(index=hoy, errors="ignore")  # si se corre dos veces, queda la última
        tabla = pd.concat([previo, fila])
    else:
        tabla = fila
    tabla = tabla.sort_index()
    tabla = tabla[sorted(tabla.columns)]
    tabla.to_csv(ARCHIVO, float_format="%.6f")
    print(f"Guardado en {ARCHIVO.name}: {len(tabla)} días, {tabla.shape[1]} tickers.")
    return 0


if __name__ == "__main__":
    sys.exit(main(forzar="--forzar" in sys.argv))
