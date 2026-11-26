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
    df = normalizar_precios(df[~df.index.duplicated(keep="last")].sort_index())

    # Cierres guardados automáticamente desde 1816: completan lo que falte en el Excel
    cierres = load_cierres_1816()
    if not cierres.empty:
        if tickers is not None:
            cierres = cierres.reindex(columns=tickers)
        df = df.combine_first(cierres)  # si una fecha está en los dos, manda el Excel
        df = df.reindex(columns=tickers) if tickers is not None else df
        df = df.dropna(how="all").sort_index()
    return df


ARCHIVO_CIERRES = "precios_1816.csv"


def load_cierres_1816() -> pd.DataFrame:
    """precios_1816.csv (lo escribe guardar_cierre.py todos los días). Vacío si no existe."""
    p = CARPETA / ARCHIVO_CIERRES
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df = df.rename(columns={df.columns[0]: "fecha"})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["fecha"] = df["fecha"].dt.date
    df = df.set_index("fecha")
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.where(df > 0)
    return normalizar_precios(df.dropna(how="all"))


def normalizar_precios(df: pd.DataFrame) -> pd.DataFrame:
    """Lleva todo a base 100 VN. En el Excel conviven precios cada 100 VN (134,5),
    cada 1 VN (1,345) y hasta cada 0,01 VN (0,01345): se multiplica por 100 hasta pasar 10."""
    def fix(s: pd.Series) -> pd.Series:
        for _ in range(3):
            s = s.where(s.isna() | (s >= 10), s * 100)
        return s
    return df.apply(fix)


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


# ================================================================ CURVAS AJUSTADAS
MIN_PUNTOS_NS = 6

def _ns_base(x: np.ndarray, tau: float) -> np.ndarray:
    z = np.maximum(x, 1e-6) / tau
    f1 = (1 - np.exp(-z)) / z
    return np.column_stack([np.ones_like(z), f1, f1 - np.exp(-z)])


def ajustar_curva(x, y, modelo: str = "Nelson-Siegel", pesos=None):
    """Ajusta TIR (y) contra duration en años (x).

    Nelson-Siegel: y = b0 + b1·(1−e^(−x/τ))/(x/τ) + b2·[(1−e^(−x/τ))/(x/τ) − e^(−x/τ)].
    Para cada τ de una grilla los b son mínimos cuadrados; se queda el τ de menor error.
    Logarítmica: y = a + b·ln(x). Con menos de MIN_PUNTOS_NS puntos, Nelson-Siegel pasa a
    logarítmica (con 4 parámetros y pocos bonos la curva pasa por todos y el rico/barato da 0).

    Devuelve (función x → y, descripción del ajuste).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y) & (x > 0)
    x, y = x[ok], y[ok]
    w = np.ones_like(x) if pesos is None else np.asarray(pesos, dtype=float)[ok]
    if len(x) < 2:
        return None, "Pocos puntos para ajustar"
    sw = np.sqrt(w)

    if modelo == "Nelson-Siegel" and len(x) >= MIN_PUNTOS_NS:
        mejor = None
        for tau in np.geomspace(0.02, 15, 160):
            X = _ns_base(x, tau)
            b, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
            sse = float((w * (X @ b - y) ** 2).sum())
            if mejor is None or sse < mejor[0]:
                mejor = (sse, tau, b)
        _, tau, b = mejor
        return (lambda xx: _ns_base(np.atleast_1d(np.asarray(xx, dtype=float)), tau) @ b,
                f"Nelson-Siegel (τ = {tau:.2f} años)")

    X = np.column_stack([np.ones_like(x), np.log(x)])
    b, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    nota = "Logarítmica" + (" (pocos puntos para Nelson-Siegel)" if modelo == "Nelson-Siegel" else "")
    return (lambda xx: b[0] + b[1] * np.log(np.maximum(np.atleast_1d(np.asarray(xx, dtype=float)),
                                                        1e-6)), nota)


def puntos_cero_cupon(precios: pd.Series, bonos: pd.DataFrame, liquidacion: date) -> pd.DataFrame:
    """Lecaps / LECER: duration = plazo en años (son cupón cero). TIR efectiva anual."""
    r = resumen(precios, bonos, liquidacion)
    return pd.DataFrame({"ticker": r.ticker, "vencimiento": r.vencimiento,
                         "duration": r.dias / 365, "TIR": r.TEA, "precio": r.precio})


def puntos_hd(precios: pd.Series, flujos: pd.DataFrame, liquidacion: date, sufijo: str,
              tickers: list[str] | None = None) -> pd.DataFrame:
    f = flujos if tickers is None else flujos[flujos.ticker.isin(tickers)]
    m = metricas_hd(precios, f, liquidacion, sufijo).dropna(subset=["TIR", "duration"])
    return m[["ticker", "vencimiento", "duration", "TIR", "precio"]].reset_index(drop=True)


# ================================================================ LETAM (TAMAR + margen)
def load_letam() -> pd.DataFrame:
    """Hoja 'Letam': Ticker | Emisión | Vencimiento | Tasa (margen sobre TAMAR, TNA)."""
    raw = _hoja("Letam")
    df = raw.iloc[:, :4].copy()
    df.columns = ["ticker", "emision", "vencimiento", "margen"]
    df = df.dropna(subset=["ticker", "emision", "vencimiento"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df.drop_duplicates("ticker")
    df["emision"] = pd.to_datetime(df["emision"]).dt.date
    df["vencimiento"] = pd.to_datetime(df["vencimiento"]).dt.date
    df["margen"] = pd.to_numeric(df["margen"], errors="coerce").fillna(0.0)
    return df.sort_values("vencimiento").reset_index(drop=True)


def load_tamar() -> pd.Series:
    """Hoja 'Tamar': Fecha | TAMAR en % TNA (24,125). Devuelve decimales (0,24125)."""
    df = _hoja("Tamar").iloc[:, :2]
    df.columns = ["fecha", "tamar"]
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["tamar"] = pd.to_numeric(df["tamar"], errors="coerce")
    df = df.dropna()
    df = df[df["tamar"] > 0].sort_values("fecha")
    s = pd.Series(df["tamar"].to_numpy(), index=df["fecha"].dt.date)
    s = s[~s.index.duplicated(keep="last")]
    return s / 100 if s.median() > 1 else s


def tamar_al(fecha: date, tamar: pd.Series) -> float:
    """Última TAMAR publicada hasta esa fecha (inclusive)."""
    d = tamar.loc[:fecha]
    return float(d.iloc[-1]) if not d.empty else float("nan")


def tem_tamar(tna: float) -> float:
    """TNA (TAMAR promedio + margen) → TEM capitalizable: [(1 + TNA·32/365)^(365/32)]^(1/12) − 1."""
    return (1 + tna * 32 / 365) ** (365 / 32 / 12) - 1


def tamar_promedio(emision: date, vencimiento: date, fecha_op: date, tamar: pd.Series,
                   proyectada, feriados: set[date], rezago: int = 10) -> tuple[float, float]:
    """Promedio simple de la TAMAR en la ventana [emisión − 10 hábiles, cobro − 10 hábiles].

    Los días con TAMAR publicada hasta fecha_op usan el dato real; los que faltan, la proyectada:
    un número (TAMAR constante) o una función fecha → TAMAR (curva esperada, p. ej. el REM).
    Devuelve (promedio, fracción de la ventana ya conocida).
    """
    ini = restar_habiles(proximo_habil(emision, feriados), rezago, feriados)
    fin = restar_habiles(proximo_habil(vencimiento, feriados), rezago, feriados)
    corte = min(fecha_op, tamar.index[-1]) if len(tamar) else fecha_op
    conocidos = tamar.loc[ini:min(fin, corte)]
    curva = proyectada if callable(proyectada) else None
    n_fut, suma_fut = 0, 0.0
    d = max(ini, corte + timedelta(days=1))
    while d <= fin:
        if es_habil(d, feriados):
            n_fut += 1
            suma_fut += curva(d) if curva else proyectada
        d += timedelta(days=1)
    n = len(conocidos) + n_fut
    if n == 0:
        return (curva(fin) if curva else proyectada), 0.0
    return (float(conocidos.sum()) + suma_fut) / n, len(conocidos) / n


def valor_final_letam(emision: date, vencimiento: date, margen: float, tamar_prom: float) -> float:
    """Pago al vencimiento cada 100 VN: capitaliza la TEM de (TAMAR promedio + margen)
    por meses 30/360 desde la emisión, igual que las Lecaps."""
    return 100 * (1 + tem_tamar(tamar_prom + margen)) ** (dias360(emision, vencimiento) / 30)


def letam_a_fecha(letam: pd.DataFrame, fecha_op: date, tamar: pd.Series, proyectada: float,
                  feriados: set[date]) -> pd.DataFrame:
    """Copia de la hoja Letam con 'vf' (pago final estimado en esa fecha) y la TAMAR usada."""
    df = letam.copy()
    proms = [tamar_promedio(e, v, fecha_op, tamar, proyectada, feriados)
             for e, v in zip(df.emision, df.vencimiento)]
    df["tamar_prom"] = [p for p, _ in proms]
    df["conocido"] = [c for _, c in proms]
    df["vf"] = [valor_final_letam(e, v, m, p)
                for e, v, m, p in zip(df.emision, df.vencimiento, df.margen, df.tamar_prom)]
    return df


def forward_historica_letam(hist: pd.DataFrame, letam: pd.DataFrame, corto: str, largo: str,
                            tamar: pd.Series, feriados: set[date], plazo_liq: int = 1,
                            tipo: str = "TEM", proyectada_hoy: float | None = None,
                            hoy: date | None = None) -> pd.DataFrame:
    """Serie diaria de la forward entre dos Letam. Cada día proyecta la TAMAR futura igual a la
    última publicada ese día (para hoy, proyectada_hoy si se pasa)."""
    a = letam.set_index("ticker").loc[corto]
    b = letam.set_index("ticker").loc[largo]
    if b.vencimiento <= a.vencimiento:
        a, b = b, a
        corto, largo = largo, corto
    df = hist.reindex(columns=[corto, largo]).dropna()
    filas = []
    for f, row in df.iterrows():
        liq = sumar_habiles(f, plazo_liq, feriados)
        da, db = (a.vencimiento - liq).days, (b.vencimiento - liq).days
        if da <= 0:
            continue
        proy = proyectada_hoy if (proyectada_hoy is not None and f == hoy) else tamar_al(f, tamar)
        pa, _ = tamar_promedio(a.emision, a.vencimiento, f, tamar, proy, feriados)
        pb, _ = tamar_promedio(b.emision, b.vencimiento, f, tamar, proy, feriados)
        fa = valor_final_letam(a.emision, a.vencimiento, a.margen, pa) / row[corto]
        fb = valor_final_letam(b.emision, b.vencimiento, b.margen, pb) / row[largo]
        filas.append({"fecha": pd.Timestamp(f),
                      f"TIR {corto}": float(convertir(fa, da, tipo)),
                      f"TIR {largo}": float(convertir(fb, db, tipo)),
                      "Forward": float(convertir(fb / fa, db - da, tipo))})
    if not filas:
        return pd.DataFrame()
    return pd.DataFrame(filas).set_index("fecha")


def margen_tamar(tea, tamar_proy):
    """Margen sobre TAMAR como en la planilla: 12·((1 + TEA)^(1/12) − 1) − TAMAR proyectada.
    TEA = TIR.NO.PER (base 365). Acepta escalares, arrays o Series (TAMAR por fecha)."""
    tea = np.asarray(tea, dtype=float) if not isinstance(tea, (pd.Series, pd.DataFrame)) else tea
    return ((1 + tea) ** (1 / 12) - 1) * 12 - tamar_proy


# ================================================================ DUALES CER / TAMAR
HOJA_DUALES = "Duales CER-TAMAR"


def load_duales() -> pd.DataFrame:
    """Hoja 'Duales CER-TAMAR': Ticker | Emisión | Vencimiento | Tasa CER | CER inicial | Margen tamar.

    Pagan el máximo entre la pata CER (100·(1+Tasa CER)·CER ref/CER inicial) y la pata TAMAR
    (TAMAR promedio + margen, capitalizable como las Letam). Vacío si la hoja no existe.
    """
    try:
        raw = _hoja(HOJA_DUALES)
    except ValueError:  # hoja inexistente
        return pd.DataFrame(columns=["ticker", "emision", "vencimiento", "tasa_cer", "cer_ini", "margen"])
    df = raw.iloc[:, :6].copy()
    df.columns = ["ticker", "emision", "vencimiento", "tasa_cer", "cer_ini", "margen"]
    df = df.dropna(subset=["ticker", "emision", "vencimiento"])
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df.drop_duplicates("ticker")
    df["emision"] = pd.to_datetime(df["emision"]).dt.date
    df["vencimiento"] = pd.to_datetime(df["vencimiento"]).dt.date
    for c in ["tasa_cer", "cer_ini", "margen"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["tasa_cer"] = df["tasa_cer"].fillna(0.0)
    df["margen"] = df["margen"].fillna(0.0)
    return df.sort_values("vencimiento").reset_index(drop=True)


def duales_como_cer(duales: pd.DataFrame) -> pd.DataFrame:
    """Pata CER del dual con el formato de la hoja LECER (para tablas, forwards y curva CER)."""
    df = duales.dropna(subset=["cer_ini"])[["ticker", "emision", "vencimiento", "tasa_cer", "cer_ini"]]
    df = df.rename(columns={"tasa_cer": "tasa"}).copy()
    df["vf_base"] = 100 * (1 + df["tasa"]) / df["cer_ini"]
    return df.reset_index(drop=True)


def duales_como_tamar(duales: pd.DataFrame) -> pd.DataFrame:
    """Pata TAMAR del dual con el formato de la hoja Letam (TAMAR + margen)."""
    return duales[["ticker", "emision", "vencimiento", "margen"]].copy().reset_index(drop=True)


# ================================================================ OPCIÓN TAMAR DE LOS DUALES
def opcion_duales(precios: pd.Series, fecha_op: date, lecer: pd.DataFrame, duales: pd.DataFrame,
                  cer: pd.Series, feriados: set[date], plazo_liq: int = 1,
                  modelo: str = "Nelson-Siegel", excluir: list[str] | None = None) -> tuple[pd.DataFrame, str]:
    """Valor implícito de la opción TAMAR de cada dual (sin modelo de opciones):

        opción = precio del dual − valor de su pata CER,

    donde la pata CER (pago CER al vencimiento) se descuenta a la TIR real que da, para ese plazo,
    la curva CER ajustada con los CER puros del día (sin los que ya conocen el CER del vencimiento).
    Devuelve (tabla, descripción del ajuste).
    """
    liq = sumar_habiles(fecha_op, plazo_liq, feriados) if plazo_liq else fecha_op
    _, cer_ref = cer_referencia(liq, cer, feriados)
    vivas = lecer[~lecer.ticker.isin(cer_ya_fijo(lecer, fecha_op, cer, feriados))]
    if excluir:
        vivas = vivas[~vivas.ticker.isin(excluir)]
    pts = puntos_cero_cupon(precios, lecer_a_fecha(vivas, cer_ref), liq).dropna(subset=["TIR"])
    fn, desc = ajustar_curva(pts.duration, pts.TIR, modelo)
    if fn is None:
        return pd.DataFrame(), desc
    plazo_max = float(pts.duration.max()) if len(pts) else 0.0

    d = lecer_a_fecha(duales_como_cer(duales), cer_ref)
    d = d[(d.vencimiento > liq) & (d.emision <= fecha_op)].copy()
    d["precio"] = d.ticker.map(precios)
    d = d.dropna(subset=["precio"])
    if d.empty:
        return pd.DataFrame(), desc
    d["plazo"] = [(v - liq).days / 365 for v in d.vencimiento]
    # Más allá del CER puro más largo la curva se extiende plana (no se extrapola la forma del ajuste)
    d["tir_curva"] = fn(np.minimum(d.plazo.to_numpy(), plazo_max))
    d["valor_cer"] = d.vf / (1 + d.tir_curva) ** d.plazo
    d["opcion"] = d.precio - d.valor_cer
    d["opcion_pct"] = d.opcion / d.precio
    d["tir_dual"] = (d.vf / d.precio) ** (1 / d.plazo) - 1
    d["resigna_pb"] = (d.tir_curva - d.tir_dual) * 1e4
    d["extrapolado"] = d.plazo > plazo_max + 1e-9
    cols = ["ticker", "vencimiento", "plazo", "precio", "vf", "tir_curva", "valor_cer", "opcion",
            "opcion_pct", "tir_dual", "resigna_pb", "extrapolado"]
    return d[cols].reset_index(drop=True), (f"{desc}. CER puro más largo: {plazo_max:.2f} años; "
                                            "para plazos mayores la curva se extiende plana")


def opcion_duales_historica(hist: pd.DataFrame, lecer: pd.DataFrame, duales: pd.DataFrame,
                            cer: pd.Series, feriados: set[date], plazo_liq: int = 1,
                            modelo: str = "Nelson-Siegel",
                            excluir: list[str] | None = None) -> pd.DataFrame:
    """Serie diaria del valor de la opción (en % del precio y en pesos) para cada dual."""
    desde = duales.emision.min() if len(duales) else None
    filas = []
    for f, row in hist.iterrows():
        if desde is None or f < desde or row.reindex(duales.ticker).isna().all():
            continue
        t, _ = opcion_duales(row, f, lecer, duales, cer, feriados, plazo_liq, modelo, excluir)
        for _, r in t.iterrows():
            filas.append({"fecha": pd.Timestamp(f), "ticker": r.ticker, "opcion_pct": r.opcion_pct,
                          "opcion": r.opcion, "resigna_pb": r.resigna_pb, "extrapolado": r.extrapolado})
    return pd.DataFrame(filas)



# ================================================================ REM y ZDM de las Letam
PATRON_REM = "relevamiento-expectativas-mercado-tablas-*.xlsx"


def archivo_rem() -> Path | None:
    """El REM más nuevo del repo (por nombre: ...-AAAA-MM.xlsx)."""
    archivos = sorted(CARPETA.glob(PATRON_REM))
    return archivos[-1] if archivos else None


def load_rem_tamar(path: Path | None = None) -> tuple[pd.Series, str]:
    """Mediana de la TAMAR esperada por mes del REM (bloque 'Tasa de interés (TAMAR)' de la hoja
    'Cuadros de resultados'). Solo las filas mensuales; devuelve (Series Period[M] → TNA decimal, mes del REM)."""
    path = path or archivo_rem()
    if path is None:
        return pd.Series(dtype=float), ""
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # imágenes WMF del archivo del BCRA
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = next(w for w in wb.worksheets if "cuadro" in w.title.lower())
    filas = list(ws.iter_rows(values_only=True))
    wb.close()
    titulo = next((str(c) for r in filas[:4] for c in r if isinstance(c, str) and "REM" in c), "")
    i0 = next(i for i, r in enumerate(filas) if any(isinstance(c, str) and "TAMAR" in c for c in r))
    enc = filas[i0 + 1]
    col_per = next(j for j, c in enumerate(enc) if isinstance(c, str) and c.strip().lower() == "período")
    col_med = next(j for j, c in enumerate(enc) if isinstance(c, str) and c.strip().lower() == "mediana")
    datos = {}
    for r in filas[i0 + 2:]:
        per = r[col_per]
        if per is None or (isinstance(per, str) and not per.strip()):
            break
        if hasattr(per, "year") and isinstance(r[col_med], (int, float)):  # solo filas mensuales
            datos[pd.Period(per, "M")] = float(r[col_med]) / 100
    return pd.Series(datos).sort_index(), titulo


def curva_tamar_esperada(rem: pd.Series, manual: dict | None = None):
    """Función fecha → TAMAR esperada (TNA). Por mes: el valor manual si lo hay, si no el del REM;
    para meses posteriores se mantiene el último valor definido; antes del primero, el primero."""
    meses = dict(rem.items())
    if manual:
        meses.update({pd.Period(k, "M"): v for k, v in manual.items() if v is not None and not pd.isna(v)})
    serie = pd.Series(meses).sort_index()
    claves = list(serie.index)
    valores = serie.to_numpy()

    def f(d) -> float:
        p = pd.Period(d, "M")
        if p <= claves[0]:
            return float(valores[0])
        i = np.searchsorted(np.array([k.ordinal for k in claves]), p.ordinal, side="right") - 1
        return float(valores[i])
    f.ultimo_mes = claves[-1]
    return f


def _segmentos_mensuales(desde: date, hasta: date) -> list[tuple[date, int]]:
    """Tramos [desde, hasta) cortados por fin de mes: (fecha de referencia del tramo, días)."""
    tramos, d = [], desde
    while d < hasta:
        fin_mes = (pd.Timestamp(d) + pd.offsets.MonthBegin(1)).date()
        corte = min(fin_mes, hasta)
        tramos.append((d, (corte - d).days))
        d = corte
    return tramos


def zdm_letam(precio: float, vf: float, liq: date, vencimiento: date, curva) -> float:
    """Zero discount margin: Z tal que precio = vf / Π (1 + (TAMAR esperada del mes + Z)/12)^(días·12/365).
    Con TAMAR constante coincide con 12·((1+TEA)^(1/12) − 1) − TAMAR."""
    if not (precio > 0) or vencimiento <= liq:
        return float("nan")
    tramos = [(curva(d), dias) for d, dias in _segmentos_mensuales(liq, vencimiento)]
    objetivo = np.log(vf / precio)

    def log_desc(z):
        return sum(dias * 12 / 365 * np.log1p((t + z) / 12) for t, dias in tramos)

    lo, hi = -0.9, 3.0
    if (log_desc(lo) - objetivo) * (log_desc(hi) - objetivo) > 0:
        return float("nan")
    for _ in range(100):
        mid = (lo + hi) / 2
        if (log_desc(mid) - objetivo) * (log_desc(lo) - objetivo) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def zdm_tabla(precios: pd.Series, letam: pd.DataFrame, fecha_op: date, tamar: pd.Series,
              curva, feriados: set[date], plazo_liq: int = 1) -> pd.DataFrame:
    """ZDM de cada Letam con la curva de TAMAR esperada (pago proyectado con la misma curva)."""
    liq = sumar_habiles(fecha_op, plazo_liq, feriados) if plazo_liq else fecha_op
    filas = []
    for _, b in letam.iterrows():
        p = precios.get(b.ticker, np.nan)
        if pd.isna(p) or b.vencimiento <= liq or b.emision > fecha_op:
            continue
        prom, _ = tamar_promedio(b.emision, b.vencimiento, fecha_op, tamar, curva, feriados)
        vf = valor_final_letam(b.emision, b.vencimiento, b.margen, prom)
        filas.append({"ticker": b.ticker, "tamar_prom_esp": prom, "vf_esp": vf,
                      "zdm": zdm_letam(p, vf, liq, b.vencimiento, curva),
                      "extrapolado": pd.Period(b.vencimiento, "M") > curva.ultimo_mes})
    return pd.DataFrame(filas)


# ================================================================ INFLACIÓN ESPERADA (REM) Y CER NOMINAL
def load_rem_inflacion(path: Path | None = None, top10: bool = False) -> tuple[pd.Series, dict, tuple | None]:
    """IPC nivel general del REM (mediana). top10=True usa la hoja 'Resultados TOP 10'.
    Devuelve (Series Period[M] → var. mensual, {año: var. i.a. a diciembre},
    (Period del fin de 'próx. 12 meses', var. i.a.) o None)."""
    path = path or archivo_rem()
    if path is None:
        return pd.Series(dtype=float), {}, None
    import openpyxl
    import re as _re
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    if top10:
        ws = next(w for w in wb.worksheets if "top" in w.title.lower())
    else:
        ws = next(w for w in wb.worksheets if "cuadro" in w.title.lower())
    filas = list(ws.iter_rows(values_only=True))
    wb.close()
    i0 = next(i for i, r in enumerate(filas)
              if any(isinstance(c, str) and "nivel general" in c.lower() for c in r))
    enc = filas[i0 + 1]
    col_per = next(j for j, c in enumerate(enc) if isinstance(c, str) and c.strip().lower() == "período")
    col_ref = col_per + 1
    col_med = next(j for j, c in enumerate(enc) if isinstance(c, str) and c.strip().lower() == "mediana")
    mensual, anual, doce = {}, {}, None
    meses_es = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
                "sep": 9, "oct": 10, "nov": 11, "dic": 12}
    for r in filas[i0 + 2:]:
        per, med = r[col_per], r[col_med]
        if per is None or (isinstance(per, str) and not per.strip()):
            break
        if not isinstance(med, (int, float)):
            continue
        if hasattr(per, "year"):
            mensual[pd.Period(per, "M")] = float(med) / 100
        elif str(per).strip().isdigit():
            anual[int(str(per).strip())] = float(med) / 100
        elif "12 meses" in str(per):
            m = _re.search(r"([a-z]{3})-(\d{2})", str(r[col_ref] or ""))
            if m and m.group(1) in meses_es:
                doce = (pd.Period(f"20{m.group(2)}-{meses_es[m.group(1)]:02d}", "M"), float(med) / 100)
    return pd.Series(mensual).sort_index(), anual, doce


def curva_inflacion(rem_mensual: pd.Series, manual: dict | None, anual_largo: float):
    """Función Period[M] → inflación mensual esperada. Mes manual > REM; antes del primer dato, el
    primero; después del último mes definido, la inflación anual de largo plazo pasada a mensual."""
    meses = dict(rem_mensual.items())
    if manual:
        meses.update({pd.Period(k, "M"): v for k, v in manual.items() if v is not None and not pd.isna(v)})
    serie = pd.Series(meses).sort_index()
    primero, ultimo = serie.index[0], serie.index[-1]
    largo = (1 + anual_largo) ** (1 / 12) - 1

    def f(p: pd.Period) -> float:
        if p in serie.index:
            return float(serie[p])
        return float(serie.iloc[0]) if p < primero else largo
    f.ultimo_mes = ultimo
    return f


def proyectar_cer(hasta: date, cer: pd.Series, inflacion) -> float:
    """CER proyectado a una fecha. Desde el último dato publicado avanza día a día: entre el 16 de un
    mes y el 15 del siguiente el CER refleja la inflación del mes anterior (en forma geométrica)."""
    if hasta <= cer.index[-1]:
        d = cer.loc[:hasta]
        return float(d.iloc[-1]) if len(d) else float("nan")
    valor, d = float(cer.iloc[-1]), cer.index[-1]
    while d < hasta:
        d += timedelta(days=1)
        inicio = pd.Period(d, "M") if d.day >= 16 else pd.Period(d, "M") - 1  # el período arranca el 16
        dias_periodo = inicio.days_in_month
        valor *= (1 + inflacion(inicio - 1)) ** (1 / dias_periodo)
    return valor


def lecer_nominal(lecer: pd.DataFrame, cer: pd.Series, inflacion, feriados: set[date]) -> pd.DataFrame:
    """Copia de la hoja LECER con 'vf' = pago nominal esperado al vencimiento (CER proyectado al
    día de referencia del pago, 10 hábiles antes del cobro)."""
    df = lecer.copy()
    refs = [ref_cer_vto(v, feriados) for v in df.vencimiento]
    df["cer_ref_vto"] = [proyectar_cer(r, cer, inflacion) for r in refs]
    df["cer_proyectado"] = [r > cer.index[-1] for r in refs]
    df["vf"] = df["vf_base"] * df["cer_ref_vto"]
    return df
