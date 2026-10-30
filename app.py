"""Calculadora de tasas forward – Lecaps (tasa fija) y CER (LECER / Boncer)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import forwards as fw
from api_1816 import get_live_prices

st.set_page_config(page_title="Forwards Lecaps y CER", page_icon="📈", layout="wide")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


# ---------------------------------------------------------------- datos
@st.cache_data
def base(mtime_excel: float, mtime_cierres: float):
    # Si cambia el Excel o el CSV de cierres de 1816, se recarga solo.
    lecaps = fw.load_lecaps()
    lecer = fw.load_lecer()
    flujos = fw.load_flujos_hd()
    hd = fw.bonos_hd(flujos)
    tickers = (lecaps.ticker.tolist() + lecer.ticker.tolist()
               + [t + s for t in hd.ticker for s in ("D", "C")])
    return (lecaps, lecer, flujos, hd, fw.load_cer(), fw.load_feriados(),
            fw.load_precios(tickers))


@st.cache_data(ttl=60, show_spinner=False)
def _vivo(tickers: tuple[str, ...]):
    # Si falla lanza excepción, así el error no queda cacheado.
    p = get_live_prices(list(tickers))
    s = fw.normalizar_precios(pd.DataFrame([p])).iloc[0]
    return s, datetime.now(TZ), (list(get_live_prices.rechazados), list(get_live_prices.errores))


def vivo(tickers: tuple[str, ...]):
    try:
        s, ts, rech = _vivo(tickers)
        return s, ts, None, rech
    except Exception as e:  # sin API → se usa el último cierre
        return None, None, str(e), ([], [])


def _mtime_cierres() -> float:
    p = fw.CARPETA / fw.ARCHIVO_CIERRES
    return p.stat().st_mtime if p.exists() else 0.0


try:
    lecaps, lecer, flujos, hd, cer, feriados, hist = base(fw.ruta_inputs().stat().st_mtime, _mtime_cierres())
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
hoy = datetime.now(TZ).date()
hd_d = [t + "D" for t in hd.ticker]
hd_c = [t + "C" for t in hd.ticker]
todos = lecaps.ticker.tolist() + lecer.ticker.tolist() + hd_d + hd_c
tipo_de = {**{t: "Lecap" for t in lecaps.ticker}, **{t: "CER" for t in lecer.ticker},
           **{t: "HD MEP" for t in hd_d}, **{t: "HD Cable" for t in hd_c}}
venc_hd = hd.set_index("ticker").vencimiento

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Renta fija")
    seccion = st.radio("Sección", ["Forwards", "Curvas", "Canje MEP/Cable"], key="seccion")
    st.markdown("---")
    st.header("Parámetros")
    tipo = st.radio("Expresar tasas en", ["TEM", "TNA", "TEA"], index=2, horizontal=True)
    plazo = st.radio("Liquidación", [1, 0], format_func=lambda x: f"T+{x}", horizontal=True)
    st.markdown("---")
    usar_api = st.checkbox("Usar precios en vivo (API 1816)", value=True)
    if usar_api and st.button("🔄 Actualizar precios"):
        _vivo.clear()

if usar_api:
    with st.spinner("Trayendo precios de 1816 (máx. 1 pedido por segundo)..."):
        live, ts_api, err, (rechazados, errores_api) = vivo(tuple(todos))
else:
    live, ts_api, err, (rechazados, errores_api) = None, None, None, ([], [])

with st.sidebar:
    if live is not None:
        n_lec = live.reindex(lecaps.ticker).notna().sum()
        n_cer = live.reindex(lecer.ticker).notna().sum()
        n_hd = live.reindex(hd_d + hd_c).notna().sum()
        st.success(f"1816 conectado: {n_lec} Lecaps, {n_cer} CER y {n_hd} precios HD (D/C), "
                   f"consulta de las {ts_api:%H:%M:%S}")
        ahora = datetime.now(TZ)
        if not (fw.es_habil(hoy, feriados) and (10, 30) <= (ahora.hour, ahora.minute) < (17, 0)):
            st.warning("Mercado cerrado: los precios pueden ser del último cierre.")
        if rechazados:
            st.warning(f"1816 rechazó estos tickers: {', '.join(rechazados)}. Para esos se usa el "
                       "precio que cargues a mano en 'Precios de hoy'.")
            for msg in errores_api:
                st.caption(f"Respuesta de 1816: `{msg}`")
    elif usar_api:
        st.error(f"Falló la API: {err}. Usando Excel.")

# Precios del día: en vivo si hay API, si no el último cierre del histórico.
if live is not None:
    precios_hoy, fecha_px, fuente = live.reindex(todos), hoy, "1816 en vivo"
else:
    precios_hoy, fecha_px, fuente = hist.iloc[-1], hist.index[-1], "último cierre (Excel)"

st.title({"Forwards": "Forwards implícitas", "Curvas": "Curvas de rendimiento",
          "Canje MEP/Cable": "Canje MEP / Cable"}[seccion])

# Precios en vivo editables a mano (se resetean al traer precios nuevos de 1816)
if live is not None:
    st.session_state.setdefault("reset_editor", 0)
    with st.expander("✏️ Precios de hoy (editables)", expanded=False):
        venc = pd.concat([lecaps.set_index("ticker").vencimiento, lecer.set_index("ticker").vencimiento,
                          venc_hd.rename(lambda t: t + "D"), venc_hd.rename(lambda t: t + "C")])
        base_px = pd.DataFrame({
            "Tipo": [tipo_de[t] for t in todos],
            "Vencimiento": venc.reindex(todos).values,
            "1816": live.reindex(todos).values,
            "Precio": live.reindex(todos).values,
        }, index=pd.Index(todos, name="Ticker"))
        ed = st.data_editor(
            base_px,
            key=f"editor_{ts_api:%H%M%S}_{st.session_state.reset_editor}",
            disabled=["Tipo", "Vencimiento", "1816"],
            column_config={
                "Vencimiento": st.column_config.DateColumn(format="DD-MM-YYYY"),
                "1816": st.column_config.NumberColumn("Precio 1816", format="%.3f"),
                "Precio": st.column_config.NumberColumn("Precio usado ✏️", format="%.3f",
                                                        min_value=0.0, step=0.001),
            },
            use_container_width=True,
        )
        st.caption("Precio cada 100 VN (en HD, cada 100 VN original, en dólares). Lo que cambies acá se usa en las tablas y en el punto de hoy "
                   "de los gráficos. Si una especie no tiene precio de 1816, podés cargarlo a mano.")
        if st.button("↩️ Volver a precios de 1816"):
            st.session_state.reset_editor += 1
            st.rerun()
    precios_hoy = fw.normalizar_precios(ed[["Precio"]])["Precio"]

    def _distinto(a, b):
        if pd.isna(a) and pd.isna(b):
            return False
        return pd.isna(a) or pd.isna(b) or abs(a - b) > 1e-9

    editados = [t for t in ed.index if _distinto(ed.at[t, "Precio"], ed.at[t, "1816"])]
    if editados:
        fuente = f"1816 en vivo + manual ({', '.join(editados)})"

# El histórico + el punto de hoy para los gráficos
hist_full = hist.copy()
if live is not None:
    hist_full.loc[hoy] = precios_hoy.reindex(hist.columns)
    hist_full = hist_full.sort_index()


def coef_cer(liq: date) -> float:
    return fw.cer_referencia(liq, cer, feriados)[1]


# ---------------------------------------------------------------- vistas
def estilo_matriz(m: pd.DataFrame):
    sty = (m.style.format(lambda v: "" if pd.isna(v) else f"{v:.2%}")
           .background_gradient(cmap="RdYlGn", axis=None)
           .highlight_null(color="#e6e6e6"))
    # Diagonal destacada en blanco (como en el Excel)
    return sty.apply(lambda df: pd.DataFrame(
        [["background-color: white; color: black; font-weight: 600" if i == j else ""
          for j in range(df.shape[1])] for i in range(df.shape[0])],
        index=df.index, columns=df.columns), axis=None)


def vista_tabla(clave: str, bonos: pd.DataFrame, es_cer: bool):
    c1, c2 = st.columns([1, 3])
    opciones = [fecha_px] + [d for d in reversed(hist.index) if d != fecha_px]
    fecha_sel = c1.selectbox("Fecha", opciones, key=f"fecha_{clave}",
                             format_func=lambda d: f"{d:%d-%m-%Y}" + (" (hoy)" if d == fecha_px else ""))
    px = precios_hoy if fecha_sel == fecha_px else hist.loc[fecha_sel]
    liq = fw.sumar_habiles(fecha_sel, plazo, feriados) if plazo else fecha_sel

    info = (f"**Precios:** {fuente if fecha_sel == fecha_px else 'cierre histórico'} · "
            f"**Liquidación:** {liq:%d-%m-%Y}")
    fijos = []
    if es_cer:
        # Las que ya conocen el CER del vencimiento pasaron a ser tasa fija: afuera
        fijos = fw.cer_ya_fijo(bonos, fecha_sel, cer, feriados)
        bonos = bonos[~bonos.ticker.isin(fijos)]
        f_ref, cer_ref = fw.cer_referencia(liq, cer, feriados)
        bonos = fw.lecer_a_fecha(bonos, cer_ref)
        info += f" · **CER ref.** ({f_ref:%d-%m-%Y}, 10 hábiles antes): {cer_ref:,.4f}"
    c2.markdown(info)
    if fijos:
        st.caption(f"ℹ️ Fuera de la tabla por tener ya conocido el CER al vencimiento "
                   f"(ahora son tasa fija): {', '.join(fijos)}")

    faltan = [t for t in bonos.ticker if t in px.index and pd.isna(px.get(t))
              and bonos.set_index("ticker").vencimiento[t] > liq]
    if fecha_sel == fecha_px and faltan:
        st.caption(f"⚠️ Sin precio para: {', '.join(faltan)}")

    m = fw.tabla_forward(px, bonos, liq, tipo)
    m.index.name = f"{fecha_sel:%d-%b}"
    st.caption("Diagonal: TIR " + ("**real** " if es_cer else "") + "de cada bono. Fila i, columna j: "
               "tasa forward " + ("real " if es_cer else "") + "implícita entre el vencimiento de i y el de j.")
    st.dataframe(estilo_matriz(m), use_container_width=True, height=36 * (len(m) + 1) + 3)

    with st.expander("Detalle por bono"):
        r = fw.resumen(px, bonos, liq)
        r = r.rename(columns={"vf": "pago final (CER hoy)" if es_cer else "pago final"})
        st.dataframe(r.style.format({"precio": "{:.3f}", r.columns[4]: "{:.3f}",
                                     "TEM": "{:.2%}", "TNA": "{:.2%}", "TEA": "{:.2%}"}),
                     use_container_width=True, hide_index=True)


def vista_historica(clave: str, bonos: pd.DataFrame, es_cer: bool):
    vivas = bonos[bonos.vencimiento > hoy].ticker.tolist()
    if es_cer:
        fijos_hoy = fw.cer_ya_fijo(bonos, hoy, cer, feriados)
        vivas = [t for t in vivas if t not in fijos_hoy]
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("Bono corto", vivas, index=0, key=f"a_{clave}")
    resto = [t for t in vivas if t != a]
    b = c2.selectbox("Bono largo", resto, index=min(len(resto) - 1, 2), key=f"b_{clave}")
    ver_tir = c3.checkbox("Mostrar TIR de cada uno", value=True, key=f"tir_{clave}")

    s = fw.forward_historica(hist_full, bonos, a, b, feriados, plazo, tipo,
                             coef=coef_cer if es_cer else None)
    if es_cer and not s.empty:
        # Se cortan los días en que alguno de los dos ya tenía el CER del vencimiento conocido
        s = s[[not ({a, b} & set(fw.cer_ya_fijo(bonos[bonos.ticker.isin([a, b])], d.date(), cer,
                                                 feriados))) for d in s.index]]
    if s.empty:
        st.info("No hay precios en común para ese par.")
        return
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
                      yaxis_title=f"{tipo} {'real' if es_cer else ''}".strip(),
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

    st.download_button("Descargar serie (CSV)", s.to_csv().encode(),
                       f"forward_{corto}_{largo}.csv", "text/csv", key=f"dl_{clave}")


# ---------------------------------------------------------------- hard dollar
def filtro_ley(clave: str) -> list[str]:
    ley = st.radio("Bonos", ["Globales (ley NY)", "Bonares (ley ARG)", "Todos"], horizontal=True,
                   key=f"ley_{clave}")
    if ley.startswith("Globales"):
        return hd[hd.ley == "NY"].ticker.tolist()
    if ley.startswith("Bonares"):
        return hd[hd.ley == "ARG"].ticker.tolist()
    return hd.ticker.tolist()


def vista_tabla_hd(clave: str, sufijo: str):
    sel = filtro_ley(clave)
    c1, c2 = st.columns([1, 3])
    opciones = [fecha_px] + [d for d in reversed(hist.index) if d != fecha_px]
    fecha_sel = c1.selectbox("Fecha", opciones, key=f"fecha_{clave}",
                             format_func=lambda d: f"{d:%d-%m-%Y}" + (" (hoy)" if d == fecha_px else ""))
    px = precios_hoy if fecha_sel == fecha_px else hist.loc[fecha_sel]
    liq = fw.sumar_habiles(fecha_sel, plazo, feriados) if plazo else fecha_sel
    c2.markdown(f"**Precios:** {fuente if fecha_sel == fecha_px else 'cierre histórico'} "
                f"(especie {sufijo}) · **Liquidación:** {liq:%d-%m-%Y}")

    met = fw.metricas_hd(px, flujos[flujos.ticker.isin(sel)], liq, sufijo)
    faltan = met[met.precio.isna()].ticker.tolist()
    if faltan:
        st.caption(f"⚠️ Sin precio para: {', '.join(t + sufijo for t in faltan)}")

    m = fw.tabla_forward_hd(met, tipo)
    m.index.name = f"{fecha_sel:%d-%b}"
    st.caption("Ordenada por duration. Diagonal: TIR de cada bono. Fila i, columna j: forward implícita "
               "entre la duration de i y la de j: ((1+TIR j)^Dj / (1+TIR i)^Di)^(1/(Dj−Di)) − 1, "
               "con duration de Macaulay. Vacío si las durations difieren menos de 3 meses.")
    st.dataframe(estilo_matriz(m), use_container_width=True, height=36 * (len(m) + 1) + 3)

    with st.expander("Detalle por bono"):
        r = met.sort_values("duration").assign(TIR=lambda d: tasa_fmt(d.TIR))
        st.dataframe(r.rename(columns={"TIR": f"TIR ({tipo})", "dur_mod": "dur. mod."})
                     .style.format({"precio": "{:.3f}", "residual": "{:.2f}", f"TIR ({tipo})": "{:.2%}",
                                    "duration": "{:.2f}", "dur. mod.": "{:.2f}"}),
                     use_container_width=True, hide_index=True)
        st.caption("Precio sucio cada 100 VN original. TIR efectiva anual (act/365) sobre los flujos "
                   "de la hoja 'Flujo HD'.")


def tasa_fmt(y):
    return fw.tasa_hd(y, tipo)


def grafico_serie(s: pd.DataFrame, ytitulo: str, pct: bool = True):
    fig = go.Figure()
    colores = ["#1f6feb", "#8b949e", "#d29922"]
    estilos = [dict(width=2.5), dict(width=1.2, dash="dot"), dict(width=1.2, dash="dash")]
    for i, c in enumerate(s.columns):
        fig.add_scatter(x=s.index, y=s[c], name=c, line=dict(color=colores[i % 3], **estilos[i % 3]))
    fig.update_layout(height=480, hovermode="x unified",
                      yaxis_tickformat=".2%" if pct else ".2f", yaxis_title=ytitulo,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)


def kpis(f: pd.Series, pct: bool = True, unidad: str = ""):
    fmt = (lambda v: f"{v:.2%}") if pct else (lambda v: f"{v:.2f}{unidad}")
    k = st.columns(4)
    if len(f) > 1:
        dif = (f.iloc[-1] - f.iloc[-2]) * (1e4 if pct else 1)
        delta = f"{dif:+.0f} pb" if pct else f"{dif:+.2f}{unidad}"
    else:
        delta = None
    k[0].metric("Actual", fmt(f.iloc[-1]), delta)
    k[1].metric("Promedio", fmt(f.mean()))
    k[2].metric("Mínimo", fmt(f.min()), f"{f.idxmin():%d-%m-%y}", delta_color="off")
    k[3].metric("Máximo", fmt(f.max()), f"{f.idxmax():%d-%m-%y}", delta_color="off")


def vista_historica_hd(clave: str, sufijo: str):
    sel = filtro_ley(clave + "_h")
    vivos = [t for t in hd.ticker if t in sel and venc_hd[t] > hoy]
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("Bono 1", vivos, index=0, key=f"a_{clave}")
    resto = [t for t in vivos if t != a]
    b = c2.selectbox("Bono 2", resto, index=min(len(resto) - 1, 2), key=f"b_{clave}")
    ver_tir = c3.checkbox("Mostrar TIR de cada uno", value=True, key=f"tir_{clave}")

    with st.spinner("Calculando TIR día por día..."):
        s = serie_hd(a, b, sufijo, plazo, tipo, hist_full.index[-1],
                     tuple(hist_full.iloc[-1].reindex([a + sufijo, b + sufijo]).fillna(-1)))
    if s.empty or s["Forward"].dropna().empty:
        st.info("No hay precios en común para ese par, o sus durations son demasiado parecidas.")
        return
    s = s.dropna(subset=["Forward"])
    corto, largo = [c.replace("TIR ", "") for c in s.columns[:2]]
    kpis(s["Forward"])
    nombre_fwd = f"Forward {corto} → {largo}"
    s_graf = s.rename(columns={"Forward": nombre_fwd})
    # Primero la forward (línea azul llena); después las TIR (punteadas)
    s_graf = s_graf[[nombre_fwd, f"TIR {corto}", f"TIR {largo}"]]
    grafico_serie(s_graf if ver_tir else s_graf[[nombre_fwd]], tipo)
    st.download_button("Descargar serie (CSV)", s.to_csv().encode(),
                       f"forward_{corto}{sufijo}_{largo}{sufijo}.csv", "text/csv", key=f"dl_{clave}")


@st.cache_data(show_spinner=False)
def serie_hd(a, b, sufijo, plazo_, tipo_, ultima_fecha, ultimo_px):
    # ultima_fecha/ultimo_px solo sirven para invalidar el cache cuando cambian los precios de hoy
    return fw.forward_historica_hd(hist_full, flujos, a, b, sufijo, feriados, plazo_, tipo_)


def vista_canje():
    st.caption("Canje MEP/cable por bono: (precio D / precio C − 1) × 100. Positivo = el cable "
               "vale más que el MEP.")
    t1, t2 = st.tabs(["Hoy", "Histórico"])
    with t1:
        c = fw.canje(precios_hoy, hd.ticker.tolist())
        df = pd.DataFrame({"Ley": hd.set_index("ticker").ley, "Vencimiento": venc_hd,
                           "Precio D": [precios_hoy.get(t + "D") for t in hd.ticker],
                           "Precio C": [precios_hoy.get(t + "C") for t in hd.ticker],
                           "Canje %": c}).sort_values("Vencimiento")
        st.markdown(f"**Precios:** {fuente}")
        st.dataframe(df.style.format({"Precio D": "{:.3f}", "Precio C": "{:.3f}", "Canje %": "{:.2f}%"})
                     .background_gradient(subset=["Canje %"], cmap="RdYlGn_r"),
                     use_container_width=True)
    with t2:
        hc = fw.canje(hist_full, hd.ticker.tolist()).dropna(how="all")
        elegidos = st.multiselect("Bonos", hc.columns.tolist(),
                                  default=[t for t in ("GD30", "AL30") if t in hc.columns])
        if not elegidos:
            st.info("Elegí al menos un bono.")
            return
        if len(elegidos) == 1:
            kpis(hc[elegidos[0]].dropna(), pct=False, unidad="%")
        fig = go.Figure()
        for t in elegidos:
            fig.add_scatter(x=pd.to_datetime(hc.index), y=hc[t], name=t)
        fig.update_layout(height=460, hovermode="x unified", yaxis_title="Canje %",
                          yaxis_ticksuffix="%", margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)
        st.download_button("Descargar canje (CSV)", hc[elegidos].to_csv().encode(), "canje.csv",
                           "text/csv", key="dl_canje")


# ---------------------------------------------------------------- curvas ajustadas
AZUL, GRIS = "#1f6feb", "#8b949e"


def precios_de(fecha):
    return precios_hoy if fecha == fecha_px else hist.loc[fecha]


def puntos_curva(curva: str, fecha, sel_hd: list[str]) -> pd.DataFrame:
    px = precios_de(fecha)
    liq = fw.sumar_habiles(fecha, plazo, feriados) if plazo else fecha
    if curva == "Tasa fija":
        p = fw.puntos_cero_cupon(px, lecaps, liq)
    elif curva == "CER":
        fijos = fw.cer_ya_fijo(lecer, fecha, cer, feriados)
        vivas = lecer[~lecer.ticker.isin(fijos)]
        p = fw.puntos_cero_cupon(px, fw.lecer_a_fecha(vivas, coef_cer(liq)), liq)
    else:
        p = fw.puntos_hd(px, flujos, liq, "D" if curva == "HD MEP" else "C", sel_hd)
    p = p.dropna(subset=["TIR", "duration"]).copy()
    p["TIR"] = fw.tasa_hd(p["TIR"], tipo)  # misma unidad que el resto de la app
    return p.sort_values("duration").reset_index(drop=True)


def fecha_anterior(objetivo):
    previas = [d for d in hist.index if d <= objetivo]
    return previas[-1] if previas else None


def vista_curvas():
    c1, c2 = st.columns([3, 1])
    curva = c1.radio("Curva", ["Tasa fija", "CER", "HD MEP", "HD Cable"], horizontal=True,
                     key="curva_tipo")
    modelo = c2.selectbox("Ajuste", ["Nelson-Siegel", "Logarítmica"], key="curva_modelo")
    sel_hd = filtro_ley("curva") if curva.startswith("HD") else []

    c1, c2, c3 = st.columns([1, 1, 2])
    opciones = [fecha_px] + [d for d in reversed(hist.index) if d != fecha_px]
    fecha = c1.selectbox("Fecha", opciones, key="curva_fecha",
                         format_func=lambda d: f"{d:%d-%m-%Y}" + (" (hoy)" if d == fecha_px else ""))
    comp = c2.selectbox("Comparar con", ["Sin comparar", "1 semana antes", "1 mes antes",
                                         "3 meses antes", "Otra fecha"], index=2, key="curva_comp")
    fecha_c = None
    if comp == "Otra fecha":
        fecha_c = c3.selectbox("Fecha de comparación", [d for d in reversed(hist.index) if d < fecha],
                               key="curva_fecha_c", format_func=lambda d: f"{d:%d-%m-%Y}")
    elif comp != "Sin comparar":
        dias = {"1 semana antes": 7, "1 mes antes": 30, "3 meses antes": 91}[comp]
        fecha_c = fecha_anterior(fecha - timedelta(days=dias))

    p = puntos_curva(curva, fecha, sel_hd)
    if len(p) < 2:
        st.info("No hay suficientes bonos con precio para armar la curva en esa fecha.")
        return
    excluir = st.multiselect("Excluir del ajuste (se siguen mostrando)", p.ticker.tolist(),
                             key=f"curva_excl_{curva}")
    usar = ~p.ticker.isin(excluir)
    fn, desc = fw.ajustar_curva(p.duration[usar], p.TIR[usar], modelo)
    if fn is None:
        st.info(desc)
        return
    p["curva"] = fn(p.duration)
    p["spread"] = (p.TIR - p.curva) * 1e4

    pc, fn_c = None, None
    if fecha_c is not None:
        pc = puntos_curva(curva, fecha_c, sel_hd)
        pc = pc[~pc.ticker.isin(excluir)]
        if len(pc) >= 2:
            fn_c, _ = fw.ajustar_curva(pc.duration, pc.TIR, modelo)
            p = p.merge(pc[["ticker", "TIR"]].rename(columns={"TIR": "TIR_c"}), on="ticker", how="left")
            p["var"] = (p.TIR - p.TIR_c) * 1e4

    unidad = f"{tipo}{' real' if curva == 'CER' else ''}"
    st.caption(f"{desc}. Ajuste sobre TIR ({unidad}) contra duration en años. Spread = TIR − curva: "
               "positivo = el bono rinde más que la curva (**barato**); negativo = **rico**.")

    # ---- gráfico
    xs = np.linspace(max(p.duration.min() * 0.9, 1e-3), p.duration.max() * 1.03, 200)
    fig = go.Figure()
    if fn_c is not None:
        fig.add_scatter(x=xs, y=fn_c(xs), mode="lines", name=f"Curva {fecha_c:%d-%m-%y}",
                        line=dict(color=GRIS, width=2, dash="dash"), hoverinfo="skip")
        fig.add_scatter(x=pc.duration, y=pc.TIR, mode="markers", name=f"Bonos {fecha_c:%d-%m-%y}",
                        marker=dict(color=GRIS, size=8, symbol="circle-open"),
                        customdata=pc[["ticker"]],
                        hovertemplate="%{customdata[0]}<br>Duration %{x:.2f}<br>TIR %{y:.2%}<extra></extra>")
    fig.add_scatter(x=xs, y=fn(xs), mode="lines", name=f"Curva {fecha:%d-%m-%y}",
                    line=dict(color=AZUL, width=2), hoverinfo="skip")
    for incl, nombre, simbolo in [(usar, f"Bonos {fecha:%d-%m-%y}", "circle"),
                                  (~usar, "Excluidos del ajuste", "x")]:
        q = p[incl]
        if q.empty:
            continue
        fig.add_scatter(x=q.duration, y=q.TIR, mode="markers+text", name=nombre, text=q.ticker,
                        textposition="top center", textfont=dict(size=11),
                        marker=dict(color=AZUL, size=9, symbol=simbolo,
                                    line=dict(color="white", width=1)),
                        customdata=np.column_stack([q.ticker, q.spread]),
                        hovertemplate="%{customdata[0]}<br>Duration %{x:.2f}<br>TIR %{y:.2%}"
                                      "<br>Spread %{customdata[1]:+.0f} pb<extra></extra>")
    fig.update_layout(height=520, xaxis_title="Duration (años)", yaxis_title=unidad,
                      yaxis_tickformat=".1%", hovermode="closest",
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

    # ---- tabla
    cols = {"ticker": "Ticker", "vencimiento": "Vencimiento", "duration": "Duration",
            "precio": "Precio", "TIR": f"TIR ({unidad})", "curva": "TIR curva",
            "spread": "Spread (pb)"}
    if "var" in p:
        cols["var"] = f"Δ TIR vs {fecha_c:%d-%m} (pb)"
    t = p[list(cols)].rename(columns=cols)
    lim = max(float(p.spread.abs().max()), 1.0)
    fmt = {"Duration": "{:.2f}", "Precio": "{:.3f}", f"TIR ({unidad})": "{:.2%}",
           "TIR curva": "{:.2%}", "Spread (pb)": "{:+.0f}"}
    if "var" in p:
        fmt[cols["var"]] = "{:+.0f}"
    st.dataframe(t.style.format(fmt, na_rep="")
                 .background_gradient(subset=["Spread (pb)"], cmap="RdYlGn", vmin=-lim, vmax=lim),
                 use_container_width=True, hide_index=True)
    st.download_button("Descargar tabla (CSV)", t.to_csv(index=False).encode(),
                       f"curva_{curva.replace(' ', '_')}_{fecha:%Y%m%d}.csv", "text/csv",
                       key="dl_curva")


if seccion == "Curvas":
    vista_curvas()

elif seccion == "Canje MEP/Cable":
    vista_canje()

else:  # Forwards
    tab_lec, tab_cer, tab_mep, tab_ccl = st.tabs(
        ["Lecaps (tasa fija)", "CER (tasa real)", "Hard dollar · MEP", "Hard dollar · Cable"])

    with tab_lec:
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla("lec", lecaps, es_cer=False)
        with t2:
            vista_historica("lec", lecaps, es_cer=False)

    with tab_cer:
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla("cer", lecer, es_cer=True)
        with t2:
            vista_historica("cer", lecer, es_cer=True)

    with tab_mep:
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla_hd("mep", "D")
        with t2:
            vista_historica_hd("mep", "D")

    with tab_ccl:
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla_hd("ccl", "C")
        with t2:
            vista_historica_hd("ccl", "C")
