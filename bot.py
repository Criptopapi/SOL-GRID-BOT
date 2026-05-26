"""
SOL/USDC Grid Bot — Backend para Railway
Autor: generado con Claude
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

# ── Estado global del bot ──────────────────────────────────────
state = {
    "running": False,
    "orders": [],
    "filled": [],
    "pnl": 0.0,
    "grid_params": None,
    "sol_price": None,
    "log": [],
    "started_at": None,
}

# ── Utilidades ─────────────────────────────────────────────────
def log(msg, level="info"):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    state["log"].append(entry)
    state["log"] = state["log"][-100:]  # máximo 100 entradas
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
        r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": SYMBOL}, timeout=5)
        price = float(r.json()["price"])
        state["sol_price"] = price
        return price
    except Exception as e:
        log(f"Error obteniendo precio: {e}", "error")
        return state["sol_price"]

# ── Datos históricos para análisis ────────────────────────────
def get_klines(interval="4h", limit=90):
    try:
        r = requests.get(f"{BASE_URL}/api/v3/klines",
            params={"symbol": SYMBOL, "interval": interval, "limit": limit}, timeout=10)
        data = r.json()
        return [{"open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]),
                 "volume": float(k[5])} for k in data]
    except Exception as e:
        log(f"Error obteniendo klines: {e}", "error")
        return []

def calc_stats(klines):
    closes  = [k["close"]  for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]
    volumes = [k["volume"] for k in klines]

    avg  = lambda arr: sum(arr) / len(arr)
    std  = lambda arr: math.sqrt(avg([(x - avg(arr))**2 for x in arr]))

    recent = closes[-20:]
    older  = closes[-50:-20]
    trend  = "alcista" if avg(recent) > avg(older) else "bajista"
    vol    = round(std(closes) / avg(closes) * 100, 2)
    support    = round(min(lows[-30:]),    2)
    resistance = round(max(highs[-30:]),   2)

    # RSI simple
    gains = losses = 0
    for i in range(len(closes) - 14, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else:     losses -= d
    rs  = (gains / 14) / (losses / 14 + 0.001)
    rsi = round(100 - (100 / (1 + rs)), 1)

    avg_vol_recent = avg(volumes[-10:])
    avg_vol_old    = avg(volumes[-30:-10])
    vol_ratio = round(avg_vol_recent / (avg_vol_old + 0.001), 2)

    return {
        "trend": trend, "volatility": vol,
        "support": support, "resistance": resistance,
        "rsi": rsi, "vol_ratio": vol_ratio,
        "current": round(closes[-1], 2),
    }

# ── Análisis con Claude AI ─────────────────────────────────────
def analyze_with_ai(stats, capital):
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        log("ANTHROPIC_API_KEY no configurada", "error")
        return None

    prompt = f"""Eres un experto en grid bots de criptomonedas. Analiza SOL/USDC y da parámetros óptimos.

DATOS:
- Precio actual: ${stats['current']}
- Tendencia: {stats['trend']}
- Volatilidad: {stats['volatility']}%
- Soporte: ${stats['support']}
- Resistencia: ${stats['resistance']}
- RSI(14): {stats['rsi']}
- Ratio volumen: {stats['vol_ratio']}x
- Capital: ${capital} USDC

Responde SOLO con JSON válido (sin markdown):
{{
  "signal": "OPEN" | "WAIT" | "AVOID",
  "signal_reason": "razón corta",
  "price_min": número,
  "price_max": número,
  "grid_count": entero,
  "profit_per_grid_pct": número,
  "estimated_daily_profit_usdc": número,
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "rebalance_trigger": "condición para cerrar el grid"
}}"""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 800,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30)
        text = r.json()["content"][0]["text"]
        return json.loads(text.strip())
    except Exception as e:
        log(f"Error en análisis IA: {e}", "error")
        return None

# ── Información de símbolo (para redondeo) ─────────────────────
def get_symbol_info():
    try:
        r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=10)
        for s in r.json()["symbols"]:
            if s["symbol"] == SYMBOL:
                qty_filter  = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
                price_filter = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
                return {
                    "qty_step":   float(qty_filter["stepSize"]),
                    "price_step": float(price_filter["tickSize"]),
                    "min_qty":    float(qty_filter["minQty"]),
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
        log("Faltan API Key o Secret", "error")
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
            log(f"✗ Error orden #{i+1}: {result.get('msg','?')}", "error")

        time.sleep(0.2)  # evitar rate limit

    state["orders"] = created
    log(f"{len(created)} órdenes activas en el grid", "success")
    return len(created) > 0

# ── Monitoreo de órdenes llenadas ──────────────────────────────
def check_orders():
    if not state["orders"]:
        return
    try:
        open_orders_ids = set()
        result = binance_get("/api/v3/openOrders", {"symbol": SYMBOL})
        if isinstance(result, list):
            open_orders_ids = {o["orderId"] for o in result}

        for order in state["orders"]:
            if order["id"] not in open_orders_ids and order["status"] == "OPEN":
                order["status"] = "FILLED"
                profit = order["value"] * (state["grid_params"].get("profit_per_grid_pct", 0.5) / 100)
                state["pnl"] += profit
                state["filled"].append({**order, "filled_at": datetime.now().strftime("%H:%M:%S"), "profit": round(profit, 4)})
                log(f"💰 LLENADA: {order['side']} {order['qty']} SOL @ ${order['price']} | +${profit:.4f}", "success")

                # colocar orden de reversa
                new_side  = "SELL" if order["side"] == "BUY" else "BUY"
                new_price = round_step(order["price"] * (1 + (state["grid_params"].get("profit_per_grid_pct", 0.5)/100))
                                        if new_side == "SELL" else
                                        order["price"] * (1 - (state["grid_params"].get("profit_per_grid_pct", 0.5)/100)),
                                        0.01)
                info = get_symbol_info()
                qty  = round_step(order["value"] / new_price, info["qty_step"])
                params = {
                    "symbol": SYMBOL, "side": new_side, "type": "LIMIT",
                    "timeInForce": "GTC",
                    "price": f"{new_price:.2f}", "quantity": f"{qty}",
                }
                res = binance_post("/api/v3/order", params)
                if "orderId" in res:
                    order["id"]     = res["orderId"]
                    order["side"]   = new_side
                    order["price"]  = new_price
                    order["qty"]    = qty
                    order["status"] = "OPEN"
                    log(f"↺ Nueva orden reversa: {new_side} @ ${new_price}", "info")
    except Exception as e:
        log(f"Error verificando órdenes: {e}", "error")

# ── Loop principal del bot ─────────────────────────────────────
def bot_loop():
    log("Bot iniciado — monitoreando SOL/USDC", "success")
    while state["running"]:
        get_price()
        check_orders()

        # Re-análisis automático cada 4 horas
        elapsed = time.time() - state.get("last_analysis", 0)
        if elapsed > 4 * 3600:
            log("Re-análisis automático programado...", "info")
            klines = get_klines()
            if klines:
                stats  = calc_stats(klines)
                result = analyze_with_ai(stats, CAPITAL)
                state["last_analysis"] = time.time()
                if result:
                    state["grid_params"].update(result)
                    log(f"IA recomienda: {result['signal']} — {result.get('signal_reason','')}", "ai")
                    if result["signal"] == "AVOID":
                        log("⚠ IA recomienda detener el grid por riesgo alto", "warn")

        time.sleep(30)
    log("Bot detenido", "warn")

# ── API REST (para el dashboard) ───────────────────────────────
@app.route("/")
def index():
    return jsonify({"status": "SOL Grid Bot activo", "version": "1.0"})

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
    stats  = calc_stats(klines)
    result = analyze_with_ai(stats, CAPITAL)
    if result:
        state["grid_params"]    = result
        state["last_analysis"]  = time.time()
        log(f"Análisis completo — Señal: {result['signal']}", "success")
        return jsonify(result)
    return jsonify({"error": "Error en análisis IA"}), 500

@app.route("/start", methods=["POST"])
def start():
    if state["running"]:
        return jsonify({"error": "El bot ya está corriendo"}), 400
    params = request.json or {}
    gp = state.get("grid_params") or {}
    price_min  = params.get("price_min",  gp.get("price_min"))
    price_max  = params.get("price_max",  gp.get("price_max"))
    grid_count = params.get("grid_count", gp.get("grid_count"))
    if not all([price_min, price_max, grid_count]):
        return jsonify({"error": "Faltan parámetros. Primero llama /analyze"}), 400
    state["grid_params"] = {**gp, "price_min": price_min, "price_max": price_max, "grid_count": grid_count}
    ok = create_grid_orders(price_min, price_max, int(grid_count), CAPITAL)
    if not ok:
        return jsonify({"error": "No se pudieron crear las órdenes"}), 500
    state["running"]    = True
    state["started_at"] = datetime.now().isoformat()
    Thread(target=bot_loop, daemon=True).start()
    return jsonify({"status": "Bot iniciado", "orders": len(state["orders"])})

@app.route("/stop", methods=["POST"])
def stop():
    state["running"] = False
    # cancelar órdenes abiertas en Binance
    try:
        result = binance_delete("/api/v3/openOrders",
                     {"symbol": SYMBOL, "timestamp": int(time.time()*1000)})
        log(f"Órdenes canceladas en Binance", "warn")
    except Exception as e:
        log(f"Error cancelando órdenes: {e}", "error")
    state["orders"] = []
    return jsonify({"status": "Bot detenido", "pnl": state["pnl"]})

@app.route("/price")
def price():
    p = get_price()
    return jsonify({"symbol": SYMBOL, "price": p})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log(f"Servidor iniciando en puerto {port}", "info")
    app.run(host="0.0.0.0", port=port)
