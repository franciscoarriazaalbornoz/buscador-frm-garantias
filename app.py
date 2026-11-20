"""Buscador de Operación FRM para cobro de garantías — Grupo Summit Post Venta."""
import json, math, os, re, time
from collections import Counter
from pathlib import Path
import requests
from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).parent
FRM = json.loads((BASE / "frm.json").read_text(encoding="utf-8"))
SERPER_KEY = os.environ.get("SERPER_API_KEY", "")
ACCESS_KEY = os.environ.get("ACCESS_KEY", "")  # opcional: clave simple para el equipo

app = Flask(__name__, static_folder="static")

# ---------- normalización de texto ----------
ABBR = {"ASSY": "ASSEMBLY", "SUBASSY": "SUBASSEMBLY", "FR": "FRONT", "RR": "REAR", "CYL": "CYLINDER",
        "SW": "SWITCH", "CTRL": "CONTROL", "BRG": "BEARING", "GSKT": "GASKET", "UPR": "UPPER",
        "LWR": "LOWER", "INR": "INNER", "OTR": "OUTER", "CTR": "CENTER", "MTR": "MOTOR",
        "REG": "REGULATOR", "LAMP": "LIGHT", "HEADLAMP": "HEADLIGHT", "PADS": "PAD", "SHOCKS": "SHOCK",
        "FILTERS": "FILTER", "PUMPS": "PUMP", "SENSORS": "SENSOR", "BELTS": "BELT", "MOUNTING": "MOUNT",
        "INSULATOR": "INSULATOR", "WHL": "WHEEL", "STRG": "STEERING", "TRANS": "TRANSMISSION"}
STOP = set("THE AND OR FOR OF ONE SIDE BOTH ALL TOYOTA LEXUS GENUINE OEM PART PARTS NEW KIT SET WITH WITHOUT "
           "FITS FIT SHIPPING FREE PRICE BUY SALE ORIGINAL".split())

def toks(s):
    s = (s or "").upper()
    s = re.sub(r"SUB[- ]?ASS(Y|EMBLY)", "SUBASSEMBLY", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return [ABBR.get(t, t) for t in s.split() if len(t) > 1 and not any(c.isdigit() for c in t)]

for r in FRM:
    r["_t"] = set(toks(r["d"]))
DF = Counter(t for r in FRM for t in r["_t"])
N = len(FRM)
def idf(t): return math.log((N + 1) / (DF.get(t, 0) + 1))
GENERIC = {"ASSEMBLY", "SUBASSEMBLY", "AND", "OR", "ONE", "SIDE", "ALL", "BOTH"}
TYPE_W = {"R&R": 1.0, "OH": 0.9, "CHNG": 0.95}

def clean_pn(pn): return re.sub(r"[^0-9A-Z]", "", (pn or "").upper())

def score(weights, pn):
    """weights: {token: peso}. Devuelve operaciones ordenadas por puntaje."""
    out = []
    qset = set(weights)
    for r in FRM:
        if r["t"] == "ADD":
            continue
        s = sum(idf(t) * w for t, w in weights.items() if t in r["_t"])
        if s <= 0:
            continue
        s /= (1 + 0.15 * len(r["_t"] - qset - GENERIC))
        s *= TYPE_W.get(r["t"], 0.85)
        if pn and len(pn) >= 3 and r["c"][:3] == pn[:3]:
            s *= 1.6
        elif pn and len(pn) >= 2 and r["c"][:2] == pn[:2]:
            s *= 1.2
        out.append((s, r))
    out.sort(key=lambda x: -x[0])
    return out

def adds_for(code):
    base = code[:6]
    return [{"codigo": r["c"], "descripcion": r["d"]} for r in FRM if r["t"] == "ADD" and r["c"].startswith(base)]

def pack(results, k=6):
    if not results:
        return []
    top = results[0][0]
    return [{"codigo": r["c"], "descripcion": r["d"], "tipo": r["t"], "grupo": r["g"],
             "confianza": round(100 * s / top), "adicionales": adds_for(r["c"])} for s, r in results[:k]]

# ---------- búsqueda del nombre del repuesto en internet ----------
CACHE_FILE = BASE / "cache_nombres.json"
CACHE = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}

def nombre_desde_internet(pn_raw):
    pn = clean_pn(pn_raw)
    key = pn[:5]                       # Toyota: mismos 5 dígitos = mismo nombre de repuesto
    if key in CACHE:
        return CACHE[key], True
    if not SERPER_KEY:
        raise RuntimeError("Falta configurar SERPER_API_KEY en Render.")
    pretty = pn[:5] + "-" + pn[5:] if len(pn) > 5 else pn
    resp = requests.post("https://google.serper.dev/search",
                         headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
                         json={"q": f"Toyota {pretty} part", "num": 10}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    textos = [(o.get("title", ""), o.get("snippet", "")) for o in data.get("organic", [])]
    # voto de palabras: cuenta en cuántos resultados aparece cada palabra del catálogo FRM
    votos = Counter()
    for title, snip in textos:
        votos.update({t for t in toks(title) if t in DF and t not in STOP})
        votos.update({t: 0.5 for t in set(toks(snip)) if t in DF and t not in STOP})
    pesos = {t: v for t, v in votos.items() if v >= 1.5}
    # nombre legible: el título más representativo
    mejor = max(textos, key=lambda x: sum(pesos.get(t, 0) for t in set(toks(x[0]))), default=("", ""))[0]
    info = {"pesos": pesos, "titulo": mejor, "fuentes": [t for t, _ in textos[:5]], "ts": int(time.time())}
    if pesos:
        CACHE[key] = info
        try:
            CACHE_FILE.write_text(json.dumps(CACHE, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return info, False

# ---------- rutas ----------
def autorizado():
    return not ACCESS_KEY or request.headers.get("X-Access-Key") == ACCESS_KEY

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/api/config")
def config():
    return jsonify({"requiere_clave": bool(ACCESS_KEY), "operaciones": N})

@app.get("/api/parte")
def por_parte():
    if not autorizado():
        return jsonify({"error": "Clave de acceso incorrecta"}), 401
    pn = clean_pn(request.args.get("pn"))
    if len(pn) < 5:
        return jsonify({"error": "Ingresa al menos los 5 primeros dígitos del número de parte."}), 400
    try:
        info, cache = nombre_desde_internet(pn)
    except Exception as e:
        return jsonify({"error": f"No se pudo buscar en internet: {e}"}), 502
    res = score(info["pesos"], pn) if info["pesos"] else []
    return jsonify({"parte": pn, "nombre": info["titulo"], "desde_cache": cache,
                    "fuentes": info.get("fuentes", []), "resultados": pack(res)})

@app.get("/api/texto")
def por_texto():
    if not autorizado():
        return jsonify({"error": "Clave de acceso incorrecta"}), 401
    q = request.args.get("q", "")
    pn = clean_pn(request.args.get("pn", ""))
    pesos = {t: 1.0 for t in toks(q) if t in DF and t not in STOP}
    return jsonify({"resultados": pack(score(pesos, pn), 10) if pesos else []})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
