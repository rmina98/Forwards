# Forwards implícitas · Lecaps

App de Streamlit que calcula la curva de Lecaps y las tasas forward implícitas entre cada par.

- **Tabla de forwards**: diagonal = TIR de cada Lecap; celda (i, j) = forward entre el vencimiento de i y el de j. Con precios en vivo de 1816 o cualquier cierre histórico.
- **Forward histórica**: elegís dos Lecaps y ves la evolución de la forward (y de sus TIR).
- Tasas en TEM / TNA (TEM×12) / TEA, liquidación T+1 o T+0.

## Datos: `Inputs FW.xlsx`
Todo sale del Excel que está junto a `app.py`:
| hoja | contenido |
|---|---|
| `Lecaps` | Ticker, Emisión, Vencimiento, Tasa (TEM). Pago final = 100·(1+TEM)^(días 30/360 / 30) |
| `Precios` | Fecha + una columna por ticker (acepta precio cada 1 o cada 100 VN) |
| `Feriados` | fechas, para calcular la liquidación |

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
