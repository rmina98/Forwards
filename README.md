# Forwards implícitas · Lecaps

App de Streamlit que calcula la curva de Lecaps y las tasas forward implícitas entre cada par.

- **Tabla de forwards**: diagonal = TIR de cada Lecap; celda (i, j) = forward entre el vencimiento de i y el de j. Con precios en vivo de 1816 o cualquier cierre histórico.
- **Forward histórica**: elegís dos Lecaps y ves la evolución de la forward (y de sus TIR).
- Tasas en TEM / TNA (TEM×12) / TEA, liquidación T+1 o T+0.

## Datos (`data/`)
| archivo | contenido |
|---|---|
| `lecaps.csv` | ticker, emisión, vencimiento, TEM de emisión. Pago final = 100·(1+TEM)^(días 30/360 / 30) |
| `precios.csv` | cierres diarios (una columna por ticker; acepta precio cada 1 o cada 100 VN) |
| `feriados.csv` | feriados para calcular la fecha de liquidación |

Para sumar una Lecap nueva: agregá la fila en `lecaps.csv` y la columna en `precios.csv`.

## Correr local
```
pip install -r requirements.txt
streamlit run app.py
```
Clave de 1816 en `.streamlit/secrets.toml`:
```
API_1816_KEY = "..."
```
