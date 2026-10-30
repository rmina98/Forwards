"""Guarda cierres de 1816 en precios_1816.csv.

Uso normal (lo corre GitHub Actions todos los días hábiles a las 17:30):
    python guardar_cierre.py

Completar días que faltan (precios de fechas pasadas):
    python guardar_cierre.py --desde 2026-10-16
    python guardar_cierre.py --desde 2026-10-16 --hasta 2026-10-29

En GitHub: pestaña Actions → "Cierre diario 1816" → Run workflow, completando "desde".

Toma las especies del Excel (Lecaps, LECER y Flujo HD con sus D y C) y agrega (o reemplaza)
una fila por día en precios_1816.csv, con el mismo formato que la hoja Precios.
Para fechas pasadas verifica que 1816 devuelva precios de ESA fecha; si devuelve otra
(o sea, si la API no deja pedir históricos), se frena sin guardar nada.

La clave se lee de la variable de entorno API_1816_KEY.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import forwards as fw
from api_1816 import get_live_prices

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ARCHIVO = Path(__file__).parent / fw.ARCHIVO_CIERRES


def tickers_vivos(dia: date) -> list[str]:
    lecaps = fw.load_lecaps()
    lecer = fw.load_lecer()
    hd = fw.bonos_hd(fw.load_flujos_hd())
    vivos = (lecaps[lecaps.vencimiento >= dia].ticker.tolist()
             + lecer[lecer.vencimiento >= dia].ticker.tolist())
    hd_vivos = hd[hd.vencimiento >= dia].ticker.tolist()
    return vivos + [t + s for t in hd_vivos for s in ("D", "C")]


def leer_csv() -> pd.DataFrame:
    if not ARCHIVO.exists():
        return pd.DataFrame()
    df = pd.read_csv(ARCHIVO, parse_dates=["Fecha"])
    df["Fecha"] = df["Fecha"].dt.date
    return df.set_index("Fecha")


def guardar(filas: dict[date, dict[str, float]]) -> None:
    tabla = leer_csv()
    nuevas = pd.DataFrame.from_dict(filas, orient="index")
    nuevas.index.name = "Fecha"
    if not tabla.empty:
        tabla = tabla.drop(index=list(filas), errors="ignore")  # una fecha repetida se reemplaza
        tabla = pd.concat([tabla, nuevas])
    else:
        tabla = nuevas
    tabla = tabla.sort_index()
    tabla = tabla[sorted(tabla.columns)]
    tabla.to_csv(ARCHIVO, float_format="%.6f")
    print(f"Guardado en {ARCHIVO.name}: {len(tabla)} días, {tabla.shape[1]} tickers.")


def bajar(dia: date, historico: bool) -> dict[str, float]:
    tickers = tickers_vivos(dia)
    precios = get_live_prices(tickers, fecha=dia.isoformat() if historico else None)
    if historico:
        devueltas = get_live_prices.fechas_respuesta
        if devueltas and devueltas != [dia.isoformat()]:
            raise SystemExit(
                f"ERROR: se pidieron precios del {dia} y 1816 devolvió fecha {', '.join(devueltas)}. "
                "Parece que la API no deja pedir fechas pasadas con 'fechaOperacion'. "
                "No se guardó nada.")
    rech = get_live_prices.rechazados
    print(f"{dia}: {len(precios)} precios de {len(tickers)} tickers"
          + (f" | sin precio: {', '.join(rech)}" if rech else ""))
    return precios


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="AAAA-MM-DD: completar días pasados desde esta fecha")
    ap.add_argument("--hasta", help="AAAA-MM-DD: hasta esta fecha (por defecto, ayer)")
    ap.add_argument("--forzar", action="store_true", help="guardar hoy aunque no sea hábil")
    a = ap.parse_args()

    hoy = datetime.now(TZ).date()
    feriados = fw.load_feriados()

    if a.desde:
        desde = date.fromisoformat(a.desde)
        hasta = date.fromisoformat(a.hasta) if a.hasta else hoy - timedelta(days=1)
        dias = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
        dias = [d for d in dias if fw.es_habil(d, feriados)]
        print(f"Completando {len(dias)} días hábiles: {dias[0]} a {dias[-1]}" if dias
              else "No hay días hábiles en ese rango.")
        filas = {}
        for d in dias:
            filas[d] = bajar(d, historico=True)
        if filas:
            guardar(filas)
        return 0

    if not a.forzar and not fw.es_habil(hoy, feriados):
        print(f"{hoy}: no es día hábil, no se guarda nada.")
        return 0
    guardar({hoy: bajar(hoy, historico=False)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
