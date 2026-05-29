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
    """Análisis completo multi-temporalidad."""
    avg = lambda arr: sum(arr) / len(arr)

    tf1h = analyze_timeframe(tf_data.get("1h", []))
    tf4h = analyze_timeframe(tf_data.get("4h", []))
    tf1d = analyze_timeframe(tf_data.get("1d", []))

    # Usar 4h como referencia principal si alguna falla
    ref = tf4h or tf1h or tf1d
    if not ref:
        return {"error": "No hay datos suficientes"}

    current = ref["current"]

    # ── Puntuaciones por temporalidad ──────────────────────────
    score1h, reasons1h = score_timeframe(tf1h)
    score4h, reasons4h = score_timeframe(tf4h)
    score1d, reasons1d = score_timeframe(tf1d)

    # Puntuación combinada ponderada:
    # 1h = 20% (corto plazo, ruido), 4h = 50% (principal), 1d = 30% (macro)
    score_combined = score1h * 0.20 + score4h * 0.50 + score1d * 0.30
    score_combined = round(score_combined, 1)

    # Confirmación multi-temporal: cuántas TF están de acuerdo
    bullish_count = sum([
        1 if tf1h and score1h >= 4 else 0,
        1 if tf4h and score4h >= 4 else 0,
        1 if tf1d and score1d >= 4 else 0,
    ])

    # ── Rango del grid basado en datos reales históricos ──────
    # Soporte: mínimo real de 30 días (1d) — donde el precio ha rebotado
    # Resistencia: máximo real de 30 días (1d) — donde el precio ha frenado
    support_1d    = tf1d["support"]    if tf1d else current * 0.90
    resistance_1d = tf1d["resistance"] if tf1d else current * 1.10

    # Rango de 15 días (4h) para el grid — más conservador
    support_4h    = tf4h["support"]    if tf4h else current * 0.92
    resistance_4h = tf4h["resistance"] if tf4h else current * 1.08

    # Rango de 7 días (1h) para validar zona actual
    support_1h    = tf1h["support"]    if tf1h else current * 0.95
    resistance_1h = tf1h["resistance"] if tf1h else current * 1.05

    # ATR ponderado por temporalidad
    atr_1h  = tf1h["atr"] if tf1h else 0
    atr_4h  = tf4h["atr"] if tf4h else 0
    atr_1d  = tf1d["atr"] if tf1d else 0
    # ATR representativo: 4h como base, ajustado por 1d
    atr_weighted = atr_4h * 0.6 + atr_1d * 0.3 + atr_1h * 0.1
    atr_pct      = round(atr_weighted / current * 100, 2)

    # ── Definir rango del grid ─────────────────────────────────
    # Precio mínimo: soporte de 15 días con buffer de 1x ATR ponderado
    # Precio máximo: resistencia de 15 días con buffer de 0.5x ATR
    # El objetivo es que el rango capture el 80% de la oscilación real
    price_min = round(max(support_4h - atr_weighted * 0.5,
                          support_1d * 0.97), 2)
    price_max = round(min(resistance_4h + atr_weighted * 0.3,
                          resistance_1d * 1.03), 2)

    # Garantizar que el precio actual esté dentro del rango
    if current <= price_min: price_min = round(current * 0.92, 2)
    if current >= price_max: price_max = round(current * 1.08, 2)

    # Garantizar mínimo 10% de rango para que sea viable
    range_pct = (price_max - price_min) / current * 100
    if range_pct < 10:
        center    = (price_min + price_max) / 2
        price_min = round(center * 0.95, 2)
        price_max = round(center * 1.05, 2)
        range_pct = 10.0

    price_range = price_max - price_min

    # ── Número de grids óptimo — sin topes fijos ─────────────
    #
    # Regla: gap entre grids debe ser >= 0.8x ATR ponderado
    # para que cada grid capture un movimiento real, no ruido.
    # Si el gap es muy pequeño, las órdenes se llenan y des-llenan
    # por volatilidad aleatoria sin generar ganancia neta.
    #
    # Fórmula:
    #   max_by_range = rango / (0.8 * ATR)  → grids que caben con gap mínimo
    #   max_by_cap   = capital / 6           → grids que financia el capital
    #   grid_count   = min(max_by_range, max_by_cap)
    #
    # Sin techo artificial — si el mercado y el capital permiten 15, son 15.

    min_gap_usdc = atr_weighted * 0.8        # gap mínimo en dólares
    max_by_range = int(price_range / min_gap_usdc) if min_gap_usdc > 0 else 20
    max_by_cap   = int(capital / 6.0)        # mínimo $6 por orden (Binance)

    grid_count        = max(3, min(max_by_range, max_by_cap))
    capital_per_order = round(capital / grid_count, 2)

    log(f"Grids: rango=${price_range:.2f} / gap_min=${min_gap_usdc:.2f} = {max_by_range} por rango | "
        f"capital ${capital} / $6 = {max_by_cap} por capital → {grid_count} grids", "info")

    # ── Modo aritmético vs geométrico ─────────────────────────
    # Geométrico: mejor cuando el rango es amplio (>12%) o ATR alto
    grid_mode = "geometrico" if range_pct > 12 or atr_pct > 2.5 else "aritmetico"

    # ── Ganancia estimada ──────────────────────────────────────
    profit_per_grid = round(range_pct / grid_count, 2)
    # Fills diarios según ATR vs gap entre grids
    gap_per_grid  = price_range / grid_count
    fills_per_day = round(min(grid_count * 0.4, atr_weighted * 2 / gap_per_grid), 2)
    est_daily     = round(fills_per_day * capital_per_order * profit_per_grid / 100, 4)

    # ── TP y SL basados en historial real ─────────────────────
    # SL: 1 ATR ponderado por debajo del soporte de 30 días
    sl = round(support_1d - atr_weighted * 1.0, 2)
    # TP: resistencia de 30 días + 0.5 ATR
    tp = round(resistance_1d + atr_weighted * 0.5, 2)

    # Límite SL: máximo 12% de pérdida del capital
    sl_pct = (current - sl) / current * 100
    if sl_pct > 12:
        sl     = round(current * 0.88, 2)
        sl_pct = 12.0

    # ── Señal final ────────────────────────────────────────────
    avoid_conditions = (
        (tf4h and (tf4h["rsi"] > 78 or tf4h["rsi"] < 22)) or
        (tf1d and (tf1d["rsi"] > 75 or tf1d["rsi"] < 25)) or
        atr_pct > 6.0
    )

    if avoid_conditions:
        signal        = "AVOID"
        signal_reason = f"Condición extrema en al menos una TF. RSI o ATR fuera de rango."
    elif score_combined >= 5.0 and bullish_count >= 2:
        signal        = "OPEN"
        signal_reason = f"Score {score_combined}/9 confirmado en {bullish_count}/3 temporalidades."
    elif score_combined >= 3.5 or bullish_count >= 1:
        signal        = "WAIT"
        signal_reason = f"Score {score_combined}/9, solo {bullish_count}/3 TF confirman. Esperar."
    else:
        signal        = "AVOID"
        signal_reason = f"Score bajo ({score_combined}/9). Señales negativas en múltiples TF."

    # ── Riesgo ─────────────────────────────────────────────────
    if atr_pct > 3.5 or score_combined < 3:
        risk_level  = "HIGH"
        risk_reason = f"ATR {atr_pct}%, score {score_combined}/9."
    elif atr_pct > 2.0 or score_combined < 5:
        risk_level  = "MEDIUM"
        risk_reason = f"ATR {atr_pct}%, score {score_combined}/9."
    else:
        risk_level  = "LOW"
        risk_reason = f"ATR {atr_pct}%, score {score_combined}/9 en {bullish_count}/3 TF."

    # ── Tendencia consolidada ──────────────────────────────────
    trend_votes = sum([
        1 if tf1h and tf1h["trend_bull"] else 0,
        1 if tf4h and tf4h["trend_bull"] else 0,
        1 if tf1d and tf1d["trend_bull"] else 0,
    ])
    trend = "alcista" if trend_votes >= 2 else "bajista"

    # Indicadores de referencia (4h como principal)
    ref_rsi  = tf4h["rsi"]      if tf4h else (tf1h["rsi"]  if tf1h else 50)
    ref_ema20 = tf4h["ema20"]   if tf4h else current
    ref_ema50 = tf4h["ema50"]   if tf4h else current
    ref_macd  = tf4h["macd_bull"] if tf4h else True
    ref_bb_u  = tf4h["bb_upper"]  if tf4h else current * 1.05
    ref_bb_l  = tf4h["bb_lower"]  if tf4h else current * 0.95
    ref_bb_m  = tf4h["bb_mid"]    if tf4h else current
    ref_vol   = tf4h["volatility"] if tf4h else 3.0

    log(f"Multi-TF: 1h={score1h} 4h={score4h} 1d={score1d} → Combined={score_combined} | "
        f"Rango ${price_min}-${price_max} ({range_pct:.1f}%) | {grid_count} grids", "info")

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
        "atr":                         round(atr_weighted, 2),
        "atr_pct":                     atr_pct,
        "bb_upper":                    round(ref_bb_u, 2),
        "bb_mid":                      round(ref_bb_m, 2),
        "bb_lower":                    round(ref_bb_l, 2),
        "volatility":                  round(ref_vol, 2),
        "support":                     round(support_1d, 2),
        "resistance":                  round(resistance_1d, 2),
        "support_4h":                  round(support_4h, 2),
        "resistance_4h":               round(resistance_4h, 2),
        "trend":                       trend,
        "current_price":               round(current, 2),
        "range_pct":                   round(range_pct, 1),
        "rebalance_trigger":           f"Si SOL cae bajo ${sl} o sube sobre ${tp}",
        "timeframes":                  {"1h": score1h, "4h": score4h, "1d": score1d},
    }
    closes  = [k["close"]  for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]
    volumes = [k["volume"] for k in klines]

    avg = lambda arr: sum(arr) / len(arr)
    std = lambda arr: math.sqrt(avg([(x - avg(arr))**2 for x in arr]))

    current    = closes[-1]
    trend      = "alcista" if avg(closes[-20:]) > avg(closes[-50:-20]) else "bajista"
    volatility = std(closes) / avg(closes) * 100

    # Soporte/resistencia 60 velas (~10 días en 4h)
    support    = min(lows[-60:])
    resistance = max(highs[-60:])

    # RSI(14)
    gains = losses = 0
    for i in range(len(closes) - 14, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else:     losses -= d
    rs  = (gains / 14) / (losses / 14 + 0.001)
    rsi = 100 - (100 / (1 + rs))

    # EMA 20 y 50
    ema20 = calc_ema(closes[-20:], 20)
    ema50 = calc_ema(closes[-50:], 50)

    # MACD (EMA12 - EMA26)
    ema12     = calc_ema(closes[-26:], 12)
    ema26     = calc_ema(closes[-26:], 26)
    macd_val  = ema12 - ema26
    macd_bull = macd_val > 0
    macd_signal_txt = "Alcista" if macd_bull else "Bajista"

    # ATR(14) — volatilidad real en dólares
    trs = []
    for i in range(1, 15):
        tr = max(highs[-i] - lows[-i],
                 abs(highs[-i] - closes[-i-1]),
                 abs(lows[-i] - closes[-i-1]))
        trs.append(tr)
    atr     = avg(trs)
    atr_pct = atr / current * 100  # ATR como % del precio

    # Bandas de Bollinger (20 periodos, 2 std)
    bb_closes = closes[-20:]
    bb_mid_v  = avg(bb_closes)
    bb_std    = std(bb_closes)
    bb_upper  = round(bb_mid_v + 2 * bb_std, 2)
    bb_lower  = round(bb_mid_v - 2 * bb_std, 2)
    bb_mid_v  = round(bb_mid_v, 2)

    # ── Puntuación ponderada de señal ─────────────────────────
    # Cada indicador aporta un peso según la fuerza de su señal
    score = 0.0

    # RSI: peso 3 — más importante en mercados laterales
    if 40 <= rsi <= 60:   score += 3.0   # zona ideal
    elif 35 <= rsi <= 65: score += 2.0   # zona aceptable
    elif 30 <= rsi <= 70: score += 1.0   # zona límite
    else:                 score -= 2.0   # extremo — penalizar

    # MACD: peso 2
    if macd_bull:
        score += 2.0
        if abs(macd_val) > atr * 0.1: score += 0.5  # señal fuerte
    else:
        score -= 0.5  # bajista penaliza menos (grids también ganan bajando)

    # EMA 20 vs 50: peso 1.5
    ema_diff_pct = abs(ema20 - ema50) / ema50 * 100
    if ema20 > ema50 and ema_diff_pct < 3: score += 1.5   # alcista moderado = ideal
    elif ema20 > ema50:                    score += 0.5   # alcista fuerte = menos ideal
    else:                                  score += 0.8   # bajista también puede funcionar

    # Bollinger: peso 1.5 — precio en zona central = mejor
    bb_range   = bb_upper - bb_lower
    bb_pos_pct = (current - bb_lower) / (bb_range + 0.001) * 100
    if 30 <= bb_pos_pct <= 70: score += 1.5   # centro de bandas
    elif 20 <= bb_pos_pct <= 80: score += 0.8
    else:                        score -= 0.5  # en los bordes

    # Volatilidad ATR: peso 1 — baja volatilidad = mejor para grid
    if atr_pct < 1.5:   score += 1.0
    elif atr_pct < 2.5: score += 0.5
    elif atr_pct > 4.0: score -= 1.0

    # Volumen: peso 0.5 — volumen creciente confirma movimiento
    vol_ratio = avg(volumes[-5:]) / (avg(volumes[-20:]) + 0.001)
    if 0.8 <= vol_ratio <= 1.5: score += 0.5  # volumen normal = estable

    # ── Decisión de señal ──────────────────────────────────────
    if rsi > 78 or rsi < 22:
        signal        = "AVOID"
        signal_reason = f"RSI extremo ({rsi:.1f}). Movimiento brusco inminente."
    elif atr_pct > 5.0:
        signal        = "AVOID"
        signal_reason = f"Volatilidad ATR muy alta ({atr_pct:.1f}%). Grid se saldría rápido."
    elif score >= 6.0:
        signal        = "OPEN"
        signal_reason = f"Puntuación alta ({score:.1f}/9). RSI {rsi:.1f}, MACD {macd_signal_txt.lower()}, ATR {atr_pct:.1f}%."
    elif score >= 3.5:
        signal        = "WAIT"
        signal_reason = f"Condiciones mixtas (puntuación {score:.1f}/9). Esperar confirmación."
    else:
        signal        = "AVOID"
        signal_reason = f"Puntuación baja ({score:.1f}/9). Señales negativas combinadas."

    # ── Rango del grid con ATR asimétrico ─────────────────────
    # Más espacio abajo (precio cae más rápido que sube)
    # Multiplicador dinámico: más volátil = rango más amplio
    atr_multi  = max(3.0, min(5.0, 4.0 / (atr_pct + 0.1)))
    range_down = atr * atr_multi * 1.3   # 60% hacia abajo
    range_up   = atr * atr_multi * 0.9   # 40% hacia arriba

    price_min = round(max(current - range_down, support  * 0.96), 2)
    price_max = round(min(current + range_up,   resistance * 1.04), 2)

    # Garantizar mínimo 8% de rango
    if (price_max - price_min) / current < 0.08:
        price_min = round(current * 0.94, 2)
        price_max = round(current * 1.06, 2)

    price_range      = price_max - price_min
    price_range_pct  = price_range / price_min * 100

    # ── Grids óptimos según capital ingresado ─────────────────
    # Mínimo $6 por orden (requisito Binance)
    min_order_usdc = 6.0
    max_by_cap     = int(capital / min_order_usdc)  # límite por capital

    # Límite por volatilidad ATR — más volátil = menos grids
    if atr_pct < 1.5:    ideal_by_atr = 10
    elif atr_pct < 2.5:  ideal_by_atr = 8
    elif atr_pct < 3.5:  ideal_by_atr = 6
    else:                ideal_by_atr = 5

    # Tomar el menor entre lo que permite el capital y lo que sugiere el ATR
    grid_count = max(3, min(max_by_cap, ideal_by_atr))
    capital_per_order = round(capital / grid_count, 2)

    # ── Modo aritmético vs geométrico según ATR ───────────────
    # Geométrico si ATR > 2% del precio (rango amplio y variable)
    grid_mode = "geometrico" if atr_pct > 2.0 else "aritmetico"

    # ── Ganancia estimada ──────────────────────────────────────
    profit_per_grid = round(price_range_pct / grid_count, 2)
    # Fills estimados: más fills con volatilidad moderada
    fills_factor  = min(0.35, max(0.1, atr_pct / 10))
    fills_per_day = grid_count * fills_factor
    est_daily     = round(fills_per_day * capital_per_order * profit_per_grid / 100, 4)

    # ── TP y SL dinámicos basados en ATR ──────────────────────
    # SL = mínimo del grid - 1.5x ATR (margen real de ruido del mercado)
    # TP = máximo del grid + 0.5x ATR
    sl = round(price_min - atr * 1.5, 2)
    tp = round(price_max + atr * 0.5, 2)

    # Verificar que SL no sea más del 15% de pérdida del capital
    sl_pct = (current - sl) / current * 100
    if sl_pct > 15:
        sl = round(current * 0.85, 2)  # máximo 15% de pérdida

    # ── Nivel de riesgo ────────────────────────────────────────
    if atr_pct > 3.5 or rsi > 70 or rsi < 30 or score < 3:
        risk_level  = "HIGH"
        risk_reason = f"ATR {atr_pct:.1f}%, RSI {rsi:.1f}, puntuación {score:.1f}/9."
    elif atr_pct > 2.0 or abs(rsi - 50) > 15 or score < 5:
        risk_level  = "MEDIUM"
        risk_reason = f"ATR moderado {atr_pct:.1f}%, RSI {rsi:.1f}."
    else:
        risk_level  = "LOW"
        risk_reason = f"ATR bajo {atr_pct:.1f}%, RSI neutro {rsi:.1f}, puntuación {score:.1f}/9."

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
        "score":                       round(score, 1),
        "rsi":                         round(rsi, 1),
        "ema20":                       ema20,
        "ema50":                       ema50,
        "macd_bull":                   macd_bull,
        "macd_signal":                 macd_signal_txt,
        "atr":                         round(atr, 2),
        "atr_pct":                     round(atr_pct, 2),
        "bb_upper":                    bb_upper,
        "bb_mid":                      bb_mid_v,
        "bb_lower":                    bb_lower,
        "volatility":                  round(volatility, 2),
        "support":                     round(support, 2),
        "resistance":                  round(resistance, 2),
        "trend":                       trend,
        "current_price":               round(current, 2),
        "rebalance_trigger":           f"Si SOL cae bajo ${sl} o sube sobre ${tp}",
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

    # Mismo algoritmo que analyze_market_multi — sin topes fijos
    min_gap_usdc = atr * 0.8
    max_by_range = max(3, int(price_range / min_gap_usdc)) if min_gap_usdc > 0 else 20
    max_by_cap   = int(capital / 6.0)

    grid_count        = max(3, min(max_by_range, max_by_cap))
    capital_per_order = round(capital / grid_count, 2)
    profit_per_grid   = round(price_range_pct / grid_count, 2)

    gap_per_grid  = price_range / grid_count
    fills_per_day = round(min(grid_count * 0.4, atr * 2 / gap_per_grid), 2) if gap_per_grid > 0 else 1
    est_daily     = round(fills_per_day * capital_per_order * profit_per_grid / 100, 4)
    est_monthly   = round(est_daily * 30, 2)

    log(f"Recalculo: capital=${capital} ATR={atr_pct}% → {grid_count} grids · ${capital_per_order}/orden", "info")

    return jsonify({
        "grid_count":          grid_count,
        "capital_per_order":   capital_per_order,
        "profit_per_grid_pct": profit_per_grid,
        "est_daily":           est_daily,
        "est_monthly":         est_monthly,
    })

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
