"""
SOL/USDC Grid Bot — Backend para Railway
Análisis por algoritmo matemático (sin costo de IA)
"""

import os
import time
import hmac
import hashlib
import requests
import json
import math
from datetime import datetime
from flask import Flask, jsonify, request
from threading import Thread

app = Flask(__name__)

# ── Configuración ──────────────────────────────────────────────
API_KEY    = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
CAPITAL    = float(os.environ.get("CAPITAL_USDC", "60"))
SYMBOL     = "SOLUSDC"
BASE_URL   = "https://api.binance.com"

# ── Estado global ──────────────────────────────────────────────
state = {
    "running": False,
    "orders": [],
    "filled": [],
    "pnl": 0.0,
    "grid_params": None,
    "sol_price": None,
    "log": [],
    "started_at": None,
    "last_analysis": 0,
}

# ── Utilidades ─────────────────────────────────────────────────
def log(msg, level="info"):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    state["log"].append(entry)
    state["log"] = state["log"][-100:]
    print(f"[{entry['time']}] [{level.upper()}] {msg}")

def sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def headers():
    return {"X-MBX-APIKEY": API_KEY}

def binance_get(path, params=None):
    params = params or {}
    r = requests.get(BASE_URL + path, params=params, headers=headers(), timeout=10)
    return r.json()

def binance_post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    r = requests.post(BASE_URL + path, params=params, headers=headers(), timeout=10)
    return r.json()

def binance_delete(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    r = requests.delete(BASE_URL + path, params=params, headers=headers(), timeout=10)
    return r.json()

# ── Precio actual ──────────────────────────────────────────────
def get_price():
    try:
        r = requests.get(f"{BASE_URL}/api/v3/ticker/price",
                         params={"symbol": SYMBOL}, timeout=5)
        price = float(r.json()["price"])
        state["sol_price"] = price
        return price
    except Exception as e:
        log(f"Error obteniendo precio: {e}", "error")
        return state["sol_price"]

# ── Datos históricos ───────────────────────────────────────────
def get_klines(interval="4h", limit=90):
    try:
        r = requests.get(f"{BASE_URL}/api/v3/klines",
            params={"symbol": SYMBOL, "interval": interval, "limit": limit}, timeout=10)
        return [{"open": float(k[1]), "high": float(k[2]),
                 "low":  float(k[3]), "close": float(k[4]),
                 "volume": float(k[5])} for k in r.json()]
    except Exception as e:
        log(f"Error obteniendo klines: {e}", "error")
        return []

# ── Algoritmo matemático de análisis ──────────────────────────
def analyze_market(klines, capital):
    closes  = [k["close"]  for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]
    volumes = [k["volume"] for k in klines]

    avg = lambda arr: sum(arr) / len(arr)
    std = lambda arr: math.sqrt(avg([(x - avg(arr))**2 for x in arr]))

    current    = closes[-1]
    recent20   = closes[-20:]
    older30    = closes[-50:-20]
    trend      = "alcista" if avg(recent20) > avg(older30) else "bajista"
    volatility = std(closes) / avg(closes) * 100  # % desviación estándar

    support    = min(lows[-30:])
    resistance = max(highs[-30:])

    # RSI(14)
    gains = losses = 0
    for i in range(len(closes) - 14, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else:     losses -= d
    rs  = (gains / 14) / (losses / 14 + 0.001)
    rsi = 100 - (100 / (1 + rs))

    # Volumen relativo
    vol_ratio = avg(volumes[-10:]) / (avg(volumes[-30:-10]) + 0.001)

    # ── Decisión de señal ──────────────────────────────────────
    # AVOID: RSI extremo o precio fuera de rango histórico
    if rsi > 78:
        signal = "AVOID"
        signal_reason = f"RSI sobrecomprado ({rsi:.1f}). Esperar corrección."
    elif rsi < 25:
        signal = "AVOID"
        signal_reason = f"RSI sobrevendido ({rsi:.1f}). Mercado en caída fuerte."
    elif current > resistance * 0.98:
        signal = "WAIT"
        signal_reason = "Precio cerca de resistencia. Grid podría quedar fuera de rango."
    elif current < support * 1.02:
        signal = "WAIT"
        signal_reason = "Precio cerca de soporte. Esperar estabilización."
    elif volatility > 18:
        signal = "WAIT"
        signal_reason = f"Volatilidad alta ({volatility:.1f}%). Riesgo de salir del rango."
    else:
        signal = "OPEN"
        signal_reason = f"RSI neutro ({rsi:.1f}), volatilidad controlada ({volatility:.1f}%)."

    # ── Calcular rango del grid ────────────────────────────────
    # Usar soporte/resistencia ajustados + buffer de seguridad
    buffer     = volatility / 100 * current  # buffer proporcional a volatilidad
    price_min  = max(support  * 0.97, current - buffer * 3)
    price_max  = min(resistance * 1.03, current + buffer * 3)

    # Asegurar que el precio actual esté dentro del rango
    price_min  = min(price_min, current * 0.92)
    price_max  = max(price_max, current * 1.08)

    # Redondear a 2 decimales
    price_min  = round(price_min, 2)
    price_max  = round(price_max, 2)

    # ── Número de grillas ──────────────────────────────────────
    # Mínimo $5 USDC por orden (requisito Binance)
    max_grids  = int(capital / 5)
    # Más volatilidad = menos grillas (más margen entre órdenes)
    if volatility < 5:
        grid_count = min(max_grids, 10)
    elif volatility < 10:
        grid_count = min(max_grids, 8)
    else:
        grid_count = min(max_grids, 6)

    grid_count = max(grid_count, 2)

    # ── Ganancia estimada por grid ─────────────────────────────
    price_range_pct    = (price_max - price_min) / price_min * 100
    profit_per_grid    = round(price_range_pct / grid_count, 2)

    # ── Ganancia diaria estimada ───────────────────────────────
    # Asume que ~20% de las grillas se llenan por día en mercado normal
    fills_per_day      = grid_count * 0.2
    capital_per_order  = capital / grid_count
    est_daily          = round(fills_per_day * capital_per_order * profit_per_grid / 100, 4)

    # ── Nivel de riesgo ────────────────────────────────────────
    if volatility > 12 or rsi > 70 or rsi < 35:
        risk_level  = "HIGH"
        risk_reason = "Alta volatilidad o RSI extremo."
    elif volatility > 7 or abs(rsi - 50) > 15:
        risk_level  = "MEDIUM"
        risk_reason = "Volatilidad moderada."
    else:
        risk_level  = "LOW"
        risk_reason = "Mercado estable, RSI neutro."

    rebalance_trigger = (
        f"Si SOL cae por debajo de ${price_min * 0.95:.2f} "
        f"o sube sobre ${price_max * 1.05:.2f}"
    )

    return {
        "signal":                    signal,
        "signal_reason":             signal_reason,
        "price_min":                 price_min,
        "price_max":                 price_max,
        "grid_count":                grid_count,
        "profit_per_grid_pct":       profit_per_grid,
        "estimated_daily_profit_usdc": est_daily,
        "risk_level":                risk_level,
        "risk_reason":               risk_reason,
        "rebalance_trigger":         rebalance_trigger,
        "rsi":                       round(rsi, 1),
        "volatility":                round(volatility, 2),
        "support":                   round(support, 2),
        "resistance":                round(resistance, 2),
        "trend":                     trend,
        "current_price":             round(current, 2),
    }

# ── Info del símbolo (para redondeo) ───────────────────────────
def get_symbol_info():
    try:
        r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=10)
        for s in r.json()["symbols"]:
            if s["symbol"] == SYMBOL:
                qty_f   = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
                price_f = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
                return {
                    "qty_step":   float(qty_f["stepSize"]),
                    "price_step": float(price_f["tickSize"]),
                    "min_qty":    float(qty_f["minQty"]),
                }
    except Exception as e:
        log(f"Error obteniendo info símbolo: {e}", "error")
    return {"qty_step": 0.01, "price_step": 0.01, "min_qty": 0.01}

def round_step(value, step):
    if step == 0: return value
    precision = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(round(value / step) * step, precision)

# ── Crear órdenes del grid ─────────────────────────────────────
def create_grid_orders(price_min, price_max, grid_count, capital):
    if not API_KEY or not API_SECRET:
        log("Faltan API Key o Secret de Binance", "error")
        return False

    info     = get_symbol_info()
    price    = get_price()
    step     = (price_max - price_min) / (grid_count - 1)
    cap_each = capital / grid_count
    created  = []

    log(f"Creando {grid_count} órdenes | Rango ${price_min}–${price_max}", "info")

    for i in range(grid_count):
        grid_price = round_step(price_min + step * i, info["price_step"])
        qty        = round_step(cap_each / grid_price,  info["qty_step"])

        if qty < info["min_qty"]:
            log(f"Orden #{i+1} omitida: qty {qty} < mínimo {info['min_qty']}", "warn")
            continue

        side = "BUY" if grid_price < price else "SELL"

        params = {
            "symbol":      SYMBOL,
            "side":        side,
            "type":        "LIMIT",
            "timeInForce": "GTC",
            "price":       f"{grid_price:.2f}",
            "quantity":    f"{qty}",
        }

        result = binance_post("/api/v3/order", params)

        if "orderId" in result:
            order = {
                "id":     result["orderId"],
                "side":   side,
                "price":  grid_price,
                "qty":    qty,
                "value":  round(grid_price * qty, 2),
                "status": "OPEN",
            }
            created.append(order)
            log(f"✓ {side} {qty} SOL @ ${grid_price}", "success")
        else:
            log(f"✗ Error orden #{i+1}: {result.get('msg', '?')}", "error")

        time.sleep(0.2)

    state["orders"] = created
    log(f"{len(created)} órdenes activas en el grid", "success")
    return len(created) > 0

# ── Monitoreo de órdenes ───────────────────────────────────────
def check_orders():
    if not state["orders"] or not API_KEY:
        return
    try:
        result = binance_get("/api/v3/openOrders", {"symbol": SYMBOL})
        if not isinstance(result, list):
            return
        open_ids = {o["orderId"] for o in result}

        for order in state["orders"]:
            if order["id"] not in open_ids and order["status"] == "OPEN":
                order["status"] = "FILLED"
                profit_pct = state["grid_params"].get("profit_per_grid_pct", 0.5)
                profit     = order["value"] * (profit_pct / 100)
                state["pnl"] += profit
                state["filled"].append({
                    **order,
                    "filled_at": datetime.now().strftime("%H:%M:%S"),
                    "profit":    round(profit, 4),
                })
                log(f"💰 LLENADA: {order['side']} {order['qty']} SOL @ ${order['price']} | +${profit:.4f}", "success")

                # Orden de reversa
                info      = get_symbol_info()
                new_side  = "SELL" if order["side"] == "BUY" else "BUY"
                factor    = (1 + profit_pct / 100) if new_side == "SELL" else (1 - profit_pct / 100)
                new_price = round_step(order["price"] * factor, info["price_step"])
                qty       = round_step(order["value"] / new_price, info["qty_step"])

                params = {
                    "symbol": SYMBOL, "side": new_side, "type": "LIMIT",
                    "timeInForce": "GTC",
                    "price": f"{new_price:.2f}", "quantity": f"{qty}",
                }
                res = binance_post("/api/v3/order", params)
                if "orderId" in res:
                    order.update({"id": res["orderId"], "side": new_side,
                                  "price": new_price, "qty": qty, "status": "OPEN"})
                    log(f"↺ Reversa: {new_side} @ ${new_price}", "info")
    except Exception as e:
        log(f"Error verificando órdenes: {e}", "error")

# ── Verificar si hay que rebalancear ───────────────────────────
def check_rebalance():
    if not state["grid_params"] or not state["sol_price"]:
        return
    gp    = state["grid_params"]
    price = state["sol_price"]
    p_min = gp.get("price_min", 0)
    p_max = gp.get("price_max", 999999)

    if price < p_min * 0.95:
        log(f"⚠ SOL cayó bajo el rango (${price:.2f} < ${p_min * 0.95:.2f}). Considera re-analizar.", "warn")
    if price > p_max * 1.05:
        log(f"⚠ SOL superó el rango (${price:.2f} > ${p_max * 1.05:.2f}). Considera re-analizar.", "warn")

# ── Loop principal ─────────────────────────────────────────────
def bot_loop():
    log("Bot iniciado — monitoreando SOL/USDC cada 30s", "success")
    while state["running"]:
        get_price()
        check_orders()
        check_rebalance()

        # Re-análisis automático cada 4 horas
        if time.time() - state["last_analysis"] > 4 * 3600:
            log("Re-análisis automático programado...", "info")
            klines = get_klines()
            if klines:
                result = analyze_market(klines, CAPITAL)
                state["last_analysis"] = time.time()
                state["grid_params"].update(result)
                log(f"Re-análisis: Señal={result['signal']} RSI={result['rsi']} Volatilidad={result['volatility']}%", "info")
                if result["signal"] == "AVOID":
                    log("⚠ Algoritmo recomienda detener el grid. Revisa manualmente.", "warn")

        time.sleep(30)
    log("Bot detenido", "warn")

# ── API REST ───────────────────────────────────────────────────
@app.route("/")
def index():
    return jsonify({"status": "SOL Grid Bot activo", "version": "2.0-noai"})

@app.route("/state")
def get_state():
    return jsonify({
        "running":     state["running"],
        "sol_price":   state["sol_price"],
        "pnl":         round(state["pnl"], 4),
        "orders":      state["orders"],
        "filled":      state["filled"][-20:],
        "grid_params": state["grid_params"],
        "log":         state["log"][-30:],
    })

@app.route("/analyze", methods=["POST"])
def analyze():
    log("Iniciando análisis de mercado...", "info")
    klines = get_klines()
    if not klines:
        return jsonify({"error": "No se pudieron obtener datos de Binance"}), 500
    result = analyze_market(klines, CAPITAL)
    state["grid_params"]   = result
    state["last_analysis"] = time.time()
    log(f"Análisis completo — Señal: {result['signal']} | RSI: {result['rsi']} | Volatilidad: {result['volatility']}%", "success")
    return jsonify(result)

@app.route("/start", methods=["POST"])
def start():
    if state["running"]:
        return jsonify({"error": "El bot ya está corriendo"}), 400

    params = request.json or {}
    gp     = state.get("grid_params") or {}

    price_min  = params.get("price_min",  gp.get("price_min"))
    price_max  = params.get("price_max",  gp.get("price_max"))
    grid_count = params.get("grid_count", gp.get("grid_count"))

    if not all([price_min, price_max, grid_count]):
        return jsonify({"error": "Faltan parámetros. Primero llama /analyze"}), 400

    state["grid_params"] = {**gp, "price_min": price_min,
                            "price_max": price_max, "grid_count": grid_count}

    ok = create_grid_orders(float(price_min), float(price_max), int(grid_count), CAPITAL)
    if not ok:
        return jsonify({"error": "No se pudieron crear las órdenes en Binance"}), 500

    state["running"]    = True
    state["started_at"] = datetime.now().isoformat()
    Thread(target=bot_loop, daemon=True).start()
    return jsonify({"status": "Bot iniciado", "orders": len(state["orders"])})

@app.route("/stop", methods=["POST"])
def stop():
    state["running"] = False
    try:
        params = {"symbol": SYMBOL, "timestamp": int(time.time() * 1000)}
        params["signature"] = sign(params)
        binance_delete("/api/v3/openOrders", params)
        log("Todas las órdenes canceladas en Binance", "warn")
    except Exception as e:
        log(f"Error cancelando órdenes: {e}", "error")
    state["orders"] = []
    return jsonify({"status": "Bot detenido", "pnl_session": round(state["pnl"], 4)})

@app.route("/price")
def price():
    p = get_price()
    return jsonify({"symbol": SYMBOL, "price": p})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log(f"Servidor iniciando en puerto {port}", "info")
    app.run(host="0.0.0.0", port=port)
