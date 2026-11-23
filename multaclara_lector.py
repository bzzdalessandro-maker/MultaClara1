#!/usr/bin/env python3
"""
MultaClara · lector de resoluciones del OEFA con IA (Llama en Ollama o Llama en la nube con Groq)

Lee una resolución en PDF (o .txt), pide a un modelo de IA que extraiga los datos de la multa
con su cita textual, verifica las citas contra el documento y rehace la fórmula B / p × F.
Guarda un .json que se pega en MultaClara («Otra IA» → «Pegar respuesta») y, en modo lote,
un .csv con la verificación de todas las resoluciones de una carpeta.

Uso:
  python multaclara_lector.py resolucion.pdf                      # Llama local con Ollama (llama3.2)
  python multaclara_lector.py resolucion.pdf --motor nube         # Llama en la nube con Groq (clave en GROQ_API_KEY)
  python multaclara_lector.py carpeta_de_pdfs/ --lote             # varias resoluciones → verificacion.csv
  python multaclara_lector.py resolucion.pdf --motor ninguno      # solo extrae el texto y las instrucciones

Requisitos: Python 3.9+ y  pip install pypdf
Ollama:     https://ollama.com  →  ollama pull llama3.2
Groq:       clave gratuita en https://console.groq.com/keys  →  set GROQ_API_KEY=gsk_...
            (sirve otro servicio compatible con la API de OpenAI: --url y --modelo)
"""
import argparse, csv, json, os, re, sys, unicodedata, urllib.request, urllib.error
from pathlib import Path

LIMITE = {"ollama": 40000, "nube": 20000}
CAMPOS = ["tipo", "numero", "fecha", "expediente", "administrado", "unidad", "rsd", "fecha_imputacion", "metodologia", "total"]

PROMPT = """Eres un asistente legal que extrae datos de documentos del procedimiento administrativo sancionador del OEFA (Perú). Tu respuesta será verificada por una persona y por un programa que comprueba que cada "cita" aparezca literalmente en el documento.

Devuelve SOLO un objeto JSON con esta forma (sin texto antes ni después):
{"tipo":{"v":"Resolución directoral | Resolución subdirectoral (imputación de cargos) | Informe final de instrucción | otro","cita":"..."},
 "numero":{"v":"Resolución Directoral N.º ...","cita":"..."},
 "fecha":{"v":"AAAA-MM-DD","cita":"..."},
 "expediente":{"v":"...","cita":"..."},
 "administrado":{"v":"...","cita":"..."},
 "unidad":{"v":"...","cita":"..."},
 "rsd":{"v":"Resolución Subdirectoral N.º ... que inició el procedimiento","cita":"..."},
 "fecha_imputacion":{"v":"AAAA-MM-DD","cita":"..."},
 "metodologia":{"v":"2013 | 2026","cita":"..."},
 "total":{"v":0.000,"cita":"..."},
 "hechos":[{"n":1,"descripcion":"conducta imputada, breve","B":0.000,"p":0.00,"F":100,"M":0.000,"cita":"fragmento de la tabla de multa de ese hecho","cita_descripcion":"..."}]}

Reglas:
- "cita": copia LITERAL del documento (máximo 300 caracteres) que contenga el dato. No la parafrasees, no corrijas errores del original. Si necesitas saltar texto, usa "…".
- Si un dato no aparece, pon null en "v" (o en el número) y deja la cita vacía. No calcules, no deduzcas ni completes datos que el documento no dice. Si la multa declarada no cuadra con B, p y F, copia la que declara el documento: la verificación la hace el programa.
- "fecha_imputacion": la fecha en que se NOTIFICÓ al administrado la resolución subdirectoral que inició el procedimiento (no la fecha en que se emitió).
- Números con punto decimal. B y M en UIT. p como decimal (0.5). F en porcentaje (100 = sin factores agravantes ni atenuantes).
- Un elemento en "hechos" por cada hecho imputado con multa calculada, en el orden del documento.

DOCUMENTO («{nombre}»; los saltos entre extractos se marcan con […]):
<<<
{texto}
>>>"""


def leer_texto(ruta: Path) -> str:
    if ruta.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("Falta pypdf: ejecuta  pip install pypdf")
        lector = PdfReader(str(ruta))
        paginas = [(p.extract_text() or "") for p in lector.pages]
        texto = "\n".join(f"[[p. {i+1}]]\n{t}" for i, t in enumerate(paginas))
        if len(re.sub(r"\s", "", texto)) < 120 * max(1, len(paginas)):
            print(f"  Aviso: {ruta.name} casi no tiene texto (¿PDF escaneado?).", file=sys.stderr)
        return unicodedata.normalize("NFC", texto)
    return unicodedata.normalize("NFC", ruta.read_text(encoding="utf-8", errors="replace"))


def extracto(t: str, limite: int) -> str:
    """Si el documento no entra en el modelo, envía las partes con datos de la multa."""
    if len(t) <= limite:
        return t
    ventanas = [(0, 4500, 0), (len(t) - 5000, len(t), 3)]
    patrones = [(r"Resoluci[óo]n\s+Subdirectoral", 250, 700, 3, 1), (r"notificad[ao]", 300, 250, 5, 1),
                (r"Beneficio\s+Il[íi]cito\s*\(B\)", 350, 900, 40, 2), (r"multa\s+(?:total\s+)?ascendente\s+a", 400, 400, 4, 2),
                (r"035-2013-OEFA|00005-2026-OEFA", 150, 150, 2, 3), (r"hecho\s+imputado\s+n[.°º\s]*\s*\d+\s*[:.\-–]", 40, 500, 40, 4)]
    for pat, antes, despues, maximo, prioridad in patrones:
        for k, m in enumerate(re.finditer(pat, t, re.I)):
            if k >= maximo:
                break
            ventanas.append((max(0, m.start() - antes), min(len(t), m.start() + despues), prioridad))
    usado, elegidas = 0, []
    for a, b, _ in sorted(ventanas, key=lambda w: (w[2], w[0])):
        if usado + (b - a) <= limite:
            elegidas.append((a, b)); usado += b - a
    unidas = []
    for a, b in sorted(elegidas):
        if unidas and a <= unidas[-1][1] + 150:
            unidas[-1][1] = max(unidas[-1][1], b)
        else:
            unidas.append([a, b])
    return "\n[…]\n".join(t[a:b] for a, b in unidas)[:limite]


def llamar(motor: str, prompt: str, modelo: str, url_ollama: str, url_nube: str = "https://api.groq.com/openai/v1") -> str:
    if motor == "ollama":
        cuerpo = {"model": modelo, "messages": [{"role": "user", "content": prompt}], "stream": False,
                  "format": "json", "options": {"temperature": 0, "num_ctx": 16384}}
        req = urllib.request.Request(url_ollama.rstrip("/") + "/api/chat", data=json.dumps(cuerpo).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read())["message"]["content"]
    if motor == "nube":
        clave = os.environ.get("GROQ_API_KEY") or os.environ.get("MULTACLARA_CLAVE")
        if not clave:
            sys.exit("Define la variable GROQ_API_KEY con tu clave (se crea gratis en https://console.groq.com/keys).")
        base = url_nube.rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        cuerpo = {"model": modelo, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": "Respondes solo con un objeto JSON válido."},
            {"role": "user", "content": prompt}]}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(cuerpo).encode(),
                                     headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    raise ValueError(motor)


def a_json(respuesta: str) -> dict:
    a, b = respuesta.find("{"), respuesta.rfind("}")
    if a < 0 or b < a:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(respuesta[a:b + 1])


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s or "")).lower()
    s = s.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"[\s|]+", " ", s).strip()


def suelto(s: str) -> str:
    return re.sub(r"[^0-9a-záéíóúñü]", "", norm(s))


def verificar_cita(cita: str, texto: str) -> str:
    if not cita:
        return "sin cita"
    partes = [p.strip() for p in re.split(r"\s*(?:\.\.\.|…)\s*", norm(cita).strip("\"'")) if len(p.strip()) > 3]
    if not partes:
        return "sin cita"
    n, l = norm(texto), suelto(texto)
    if all(p in n for p in partes):
        return "literal"
    if all(len(suelto(p)) > 3 and suelto(p) in l for p in partes):
        return "aproximada"
    return "NO ENCONTRADA"


def numero(x):
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^\d.,\-]", "", str(x))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) and not s.startswith("0,") else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def verificar(datos: dict, texto: str) -> dict:
    informe = {"campos": {}, "hechos": [], "total": None}
    for c in CAMPOS:
        x = datos.get(c)
        if isinstance(x, dict) and x.get("v") not in (None, ""):
            informe["campos"][c] = {"valor": x["v"], "cita": verificar_cita(x.get("cita", ""), texto)}
    pv = {1, 0.75, 0.5, 0.25, 0.1}
    suma = 0.0
    for h in datos.get("hechos") or []:
        B, p, F, M = (numero(h.get(k)) for k in ("B", "p", "F", "M"))
        if p and p > 1:
            p = p / 100
        fila = {"n": h.get("n"), "descripcion": h.get("descripcion", ""), "B": B, "p": p, "F": F, "M": M,
                "cita": verificar_cita(h.get("cita", ""), texto), "recalculada": None, "cuadra": None, "p_valida": p in pv if p else None}
        if None not in (B, p, F) and p:
            fila["recalculada"] = round(B / p * F / 100, 3)
            if M is not None:
                fila["cuadra"] = abs(M - fila["recalculada"]) <= max(0.0015, abs(fila["recalculada"]) * 0.0005)
        suma += M or 0
        informe["hechos"].append(fila)
    tot = numero((datos.get("total") or {}).get("v")) if isinstance(datos.get("total"), dict) else numero(datos.get("total"))
    if tot is not None and informe["hechos"]:
        informe["total"] = {"declarado": tot, "suma_hechos": round(suma, 3), "cuadra": abs(suma - tot) <= max(0.0015, tot * 0.0005)}
    return informe


def imprimir(nombre: str, inf: dict):
    print(f"\n== {nombre}")
    for c, d in inf["campos"].items():
        print(f"  {c:17} {str(d['valor'])[:60]:60}  cita: {d['cita']}")
    for h in inf["hechos"]:
        estado = "cuadra" if h["cuadra"] else ("NO CUADRA" if h["cuadra"] is False else "sin datos")
        print(f"  Hecho {h['n']}: B {h['B']} · p {h['p']} · F {h['F']} % · declarada {h['M']} · recalculada {h['recalculada']} → {estado} · cita: {h['cita']}")
    if inf["total"]:
        t = inf["total"]
        print(f"  Total declarado {t['declarado']} · suma {t['suma_hechos']} → {'cuadra' if t['cuadra'] else 'NO CUADRA'}")


def procesar(ruta: Path, args) -> dict:
    texto = leer_texto(ruta)
    limite = LIMITE.get(args.motor, 40000)
    prompt = PROMPT.replace("{nombre}", ruta.name).replace("{texto}", extracto(texto, limite))
    if args.motor == "ninguno":
        ruta.with_suffix(".txt").write_text(texto, encoding="utf-8")
        ruta.with_name(ruta.stem + "_instrucciones.txt").write_text(prompt, encoding="utf-8")
        print(f"  Texto e instrucciones guardados junto a {ruta.name}. Pega las instrucciones en cualquier IA.")
        return {}
    if args.respuesta:
        respuesta = Path(args.respuesta).read_text(encoding="utf-8")
    else:
        modelo = args.modelo or ("llama3.2" if args.motor == "ollama" else "llama-3.3-70b-versatile")
        print(f"  Leyendo {ruta.name} con {args.motor} ({modelo})…")
        try:
            respuesta = llamar(args.motor, prompt, modelo, args.ollama, args.url)
        except urllib.error.HTTPError as e:
            sys.exit(f"  El servicio respondió {e.code}: {e.read()[:300].decode(errors='replace')}")
        except urllib.error.URLError as e:
            sys.exit(f"  No pude conectar ({e.reason}). ¿Está abierto Ollama o hay internet?")
    datos = a_json(respuesta)
    salida = ruta.with_name(ruta.stem + "_multaclara.json")
    salida.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    inf = verificar(datos, texto)
    imprimir(ruta.name, inf)
    print(f"  Guardado: {salida.name}  (pégalo en MultaClara → Otra IA → Pegar respuesta)")
    return inf


def main():
    ap = argparse.ArgumentParser(description="Lector de resoluciones del OEFA para MultaClara")
    ap.add_argument("ruta", help="PDF o .txt de la resolución, o una carpeta con --lote")
    ap.add_argument("--motor", choices=["ollama", "nube", "ninguno"], default="ollama")
    ap.add_argument("--modelo", help="llama3.2 (Ollama) o llama-3.3-70b-versatile (Groq), o el que prefieras")
    ap.add_argument("--url", default="https://api.groq.com/openai/v1", help="servicio en la nube compatible con la API de OpenAI (por defecto, Groq)")
    ap.add_argument("--ollama", default="http://localhost:11434", help="dirección del servidor Ollama")
    ap.add_argument("--lote", action="store_true", help="procesa todos los PDF de la carpeta y genera verificacion.csv")
    ap.add_argument("--respuesta", help="usa una respuesta JSON ya guardada en vez de llamar a la IA")
    args = ap.parse_args()
    ruta = Path(args.ruta)
    if args.lote:
        filas = []
        for pdf in sorted(ruta.glob("*.pdf")):
            inf = procesar(pdf, args)
            for h in inf.get("hechos", []):
                filas.append({"archivo": pdf.name, "expediente": inf["campos"].get("expediente", {}).get("valor", ""),
                              "hecho": h["n"], "B": h["B"], "p": h["p"], "F": h["F"], "multa_declarada": h["M"],
                              "multa_recalculada": h["recalculada"], "cuadra": h["cuadra"], "cita": h["cita"]})
        with open(ruta / "verificacion.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()) if filas else ["archivo"], delimiter=";")
            w.writeheader(); w.writerows(filas)
        print(f"\nResumen guardado en {ruta / 'verificacion.csv'} ({len(filas)} hechos).")
    else:
        procesar(ruta, args)


if __name__ == "__main__":
    main()
