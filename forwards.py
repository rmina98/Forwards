"""Cálculos de TIR y tasas forward implícitas entre Lecaps (tasa fija)."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable
from pathlib import Path

import numpy as np
import pandas as pd

CARPETA = Path(__file__).parent
NOMBRES_EXCEL = ("Inputs FW.xlsx", "Inputs_FW.xlsx", "inputs FW.xlsx", "INPUTS FW.xlsx")


# ---------------------------------------------------------------- datos base
def ruta_inputs() -> Path:
    """Excel único con toda la info (Linux distingue mayúsculas, por eso las variantes)."""
    for nombre in NOMBRES_EXCEL:
        p = CARPETA / nombre
        if p.exists():
            return p
    raise FileNotFoundError(f"No encuentro el Excel de inputs ({NOMBRES_EXCEL[0]}) junto a app.py")


def _hoja(nombre: str) -> pd.DataFrame:
    df = pd.read_excel(ruta_inputs(), sheet_name=nombre)
    df.columns = [str(c).strip() for c in df.columns]
    return df.loc[:, ~df.columns.str.startswith("Unnamed")]


def load_lecaps() -> pd.DataFrame:
    """Hoja 'Lecaps': Ticker | Emisión | Vencimiento | Tasa (TEM) | [Valor final].

    'Valor final' (opcional, cada 100 VN): si está cargado se usa ese pago;
    si está vacío se calcula con la TEM y los días 30/360.
    """
    raw = _hoja("Lecaps")
    df = raw.iloc[:, :4].copy()
    df.columns = ["ticker", "emision", "vencimiento", "tem"]
    col_vf = next((c for c in raw.columns if "FINAL" in c.upper()), None)
    df["vf_manual"] = pd.to_numeric(raw[col_vf], errors="coerce") if col_vf else float("nan")
    df = df.dropna(subset=["ticker", "vencimiento"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["emision"] = pd.to_datetime(df["emision"]).dt.date
    df["vencimiento"] = pd.to_datetime(df["vencimiento"]).dt.date
    df["tem"] = pd.to_numeric(df["tem"])
    df["vf_calc"] = [valor_final(e, v, t) for e, v, t in zip(df.emision, df.vencimiento, df.tem)]
    # Acepta el valor final cada 1 VN (1,1168) o cada 100 (111,68)
    vm = df["vf_manual"].where(df["vf_manual"].isna() | (df["vf_manual"] >= 10), df["vf_manual"] * 100)
    df["vf"] = vm.fillna(df["vf_calc"])
    return df.drop(columns="vf_manual").sort_values("vencimiento").reset_index(drop=True)


def load_lecer() -> pd.DataFrame:
    """Hoja 'LECER': Ticker | Emisión | Vencimiento | Tasa | CER inicial.

    vf_base = pago al vencimiento por unidad de CER: 100·(1+Tasa)/CER inicial.
    El pago en pesos a una fecha es vf_base × CER de referencia.
    """
    raw = _hoja("LECER")
    df = raw.iloc[:, :5].copy()
    df.columns = ["ticker", "emision", "vencimiento", "tasa", "cer_ini"]
    df = df.dropna(subset=["ticker", "vencimiento", "cer_ini"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df.drop_duplicates("ticker")
    df["emision"] = pd.to_datetime(df["emision"]).dt.date
    df["vencimiento"] = pd.to_datetime(df["vencimiento"]).dt.date
    df["tasa"] = pd.to_numeric(df["tasa"]).fillna(0.0)
    df["cer_ini"] = pd.to_numeric(df["cer_ini"])
    df["vf_base"] = 100 * (1 + df["tasa"]) / df["cer_ini"]
    return df.sort_values("vencimiento").reset_index(drop=True)


def load_cer() -> pd.Series:
    """Hoja 'CER': Fecha | CER. Descarta la cola de valores constantes (proyección)."""
    df = _hoja("CER").iloc[:, :2]
    df.columns = ["fecha", "cer"]
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["cer"] = pd.to_numeric(df["cer"], errors="coerce")
    df = df.dropna().sort_values("fecha")
    s = pd.Series(df["cer"].to_numpy(), index=df["fecha"].dt.date)
    cambia = s.diff().abs() > 0
    if cambia.any():
        s = s.loc[: s.index[cambia.to_numpy()][-1]]
    return s


def cer_referencia(liquidacion: date, cer: pd.Series, feriados: set[date],
                   rezago: int = 10) -> tuple[date, float]:
    """CER de referencia para una liquidación: el de 10 hábiles antes (como en app_BE)."""
    f_ref = restar_habiles(liquidacion, rezago, feriados)
    d = cer.loc[:f_ref]
    if d.empty:
        return f_ref, float("nan")
    return f_ref, float(d.iloc[-1])


def lecer_a_fecha(lecer: pd.DataFrame, cer_ref: float) -> pd.DataFrame:
    """Copia de la hoja LECER con 'vf' en pesos usando el CER de referencia."""
    df = lecer.copy()
    df["vf"] = df["vf_base"] * cer_ref
    return df


def load_feriados() -> set[date]:
    """Hoja 'Feriados': una columna de fechas."""
    s = pd.to_datetime(_hoja("Feriados").iloc[:, 0], errors="coerce").dropna()
    return set(s.dt.date)


def load_precios(tickers: list[str] | None = None) -> pd.DataFrame:
    """Hoja 'Precios': Fecha | un ticker por columna. Normalizado a base 100 de VN."""
    df = _hoja("Precios")
    df = df.rename(columns={df.columns[0]: "fecha"})
    df.columns = ["fecha"] + [c.upper() for c in df.columns[1:]]
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["fecha"] = df["fecha"].dt.date
    df = df.set_index("fecha")
    # Tickers repetidos en la hoja (pandas los renombra X29E7.1): se combinan
    df.columns = [re.sub(r"\.\d+$", "", c) for c in df.columns]
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.T.groupby(level=0, sort=False).first().T
    if tickers is not None:
        df = df.reindex(columns=tickers)
    df = df.apply(pd.to_numeric, errors="coerce")  # celdas con #N/A, #REF → vacío
    df = df.dropna(how="all")
    return normalizar_precios(df[~df.index.duplicated(keep="last")].sort_index())


def normalizar_precios(df: pd.DataFrame) -> pd.DataFrame:
    # En el Excel conviven precios cada 1 VN (1,26) y cada 100 VN (134,5).
    return df.apply(lambda s: s.where(s.isna() | (s >= 10), s * 100))


# ---------------------------------------------------------------- fechas
def dias360(d1: date, d2: date) -> int:
    """30/360 US (equivale a DAYS360 de Excel)."""
    a, b = min(d1.day, 30), d2.day
    if b == 31 and a == 30:
        b = 30
    return 360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + b - a


def es_habil(d: date, feriados: set[date]) -> bool:
    return d.weekday() < 5 and d not in feriados


def sumar_habiles(d: date, n: int, feriados: set[date]) -> date:
    while n > 0:
        d += timedelta(days=1)
        if es_habil(d, feriados):
            n -= 1
    return d


def restar_habiles(d: date, n: int, feriados: set[date]) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if es_habil(d, feriados):
            n -= 1
    return d


def valor_final(emision: date, vencimiento: date, tem: float) -> float:
    """Pago al vencimiento cada 100 VN: capitaliza la TEM por meses 30/360."""
    return 100 * (1 + tem) ** (dias360(emision, vencimiento) / 30)


# ---------------------------------------------------------------- tasas
def convertir(factor: np.ndarray | float, dias: np.ndarray | float, tipo: str):
    """Pasa un factor de capitalización a TEM / TNA / TEA."""
    with np.errstate(invalid="ignore", divide="ignore"):
        tem = np.power(factor, 30 / dias) - 1
        if tipo == "TEM":
            return tem
        if tipo == "TNA":
            return tem * 12  # TNA convención mercado: TEM x 12 (base 30/360)
        if tipo == "TEA":
            return np.power(factor, 365 / dias) - 1
    raise ValueError(tipo)


def tabla_forward(precios: pd.Series, lecaps: pd.DataFrame, liquidacion: date,
                  tipo: str = "TEM") -> pd.DataFrame:
    """Matriz triangular: diagonal = TIR de cada Lecap; (i, j) = forward de i a j."""
    lk = lecaps[lecaps.vencimiento > liquidacion].copy()
    lk["precio"] = lk.ticker.map(precios)
    lk = lk.dropna(subset=["precio"])
    lk["dias"] = [(v - liquidacion).days for v in lk.vencimiento]
    lk["factor"] = lk.vf / lk.precio

    t = lk.ticker.tolist()
    f, d = lk.factor.to_numpy(), lk.dias.to_numpy()
    m = pd.DataFrame(np.nan, index=t, columns=t)
    for i in range(len(t)):
        m.iat[i, i] = convertir(f[i], d[i], tipo)
        for j in range(i + 1, len(t)):
            if d[j] > d[i]:
                m.iat[i, j] = convertir(f[j] / f[i], d[j] - d[i], tipo)
    return m


def resumen(precios: pd.Series, lecaps: pd.DataFrame, liquidacion: date) -> pd.DataFrame:
    lk = lecaps[lecaps.vencimiento > liquidacion].copy()
    lk["precio"] = lk.ticker.map(precios)
    lk = lk.dropna(subset=["precio"])
    lk["dias"] = [(v - liquidacion).days for v in lk.vencimiento]
    fac = lk.vf / lk.precio
    lk["TEM"] = convertir(fac, lk.dias, "TEM")
    lk["TNA"] = lk.TEM * 12
    lk["TEA"] = convertir(fac, lk.dias, "TEA")
    return lk[["ticker", "vencimiento", "dias", "precio", "vf", "TEM", "TNA", "TEA"]]


def forward_historica(hist: pd.DataFrame, lecaps: pd.DataFrame, corto: str, largo: str,
                      feriados: set[date], plazo_liq: int = 1, tipo: str = "TEM",
                      coef: Callable[[date], float] | None = None) -> pd.DataFrame:
    """Serie diaria de la forward entre `corto` y `largo` + la TIR de cada uno.

    coef(liquidación) multiplica la columna 'vf' (para CER: vf_base × CER de referencia).
    Para Lecaps no se pasa y 'vf' es el pago final fijo.
    """
    if coef is not None:
        lecaps = lecaps.assign(vf=lecaps["vf_base"])
    a = lecaps.set_index("ticker").loc[corto]
    b = lecaps.set_index("ticker").loc[largo]
    if b.vencimiento <= a.vencimiento:
        a, b = b, a
        corto, largo = largo, corto
    df = hist[[corto, largo]].dropna().copy()
    liq = [sumar_habiles(f, plazo_liq, feriados) for f in df.index]
    da = np.array([(a.vencimiento - l).days for l in liq], dtype=float)
    db = np.array([(b.vencimiento - l).days for l in liq], dtype=float)
    k = np.array([coef(l) for l in liq], dtype=float) if coef is not None else 1.0
    ok = da > 0
    fa = a.vf * k / df[corto].to_numpy()
    fb = b.vf * k / df[largo].to_numpy()
    out = pd.DataFrame({
        f"TIR {corto}": convertir(fa, da, tipo),
        f"TIR {largo}": convertir(fb, db, tipo),
        "Forward": convertir(fb / fa, db - da, tipo),
    }, index=pd.to_datetime(df.index))
    return out[ok]
