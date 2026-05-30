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
        data = r.json()
        if "price" not in data:
            log(f"Binance responde error: {data}", "error")
            return state["sol_price"]
        price = float(data["price"])
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
        log(f"Error obteniendo klines {interval}: {e}", "error")
        return []

def get_multi_timeframe():
    """Obtiene datos de 3 temporalidades simultáneamente."""
    tf = {}
    # 1h — 168 velas = 7 días
    tf["1h"] = get_klines("1h", 168)
    # 4h — 90 velas = 15 días
    tf["4h"] = get_klines("4h", 90)
    # 1d — 30 velas = 30 días
    tf["1d"] = get_klines("1d", 30)
    return tf

# ── Algoritmo matemático de análisis ──────────────────────────
def calc_ema(closes, period):
    k = 2 / (period + 1)
    ema = closes[0]
    for p in closes[1:]:
        ema = p * k + ema * (1 - k)
    return round(ema, 2)

def calc_rsi(closes, period=14):
    gains = losses = 0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else:     losses -= d
    rs = (gains / period) / (losses / period + 0.001)
    return round(100 - (100 / (1 + rs)), 1)

def calc_atr(highs, lows, closes, period=14):
    avg = lambda arr: sum(arr) / len(arr)
    trs = []
    for i in range(1, period + 1):
        tr = max(highs[-i] - lows[-i],
                 abs(highs[-i] - closes[-i-1]),
                 abs(lows[-i] - closes[-i-1]))
        trs.append(tr)
    return avg(trs)

def simulate_grid_bot(prices, price_min, price_max, grid_count, capital, mode="aritmetico"):
    """Simula un grid bot sobre precios históricos reales."""
    if price_max <= price_min or grid_count < 2 or not prices:
        return None
    if mode == "geometrico":
        ratio  = (price_max / price_min) ** (1 / grid_count)
        levels = [price_min * (ratio ** i) for i in range(grid_count + 1)]
    else:
        step   = (price_max - price_min) / grid_count
        levels = [price_min + step * i for i in range(grid_count + 1)]

    fee           = 0.001
    cap_per_order = capital / grid_count
    p0            = prices[0]
    state         = {}
    for lv in levels:
        state[lv] = "buy" if lv < p0 else "idle"

    pnl    = 0.0
    trades = 0
    prev   = p0

    for price in prices[1:]:
        for i, lv in enumerate(levels):
            if prev > lv > price or (prev > lv and price <= lv):
                if state[lv] == "buy":
                    qty = cap_per_order * (1 - fee) / lv
                    if i + 1 < len(levels):
                        state[levels[i+1]] = {"type":"sell","qty":qty,"cost":cap_per_order}
                    state[lv] = "idle"
                    trades += 1
            elif prev < lv < price or (prev < lv and price >= lv):
                if isinstance(state[lv], dict) and state[lv].get("type") == "sell":
                    s    = state[lv]
                    pnl += s["qty"] * lv * (1-fee) - s["cost"]
                    if i - 1 >= 0:
                        state[levels[i-1]] = "buy"
                    state[lv] = "idle"
                    trades += 1
        prev = price

    roi = (pnl / capital) * 100
    return {"pnl": round(pnl,4), "roi_pct": round(roi,3), "trades": trades}

def run_backtest(capital):
    """Corre backtesting con datos reales de Binance y encuentra la configuración óptima."""
    # Obtener 90 días de datos de 1h (2160 velas)
    klines_1h = get_klines("1h", 168)   # 7 días recientes
    klines_4h = get_klines("4h", 90)    # 15 días
    klines_1d = get_klines("1d", 90)    # 90 días

    if not klines_1h or not klines_4h:
        return None

    # Precios de cierre de cada temporalidad
    prices_1h = [k["close"] for k in klines_1h]
    prices_4h = [k["close"] for k in klines_4h]
    prices_1d = [k["close"] for k in klines_1d] if klines_1d else prices_4h

    # Soporte y resistencia real de 90 días
    all_highs = [k["high"] for k in (klines_1d or klines_4h)]
    all_lows  = [k["low"]  for k in (klines_1d or klines_4h)]
    support_90d    = min(all_lows)
    resistance_90d = max(all_highs)
    current_price  = prices_1h[-1]

    # Rangos a probar basados en el historial real
    ranges_to_test = []
    # Rango conservador: soporte/resistencia de 15 días
    sup_15d = min(k["low"]  for k in klines_4h)
    res_15d = max(k["high"] for k in klines_4h)
    ranges_to_test.append((round(sup_15d * 0.99, 2), round(res_15d * 1.01, 2), "15d"))
    # Rango moderado: ±10% del precio actual
    ranges_to_test.append((round(current_price * 0.90, 2), round(current_price * 1.10, 2), "±10%"))
    # Rango amplio: soporte/resistencia de 90 días
    ranges_to_test.append((round(support_90d * 0.98, 2), round(resistance_90d * 1.02, 2), "90d"))

    best_result = None
    best_config = None
    all_results = []

    avg = lambda arr: sum(arr)/len(arr) if arr else 0
    # ATR de 1h para calcular grids
    atr_1h = avg([max(klines_1h[i]["high"]-klines_1h[i]["low"],
                      abs(klines_1h[i]["high"]-klines_1h[i-1]["close"]),
                      abs(klines_1h[i]["low"]-klines_1h[i-1]["close"]))
                  for i in range(1, len(klines_1h))])

    for p_min, p_max, range_name in ranges_to_test:
        price_range  = p_max - p_min
        precio_medio = (p_min + p_max) / 2
        min_notional = max(7.0, precio_medio * 0.008)
        max_by_cap   = int(capital / min_notional)
        gap_ideal    = atr_1h * 0.5
        max_by_range = max(2, int(price_range / gap_ideal)) if gap_ideal > 0 else 20

        # Grids a evaluar: de 3 hasta el máximo posible
        grid_options = sorted(set(range(3, min(max_by_cap, max_by_range, 50) + 1)))

        for grid_count in grid_options:
            for mode in ["aritmetico", "geometrico"]:
                # Simular sobre datos de 1h (7 días)
                r_1h = simulate_grid_bot(prices_1h, p_min, p_max, grid_count, capital, mode)
                # Simular sobre datos de 4h (15 días)
                r_4h = simulate_grid_bot(prices_4h, p_min, p_max, grid_count, capital, mode)

                if not r_1h or not r_4h:
                    continue

                # Score combinado: promedio ponderado de ambas simulaciones
                # 4h tiene más peso porque cubre más tiempo
                combined_pnl     = r_1h["pnl"] * 0.4 + r_4h["pnl"] * 0.6
                combined_trades  = r_1h["trades"] + r_4h["trades"]
                capital_per_order = round(capital / grid_count, 2)

                result = {
                    "grid_count":      grid_count,
                    "mode":            mode,
                    "price_min":       p_min,
                    "price_max":       p_max,
                    "range_name":      range_name,
                    "pnl_7d":          r_1h["pnl"],
                    "pnl_15d":         r_4h["pnl"],
                    "combined_pnl":    round(combined_pnl, 4),
                    "trades_total":    combined_trades,
                    "roi_7d":          r_1h["roi_pct"],
                    "roi_15d":         r_4h["roi_pct"],
                    "capital_per_order": capital_per_order,
                }
                all_results.append(result)

                if best_result is None or combined_pnl > best_result["combined_pnl"]:
                    best_result = result
                    best_config = result

    # Ordenar y retornar top 5
    all_results.sort(key=lambda x: -x["combined_pnl"])
    return {
        "best":    best_config,
        "top5":    all_results[:5],
        "current": current_price,
        "support_90d":    round(support_90d, 2),
        "resistance_90d": round(resistance_90d, 2),
        "atr_1h":         round(atr_1h, 2),
    }

def analyze_timeframe(klines):
    """Analiza una temporalidad y devuelve sus métricas clave."""
    if not klines or len(klines) < 20:
        return None
    avg = lambda arr: sum(arr) / len(arr)
    std = lambda arr: math.sqrt(avg([(x - avg(arr))**2 for x in arr]))

    closes  = [k["close"]  for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]
    volumes = [k["volume"] for k in klines]

    current    = closes[-1]
    ema20      = calc_ema(closes[-min(20,len(closes)):], 20)
    ema50      = calc_ema(closes[-min(50,len(closes)):], min(50,len(closes)))
    rsi        = calc_rsi(closes[-15:])
    atr        = calc_atr(highs, lows, closes)
    atr_pct    = atr / current * 100

    # MACD
    ema12      = calc_ema(closes[-min(26,len(closes)):], 12)
    ema26      = calc_ema(closes[-min(26,len(closes)):], 26)
    macd_val   = ema12 - ema26
    macd_bull  = macd_val > 0

    # Bollinger
    bb_closes  = closes[-20:]
    bb_mid     = avg(bb_closes)
    bb_std     = std(bb_closes)
    bb_upper   = bb_mid + 2 * bb_std
    bb_lower   = bb_mid - 2 * bb_std
    bb_pos_pct = (current - bb_lower) / (bb_upper - bb_lower + 0.001) * 100

    # Soporte y resistencia del período completo
    support    = min(lows)
    resistance = max(highs)

    # Tendencia
    mid = len(closes) // 2
    trend_bull = avg(closes[mid:]) > avg(closes[:mid])

    # Volatilidad histórica
    volatility = std(closes) / avg(closes) * 100

    # Volumen relativo
    vol_ratio  = avg(volumes[-5:]) / (avg(volumes[-20:]) + 0.001)

    return {
        "current": current, "ema20": ema20, "ema50": ema50,
        "rsi": rsi, "atr": atr, "atr_pct": atr_pct,
        "macd_bull": macd_bull, "macd_val": macd_val,
        "bb_upper": bb_upper, "bb_lower": bb_lower, "bb_mid": bb_mid,
        "bb_pos_pct": bb_pos_pct,
        "support": support, "resistance": resistance,
        "trend_bull": trend_bull, "volatility": volatility,
        "vol_ratio": vol_ratio,
    }

def score_timeframe(tf):
    """Puntúa las condiciones de una temporalidad (0-9)."""
    if not tf: return 0, []
    score = 0.0
    reasons = []

    # RSI (peso 3)
    if 40 <= tf["rsi"] <= 60:
        score += 3.0; reasons.append(f"RSI ideal ({tf['rsi']})")
    elif 33 <= tf["rsi"] <= 67:
        score += 1.5; reasons.append(f"RSI aceptable ({tf['rsi']})")
    elif tf["rsi"] > 75 or tf["rsi"] < 25:
        score -= 2.0; reasons.append(f"RSI extremo ({tf['rsi']})")

    # MACD (peso 2)
    if tf["macd_bull"]:
        score += 2.0; reasons.append("MACD alcista")
    else:
        score -= 0.5; reasons.append("MACD bajista")

    # Bollinger posición (peso 1.5)
    if 25 <= tf["bb_pos_pct"] <= 75:
        score += 1.5; reasons.append("Precio en centro Bollinger")
    elif tf["bb_pos_pct"] < 10 or tf["bb_pos_pct"] > 90:
        score -= 0.5; reasons.append("Precio en extremo Bollinger")

    # EMA tendencia (peso 1.5)
    if tf["ema20"] > tf["ema50"]:
        score += 1.5; reasons.append("EMA20 > EMA50 (alcista)")
    else:
        score += 0.5; reasons.append("EMA20 < EMA50 (bajista)")

    # ATR volatilidad (peso 1)
    if tf["atr_pct"] < 1.5:
        score += 1.0; reasons.append(f"ATR bajo ({tf['atr_pct']:.1f}%)")
    elif tf["atr_pct"] < 3.0:
        score += 0.5; reasons.append(f"ATR moderado ({tf['atr_pct']:.1f}%)")
    else:
        score -= 0.5; reasons.append(f"ATR alto ({tf['atr_pct']:.1f}%)")

    return round(score, 1), reasons

def analyze_market(klines, capital):
    """Wrapper para compatibilidad — usa multi-timeframe internamente."""
    # Obtener las 3 temporalidades
    tf_data = get_multi_timeframe()

    # Si alguna falla, usar los klines de 4h que ya tenemos
    if not tf_data["4h"]: tf_data["4h"] = klines
    if not tf_data["1h"]: tf_data["1h"] = klines
    if not tf_data["1d"]: tf_data["1d"] = klines

    return analyze_market_multi(tf_data, capital)

def analyze_market_multi(tf_data, capital):
    """Análisis completo multi-temporalidad con estrategia adaptativa."""
    avg = lambda arr: sum(arr) / len(arr)
    std = lambda arr: math.sqrt(avg([(x - avg(arr))**2 for x in arr]))

    tf1h = analyze_timeframe(tf_data.get("1h", []))
    tf4h = analyze_timeframe(tf_data.get("4h", []))
    tf1d = analyze_timeframe(tf_data.get("1d", []))

    ref = tf4h or tf1h or tf1d
    if not ref:
        return {"error": "No hay datos suficientes"}

    current = ref["current"]

    # ── Puntuaciones por temporalidad ──────────────────────────
    score1h, _ = score_timeframe(tf1h)
    score4h, _ = score_timeframe(tf4h)
    score1d, _ = score_timeframe(tf1d)

    # Ponderado: 1h=20%, 4h=50%, 1d=30%
    score_combined = round(score1h * 0.20 + score4h * 0.50 + score1d * 0.30, 1)
    bullish_count  = sum([
        1 if tf1h and score1h >= 4 else 0,
        1 if tf4h and score4h >= 4 else 0,
        1 if tf1d and score1d >= 4 else 0,
    ])

    # ── Soportes y resistencias reales por temporalidad ────────
    sup_1h = tf1h["support"]    if tf1h else current * 0.95
    res_1h = tf1h["resistance"] if tf1h else current * 1.05
    sup_4h = tf4h["support"]    if tf4h else current * 0.92
    res_4h = tf4h["resistance"] if tf4h else current * 1.08
    sup_1d = tf1d["support"]    if tf1d else current * 0.88
    res_1d = tf1d["resistance"] if tf1d else current * 1.12

    # ── ATR ponderado ──────────────────────────────────────────
    atr_1h = tf1h["atr"] if tf1h else 0
    atr_4h = tf4h["atr"] if tf4h else 0
    atr_1d = tf1d["atr"] if tf1d else 0
    atr_w  = atr_4h * 0.5 + atr_1d * 0.3 + atr_1h * 0.2
    atr_pct = round(atr_w / current * 100, 2)

    # ── Rango del grid ─────────────────────────────────────────
    # Basado en el historial real de 15 días (4h)
    # El precio mínimo es el soporte de 15 días con un colchón de seguridad
    # El precio máximo es la resistencia de 15 días con colchón
    # Objetivo: capturar el 85% de la oscilación real del periodo

    # Colchón = 0.5 × ATR ponderado (margen de seguridad sobre soporte)
    price_min = round(sup_4h - atr_w * 0.5, 2)
    price_max = round(res_4h + atr_w * 0.3, 2)

    # Verificar que el precio actual esté dentro del rango
    if current <= price_min: price_min = round(current * 0.93, 2)
    if current >= price_max: price_max = round(current * 1.07, 2)

    # Rango mínimo de 8% para ser viable
    range_pct = (price_max - price_min) / current * 100
    if range_pct < 8:
        price_min = round(current * 0.94, 2)
        price_max = round(current * 1.06, 2)
        range_pct = 12.0

    price_range = price_max - price_min

    # ── Movimiento diario real de SOL ──────────────────────────
    # Usamos los datos de 1h para calcular cuánto se mueve SOL en un día típico
    # Tomamos el promedio de los rangos diarios (high - low) de los últimos 7 días
    if tf1h and len(tf1h.get("highs", [])) == 0:
        # Reconstruir desde klines de 1h
        klines_1h = tf_data.get("1h", [])
        if len(klines_1h) >= 24:
            daily_ranges = []
            for day in range(min(7, len(klines_1h)//24)):
                day_data = klines_1h[-(day+1)*24 : -day*24 if day > 0 else None]
                if day_data:
                    dh = max(k["high"]  for k in day_data)
                    dl = min(k["low"]   for k in day_data)
                    daily_ranges.append(dh - dl)
            daily_move = avg(daily_ranges) if daily_ranges else atr_w * 3
        else:
            daily_move = atr_w * 3
    else:
        # Estimación: ATR de 4h × √6 (6 velas de 4h por día)
        daily_move = atr_4h * (6 ** 0.5)

    daily_move = max(daily_move, atr_w * 2)  # mínimo razonable

    # ── Número óptimo de grids ────────────────────────────────
    #
    # Lógica del grid bot:
    # El gap ideal entre grids = 0.5 × ATR ponderado
    # Esto captura las oscilaciones reales del mercado sin llenarse
    # por ruido (movimientos menores al ATR).
    #
    # Restricciones de Binance:
    #   - Mínimo por orden: $5 USDC
    #   - Máximo grids: 170, mínimo: 2
    #
    # El número de grids es el mínimo entre:
    #   - Lo que permite el rango (rango / gap_ideal)
    #   - Lo que financia el capital (capital / $5)
    #
    # Si el capital es el limitante → más capital = más grids
    # Si el rango es el limitante  → más capital = mismo nº grids, más $/orden

    precio_medio     = (price_min + price_max) / 2
    gap_ideal        = atr_w * 0.5   # gap que captura oscilaciones reales

    # ── Mínimo real por orden en Binance ──────────────────────
    # Binance Grid Bot requiere capital suficiente para colocar
    # TODAS las órdenes de compra por debajo del precio actual.
    # Estimación conservadora: capital mínimo total ≈ grids × precio_medio × 0.015
    # (equivale a tener al menos 0.015 SOL por orden a precio medio)
    # En la práctica Binance pide ~$6-8 por orden para SOL/USDC.
    # Usamos max($7, precio_medio × 0.008) como mínimo seguro.
    min_notional     = max(7.0, precio_medio * 0.008)

    grids_por_rango  = max(2, int(price_range / gap_ideal))
    grids_por_cap    = max(2, int(capital / min_notional))
    grid_count       = max(3, min(grids_por_rango, grids_por_cap, 170))
    capital_per_order = round(capital / grid_count, 2)

    # Capital mínimo recomendado para este número de grids
    capital_minimo   = round(grid_count * min_notional, 2)
    capital_optimo   = round(grid_count * min_notional * 2, 2)  # 2× el mínimo = holgura

    # Gap real después de ajuste
    best_gap         = round(price_range / grid_count, 2)
    gap_vs_atr       = round(best_gap / atr_w, 2)
    limitante        = "capital" if grids_por_cap < grids_por_rango else "mercado"

    log(f"Grids: rango/${gap_ideal:.2f}={grids_por_rango} vs capital/${min_notional:.2f}={grids_por_cap} "
        f"→ {grid_count} grids [limitante: {limitante}] · gap=${best_gap} ({gap_vs_atr}x ATR) "
        f"· mín. capital=${capital_minimo}", "info")

    # Ganancia estimada
    oscilacion_diaria = atr_w * 2
    fills_day_final   = round(min(oscilacion_diaria / best_gap * grid_count * 0.15,
                                  grid_count * 0.4), 2)
    profit_per_grid   = round((best_gap / precio_medio - 0.002) * 100, 3)
    est_daily         = round(fills_day_final * capital_per_order * profit_per_grid / 100, 4)

    # ── Modo aritmético vs geométrico ─────────────────────────
    # Geométrico: el gap crece proporcionalmente con el precio
    # Mejor cuando el rango es > 12% (precios muy diferentes arriba vs abajo)
    range_pct_final = (price_max - price_min) / price_min * 100
    grid_mode = "geometrico" if range_pct_final > 12 else "aritmetico"

    # ── TP y SL basados en historial real ─────────────────────
    # SL: mínimo absoluto de 30 días menos 1 ATR (zona donde el mercado ha rebotado)
    sl = round(sup_1d - atr_w, 2)
    tp = round(res_1d + atr_w * 0.5, 2)

    # Protección: SL no más del 15% bajo el precio actual
    sl_pct = (current - sl) / current * 100
    if sl_pct > 15:
        sl     = round(current * 0.85, 2)
        sl_pct = 15.0

    # ── Señal final ────────────────────────────────────────────
    avoid = (
        (tf4h and (tf4h["rsi"] > 78 or tf4h["rsi"] < 22)) or
        (tf1d and (tf1d["rsi"] > 75 or tf1d["rsi"] < 25)) or
        atr_pct > 6.0
    )
    if avoid:
        signal        = "AVOID"
        signal_reason = "Condición extrema: RSI fuera de rango o volatilidad muy alta."
    elif score_combined >= 5.0 and bullish_count >= 2:
        signal        = "OPEN"
        signal_reason = f"Score {score_combined}/9 confirmado en {bullish_count}/3 TF."
    elif score_combined >= 3.5 or bullish_count >= 1:
        signal        = "WAIT"
        signal_reason = f"Score {score_combined}/9, solo {bullish_count}/3 TF confirman."
    else:
        signal        = "AVOID"
        signal_reason = f"Score bajo ({score_combined}/9). Condiciones desfavorables."

    # ── Riesgo ─────────────────────────────────────────────────
    if atr_pct > 3.5 or score_combined < 3:
        risk_level  = "HIGH"
        risk_reason = f"ATR {atr_pct}%, score {score_combined}/9."
    elif atr_pct > 2.0 or score_combined < 5:
        risk_level  = "MEDIUM"
        risk_reason = f"ATR {atr_pct}%, score {score_combined}/9."
    else:
        risk_level  = "LOW"
        risk_reason = f"ATR {atr_pct}% controlado, score {score_combined}/9."

    # Tendencia consolidada
    trend_votes = sum([
        1 if tf1h and tf1h["trend_bull"] else 0,
        1 if tf4h and tf4h["trend_bull"] else 0,
        1 if tf1d and tf1d["trend_bull"] else 0,
    ])
    trend = "alcista" if trend_votes >= 2 else "bajista"

    # Indicadores de referencia (4h como principal)
    ref_rsi   = tf4h["rsi"]       if tf4h else 50
    ref_ema20 = tf4h["ema20"]     if tf4h else current
    ref_ema50 = tf4h["ema50"]     if tf4h else current
    ref_macd  = tf4h["macd_bull"] if tf4h else True
    ref_bb_u  = tf4h["bb_upper"]  if tf4h else current * 1.05
    ref_bb_l  = tf4h["bb_lower"]  if tf4h else current * 0.95
    ref_bb_m  = tf4h["bb_mid"]    if tf4h else current
    ref_vol   = tf4h["volatility"] if tf4h else 3.0

    log(f"Multi-TF scores: 1h={score1h} 4h={score4h} 1d={score1d} → {score_combined}/9 | "
        f"Rango ${price_min}-${price_max} ({range_pct_final:.1f}%) | "
        f"{grid_count} grids @ ${capital_per_order}/orden | SL=${sl} TP=${tp}", "success")

    return {
        "signal":                      signal,
        "signal_reason":               signal_reason,
        "price_min":                   price_min,
        "price_max":                   price_max,
        "grid_count":                  grid_count,
        "grid_mode":                   grid_mode,
        "capital_per_order":           capital_per_order,
        "profit_per_grid_pct":         profit_per_grid,
        "estimated_daily_profit_usdc": est_daily,
        "risk_level":                  risk_level,
        "risk_reason":                 risk_reason,
        "tp":                          tp,
        "sl":                          sl,
        "sl_pct":                      round(sl_pct, 1),
        "score":                       score_combined,
        "score_1h":                    score1h,
        "score_4h":                    score4h,
        "score_1d":                    score1d,
        "bullish_count":               bullish_count,
        "rsi":                         ref_rsi,
        "ema20":                       round(ref_ema20, 2),
        "ema50":                       round(ref_ema50, 2),
        "macd_bull":                   ref_macd,
        "macd_signal":                 "Alcista" if ref_macd else "Bajista",
        "atr":                         round(atr_w, 2),
        "atr_pct":                     atr_pct,
        "daily_move":                  round(daily_move, 2),
        "bb_upper":                    round(ref_bb_u, 2),
        "bb_mid":                      round(ref_bb_m, 2),
        "bb_lower":                    round(ref_bb_l, 2),
        "volatility":                  round(ref_vol, 2),
        "support":                     round(sup_1d, 2),
        "resistance":                  round(res_1d, 2),
        "support_4h":                  round(sup_4h, 2),
        "resistance_4h":               round(res_4h, 2),
        "trend":                       trend,
        "current_price":               round(current, 2),
        "range_pct":                   round(range_pct_final, 1),
        "rebalance_trigger":           f"Si SOL cae bajo ${sl} o sube sobre ${tp}",
        "capital_minimo":              capital_minimo,
        "capital_optimo":              capital_optimo,
        "timeframes":                  {"1h": score1h, "4h": score4h, "1d": score1d},
    }


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
    log("Iniciando análisis multi-temporalidad (1h / 4h / 1d)...", "info")
    body    = request.json or {}
    capital = float(body.get("capital", CAPITAL))

    tf_data = get_multi_timeframe()
    if not any(tf_data.values()):
        return jsonify({"error": "No se pudieron obtener datos de Binance"}), 500

    result = analyze_market_multi(tf_data, capital)
    state["grid_params"]   = result
    state["last_analysis"] = time.time()
    log(f"Análisis completo — Señal: {result['signal']} | Score: {result['score']}/9 | "
        f"Scores: 1h={result['score_1h']} 4h={result['score_4h']} 1d={result['score_1d']} | "
        f"Rango: ${result['price_min']}-${result['price_max']} ({result['range_pct']}%) | "
        f"{result['grid_count']} grids @ ${result['capital_per_order']}/orden", "success")
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

@app.route("/recalculate", methods=["POST"])
def recalculate():
    """Recalcula grids con nuevo capital usando el mismo algoritmo multi-TF."""
    body      = request.json or {}
    capital   = float(body.get("capital", CAPITAL))
    atr       = float(body.get("atr", 2.0))
    atr_pct   = float(body.get("atr_pct", 2.0))
    price_min = float(body.get("price_min", 0))
    price_max = float(body.get("price_max", 0))

    if not price_min or not price_max:
        return jsonify({"error": "Faltan price_min y price_max"}), 400

    price_range     = price_max - price_min
    price_range_pct = price_range / price_min * 100

    # Mismo algoritmo que analyze_market_multi
    precio_medio      = (price_min + price_max) / 2
    min_notional      = 5.0
    gap_ideal         = atr * 0.5
    grids_por_rango   = max(2, int(price_range / gap_ideal))
    grids_por_cap     = max(2, int(capital / min_notional))
    grid_count        = max(3, min(grids_por_rango, grids_por_cap, 170))
    capital_per_order = round(capital / grid_count, 2)
    profit_per_grid   = round(price_range_pct / grid_count, 2)

    gap_per_grid  = price_range / grid_count
    fills_per_day = round(min(grid_count * 0.4, atr * 2 / gap_per_grid), 2) if gap_per_grid > 0 else 1
    est_daily     = round(fills_per_day * capital_per_order * profit_per_grid / 100, 4)
    est_monthly   = round(est_daily * 30, 2)

    capital_minimo = round(grid_count * min_notional, 2)
    capital_optimo = round(grid_count * min_notional * 2, 2)

    log(f"Recalculo: capital=${capital} ATR={atr_pct}% → {grid_count} grids · ${capital_per_order}/orden · mín=${capital_minimo}", "info")

    return jsonify({
        "grid_count":          grid_count,
        "capital_per_order":   capital_per_order,
        "profit_per_grid_pct": profit_per_grid,
        "est_daily":           est_daily,
        "est_monthly":         est_monthly,
        "capital_minimo":      capital_minimo,
        "capital_optimo":      capital_optimo,
    })

@app.route("/backtest", methods=["POST"])
def backtest():
    body    = request.json or {}
    capital = float(body.get("capital", CAPITAL))
    log(f"Iniciando backtesting con capital=${capital}...", "info")
    result = run_backtest(capital)
    if not result:
        return jsonify({"error": "No se pudieron obtener datos históricos"}), 500
    best = result["best"]
    if best:
        log(f"Backtest completo — Mejor: {best['grid_count']} grids {best['mode']} "
            f"rango ${best['price_min']}-${best['price_max']} | "
            f"PNL 7d=${best['pnl_7d']} 15d=${best['pnl_15d']}", "success")
    return jsonify(result)

@app.route("/price")
def price():
    p = get_price()
    return jsonify({"symbol": SYMBOL, "price": p})

@app.route("/dashboard")
def dashboard():
    from flask import send_file
    return send_file("dashboard.html")

def price_updater():
    """Actualiza el precio de SOL en background cada 15s"""
    while True:
        try:
            get_price()
        except:
            pass
        time.sleep(15)

# Iniciar actualizador de precio en background al arrancar
Thread(target=price_updater, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log(f"Servidor iniciando en puerto {port}", "info")
    app.run(host="0.0.0.0", port=port)
