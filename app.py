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
    letam = fw.load_letam()
    duales = fw.load_duales()
    tickers = (lecaps.ticker.tolist() + lecer.ticker.tolist() + letam.ticker.tolist()
               + duales.ticker.tolist() + [t + s for t in hd.ticker for s in ("D", "C")])
    return (lecaps, lecer, flujos, hd, fw.load_cer(), fw.load_feriados(),
            fw.load_precios(tickers), letam, fw.load_tamar(), duales)


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
    (lecaps, lecer, flujos, hd, cer, feriados, hist, letam,
     tamar, duales) = base(fw.ruta_inputs().stat().st_mtime, _mtime_cierres())
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
hoy = datetime.now(TZ).date()
hd_d = [t + "D" for t in hd.ticker]
hd_c = [t + "C" for t in hd.ticker]
todos = (lecaps.ticker.tolist() + lecer.ticker.tolist() + letam.ticker.tolist()
         + duales.ticker.tolist() + hd_d + hd_c)
duales_cer = fw.duales_como_cer(duales)      # pata CER, formato LECER
duales_tamar = fw.duales_como_tamar(duales)  # pata TAMAR, formato Letam
es_dual = set(duales.ticker)
tipo_de = {**{t: "Lecap" for t in lecaps.ticker}, **{t: "CER" for t in lecer.ticker},
           **{t: "Letam" for t in letam.ticker}, **{t: "Dual CER-TAMAR" for t in duales.ticker},
           **{t: "HD MEP" for t in hd_d}, **{t: "HD Cable" for t in hd_c}}
venc_hd = hd.set_index("ticker").vencimiento

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Renta fija")
    seccion = st.radio("Sección", ["Forwards", "Curvas", "Opción duales", "Canje MEP/Cable"],
                       key="seccion")
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
        n_ltm = live.reindex(letam.ticker).notna().sum()
        n_dua = live.reindex(duales.ticker).notna().sum()
        st.success(f"1816 conectado: {n_lec} Lecaps, {n_cer} CER, {n_ltm} Letam, {n_dua} duales y "
                   f"{n_hd} precios HD (D/C), "
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
          "Opción duales": "Opción TAMAR de los duales", "Canje MEP/Cable": "Canje MEP / Cable"}[seccion])

# Precios en vivo editables a mano (se resetean al traer precios nuevos de 1816)
if live is not None:
    st.session_state.setdefault("reset_editor", 0)
    with st.expander("✏️ Precios de hoy (editables)", expanded=False):
        venc = pd.concat([lecaps.set_index("ticker").vencimiento, lecer.set_index("ticker").vencimiento,
                          letam.set_index("ticker").vencimiento, duales.set_index("ticker").vencimiento,
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


# ---------------------------------------------------------------- Letam (TAMAR)
def input_tamar() -> float:
    """TAMAR proyectada para la parte de la ventana que todavía no se publicó (TNA)."""
    ult_f, ult = tamar.index[-1], float(tamar.iloc[-1])
    c1, c2 = st.columns([1, 3])
    v = c1.number_input("TAMAR proyectada (TNA %)", min_value=0.0, max_value=200.0,
                        value=round(ult * 100, 4), step=0.25, format="%.4f", key="tamar_proy")
    c2.markdown(f"Última TAMAR publicada en la hoja Tamar: **{ult:.4%}** ({ult_f:%d-%m-%Y}). "
                "La parte de la ventana que todavía no se publicó se completa con la TAMAR proyectada. "
                "Por defecto es la última publicada, o sea TAMAR constante.")
    return v / 100


@st.cache_data(show_spinner=False)
def rem_tamar(mtime: float):
    return fw.load_rem_tamar()


def editor_tamar_esperada(lugar: str = "ltm"):
    """Curva de TAMAR esperada: REM (mediana) + tus valores para los meses que siguen.
    Tus valores se guardan en la sesión y se comparten entre la solapa Letam y Curvas."""
    p = fw.archivo_rem()
    if p is None:
        return None
    rem, titulo = rem_tamar(p.stat().st_mtime)
    if rem.empty:
        return None
    ultimo = rem.index[-1]
    hasta = pd.Period(f"{ultimo.year + (1 if ultimo.month == 12 else 0)}-12", "M")
    meses_man = pd.period_range(ultimo + 1, hasta, freq="M")
    with st.expander(f"TAMAR esperada para el ZDM: REM hasta {ultimo.strftime('%b-%y')} + tus supuestos "
                     f"hasta {hasta.strftime('%b-%y')}"):
        c1, c2 = st.columns([1, 2])
        c1.markdown(f"**{titulo or p.name}** (mediana)")
        c1.dataframe(pd.DataFrame({"Mes": [m.strftime("%b-%y") for m in rem.index],
                                   "TAMAR REM": rem.values}).style.format({"TAMAR REM": "{:.2%}"}),
                     hide_index=True, use_container_width=True)
        guardados = st.session_state.setdefault("tamar_manual_vals", {})
        defecto = round(float(rem.iloc[-1]) * 100, 2)
        base_man = pd.DataFrame({"Mes": [m.strftime("%b-%y") for m in meses_man],
                                 "TAMAR (TNA %)": [guardados.get(str(m), defecto) for m in meses_man]})
        ed = c2.data_editor(base_man, key=f"tamar_manual_{lugar}_{ultimo}", hide_index=True,
                            disabled=["Mes"], use_container_width=True,
                            column_config={"TAMAR (TNA %)": st.column_config.NumberColumn(
                                min_value=0.0, max_value=200.0, step=0.25, format="%.2f")})
        c2.caption(f"Arrancan con la de {ultimo.strftime('%b-%y')}. Después de {hasta.strftime('%b-%y')} "
                   "se mantiene tu último valor.")
    st.session_state["tamar_manual_vals"] = {str(m): v for m, v in zip(meses_man, ed["TAMAR (TNA %)"])
                                             if pd.notna(v)}
    manual = {m: (v / 100 if pd.notna(v) else None) for m, v in zip(meses_man, ed["TAMAR (TNA %)"])}
    return fw.curva_tamar_esperada(rem, manual)


def vista_tabla_letam(proy_hoy: float, en_margen: bool, base_ltm: pd.DataFrame, curva_rem=None):
    c1, c2 = st.columns([1, 3])
    opciones = [fecha_px] + [d for d in reversed(hist.index) if d != fecha_px]
    fecha_sel = c1.selectbox("Fecha", opciones, key="fecha_ltm",
                             format_func=lambda d: f"{d:%d-%m-%Y}" + (" (hoy)" if d == fecha_px else ""))
    px = precios_hoy if fecha_sel == fecha_px else hist.loc[fecha_sel]
    liq = fw.sumar_habiles(fecha_sel, plazo, feriados) if plazo else fecha_sel
    proy = proy_hoy if fecha_sel == fecha_px else fw.tamar_al(fecha_sel, tamar)
    bonos = fw.letam_a_fecha(base_ltm, fecha_sel, tamar, proy, feriados)
    c2.markdown(f"**Precios:** {fuente if fecha_sel == fecha_px else 'cierre histórico'} · "
                f"**Liquidación:** {liq:%d-%m-%Y} · **TAMAR proyectada:** {proy:.2%}"
                + ("" if fecha_sel == fecha_px else " (la publicada ese día)"))

    faltan = [t for t in bonos.ticker if pd.isna(px.get(t))
              and bonos.set_index("ticker").vencimiento[t] > liq
              and bonos.set_index("ticker").emision[t] <= fecha_sel]
    if fecha_sel == fecha_px and faltan:
        st.caption(f"⚠️ Sin precio para: {', '.join(faltan)}")

    m = fw.tabla_forward(px, bonos, liq, "TEA" if en_margen else tipo)
    if m.empty:
        st.info("No hay Letam con precio en esa fecha.")
        return
    if en_margen:
        m = fw.margen_tamar(m, proy)
    m.index.name = f"{fecha_sel:%d-%b}"
    if en_margen:
        st.caption("Margen sobre TAMAR = 12 × ((1 + TEA)^(1/12) − 1) − TAMAR proyectada. Diagonal: margen "
                   "de cada Letam. Fila i, columna j: margen de la forward entre el vencimiento de i y el de j.")
    else:
        st.caption("Diagonal: TIR (tasa directa) de cada Letam con la TAMAR proyectada. Fila i, columna j: "
                   "tasa forward implícita entre el vencimiento de i y el de j.")
    st.dataframe(estilo_matriz(m), use_container_width=True, height=36 * (len(m) + 1) + 3)

    with st.expander("Detalle por Letam"):
        r = fw.resumen(px, bonos, liq).merge(
            bonos[["ticker", "margen", "tamar_prom", "conocido"]], on="ticker")
        # Spread como en tu planilla: TIR.NO.PER (TEA, base 365) llevada a TNA de capitalización
        # mensual, menos la TAMAR proyectada: 12·((1+TIR)^(1/12) − 1) − TAMAR
        r["spread"] = ((1 + r.TEA) ** (1 / 12) - 1) * 12 - proy
        extrap = []
        if curva_rem is not None:
            z = fw.zdm_tabla(px, bonos, fecha_sel, tamar, curva_rem, feriados, plazo)
            if not z.empty:
                r = r.merge(z[["ticker", "zdm", "extrapolado"]], on="ticker", how="left")
                extrap = r.loc[r.extrapolado.fillna(False).astype(bool), "ticker"].tolist()
        if "zdm" not in r:
            r["zdm"] = np.nan
        r = r[["ticker", "vencimiento", "dias", "precio", "margen", "tamar_prom", "conocido", "vf",
               "TEM", "TNA", "TEA", "spread", "zdm"]]
        r.columns = ["Ticker", "Vencimiento", "Días", "Precio", "Margen", "TAMAR prom. ventana",
                     "% ventana publicada", "Pago final est.", "TEM", "TNA", "TEA", "Spread vs TAMAR",
                     "ZDM (REM)"]
        r["Ticker"] = [t + (" *" if t in extrap else "") for t in r["Ticker"]]
        st.dataframe(r.style.format({"Precio": "{:.3f}", "Margen": "{:.2%}", "TAMAR prom. ventana": "{:.2%}",
                                     "% ventana publicada": "{:.0%}", "Pago final est.": "{:.3f}",
                                     "TEM": "{:.2%}", "TNA": "{:.2%}", "TEA": "{:.2%}",
                                     "Spread vs TAMAR": "{:+.2%}", "ZDM (REM)": "{:+.2%}"}, na_rep=""),
                     use_container_width=True, hide_index=True)
        st.caption("ZDM (REM) = Z tal que precio = pago proyectado / Π (1 + (TAMAR esperada del mes + Z)/12)"
                   "^(días·12/365); el pago también se proyecta con esa curva. Con TAMAR constante coincide "
                   "con el Spread vs TAMAR."
                   + (f" * {', '.join(extrap)}: vencen después del último mes de la curva, se usa tu último "
                      "valor plano." if extrap else ""))
        st.caption("Pago final = 100 × (1 + TEM)^(meses 30/360 desde la emisión), con "
                   "TEM = [(1 + (TAMAR prom. + margen) × 32/365)^(365/32)]^(1/12) − 1. La TAMAR promedio "
                   "es la de la ventana [emisión − 10 hábiles, vencimiento − 10 hábiles]: lo publicado "
                   "sale de la hoja Tamar y lo que falta, de la TAMAR proyectada. "
                   "Spread vs TAMAR = 12 × ((1 + TEA)^(1/12) − 1) − TAMAR proyectada, con TEA = "
                   "TIR.NO.PER (base 365).")


def vista_historica_letam(proy_hoy: float, en_margen: bool, base_ltm: pd.DataFrame):
    vivas = base_ltm[base_ltm.vencimiento > hoy].ticker.tolist()
    if len(vivas) < 2:
        st.info("Hacen falta al menos dos Letam vivas.")
        return
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("Letam corta", vivas, index=0, key="a_ltm")
    resto = [t for t in vivas if t != a]
    b = c2.selectbox("Letam larga", resto, index=len(resto) - 1, key="b_ltm")
    ver_tir = c3.checkbox("Mostrar cada Letam", value=True, key="tir_ltm")

    proy_vivo = proy_hoy if live is not None else None
    s = fw.forward_historica_letam(hist_full, base_ltm, a, b, tamar, feriados, plazo,
                                   "TEA" if en_margen else tipo, proyectada_hoy=proy_vivo, hoy=hoy)
    if s.empty:
        st.info("No hay precios en común para ese par.")
        return
    if en_margen:  # cada día contra la TAMAR proyectada de ese día
        proys = pd.Series([proy_vivo if (proy_vivo is not None and d.date() == hoy)
                           else fw.tamar_al(d.date(), tamar) for d in s.index], index=s.index)
        s = s.apply(lambda col: fw.margen_tamar(col, proys))
    corto, largo = [c.replace("TIR ", "") for c in s.columns[:2]]
    nombre_fwd = f"Forward {corto} → {largo}"
    kpis(s["Forward"])
    s_graf = s.rename(columns={"Forward": nombre_fwd})[[nombre_fwd, f"TIR {corto}", f"TIR {largo}"]]
    grafico_serie(s_graf if ver_tir else s_graf[[nombre_fwd]],
                  "Margen sobre TAMAR" if en_margen else tipo)
    st.caption("Cada día se proyecta la TAMAR futura igual a la última publicada ese día "
               "(hoy, la TAMAR proyectada de arriba)"
               + (". En modo margen, las líneas de cada Letam son su margen sobre TAMAR." if en_margen else "."))
    st.download_button("Descargar serie (CSV)", s.to_csv().encode(),
                       f"forward_{corto}_{largo}.csv", "text/csv", key="dl_ltm")


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
AZUL, GRIS, NARANJA = "#1f6feb", "#8b949e", "#d29922"


def precios_de(fecha):
    return precios_hoy if fecha == fecha_px else hist.loc[fecha]


def puntos_curva(curva: str, fecha, sel_hd: list[str], eje: str = "Tasa directa",
                 con_duales: bool = False, curva_rem=None) -> pd.DataFrame:
    px = precios_de(fecha)
    liq = fw.sumar_habiles(fecha, plazo, feriados) if plazo else fecha
    if curva == "Tasa fija":
        p = fw.puntos_cero_cupon(px, lecaps, liq)
    elif curva == "CER":
        base_c = pd.concat([lecer, duales_cer], ignore_index=True) if con_duales else lecer
        fijos = fw.cer_ya_fijo(base_c, fecha, cer, feriados)
        vivas = base_c[~base_c.ticker.isin(fijos)]
        p = fw.puntos_cero_cupon(px, fw.lecer_a_fecha(vivas, coef_cer(liq)), liq)
    elif curva == "TAMAR":
        # Hoy: la TAMAR proyectada de la solapa Letam (si se tocó); otra fecha: la publicada ese día
        proy = (st.session_state["tamar_proy"] / 100
                if fecha == fecha_px and "tamar_proy" in st.session_state else fw.tamar_al(fecha, tamar))
        base_t = pd.concat([letam, duales_tamar], ignore_index=True) if con_duales else letam
        p = fw.puntos_cero_cupon(px, fw.letam_a_fecha(base_t, fecha, tamar, proy, feriados), liq)
        if eje == "Margen sobre TAMAR":  # TIR (TEA) → margen, sin pasar por la unidad de la barra lateral
            p = p.dropna(subset=["TIR", "duration"]).copy()
            p["TIR"] = fw.margen_tamar(p["TIR"], proy)
            return p.sort_values("duration").reset_index(drop=True)
        if eje == "ZDM (REM)" and curva_rem is not None:
            z = fw.zdm_tabla(px, base_t, fecha, tamar, curva_rem, feriados, plazo)
            p = p.drop(columns="TIR").merge(z[["ticker", "zdm"]].rename(columns={"zdm": "TIR"}), on="ticker")
            p = p.dropna(subset=["TIR", "duration"])
            return p.sort_values("duration").reset_index(drop=True)
    elif curva == "Pesos (fija + TAMAR)":
        # Lecaps + Letam con su TIR nominal: el pago de las Letam se proyecta con tu curva de TAMAR
        # (REM + tus meses); sin REM, con la TAMAR publicada ese día constante.
        proy = curva_rem if curva_rem is not None else fw.tamar_al(fecha, tamar)
        pl = fw.puntos_cero_cupon(px, lecaps, liq)
        pt = fw.puntos_cero_cupon(px, fw.letam_a_fecha(letam, fecha, tamar, proy, feriados), liq)
        p = pd.concat([pl, pt], ignore_index=True)
    else:
        p = fw.puntos_hd(px, flujos, liq, "D" if curva == "HD MEP" else "C", sel_hd)
    p = p.dropna(subset=["TIR", "duration"]).copy()
    p["TIR"] = fw.tasa_hd(p["TIR"], tipo)  # misma unidad que el resto de la app
    return p.sort_values("duration").reset_index(drop=True)


def plana_fuera_de_rango(fn, duraciones):
    """La curva ajustada fuera del rango de los bonos del ajuste sigue plana (no extrapola la forma)."""
    if fn is None or len(duraciones) == 0:
        return fn
    lo, hi = float(np.min(duraciones)), float(np.max(duraciones))
    return lambda x: fn(np.clip(np.asarray(x, dtype=float), lo, hi))


def fecha_anterior(objetivo):
    previas = [d for d in hist.index if d <= objetivo]
    return previas[-1] if previas else None


def vista_curvas():
    c1, c2 = st.columns([3, 1])
    curva = c1.radio("Curva", ["Tasa fija", "CER", "TAMAR", "Pesos (fija + TAMAR)", "HD MEP", "HD Cable"],
                     horizontal=True, key="curva_tipo")
    modelo = c2.selectbox("Ajuste", ["Nelson-Siegel", "Logarítmica"], key="curva_modelo")
    sel_hd = filtro_ley("curva") if curva.startswith("HD") else []
    eje = st.radio("Eje vertical", ["Tasa directa", "Margen sobre TAMAR", "ZDM (REM)"], horizontal=True,
                   key="curva_tamar_eje") if curva == "TAMAR" else "Tasa directa"
    en_margen = eje != "Tasa directa"
    curva_rem = None
    if curva == "Pesos (fija + TAMAR)" or eje == "ZDM (REM)":
        curva_rem = editor_tamar_esperada("curva")
        if curva_rem is None and eje == "ZDM (REM)":
            st.info("Para el ZDM hace falta el REM en el repo (relevamiento-expectativas-mercado-tablas-AAAA-MM.xlsx).")
            return
    solo_fija = curva == "Pesos (fija + TAMAR)" and st.radio(
        "Ajustar la curva con", ["Solo tasa fija", "Lecaps y Letam"], horizontal=True,
        key="curva_pesos_ajuste") == "Solo tasa fija"
    if curva == "Pesos (fija + TAMAR)":
        st.caption("Letam con su TIR nominal: el pago se proyecta con la TAMAR esperada (REM + tus meses). "
                   + ("La curva se ajusta solo con Lecaps: el spread de cada Letam dice si está rica o barata "
                      "contra la tasa fija, según tu visión de TAMAR." if solo_fija else
                      "La curva se ajusta con Lecaps y Letam juntas."))
    con_duales = curva in ("CER", "TAMAR") and eje != "ZDM (REM)" and (not duales.empty) and st.checkbox(
        f"Incluir duales CER-TAMAR (pata {curva})", key="curva_duales")
    if con_duales:
        st.caption(f"ℹ️ Duales con ◆. Pagan el máximo entre CER y TAMAR: su tasa por la pata {curva} es un "
                   "piso. Si no querés que muevan la curva, sacalos del ajuste.")

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

    p = puntos_curva(curva, fecha, sel_hd, eje, con_duales, curva_rem)
    if len(p) < 2:
        st.info("No hay suficientes bonos con precio para armar la curva en esa fecha.")
        return
    excluir = st.multiselect("Excluir del ajuste (se siguen mostrando)", p.ticker.tolist(),
                             key=f"curva_excl_{curva}_{int(con_duales)}")
    es_letam = p.ticker.isin(set(letam.ticker))
    usar = ~p.ticker.isin(excluir)
    if solo_fija:
        usar = usar & ~es_letam
    fn, desc = fw.ajustar_curva(p.duration[usar], p.TIR[usar], modelo)
    fn = plana_fuera_de_rango(fn, p.duration[usar])
    extrap = p.loc[(p.duration > p.duration[usar].max() + 1e-9) | (p.duration < p.duration[usar].min() - 1e-9),
                   "ticker"].tolist() if fn is not None else []
    if fn is None:
        st.info(desc)
        return
    p["curva"] = fn(p.duration)
    p["spread"] = (p.TIR - p.curva) * 1e4

    pc, fn_c = None, None
    if fecha_c is not None:
        pc = puntos_curva(curva, fecha_c, sel_hd, eje, con_duales, curva_rem)
        pc = pc[~pc.ticker.isin(excluir)]
        if len(pc) >= 2:
            usar_c = ~pc.ticker.isin(set(letam.ticker)) if solo_fija else pc.ticker.notna()
            fn_c, _ = fw.ajustar_curva(pc.duration[usar_c], pc.TIR[usar_c], modelo)
            fn_c = plana_fuera_de_rango(fn_c, pc.duration[usar_c])
            p = p.merge(pc[["ticker", "TIR"]].rename(columns={"TIR": "TIR_c"}), on="ticker", how="left")
            p["var"] = (p.TIR - p.TIR_c) * 1e4

    unidad = "s/TAMAR" if en_margen else f"{tipo}{' real' if curva == 'CER' else ''}"
    nom = {"Margen sobre TAMAR": "Margen", "ZDM (REM)": "ZDM"}.get(eje, "TIR")
    if curva == "TAMAR":
        st.caption("Letam: TIR con el pago final estimado con la TAMAR proyectada (hoy, la de la solapa "
                   "Letam; en otras fechas, la publicada ese día). Con menos de 6 especies el ajuste "
                   "pasa a logarítmico.")
    if eje == "Margen sobre TAMAR":
        st.caption("Margen sobre TAMAR = 12 × ((1 + TEA)^(1/12) − 1) − TAMAR proyectada.")
    elif eje == "ZDM (REM)":
        st.caption("ZDM = spread sobre la TAMAR esperada mes a mes (REM + tus meses). Letam que vencen "
                   "después del último mes de la curva usan tu último valor plano.")
    st.caption(f"{desc}. Ajuste sobre {nom} ({unidad}) contra duration en años. Spread = {nom} − curva: "
               "positivo = el bono rinde más que la curva (**barato**); negativo = **rico**.")
    if extrap:
        st.caption(f"⚠️ {', '.join(extrap)}: fuera del rango de los bonos del ajuste; la curva se extiende "
                   "plana, así que su spread depende de ese supuesto.")

    # ---- gráfico
    xs = np.linspace(max(p.duration.min() * 0.9, 1e-3), p.duration.max() * 1.03, 200)
    fig = go.Figure()
    if fn_c is not None:
        fig.add_scatter(x=xs, y=fn_c(xs), mode="lines", name=f"Curva {fecha_c:%d-%m-%y}",
                        line=dict(color=GRIS, width=2, dash="dash"), hoverinfo="skip")
        fig.add_scatter(x=pc.duration, y=pc.TIR, mode="markers", name=f"Bonos {fecha_c:%d-%m-%y}",
                        marker=dict(color=GRIS, size=8, symbol="circle-open"),
                        customdata=pc[["ticker"]],
                        hovertemplate="%{customdata[0]}<br>Duration %{x:.2f}<br>" + nom
                                      + " %{y:.2%}<extra></extra>")
    fig.add_scatter(x=xs, y=fn(xs), mode="lines", name=f"Curva {fecha:%d-%m-%y}",
                    line=dict(color=AZUL, width=2), hoverinfo="skip")
    dual = p.ticker.isin(es_dual)
    pesos = curva == "Pesos (fija + TAMAR)"
    grupos = [(usar & ~dual & ~(es_letam & pesos), "Lecaps" if pesos else f"Bonos {fecha:%d-%m-%y}",
               "circle", AZUL),
              (usar & dual, "Duales CER-TAMAR", "diamond", AZUL),
              (es_letam & pesos, "Letam (TAMAR esperada)" + (" · fuera del ajuste" if solo_fija else ""),
               "square", NARANJA),
              (~usar & ~(es_letam & pesos), "Excluidos del ajuste", "x", AZUL)]
    for incl, nombre, simbolo, color in grupos:
        q = p[incl]
        if q.empty:
            continue
        fig.add_scatter(x=q.duration, y=q.TIR, mode="markers+text", name=nombre, text=q.ticker,
                        textposition="top center", textfont=dict(size=11),
                        marker=dict(color=color, size=9, symbol=simbolo,
                                    line=dict(color="white", width=1)),
                        customdata=np.column_stack([q.ticker, q.spread]),
                        hovertemplate="%{customdata[0]}<br>Duration %{x:.2f}<br>" + nom
                                      + " %{y:.2%}<br>Spread %{customdata[1]:+.0f} pb<extra></extra>")
    fig.update_layout(height=520, xaxis_title="Duration (años)",
                      yaxis_title="Margen sobre TAMAR" if en_margen else unidad,
                      yaxis_tickformat=".1%", hovermode="closest",
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

    # ---- tabla
    cols = {"ticker": "Ticker", "vencimiento": "Vencimiento", "duration": "Duration",
            "precio": "Precio", "TIR": f"{nom} ({unidad})", "curva": f"{nom} curva",
            "spread": "Spread (pb)"}
    if "var" in p:
        cols["var"] = f"Δ {nom} vs {fecha_c:%d-%m} (pb)"
    t = p[list(cols)].rename(columns=cols)
    lim = max(float(p.spread.abs().max()), 1.0)
    fmt = {"Duration": "{:.2f}", "Precio": "{:.3f}", f"{nom} ({unidad})": "{:.2%}",
           f"{nom} curva": "{:.2%}", "Spread (pb)": "{:+.0f}"}
    if "var" in p:
        fmt[cols["var"]] = "{:+.0f}"
    st.dataframe(t.style.format(fmt, na_rep="")
                 .background_gradient(subset=["Spread (pb)"], cmap="RdYlGn", vmin=-lim, vmax=lim),
                 use_container_width=True, hide_index=True)
    st.download_button("Descargar tabla (CSV)", t.to_csv(index=False).encode(),
                       f"curva_{curva.replace(' ', '_')}_{fecha:%Y%m%d}.csv", "text/csv",
                       key="dl_curva")


# ---------------------------------------------------------------- opción TAMAR de los duales
@st.cache_data(show_spinner=False)
def serie_opcion(modelo_, excluir_, plazo_, firma_precios):
    # firma_precios: invalida el cache cuando cambian el histórico o los precios de hoy
    return fw.opcion_duales_historica(hist_full, lecer, duales, cer, feriados, plazo_, modelo_,
                                      list(excluir_))


def vista_opcion_duales():
    if duales.empty:
        st.info("No hay duales en la hoja 'Duales CER-TAMAR'.")
        return
    st.markdown(
        "Un dual paga el máximo entre su pata CER y su pata TAMAR, o sea: **dual = bono CER + opción "
        "de pasarse a TAMAR**. Sin modelo de opciones, el valor implícito de esa opción es "
        "**precio del dual − valor de su pata CER**, con la pata CER descontada a la TIR real que da "
        "la curva CER (solo CER puros) para ese plazo.")
    c1, c2, c3 = st.columns([1, 1, 2])
    opciones = [fecha_px] + [d for d in reversed(hist.index) if d != fecha_px]
    fecha_sel = c1.selectbox("Fecha", opciones, key="opc_fecha",
                             format_func=lambda d: f"{d:%d-%m-%Y}" + (" (hoy)" if d == fecha_px else ""))
    modelo = c2.selectbox("Ajuste de la curva CER", ["Nelson-Siegel", "Logarítmica"], key="opc_modelo")
    excluir = c3.multiselect("Excluir CER del ajuste", lecer.ticker.tolist(), key="opc_excl")

    t1, t2 = st.tabs(["Hoy", "Histórico"])
    with t1:
        px = precios_de(fecha_sel)
        t, desc = fw.opcion_duales(px, fecha_sel, lecer, duales, cer, feriados, plazo, modelo, excluir)
        if t.empty:
            st.info(f"No hay duales con precio en esa fecha. {desc}")
        else:
            st.caption(desc + ".")
            tabla = t.assign(extrapolado=t.extrapolado.map({True: "sí", False: ""}))
            tabla.columns = ["Dual", "Vencimiento", "Plazo (años)", "Precio", "Pago CER hoy",
                             "TIR real curva", "Valor pata CER", "Opción ($ c/100 VN)",
                             "Opción (% precio)", "TIR real del dual", "TIR que resigna (pb)",
                             "Curva extrapolada"]
            st.dataframe(tabla.style.format({
                "Plazo (años)": "{:.2f}", "Precio": "{:.2f}", "Pago CER hoy": "{:.2f}",
                "TIR real curva": "{:.2%}", "Valor pata CER": "{:.2f}", "Opción ($ c/100 VN)": "{:.2f}",
                "Opción (% precio)": "{:.1%}", "TIR real del dual": "{:.2%}",
                "TIR que resigna (pb)": "{:.0f}"}),
                use_container_width=True, hide_index=True)
            st.caption("TIR que resigna = TIR real de la curva − TIR real del dual por su pata CER: lo que "
                       "el mercado cede en rendimiento real a cambio de la opción. 'Curva extrapolada': el "
                       "dual vence después del CER puro más largo, así que su valor depende de suponer la "
                       "curva plana; tomalo con más cuidado.")
    with t2:
        firma = (str(hist_full.index[-1]), float(hist_full.reindex(columns=duales.ticker).iloc[-1].fillna(0).sum()),
                 len(hist_full))
        with st.spinner("Recalculando la curva CER de cada día..."):
            h = serie_opcion(modelo, tuple(excluir), plazo, firma)
        if h.empty:
            st.info("No hay histórico de precios de los duales.")
            return
        c1, c2 = st.columns([2, 1])
        elegidos = c1.multiselect("Duales", duales.ticker.tolist(), default=duales.ticker.tolist(),
                                  key="opc_duales")
        medida = c2.radio("Medida", ["% del precio", "$ cada 100 VN", "TIR que resigna (pb)"],
                          horizontal=True, key="opc_medida")
        col = {"% del precio": "opcion_pct", "$ cada 100 VN": "opcion",
               "TIR que resigna (pb)": "resigna_pb"}[medida]
        if not elegidos:
            st.info("Elegí al menos un dual.")
            return
        p = h[h.ticker.isin(elegidos)].pivot(index="fecha", columns="ticker", values=col)
        if len(elegidos) == 1:
            kpis(p[elegidos[0]].dropna(), pct=(col == "opcion_pct"),
                 unidad="" if col == "opcion" else " pb")
        fig = go.Figure()
        for tk in elegidos:
            if tk in p:
                fig.add_scatter(x=p.index, y=p[tk], name=tk, mode="lines", line=dict(width=2))
        fig.update_layout(height=460, hovermode="x unified", yaxis_title=medida,
                          yaxis_tickformat=".1%" if col == "opcion_pct" else ".2f",
                          margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Cada día se ajusta la curva CER con los CER puros de ese día y se valúa la pata CER de "
                   "cada dual. Los duales que vencen después del CER puro más largo usan la curva plana "
                   "desde ese punto.")
        st.download_button("Descargar serie (CSV)",
                           h.pivot(index="fecha", columns="ticker", values=col).to_csv().encode(),
                           "opcion_duales.csv", "text/csv", key="dl_opc")


if seccion == "Curvas":
    vista_curvas()

elif seccion == "Opción duales":
    vista_opcion_duales()

elif seccion == "Canje MEP/Cable":
    vista_canje()

else:  # Forwards
    tab_lec, tab_cer, tab_ltm, tab_mep, tab_ccl = st.tabs(
        ["Lecaps (tasa fija)", "CER (tasa real)", "Letam (TAMAR)", "Hard dollar · MEP",
         "Hard dollar · Cable"])

    with tab_lec:
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla("lec", lecaps, es_cer=False)
        with t2:
            vista_historica("lec", lecaps, es_cer=False)

    with tab_cer:
        incl = (not duales.empty) and st.checkbox(
            "Incluir duales CER-TAMAR (pata CER)", key="dual_cer")
        if incl:
            st.caption("ℹ️ Los duales pagan el máximo entre CER y TAMAR: su TIR real por la pata CER es un "
                       "piso, no se compara uno a uno con un CER puro.")
        base_cer = pd.concat([lecer, duales_cer], ignore_index=True) if incl else lecer
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla("cer", base_cer, es_cer=True)
        with t2:
            vista_historica("cer", base_cer, es_cer=True)

    with tab_ltm:
        proy = input_tamar()
        curva_rem = editor_tamar_esperada()
        en_margen = st.radio("Mostrar", ["Tasa directa", "Margen sobre TAMAR"], horizontal=True,
                             key="ltm_vista") == "Margen sobre TAMAR"
        incl = (not duales.empty) and st.checkbox(
            "Incluir duales CER-TAMAR (pata TAMAR: TAMAR + margen del dual)", key="dual_ltm")
        if incl:
            st.caption("ℹ️ Los duales pagan el máximo entre CER y TAMAR: su tasa por la pata TAMAR es un "
                       "piso, no se compara uno a uno con una Letam pura.")
        base_ltm = pd.concat([letam, duales_tamar], ignore_index=True) if incl else letam
        t1, t2 = st.tabs(["Tabla de forwards", "Forward histórica"])
        with t1:
            vista_tabla_letam(proy, en_margen, base_ltm, curva_rem)
        with t2:
            vista_historica_letam(proy, en_margen, base_ltm)

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
