"""Calculadora de tasas forward – Lecaps (tasa fija)."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import forwards as fw
from api_1816 import get_live_prices

st.set_page_config(page_title="Forwards Lecaps", page_icon="📈", layout="wide")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


# ---------------------------------------------------------------- datos
@st.cache_data
def base(_mtime: float):
    # _mtime: si el Excel cambia, se recarga solo.
    lecaps = fw.load_lecaps()
    return lecaps, fw.load_feriados(), fw.load_precios(lecaps.ticker.tolist())


@st.cache_data(ttl=60, show_spinner=False)
def _vivo(tickers: tuple[str, ...]):
    # Si falla lanza excepción, así el error no queda cacheado.
    p = get_live_prices(list(tickers))
    s = fw.normalizar_precios(pd.DataFrame([p])).iloc[0]
    return s, datetime.now(TZ)


def vivo(tickers: tuple[str, ...]):
    try:
        s, ts = _vivo(tickers)
        return s, ts, None
    except Exception as e:  # sin API → se usa el último cierre
        return None, None, str(e)


try:
    lecaps, feriados, hist = base(fw.ruta_inputs().stat().st_mtime)
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
hoy = datetime.now(TZ).date()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Parámetros")
    tipo = st.radio("Expresar tasas en", ["TEM", "TNA", "TEA"], horizontal=True)
    plazo = st.radio("Liquidación", [1, 0], format_func=lambda x: f"T+{x}", horizontal=True)
    st.markdown("---")
    usar_api = st.checkbox("Usar precios en vivo (API 1816)", value=True)
    if usar_api and st.button("🔄 Actualizar precios"):
        _vivo.clear()

live, ts_api, err = (vivo(tuple(lecaps.ticker)) if usar_api else (None, None, None))

with st.sidebar:
    if live is not None:
        st.success(f"1816 conectado: {live.notna().sum()} Lecaps, consulta de las {ts_api:%H:%M:%S}")
        ahora = datetime.now(TZ)
        if not (fw.es_habil(hoy, feriados) and 11 <= ahora.hour < 17):
            st.warning("Mercado cerrado: los precios pueden ser del último cierre.")
    elif usar_api:
        st.error(f"Falló la API: {err}. Usando Excel.")

# Precios del día: en vivo si hay API, si no el último cierre del histórico.
if live is not None:
    precios_hoy, fecha_px, fuente = live, hoy, "1816 en vivo"
else:
    precios_hoy, fecha_px, fuente = hist.iloc[-1], hist.index[-1], "último cierre (Excel)"

# El histórico + el punto de hoy para el gráfico
hist_full = hist.copy()
if live is not None:
    hist_full.loc[hoy] = live.reindex(hist.columns)
    hist_full = hist_full.sort_index()

st.title("Forwards implícitas · Lecaps")

tab1, tab2 = st.tabs(["Tabla de forwards", "Forward histórica"])

# ---------------------------------------------------------------- tabla
with tab1:
    c1, c2 = st.columns([1, 3])
    opciones = [fecha_px] + [d for d in reversed(hist.index) if d != fecha_px]
    fecha_sel = c1.selectbox("Fecha", opciones,
                             format_func=lambda d: f"{d:%d-%m-%Y}" + (" (hoy)" if d == fecha_px else ""))
    px = precios_hoy if fecha_sel == fecha_px else hist.loc[fecha_sel]
    liq = fw.sumar_habiles(fecha_sel, plazo, feriados) if plazo else fecha_sel

    c2.markdown(f"**Precios:** {fuente if fecha_sel == fecha_px else 'cierre histórico'} · "
                f"**Liquidación:** {liq:%d-%m-%Y}")
    if fecha_sel == fecha_px and live is not None:
        faltan = [t for t in lecaps.ticker if pd.isna(live.get(t))]
        if faltan:
            st.caption(f"⚠️ 1816 no devolvió precio para: {', '.join(faltan)}")

    m = fw.tabla_forward(px, lecaps, liq, tipo)
    m.index.name = f"{fecha_sel:%d-%b}"
    st.caption("Diagonal: TIR de cada Lecap. Fila i, columna j: tasa forward implícita "
               "entre el vencimiento de i y el de j.")
    sty = (m.style.format(lambda v: "" if pd.isna(v) else f"{v:.2%}")
           .background_gradient(cmap="RdYlGn", axis=None)
           .highlight_null(color="#e6e6e6"))
    # Diagonal destacada en blanco (como en el Excel)
    sty = sty.apply(lambda df: pd.DataFrame(
        [["background-color: white; color: black; font-weight: 600" if i == j else ""
          for j in range(df.shape[1])] for i in range(df.shape[0])],
        index=df.index, columns=df.columns), axis=None)
    st.dataframe(sty, use_container_width=True, height=38 * (len(m) + 1) + 3)

    with st.expander("Detalle por Lecap"):
        r = fw.resumen(px, lecaps, liq)
        st.dataframe(r.style.format({"precio": "{:.3f}", "vf": "{:.3f}",
                                     "TEM": "{:.2%}", "TNA": "{:.2%}", "TEA": "{:.2%}"}),
                     use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- histórico
with tab2:
    vivas = lecaps[lecaps.vencimiento > hoy].ticker.tolist()
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("Lecap corta", vivas, index=0)
    resto = [t for t in vivas if t != a]
    b = c2.selectbox("Lecap larga", resto, index=min(len(resto) - 1, 2))
    ver_tir = c3.checkbox("Mostrar TIR de cada una", value=True)

    s = fw.forward_historica(hist_full, lecaps, a, b, feriados, plazo, tipo)
    if s.empty:
        st.info("No hay precios en común para ese par.")
    else:
        corto, largo = [c.replace("TIR ", "") for c in s.columns[:2]]
        f = s["Forward"]
        k = st.columns(4)
        k[0].metric("Forward actual", f"{f.iloc[-1]:.2%}",
                    f"{(f.iloc[-1] - f.iloc[-2]) * 1e4:+.0f} pb" if len(f) > 1 else None)
        k[1].metric("Promedio", f"{f.mean():.2%}")
        k[2].metric("Mínimo", f"{f.min():.2%}", f"{f.idxmin():%d-%m-%y}", delta_color="off")
        k[3].metric("Máximo", f"{f.max():.2%}", f"{f.idxmax():%d-%m-%y}", delta_color="off")

        fig = go.Figure()
        fig.add_scatter(x=s.index, y=f, name=f"Forward {corto} → {largo}",
                        line=dict(width=2.5, color="#1f6feb"))
        if ver_tir:
            fig.add_scatter(x=s.index, y=s.iloc[:, 0], name=f"TIR {corto}",
                            line=dict(width=1.2, dash="dot", color="#8b949e"))
            fig.add_scatter(x=s.index, y=s.iloc[:, 1], name=f"TIR {largo}",
                            line=dict(width=1.2, dash="dash", color="#d29922"))
        fig.update_layout(height=480, hovermode="x unified", yaxis_tickformat=".2%",
                          yaxis_title=tipo, margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)

        st.download_button("Descargar serie (CSV)", s.to_csv().encode(),
                           f"forward_{corto}_{largo}.csv", "text/csv")
