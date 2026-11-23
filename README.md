# MultaClara

**Simulador y auditor de multas ambientales del OEFA, con lectura de resoluciones por IA.**

Proyecto del curso Legal Tech · Universidad Científica del Sur · 2026
D'Alessandro Bazán Huamaní · Evelyn Medrano Cercedo

👉 **Abrir la herramienta:** https://bzzdalessandro-maker.github.io/MultaClara1/

## Qué hace

MultaClara lee una resolución del OEFA, revisa si todavía procede sancionar, rehace el cálculo de la multa y explica cada número en palabras simples.

Funciona en cuatro capas:

1. **Leer:** sube el PDF de la resolución; la herramienta extrae los datos con su cita y autocompleta el caso.
2. **Caso y defensas:** revisa prescripción, caducidad y subsanación voluntaria.
3. **Cálculo:** aplica la metodología de 2013 o la de 2026 según la fecha de imputación: `Multa = B / p × (1 + F + I)`, con topes y reducciones.
4. **Resultados:** revisión de la multa, escenarios, borrador de descargos e informe de cálculo.

Tiene **Modo simple** (explica cada paso en lenguaje común) y **Modo experto** (todas las opciones técnicas).

Validación: reproduce las nueve multas de la RD N.º 00085-2026-OEFA/DFAI (31.916 UIT).

## Archivos

| Archivo | Qué es |
| --- | --- |
| `index.html` | La herramienta completa (HTML, CSS y JavaScript en un solo archivo; no necesita servidor) |
| `multaclara_lector.py` | Lector en lote en Python: lee varias resoluciones y genera `verificacion.csv` |
| `resolucion_ejemplo_ficticia_OEFA.pdf` | Resolución ficticia para probar la lectura (tiene un error a propósito en el hecho 3) |
| `requirements.txt` | Dependencia del script de Python (pypdf) |

## Lectura con IA

- **Sin IA:** «Leer sin IA (reglas)» funciona siempre.
- **Llama local con Ollama:** instala [Ollama](https://ollama.com), ejecuta `ollama pull llama3.2` y abre `index.html` desde tu computadora.
- **Llama en la nube con Groq:** crea una clave gratuita en https://console.groq.com/keys y pégala en la capa 1.

La IA solo extrae datos con su cita literal; MultaClara verifica cada cita, rehace la fórmula y nada entra al caso sin revisión humana.

## Script de Python

```bash
pip install -r requirements.txt
python multaclara_lector.py resolucion.pdf --motor ninguno   # sin IA: extrae el texto y la instrucción
python multaclara_lector.py resolucion.pdf                   # con Llama en Ollama
python multaclara_lector.py carpeta/ --lote                  # varias resoluciones → verificacion.csv
```

## Aviso

Prototipo académico. El resultado es orientativo y no reemplaza el informe técnico de cálculo del OEFA ni el criterio profesional.
