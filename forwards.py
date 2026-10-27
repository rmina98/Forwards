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
    df = df.where(df > 0)  # precios en 0 → vacío
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


# ================================================================ HARD DOLLAR
LEY_NY = ("GD",)  # Globales; el resto (AL, AE, AN, AO) se toma como ley argentina


def load_flujos_hd() -> pd.DataFrame:
    """Hoja 'Flujo HD': Ticker | Fecha efectiva de pago | Amortizacion | Valor residual |
    Tasa de interés | Interés (vn). Montos como fracción del VN original.

    Devuelve flujos cada 100 VN original: interes, amort y total.
    """
    raw = _hoja("Flujo HD").iloc[:, :6]
    raw.columns = ["ticker", "fecha", "amort", "residual", "tasa", "interes"]
    df = raw.dropna(subset=["ticker", "fecha"]).copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    for c in ["amort", "residual", "tasa", "interes"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)  # hay ceros cargados como texto
    df["amort"] *= 100
    df["interes"] *= 100
    df["residual"] *= 100
    df["flujo"] = df["amort"] + df["interes"]
    return df.dropna(subset=["fecha"]).sort_values(["ticker", "fecha"]).reset_index(drop=True)


def bonos_hd(flujos: pd.DataFrame) -> pd.DataFrame:
    g = flujos.groupby("ticker")
    df = pd.DataFrame({
        "vencimiento": g["fecha"].max(),
        "amort_total": g["amort"].sum(),
    }).reset_index()
    df["ley"] = np.where(df.ticker.str.startswith(LEY_NY), "NY", "ARG")
    return df.sort_values("vencimiento").reset_index(drop=True)


def _tir(precio: float, t: np.ndarray, cf: np.ndarray) -> float:
    """TIR efectiva anual (act/365) con precio sucio. Newton con resguardo."""
    if not (precio > 0) or len(cf) == 0:
        return float("nan")
    y = 0.08
    for _ in range(100):
        v = (1 + y) ** -t
        f = (cf * v).sum() - precio
        d = -(t * cf * v / (1 + y)).sum()
        if d == 0:
            break
        paso = f / d
        y_nuevo = y - paso
        if y_nuevo <= -0.99:
            y_nuevo = (y - 0.99) / 2
        if abs(y_nuevo - y) < 1e-11:
            return y_nuevo
        y = y_nuevo
    return y if abs(f) < 1e-6 else float("nan")


def metricas_hd(precios: pd.Series, flujos: pd.DataFrame, liquidacion: date,
                sufijo: str) -> pd.DataFrame:
    """TIR y duration (Macaulay y modificada) de cada bono con su precio `ticker+sufijo`."""
    filas = []
    for tk, f in flujos.groupby("ticker"):
        fut = f[f.fecha > liquidacion]
        if fut.empty:
            continue
        p = precios.get(tk + sufijo, np.nan)
        t = np.array([(d - liquidacion).days / 365 for d in fut.fecha])
        cf = fut.flujo.to_numpy()
        y = _tir(p, t, cf)
        if np.isnan(y):
            dur = np.nan
        else:
            v = cf * (1 + y) ** -t
            dur = (t * v).sum() / v.sum()
        residual = f[f.fecha <= liquidacion].residual.iloc[-1] if (f.fecha <= liquidacion).any() else 100.0
        filas.append({"ticker": tk, "vencimiento": fut.fecha.iloc[-1], "precio": p,
                      "residual": residual, "TIR": y, "duration": dur,
                      "dur_mod": dur / (1 + y) if not np.isnan(y) else np.nan})
    return pd.DataFrame(filas)


def tasa_hd(y, tipo: str):
    """Convierte TIR efectiva anual a TEM / TNA / TEA (mismo criterio que el resto)."""
    y = np.asarray(y, dtype=float)
    if tipo == "TEA":
        return y
    tem = (1 + y) ** (1 / 12) - 1
    return tem if tipo == "TEM" else tem * 12


def forward_hd(y_a, d_a, y_b, d_b, min_dif: float = 0.25):
    """Forward efectiva anual entre dos bonos por TIR y duration de Macaulay (años)."""
    y_a, d_a, y_b, d_b = (np.asarray(x, dtype=float) for x in (y_a, d_a, y_b, d_b))
    dd = d_b - d_a
    with np.errstate(invalid="ignore", divide="ignore"):
        f = ((1 + y_b) ** d_b / (1 + y_a) ** d_a) ** (1 / dd) - 1
    return np.where(dd > min_dif, f, np.nan)


def tabla_forward_hd(met: pd.DataFrame, tipo: str = "TEA") -> pd.DataFrame:
    """Matriz ordenada por duration: diagonal = TIR; (i, j) = forward entre durations."""
    m0 = met.dropna(subset=["TIR", "duration"]).sort_values("duration")
    t = m0.ticker.tolist()
    y, d = m0.TIR.to_numpy(), m0.duration.to_numpy()
    m = pd.DataFrame(np.nan, index=t, columns=t)
    for i in range(len(t)):
        m.iat[i, i] = float(tasa_hd(y[i], tipo))
        for j in range(i + 1, len(t)):
            m.iat[i, j] = float(tasa_hd(forward_hd(y[i], d[i], y[j], d[j]), tipo))
    return m


def forward_historica_hd(hist: pd.DataFrame, flujos: pd.DataFrame, a: str, b: str, sufijo: str,
                         feriados: set[date], plazo_liq: int = 1, tipo: str = "TEA") -> pd.DataFrame:
    cols = [a + sufijo, b + sufijo]
    df = hist.reindex(columns=cols).dropna()
    filas = []
    for f, row in df.iterrows():
        liq = sumar_habiles(f, plazo_liq, feriados)
        m = metricas_hd(row, flujos[flujos.ticker.isin([a, b])], liq, sufijo).set_index("ticker")
        if a not in m.index or b not in m.index:
            continue
        ra, rb = m.loc[a], m.loc[b]
        if ra.duration > rb.duration:
            ra, rb = rb, ra
        filas.append({"fecha": pd.Timestamp(f),
                      f"TIR {ra.name}": ra.TIR, f"TIR {rb.name}": rb.TIR,
                      "Forward": float(forward_hd(ra.TIR, ra.duration, rb.TIR, rb.duration)),
                      "_dur": (ra.name, rb.name)})
    if not filas:
        return pd.DataFrame()
    out = pd.DataFrame(filas).set_index("fecha")
    # el orden corto/largo es por duration; se fija el del último día
    corto, largo = out["_dur"].iloc[-1]
    out = out.drop(columns="_dur")
    out = out[[f"TIR {corto}", f"TIR {largo}", "Forward"]] if f"TIR {corto}" in out else out
    return out.apply(lambda s: tasa_hd(s, tipo))


def canje(precios: pd.Series | pd.DataFrame, tickers: list[str]):
    """Canje MEP/cable en %: (precio D / precio C − 1) × 100."""
    if isinstance(precios, pd.Series):
        return pd.Series({t: (precios.get(t + "D", np.nan) / precios.get(t + "C", np.nan) - 1) * 100
                          for t in tickers})
    return pd.DataFrame({t: (precios.get(t + "D") / precios.get(t + "C") - 1) * 100
                         for t in tickers if t + "D" in precios and t + "C" in precios})


# ---------------------------------------------------------------- CER ya conocido
def proximo_habil(d: date, feriados: set[date]) -> date:
    while not es_habil(d, feriados):
        d += timedelta(days=1)
    return d


def ref_cer_vto(vencimiento: date, feriados: set[date], rezago: int = 10) -> date:
    """Fecha del CER que define el pago: 10 hábiles antes del día de cobro (como app_BE)."""
    return restar_habiles(proximo_habil(vencimiento, feriados), rezago, feriados)


def limite_cer_publicado(fecha_op: date, cer: pd.Series) -> date:
    """Hasta qué fecha se conoce el CER en fecha_op: el IPC se publica ~día 15, así que el CER
    se conoce hasta el 15 (de este mes o del próximo). Tope: último dato cargado en la hoja."""
    if fecha_op.day < 15:
        lim = date(fecha_op.year, fecha_op.month, 15)
    else:
        lim = date(fecha_op.year + 1, 1, 15) if fecha_op.month == 12 else \
            date(fecha_op.year, fecha_op.month + 1, 15)
    return min(lim, cer.index[-1])


def cer_ya_fijo(lecer: pd.DataFrame, fecha_op: date, cer: pd.Series,
                feriados: set[date]) -> list[str]:
    """Tickers cuyo CER al vencimiento ya se conoce en fecha_op (pasaron a ser tasa fija)."""
    lim = limite_cer_publicado(fecha_op, cer)
    return [t for t, v in zip(lecer.ticker, lecer.vencimiento) if ref_cer_vto(v, feriados) <= lim]
