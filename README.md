# Forwards implícitas · Lecaps y CER

App de Streamlit con una solapa de **Curvas** (TIR vs duration con ajuste Nelson-Siegel o logarítmico, rico/barato en pb y comparación contra otra fecha) y cinco solapas de forwards: **Lecaps** (tasa fija), **CER** (tasa real), **Hard dollar MEP** y **Hard dollar Cable** (Globales y Bonares, forward por TIR y duration de Macaulay) y **Canje MEP/Cable** ((D/C − 1) × 100). Calcula la curva y las forwards implícitas entre cada par.

- **Tabla de forwards**: diagonal = TIR de cada Lecap; celda (i, j) = forward entre el vencimiento de i y el de j. Con precios en vivo de 1816 o cualquier cierre histórico.
- **Forward histórica**: elegís dos Lecaps y ves la evolución de la forward (y de sus TIR).
- Tasas en TEM / TNA (TEM×12) / TEA, liquidación T+1 o T+0.

## Datos: `Inputs FW.xlsx`
Todo sale del Excel que está junto a `app.py`:
| hoja | contenido |
|---|---|
| `Lecaps` | Ticker, Emisión, Vencimiento, Tasa (TEM). Pago final = 100·(1+TEM)^(días 30/360 / 30) |
| `Precios` | Fecha + una columna por ticker (acepta precio cada 1 o cada 100 VN) |
| `LECER` | Ticker, Emisión, Vencimiento, Tasa, CER inicial. Pago = 100·(1+Tasa)·CER ref / CER inicial |
| `CER` | Fecha, CER (se descarta la cola constante de proyección) |
| `Flujo HD` | Ticker, Fecha de pago, Amortización, Valor residual, Tasa, Interés (vn). Montos como fracción del VN original |
| `Precios` (HD) | columnas TICKER+D (MEP) y TICKER+C (cable), precio cada 1 o 100 VN original |
| `Feriados` | fechas, para calcular la liquidación |

CER de referencia: el de 10 días hábiles antes de la liquidación (misma convención que app_BE).

Para sumar una Lecap nueva: fila en `Lecaps` y columna en `Precios`. Al subir el Excel actualizado a GitHub, la app lo recarga sola.

## Correr local
```
pip install -r requirements.txt
streamlit run app.py
```
Clave de 1816 en `.streamlit/secrets.toml`:
```
API_1816_KEY = "..."
```


## Cierre diario automático (GitHub Actions)
`.github/workflows/cierre_diario.yml` corre lunes a viernes a las 17:30 (Argentina) y ejecuta
`guardar_cierre.py`: pide a 1816 los precios de todas las especies vivas del Excel y agrega la fila
del día a `precios_1816.csv`. La app suma ese CSV al histórico de la hoja Precios (si una fecha está
en los dos, manda el Excel). Feriados: los saltea según la hoja Feriados.

Requiere el secret `API_1816_KEY` en GitHub: Settings → Secrets and variables → Actions.
Para correrlo a mano: pestaña Actions → "Cierre diario 1816" → Run workflow.

Límites del plan 1816 que respeta `api_1816.py`: 1 pedido por segundo y reuso del token (máx. 50 por día).
