"""Buscador de Operación FRM para cobro de garantías — Grupo Summit Post Venta."""
import base64, json, math, os, re, secrets, time
from collections import Counter
from pathlib import Path
import requests
from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).parent
FRM = json.loads((BASE / "frm.json").read_text(encoding="utf-8"))
SERPER_KEY = os.environ.get("SERPER_API_KEY", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")        # para guardar usuarios en el repo
GITHUB_REPO = os.environ.get("GITHUB_REPO", "franciscoarriazaalbornoz/buscador-frm-garantias")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)

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

# ---------- usuarios ----------
USERS_FILE = BASE / "usuarios.json"
USERS = json.loads(USERS_FILE.read_text(encoding="utf-8")) if USERS_FILE.exists() else {}
USERS_SHA = None

def _gh(method, **kw):
    return requests.request(method, f"https://api.github.com/repos/{GITHUB_REPO}/contents/usuarios.json",
                            headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                                     "Accept": "application/vnd.github+json"}, timeout=15, **kw)

def cargar_usuarios():
    """Al iniciar, trae la versión más reciente de usuarios.json desde GitHub."""
    global USERS, USERS_SHA
    if not GITHUB_TOKEN:
        return
    try:
        r = _gh("GET")
        if r.status_code == 200:
            d = r.json()
            USERS = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
            USERS_SHA = d["sha"]
    except Exception as e:
        print("No se pudo leer usuarios desde GitHub:", e)

def guardar_usuarios(motivo):
    global USERS_SHA
    data = json.dumps(USERS, ensure_ascii=False, indent=1)
    USERS_FILE.write_text(data, encoding="utf-8")
    if not GITHUB_TOKEN:
        return "sin_github"
    body = {"message": f"[skip render] usuarios: {motivo}",
            "content": base64.b64encode(data.encode("utf-8")).decode()}
    if USERS_SHA:
        body["sha"] = USERS_SHA
    r = _gh("PUT", json=body)
    if r.status_code in (200, 201):
        USERS_SHA = r.json()["content"]["sha"]
        return "ok"
    if r.status_code in (409, 422):          # el archivo cambió: recarga sha y reintenta una vez
        g = _gh("GET")
        if g.status_code == 200:
            body["sha"] = USERS_SHA = g.json()["sha"]
            r = _gh("PUT", json=body)
            if r.status_code in (200, 201):
                USERS_SHA = r.json()["content"]["sha"]
                return "ok"
    return f"error_github_{r.status_code}"

cargar_usuarios()

def usuario_actual():
    u = session.get("u")
    if not u:
        return None
    if u == ADMIN_USER and ADMIN_PASSWORD:
        return {"usuario": u, "nombre": "Administrador", "rol": "admin"}
    d = USERS.get(u)
    if not d or not d.get("activo", True):
        return None
    return {"usuario": u, "nombre": d.get("nombre", u), "rol": d.get("rol", "usuario")}

def requiere(rol=None):
    u = usuario_actual()
    if not u:
        return None, (jsonify({"error": "Debes iniciar sesión", "login": True}), 401)
    if rol == "admin" and u["rol"] != "admin":
        return None, (jsonify({"error": "Solo el administrador puede hacer esto"}), 403)
    return u, None

# ---------- rutas ----------
@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")

@app.get("/api/config")
def config():
    return jsonify({"operaciones": N, "usuario": usuario_actual()})

@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = (d.get("usuario") or "").strip().lower()
    p = d.get("clave") or ""
    ok = False
    if u == ADMIN_USER and ADMIN_PASSWORD and secrets.compare_digest(p, ADMIN_PASSWORD):
        ok = True
    elif u in USERS and USERS[u].get("activo", True) and check_password_hash(USERS[u]["hash"], p):
        ok = True
    if not ok:
        time.sleep(0.8)
        return jsonify({"error": "Usuario o clave incorrectos"}), 401
    session.permanent = True
    session["u"] = u
    return jsonify({"usuario": usuario_actual()})

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.post("/api/mi-clave")
def mi_clave():
    u, err = requiere()
    if err: return err
    if u["usuario"] not in USERS:
        return jsonify({"error": "La clave del administrador se cambia en Render (ADMIN_PASSWORD)."}), 400
    d = request.get_json(silent=True) or {}
    if not check_password_hash(USERS[u["usuario"]]["hash"], d.get("actual", "")):
        return jsonify({"error": "La clave actual no es correcta"}), 400
    nueva = d.get("nueva", "")
    if len(nueva) < 6:
        return jsonify({"error": "La nueva clave debe tener al menos 6 caracteres"}), 400
    USERS[u["usuario"]]["hash"] = generate_password_hash(nueva)
    return jsonify({"ok": True, "guardado": guardar_usuarios(f"cambio de clave {u['usuario']}")})

@app.get("/api/usuarios")
def listar_usuarios():
    _, err = requiere("admin")
    if err: return err
    lista = [{"usuario": k, "nombre": v.get("nombre", ""), "sucursal": v.get("sucursal", ""),
              "rol": v.get("rol", "usuario"), "activo": v.get("activo", True), "creado": v.get("creado", "")}
             for k, v in sorted(USERS.items())]
    return jsonify({"usuarios": lista, "persistencia": "github" if GITHUB_TOKEN else "temporal"})

@app.post("/api/usuarios")
def crear_o_editar_usuario():
    admin, err = requiere("admin")
    if err: return err
    d = request.get_json(silent=True) or {}
    u = re.sub(r"[^a-z0-9._-]", "", (d.get("usuario") or "").strip().lower())
    if len(u) < 3:
        return jsonify({"error": "El usuario debe tener al menos 3 caracteres (letras, números, punto o guion)"}), 400
    if u == ADMIN_USER:
        return jsonify({"error": "Ese nombre de usuario está reservado"}), 400
    nuevo = u not in USERS
    clave = d.get("clave") or ""
    if nuevo and len(clave) < 6:
        return jsonify({"error": "La clave debe tener al menos 6 caracteres"}), 400
    reg = USERS.get(u, {"creado": time.strftime("%Y-%m-%d")})
    reg["nombre"] = (d.get("nombre") or reg.get("nombre") or u).strip()
    reg["sucursal"] = (d.get("sucursal") or reg.get("sucursal") or "").strip()
    reg["rol"] = "admin" if d.get("rol") == "admin" else "usuario"
    reg["activo"] = bool(d.get("activo", reg.get("activo", True)))
    if clave:
        if len(clave) < 6:
            return jsonify({"error": "La clave debe tener al menos 6 caracteres"}), 400
        reg["hash"] = generate_password_hash(clave)
    USERS[u] = reg
    estado = guardar_usuarios(("alta " if nuevo else "cambio ") + u)
    return jsonify({"ok": True, "nuevo": nuevo, "guardado": estado})

@app.get("/api/parte")
def por_parte():
    _, err = requiere()
    if err: return err
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
    _, err = requiere()
    if err: return err
    q = request.args.get("q", "")
    pn = clean_pn(request.args.get("pn", ""))
    pesos = {t: 1.0 for t in toks(q) if t in DF and t not in STOP}
    return jsonify({"resultados": pack(score(pesos, pn), 10) if pesos else []})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
