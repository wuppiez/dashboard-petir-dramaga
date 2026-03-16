"""
Dashboard Pemantauan Cuaca & Hidrometeorologi - Desa Petir, Dramaga
Fitur: Cuaca Real-time, Peta Rawan Bencana, Grafik Data, Tren Historis, Bot Telegram
"""

import dash
from dash import dcc, html, Input, Output, State, callback_context
import dash_leaflet as dl
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np
import requests
import json
from datetime import datetime, timedelta, timezone
import threading
import time
from collections import deque
import os

# ─── TIMEZONE WIB (UTC+7) ──────────────────────────────────────────────────────
WIB = timezone(timedelta(hours=7))

def now_wib():
    """Ambil waktu sekarang dalam WIB (UTC+7)."""
    return datetime.now(timezone.utc).astimezone(WIB)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_OPENWEATHER_API_KEY")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_TELEGRAM_CHAT_ID")

LAT, LON        = -6.6121, 106.7231
LOCATION_NAME   = "Desa Petir, Dramaga, Bogor"
DATA_FILE       = "data/rainfall_historical.csv"

# Ambang batas peringatan (mm)
THRESHOLD = {
    "WASPADA":   50,   # >50 mm/hari
    "SIAGA":    100,   # >100 mm/hari
    "AWAS":     150,   # >150 mm/hari
}

# ─── LOAD HISTORICAL DATA ──────────────────────────────────────────────────────
def load_historical():
    df = pd.read_csv(DATA_FILE, parse_dates=["date"])
    df.columns = ["date", "rainfall"]
    df = df.sort_values("date").reset_index(drop=True)
    df["month"]    = df["date"].dt.month
    df["year"]     = df["date"].dt.year
    df["doy"]      = df["date"].dt.dayofyear
    df["month_str"]= df["date"].dt.strftime("%b")
    return df

df_hist = load_historical()

# ─── SIMULATED REAL-TIME BUFFER ────────────────────────────────────────────────
# (Ganti dengan sensor / API aktual di produksi)
realtime_buffer = deque(maxlen=288)   # 24 jam x 12 (5-menit interval)
def _seed_realtime():
    now = now_wib()
    for i in range(288, 0, -1):
        ts  = now - timedelta(minutes=i * 5)
        val = max(0, np.random.normal(5, 8))
        realtime_buffer.append({"time": ts, "rainfall_mm": round(val, 2)})

_seed_realtime()

# ─── WEATHER API ───────────────────────────────────────────────────────────────
def fetch_weather():
    try:
        url = (f"https://api.openweathermap.org/data/2.5/weather"
               f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_API_KEY}&units=metric&lang=id")
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    # Demo fallback
    return {
        "main":    {"temp": 27.5, "humidity": 82, "feels_like": 30.1, "pressure": 1010},
        "wind":    {"speed": 2.3, "deg": 180},
        "weather": [{"description": "hujan ringan", "icon": "10d"}],
        "rain":    {"1h": 2.4},
        "visibility": 8000,
        "name": LOCATION_NAME,
    }

def fetch_forecast():
    try:
        url = (f"https://api.openweathermap.org/data/2.5/forecast"
               f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_API_KEY}&units=metric&lang=id&cnt=40")
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    # Demo fallback: 5-hari (3-jam interval)
    items = []
    for i in range(40):
        dt  = now_wib() + timedelta(hours=i * 3)
        ch  = max(0, np.random.normal(3, 5))
        tmp = 24 + 5 * np.sin(i / 8)
        items.append({"dt_txt": dt.strftime("%Y-%m-%d %H:%M:%S"),
                      "main": {"temp": round(tmp, 1)},
                      "rain": {"3h": round(ch, 2)}})
    return {"list": items}

# ─── OPEN-METEO API (Suhu Tanah, Kelembaban Tanah, UV, dll) ──────────────────────
def fetch_openmeteo():
    """Ambil data lengkap dari Open-Meteo API - gratis, tanpa API key."""
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={LAT}&longitude={LON}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            f"precipitation,rain,wind_speed_10m,wind_direction_10m,"
            f"surface_pressure,cloud_cover,uv_index,dew_point_2m,"
            f"soil_temperature_0cm,soil_temperature_6cm,soil_temperature_18cm,"
            f"soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm"
            f"&daily=precipitation_sum,temperature_2m_max,temperature_2m_min,"
            f"uv_index_max,wind_speed_10m_max,et0_fao_evapotranspiration"
            f"&timezone=Asia%2FJakarta&forecast_days=7"
        )
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Open-Meteo error: {e}")
    # Fallback demo data
    return {
        "current": {
            "temperature_2m": 27.5,
            "relative_humidity_2m": 82,
            "apparent_temperature": 30.1,
            "precipitation": 2.4,
            "rain": 2.4,
            "wind_speed_10m": 8.3,
            "wind_direction_10m": 180,
            "surface_pressure": 1010.0,
            "cloud_cover": 75,
            "uv_index": 3.5,
            "dew_point_2m": 22.1,
            "soil_temperature_0cm": 28.2,
            "soil_temperature_6cm": 26.5,
            "soil_temperature_18cm": 25.1,
            "soil_moisture_0_to_1cm": 0.35,
            "soil_moisture_1_to_3cm": 0.38,
            "soil_moisture_3_to_9cm": 0.40,
        },
        "daily": {
            "time": [(now_wib() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)],
            "precipitation_sum": [12.1, 5.3, 0.0, 8.7, 15.2, 3.1, 0.5],
            "temperature_2m_max": [31.2, 30.5, 32.1, 29.8, 28.5, 31.0, 32.5],
            "temperature_2m_min": [23.1, 22.8, 24.0, 22.5, 21.8, 23.5, 24.1],
            "uv_index_max": [8.2, 7.5, 9.1, 6.8, 5.5, 8.0, 9.2],
            "wind_speed_10m_max": [15.2, 12.8, 10.5, 18.3, 20.1, 14.5, 11.2],
            "et0_fao_evapotranspiration": [4.2, 3.8, 4.5, 3.5, 3.2, 4.0, 4.8],
        }
    }

# ─── BMKG API ─────────────────────────────────────────────────────────────────
# Kode wilayah BMKG: Kecamatan Dramaga, Kabupaten Bogor
BMKG_AREA_CODE = "501212"  # Kode adm4 Dramaga

def fetch_bmkg():
    """Ambil prakiraan cuaca BMKG untuk Kecamatan Dramaga."""
    try:
        url = f"https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={BMKG_AREA_CODE}"
        r   = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            # Ambil data cuaca terkini (index 0 = periode terdekat)
            lokasi = data.get("data", [{}])[0]
            cuaca  = lokasi.get("cuaca", [[]])[0]
            if cuaca:
                item = cuaca[0]
                return {
                    "temp":     item.get("t",   27.0),
                    "humidity": item.get("hu",  80.0),
                    "wind_speed":  item.get("ws", 5.0),
                    "wind_dir":    item.get("wd", "S"),
                    "weather_desc": item.get("weather_desc", "Berawan"),
                    "rain_intensity": item.get("rain_intensity", "-"),
                    "source": "BMKG",
                    "ok": True,
                }
    except Exception as e:
        print(f"BMKG error: {e}")
    # Fallback
    return {
        "temp": 27.8, "humidity": 83.0,
        "wind_speed": 6.2, "wind_dir": "S",
        "weather_desc": "Berawan (fallback)",
        "rain_intensity": "-",
        "source": "BMKG", "ok": False,
    }

# ─── DATA FUSION ENGINE ────────────────────────────────────────────────────────
# Bobot tiap sumber (total harus = 1.0)
WEIGHTS = {
    "bmkg":  0.50,   # Paling lokal (stasiun Bogor)
    "owm":   0.30,   # Real-time akurat
    "meteo": 0.20,   # Model numerik global
}

def wind_dir_to_deg(d):
    """Konversi arah angin BMKG (string) ke derajat."""
    mapping = {
        "N":0,"NNE":22.5,"NE":45,"ENE":67.5,
        "E":90,"ESE":112.5,"SE":135,"SSE":157.5,
        "S":180,"SSW":202.5,"SW":225,"WSW":247.5,
        "W":270,"WNW":292.5,"NW":315,"NNW":337.5,
        "U":0,"TL":45,"T":90,"TG":135,
        "BD":225,"B":270,"BL":315,
        "TENGGARA":135,"BARAT DAYA":225,"BARAT LAUT":315,
        "UTARA":0,"SELATAN":180,"TIMUR":90,"BARAT":270,
        "VARIABLE":0,"VAR":0,"-":0,
    }
    return mapping.get(str(d).upper().strip(), 0)

def weighted_avg(values, weights):
    """Hitung weighted average, skip nilai None."""
    total_w, total_v = 0, 0
    for v, w in zip(values, weights):
        if v is not None:
            total_v += v * w
            total_w += w
    return round(total_v / total_w, 2) if total_w > 0 else None

def fuse_data(owm_data, meteo_data, bmkg_data):
    """
    Gabungkan data dari 3 sumber dengan weighted average.
    Return dict berisi nilai fused + breakdown per sumber.
    """
    # Ekstrak nilai dari masing-masing sumber
    owm_temp    = owm_data.get("main", {}).get("temp")
    owm_hum     = owm_data.get("main", {}).get("humidity")
    owm_rain    = owm_data.get("rain", {}).get("1h", 0)
    owm_wind    = owm_data.get("wind", {}).get("speed")
    owm_pres    = owm_data.get("main", {}).get("pressure")
    owm_wdir    = owm_data.get("wind", {}).get("deg", 0)

    mc          = meteo_data.get("current", {})
    met_temp    = mc.get("temperature_2m")
    met_hum     = mc.get("relative_humidity_2m")
    met_rain    = mc.get("precipitation", 0)
    met_wind    = mc.get("wind_speed_10m", 0) / 3.6  # km/h → m/s
    met_pres    = mc.get("surface_pressure")
    met_wdir    = mc.get("wind_direction_10m", 0)

    bmkg_temp   = bmkg_data.get("temp")
    bmkg_hum    = bmkg_data.get("humidity")
    bmkg_rain   = 0  # BMKG tidak beri nilai numerik CH
    bmkg_wind   = bmkg_data.get("wind_speed", 0) / 3.6  # km/h → m/s
    bmkg_pres   = None  # BMKG tidak sediakan tekanan
    bmkg_wdir   = wind_dir_to_deg(bmkg_data.get("wind_dir", "S"))

    W = [WEIGHTS["bmkg"], WEIGHTS["owm"], WEIGHTS["meteo"]]

    fused = {
        "temp":     weighted_avg([bmkg_temp, owm_temp, met_temp],   W),
        "humidity": weighted_avg([bmkg_hum,  owm_hum,  met_hum],    W),
        "rain":     weighted_avg([bmkg_rain, owm_rain, met_rain],    [0, 0.5, 0.5]),  # OWM+Meteo saja
        "wind":     weighted_avg([bmkg_wind, owm_wind, met_wind],    W),
        "pressure": weighted_avg([bmkg_pres, owm_pres, met_pres],    [0, 0.5, 0.5]),
        "wind_dir": weighted_avg([bmkg_wdir, owm_wdir, met_wdir],    W),
        # Breakdown per sumber untuk ditampilkan di dashboard
        "breakdown": {
            "temp":     {"BMKG": bmkg_temp,  "OpenWeather": owm_temp, "Open-Meteo": met_temp},
            "humidity": {"BMKG": bmkg_hum,   "OpenWeather": owm_hum,  "Open-Meteo": met_hum},
            "rain":     {"BMKG": bmkg_rain,  "OpenWeather": owm_rain, "Open-Meteo": met_rain},
            "wind":     {"BMKG": bmkg_wind,  "OpenWeather": owm_wind, "Open-Meteo": met_wind},
        },
        "bmkg_desc":   bmkg_data.get("weather_desc", "-"),
        "bmkg_ok":     bmkg_data.get("ok", False),
        "sources_ok":  sum([
            1 if bmkg_data.get("ok") else 0,
            1 if owm_temp else 0,
            1 if met_temp else 0,
        ]),
    }
    return fused

def soil_moisture_status(val):
    """Interpretasi nilai kelembaban tanah (m³/m³)."""
    if val >= 0.40:   return "Jenuh 💦", "#3b82f6"
    elif val >= 0.30: return "Basah 🌊", "#06b6d4"
    elif val >= 0.20: return "Lembab 🌱", "#10b981"
    elif val >= 0.10: return "Kering 🏜️", "#f59e0b"
    else:             return "Sangat Kering ⚠️", "#ef4444"

def uv_status(val):
    """Interpretasi indeks UV."""
    if val >= 11:   return "Ekstrem 🔴", "#ef4444"
    elif val >= 8:  return "Sangat Tinggi 🟠", "#f97316"
    elif val >= 6:  return "Tinggi 🟡", "#eab308"
    elif val >= 3:  return "Sedang 🟢", "#22c55e"
    else:           return "Rendah ✅", "#10b981"

# ─── TELEGRAM ──────────────────────────────────────────────────────────────────
last_alert_level = {"level": None, "time": datetime(2000, 1, 1, tzinfo=WIB)}

def send_telegram(message: str) -> bool:
    try:
        if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
            print("ERROR: TELEGRAM_BOT_TOKEN belum diisi di Render!")
            return False
        if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
            print("ERROR: TELEGRAM_CHAT_ID belum diisi di Render!")
            return False
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r    = requests.post(url, data=data, timeout=10)
        if r.status_code != 200:
            print(f"ERROR Telegram API {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:
        print(f"ERROR Telegram exception: {e}")
        return False

def check_and_alert(rainfall_1h: float):
    global last_alert_level
    now = now_wib()
    level = None
    emoji = ""

    if rainfall_1h >= THRESHOLD["AWAS"]:
        level, emoji = "AWAS 🔴", "🚨"
    elif rainfall_1h >= THRESHOLD["SIAGA"]:
        level, emoji = "SIAGA 🟠", "⚠️"
    elif rainfall_1h >= THRESHOLD["WASPADA"]:
        level, emoji = "WASPADA 🟡", "⚡"

    if level and (last_alert_level["level"] != level or
                  (now - last_alert_level["time"]).seconds > 3600):
        msg = (
            f"{emoji} <b>PERINGATAN HIDROMETEOROLOGI</b> {emoji}\n"
            f"📍 {LOCATION_NAME}\n"
            f"🕐 {now.strftime('%d %b %Y %H:%M WIB')}\n"
            f"🌧️ Curah Hujan: <b>{rainfall_1h:.1f} mm/jam</b>\n"
            f"⚠️ Status: <b>{level}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{'🔴 AWAS – Potensi banjir bandang & longsor!' if rainfall_1h >= THRESHOLD['AWAS'] else ''}"
            f"{'🟠 SIAGA – Bersiap evakuasi!' if THRESHOLD['SIAGA'] <= rainfall_1h < THRESHOLD['AWAS'] else ''}"
            f"{'🟡 WASPADA – Pantau terus kondisi!' if THRESHOLD['WASPADA'] <= rainfall_1h < THRESHOLD['SIAGA'] else ''}\n"
            f"📊 Sumber: Dashboard Desa Petir"
        )
        send_telegram(msg)
        last_alert_level = {"level": level, "time": now}

# ─── HAZARD ZONES ──────────────────────────────────────────────────────────────
hazard_zones = [
    {"name": "Zona Longsor – Lereng Timur",
     "coords": [[-6.600, 106.710], [-6.605, 106.720], [-6.615, 106.715],
                [-6.610, 106.705], [-6.600, 106.710]],
     "risk": "Tinggi", "color": "#e74c3c"},
    {"name": "Zona Genangan – Lembah Barat",
     "coords": [[-6.620, 106.700], [-6.628, 106.712], [-6.630, 106.705],
                [-6.622, 106.698], [-6.620, 106.700]],
     "risk": "Sedang", "color": "#e67e22"},
    {"name": "Zona Longsor – Bukit Selatan",
     "coords": [[-6.635, 106.718], [-6.640, 106.728], [-6.645, 106.720],
                [-6.638, 106.712], [-6.635, 106.718]],
     "risk": "Tinggi", "color": "#e74c3c"},
    {"name": "Zona Banjir – DAS Cianten",
     "coords": [[-6.610, 106.730], [-6.615, 106.740], [-6.625, 106.735],
                [-6.618, 106.725], [-6.610, 106.730]],
     "risk": "Sedang", "color": "#e67e22"},
    {"name": "Kawasan Aman – Dataran Tengah",
     "coords": [[-6.618, 106.715], [-6.622, 106.725], [-6.628, 106.720],
                [-6.624, 106.710], [-6.618, 106.715]],
     "risk": "Rendah", "color": "#27ae60"},
]

# ─── APP LAYOUT ────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css",
    ],
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    title="Dashboard Hidrometeorologi – Desa Petir",
)

# ─── HELPER: CARD METRIC ───────────────────────────────────────────────────────
def metric_card(icon, label, value_id, unit, color="#2196F3"):
    return html.Div([
        html.Div([
            html.I(className=f"fa {icon}", style={"fontSize": "24px", "color": color}),
        ], style={"marginBottom": "8px"}),
        html.Div(label, style={"fontSize": "12px", "color": "#94a3b8", "fontWeight": "600",
                                "textTransform": "uppercase", "letterSpacing": "0.05em"}),
        html.Div([
            html.Span(id=value_id, style={"fontSize": "28px", "fontWeight": "700", "color": "#f1f5f9"}),
            html.Span(unit, style={"fontSize": "14px", "color": "#64748b", "marginLeft": "4px"}),
        ]),
    ], style={
        "background": "linear-gradient(135deg, #1e293b 0%, #0f172a 100%)",
        "border": f"1px solid {color}33",
        "borderRadius": "12px",
        "padding": "16px 20px",
        "flex": "1",
        "minWidth": "140px",
        "boxShadow": f"0 4px 20px {color}22",
    })

# ─── LAYOUT ────────────────────────────────────────────────────────────────────
app.layout = html.Div([
    dcc.Interval(id="interval-realtime", interval=30_000, n_intervals=0),   # 30 detik
    dcc.Interval(id="interval-weather",  interval=300_000, n_intervals=0),  # 5 menit
    dcc.Store(id="store-weather"),
    dcc.Store(id="store-openmeteo"),
    dcc.Store(id="store-bmkg"),
    dcc.Store(id="store-fused"),
    dcc.Store(id="store-alert-log", data=[]),
    dcc.Interval(id="interval-bmkg", interval=1_800_000, n_intervals=0),  # 30 menit
    dcc.Interval(id="interval-openmeteo", interval=600_000, n_intervals=0),  # 10 menit

    # ── HEADER ─────────────────────────────────────────────────────────────────
    html.Div([
        html.Div([
            html.Div([
                html.I(className="fa fa-cloud-rain", style={"fontSize": "32px", "color": "#38bdf8"}),
                html.Div([
                    html.H1("Dashboard Hidrometeorologi",
                            style={"margin": "0", "fontSize": "22px", "fontWeight": "700", "color": "#f1f5f9"}),
                    html.P(f"📍 {LOCATION_NAME}",
                           style={"margin": "0", "fontSize": "13px", "color": "#64748b"}),
                ]),
            ], style={"display": "flex", "alignItems": "center", "gap": "16px"}),
            html.Div([
                html.Div(id="header-datetime",
                         style={"fontSize": "14px", "color": "#64748b", "textAlign": "right"}),
                html.Div(id="alert-badge",
                         style={"marginTop": "4px", "textAlign": "right"}),
            ]),
        ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                  "maxWidth": "1600px", "margin": "0 auto"}),
    ], style={
        "background": "linear-gradient(90deg, #0f172a 0%, #1e293b 100%)",
        "padding": "16px 24px",
        "borderBottom": "2px solid #1d4ed8",
        "boxShadow": "0 4px 20px rgba(0,0,0,0.4)",
    }),

    # ── MAIN CONTENT ───────────────────────────────────────────────────────────
    html.Div([

        # ── ROW 1: METRIC CARDS ─────────────────────────────────────────────
        html.Div([
            metric_card("fa-thermometer-half", "Suhu",         "val-temp",     "°C",     "#ef4444"),
            metric_card("fa-tint",             "Kelembapan",   "val-humidity", "%",      "#3b82f6"),
            metric_card("fa-cloud-rain",       "CH Sekarang",  "val-rain1h",   "mm/jam", "#06b6d4"),
            metric_card("fa-wind",             "Kecepatan Angin","val-wind",   "m/s",    "#8b5cf6"),
            metric_card("fa-compress-arrows-alt","Tekanan",    "val-pressure", "hPa",    "#f59e0b"),
            metric_card("fa-eye",              "Visibilitas",  "val-vis",      "km",     "#10b981"),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "16px"}),

        # ── ROW FUSION: DATA FUSION PANEL ──────────────────────────────────────────
        html.Div([
            html.Div([
                # Header fusion
                html.Div([
                    html.Div([
                        html.Span("🔀 Data Fusion Engine",
                                  style={"fontSize": "14px", "fontWeight": "700", "color": "#38bdf8"}),
                        html.Span(" — Weighted Average (BMKG 50% · OpenWeather 30% · Open-Meteo 20%)",
                                  style={"fontSize": "11px", "color": "#64748b"}),
                    ]),
                    html.Div(id="fusion-sources-badge"),
                ], style={"display": "flex", "justifyContent": "space-between",
                          "alignItems": "center", "marginBottom": "14px", "flexWrap": "wrap", "gap": "8px"}),
                # Fusion metric cards
                html.Div([
                    # Suhu
                    html.Div([
                        html.Div("🌡️ Suhu Udara", style={"fontSize": "11px", "color": "#94a3b8",
                            "textTransform": "uppercase", "marginBottom": "4px"}),
                        html.Div([
                            html.Span(id="fused-temp",
                                      style={"fontSize": "28px", "fontWeight": "800", "color": "#f1f5f9"}),
                            html.Span("°C", style={"fontSize": "14px", "color": "#64748b", "marginLeft": "4px"}),
                        ]),
                        html.Div(id="fused-temp-breakdown",
                                 style={"marginTop": "8px", "fontSize": "10px"}),
                    ], style={"flex": "1", "minWidth": "140px", "padding": "12px",
                              "background": "#0f172a", "borderRadius": "10px",
                              "border": "1px solid #ef444433"}),
                    # Kelembaban
                    html.Div([
                        html.Div("💧 Kelembaban", style={"fontSize": "11px", "color": "#94a3b8",
                            "textTransform": "uppercase", "marginBottom": "4px"}),
                        html.Div([
                            html.Span(id="fused-humidity",
                                      style={"fontSize": "28px", "fontWeight": "800", "color": "#f1f5f9"}),
                            html.Span("%", style={"fontSize": "14px", "color": "#64748b", "marginLeft": "4px"}),
                        ]),
                        html.Div(id="fused-humidity-breakdown",
                                 style={"marginTop": "8px", "fontSize": "10px"}),
                    ], style={"flex": "1", "minWidth": "140px", "padding": "12px",
                              "background": "#0f172a", "borderRadius": "10px",
                              "border": "1px solid #3b82f633"}),
                    # CH
                    html.Div([
                        html.Div("🌧️ Curah Hujan", style={"fontSize": "11px", "color": "#94a3b8",
                            "textTransform": "uppercase", "marginBottom": "4px"}),
                        html.Div([
                            html.Span(id="fused-rain",
                                      style={"fontSize": "28px", "fontWeight": "800", "color": "#f1f5f9"}),
                            html.Span("mm/jam", style={"fontSize": "11px", "color": "#64748b", "marginLeft": "4px"}),
                        ]),
                        html.Div(id="fused-rain-breakdown",
                                 style={"marginTop": "8px", "fontSize": "10px"}),
                    ], style={"flex": "1", "minWidth": "140px", "padding": "12px",
                              "background": "#0f172a", "borderRadius": "10px",
                              "border": "1px solid #06b6d433"}),
                    # Angin
                    html.Div([
                        html.Div("💨 Kec. Angin", style={"fontSize": "11px", "color": "#94a3b8",
                            "textTransform": "uppercase", "marginBottom": "4px"}),
                        html.Div([
                            html.Span(id="fused-wind",
                                      style={"fontSize": "28px", "fontWeight": "800", "color": "#f1f5f9"}),
                            html.Span("m/s", style={"fontSize": "11px", "color": "#64748b", "marginLeft": "4px"}),
                        ]),
                        html.Div(id="fused-wind-breakdown",
                                 style={"marginTop": "8px", "fontSize": "10px"}),
                    ], style={"flex": "1", "minWidth": "140px", "padding": "12px",
                              "background": "#0f172a", "borderRadius": "10px",
                              "border": "1px solid #8b5cf633"}),
                    # Kondisi BMKG
                    html.Div([
                        html.Div("📡 Kondisi BMKG", style={"fontSize": "11px", "color": "#94a3b8",
                            "textTransform": "uppercase", "marginBottom": "4px"}),
                        html.Div(id="fused-bmkg-desc",
                                 style={"fontSize": "14px", "fontWeight": "700",
                                        "color": "#38bdf8", "lineHeight": "1.4"}),
                        html.Div("Sumber: BMKG Dramaga",
                                 style={"fontSize": "10px", "color": "#475569", "marginTop": "6px"}),
                    ], style={"flex": "1", "minWidth": "140px", "padding": "12px",
                              "background": "#0f172a", "borderRadius": "10px",
                              "border": "1px solid #10b98133"}),
                ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "2px solid #1d4ed8",
                "borderRadius": "12px",
                "padding": "20px",
                "boxShadow": "0 4px 24px rgba(29,78,216,0.25)",
            }),
        ], style={"marginBottom": "16px"}),

        # ── ROW 1B: OPEN-METEO CARDS (Tanah & Lingkungan) ──────────────────────
        html.Div([
            # Suhu Tanah
            html.Div([
                html.Div("🌱 Suhu & Kelembaban Tanah", style={"fontSize": "12px", "color": "#94a3b8",
                    "fontWeight": "600", "textTransform": "uppercase", "marginBottom": "10px"}),
                html.Div([
                    html.Div([
                        html.Div("Permukaan (0cm)", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-soil-temp-0", style={"fontSize": "20px", "fontWeight": "700", "color": "#f97316"}),
                        html.Span("°C", style={"fontSize": "12px", "color": "#64748b", "marginLeft": "3px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("Dalam (6cm)", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-soil-temp-6", style={"fontSize": "20px", "fontWeight": "700", "color": "#f59e0b"}),
                        html.Span("°C", style={"fontSize": "12px", "color": "#64748b", "marginLeft": "3px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("Dalam (18cm)", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-soil-temp-18", style={"fontSize": "20px", "fontWeight": "700", "color": "#eab308"}),
                        html.Span("°C", style={"fontSize": "12px", "color": "#64748b", "marginLeft": "3px"}),
                    ], style={"flex": "1"}),
                ], style={"display": "flex", "gap": "12px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #f97316 33", "borderRadius": "12px",
                "padding": "16px 20px", "flex": "1", "minWidth": "260px",
                "boxShadow": "0 4px 20px #f9731622",
            }),
            # Kelembaban Tanah
            html.Div([
                html.Div("💦 Kelembaban Tanah", style={"fontSize": "12px", "color": "#94a3b8",
                    "fontWeight": "600", "textTransform": "uppercase", "marginBottom": "10px"}),
                html.Div([
                    html.Div([
                        html.Div("0–1 cm", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-soil-moist-0", style={"fontSize": "18px", "fontWeight": "700", "color": "#38bdf8"}),
                        html.Div(id="val-soil-moist-0-status", style={"fontSize": "10px", "marginTop": "2px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("1–3 cm", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-soil-moist-1", style={"fontSize": "18px", "fontWeight": "700", "color": "#06b6d4"}),
                        html.Div(id="val-soil-moist-1-status", style={"fontSize": "10px", "marginTop": "2px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("3–9 cm", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-soil-moist-3", style={"fontSize": "18px", "fontWeight": "700", "color": "#3b82f6"}),
                        html.Div(id="val-soil-moist-3-status", style={"fontSize": "10px", "marginTop": "2px"}),
                    ], style={"flex": "1"}),
                ], style={"display": "flex", "gap": "12px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #38bdf833", "borderRadius": "12px",
                "padding": "16px 20px", "flex": "1", "minWidth": "260px",
                "boxShadow": "0 4px 20px #38bdf822",
            }),
            # UV & Titik Embun
            html.Div([
                html.Div("☀️ Indeks UV & Atmosfer", style={"fontSize": "12px", "color": "#94a3b8",
                    "fontWeight": "600", "textTransform": "uppercase", "marginBottom": "10px"}),
                html.Div([
                    html.Div([
                        html.Div("Indeks UV", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-uv", style={"fontSize": "22px", "fontWeight": "700", "color": "#eab308"}),
                        html.Div(id="val-uv-status", style={"fontSize": "10px", "marginTop": "2px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("Titik Embun", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-dewpoint", style={"fontSize": "22px", "fontWeight": "700", "color": "#a78bfa"}),
                        html.Span("°C", style={"fontSize": "12px", "color": "#64748b", "marginLeft": "3px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("Tutupan Awan", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-cloud", style={"fontSize": "22px", "fontWeight": "700", "color": "#94a3b8"}),
                        html.Span("%", style={"fontSize": "12px", "color": "#64748b", "marginLeft": "3px"}),
                    ], style={"flex": "1"}),
                ], style={"display": "flex", "gap": "12px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #eab30833", "borderRadius": "12px",
                "padding": "16px 20px", "flex": "1", "minWidth": "260px",
                "boxShadow": "0 4px 20px #eab30822",
            }),
            # Evapotranspirasi & Angin
            html.Div([
                html.Div("🌬️ Evapotranspirasi & Angin", style={"fontSize": "12px", "color": "#94a3b8",
                    "fontWeight": "600", "textTransform": "uppercase", "marginBottom": "10px"}),
                html.Div([
                    html.Div([
                        html.Div("Evapotranspirasi", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-et0", style={"fontSize": "22px", "fontWeight": "700", "color": "#10b981"}),
                        html.Span("mm/hr", style={"fontSize": "11px", "color": "#64748b", "marginLeft": "3px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Div("Arah Angin", style={"fontSize": "11px", "color": "#64748b"}),
                        html.Span(id="val-wind-dir", style={"fontSize": "22px", "fontWeight": "700", "color": "#8b5cf6"}),
                        html.Div(id="val-wind-dir-label", style={"fontSize": "10px", "color": "#64748b", "marginTop": "2px"}),
                    ], style={"flex": "1"}),
                ], style={"display": "flex", "gap": "12px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #10b98133", "borderRadius": "12px",
                "padding": "16px 20px", "flex": "1", "minWidth": "220px",
                "boxShadow": "0 4px 20px #10b98122",
            }),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "16px"}),

        # ── ROW 1C: GRAFIK PRAKIRAAN 7 HARI OPEN-METEO ──────────────────────
        html.Div([
            html.Div([
                html.H3("📅 Prakiraan 7 Hari – Open-Meteo",
                        style={"color": "#38bdf8", "margin": "0 0 12px", "fontSize": "15px", "fontWeight": "600"}),
                dcc.Graph(id="chart-openmeteo-daily", config={"displayModeBar": False},
                          style={"height": "220px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33", "borderRadius": "12px",
                "padding": "20px", "flex": "2",
            }),
            html.Div([
                html.H3("🌱 Tren Kelembaban Tanah 24 Jam",
                        style={"color": "#38bdf8", "margin": "0 0 12px", "fontSize": "15px", "fontWeight": "600"}),
                dcc.Graph(id="chart-soil-moisture", config={"displayModeBar": False},
                          style={"height": "220px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33", "borderRadius": "12px",
                "padding": "20px", "flex": "1", "minWidth": "300px",
            }),
        ], style={"display": "flex", "gap": "16px", "marginBottom": "16px", "flexWrap": "wrap"}),

        # ── ROW 2: KONDISI & MAP ────────────────────────────────────────────
        html.Div([

            # Panel kiri: kondisi cuaca + prakiraan
            html.Div([
                html.Div([
                    html.H3("🌤 Kondisi Cuaca Terkini", style={"color": "#38bdf8", "margin": "0 0 16px",
                                                               "fontSize": "15px", "fontWeight": "600"}),
                    html.Div(id="weather-description",
                             style={"fontSize": "16px", "color": "#f1f5f9", "marginBottom": "8px"}),
                    html.Div(id="weather-feelslike",
                             style={"fontSize": "13px", "color": "#94a3b8"}),
                    html.Hr(style={"borderColor": "#1e293b", "margin": "16px 0"}),
                    html.H3("📡 Prakiraan 5 Hari", style={"color": "#38bdf8", "margin": "0 0 12px",
                                                          "fontSize": "15px", "fontWeight": "600"}),
                    dcc.Graph(id="chart-forecast", config={"displayModeBar": False},
                              style={"height": "200px"}),
                ], style={"height": "100%"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "1",
                "minWidth": "300px",
            }),

            # Panel kanan: peta
            html.Div([
                html.H3("🗺 Peta Rawan Bencana Hidrometeorologi",
                        style={"color": "#38bdf8", "margin": "0 0 12px",
                               "fontSize": "15px", "fontWeight": "600"}),
                dl.Map([
                    dl.TileLayer(url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                                 attribution="© OpenStreetMap"),
                    dl.LayerGroup(id="hazard-layer", children=[
                        dl.Polygon(
                            positions=z["coords"],
                            color=z["color"],
                            fillColor=z["color"],
                            fillOpacity=0.4,
                            weight=2,
                            children=[dl.Tooltip(z["name"] + f" – Risiko {z['risk']}")],
                        ) for z in hazard_zones
                    ]),
                    dl.Marker(position=[LAT, LON],
                              children=[dl.Tooltip(f"📍 {LOCATION_NAME}")]),
                ],
                center=[LAT, LON], zoom=13,
                style={"height": "340px", "borderRadius": "8px"},
                id="main-map"),
                # Legenda
                html.Div([
                    html.Div([
                        html.Span("■", style={"color": "#e74c3c", "marginRight": "4px"}), "Longsor Tinggi",
                        html.Span("■", style={"color": "#e67e22", "marginLeft": "12px", "marginRight": "4px"}), "Banjir Sedang",
                        html.Span("■", style={"color": "#27ae60", "marginLeft": "12px", "marginRight": "4px"}), "Aman",
                    ], style={"fontSize": "12px", "color": "#94a3b8", "marginTop": "8px"}),
                ]),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "2",
                "minWidth": "400px",
            }),

        ], style={"display": "flex", "gap": "16px", "marginBottom": "16px", "flexWrap": "wrap"}),

        # ── ROW 3: REALTIME + ALERT LOG ─────────────────────────────────────
        html.Div([
            html.Div([
                html.H3("📈 Data Real-Time (24 Jam Terakhir)",
                        style={"color": "#38bdf8", "margin": "0 0 12px", "fontSize": "15px", "fontWeight": "600"}),
                dcc.Graph(id="chart-realtime", config={"displayModeBar": False},
                          style={"height": "260px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "2",
            }),
            html.Div([
                html.H3("🔔 Log Peringatan",
                        style={"color": "#f59e0b", "margin": "0 0 12px", "fontSize": "15px", "fontWeight": "600"}),
                html.Div(id="alert-log-container",
                         style={"maxHeight": "260px", "overflowY": "auto",
                                "fontSize": "12px", "color": "#cbd5e1"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #f59e0b33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "1",
                "minWidth": "260px",
            }),
        ], style={"display": "flex", "gap": "16px", "marginBottom": "16px", "flexWrap": "wrap"}),

        # ── ROW 4: TREN HISTORIS ────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Div([
                    html.H3("📊 Analisis Tren Historis (2005–2025)",
                            style={"color": "#38bdf8", "margin": "0", "fontSize": "15px", "fontWeight": "600"}),
                    html.Div([
                        dcc.Dropdown(
                            id="hist-view",
                            options=[
                                {"label": "Rata-rata Bulanan", "value": "monthly"},
                                {"label": "Total Tahunan",     "value": "annual"},
                                {"label": "Tren Harian (scatter)", "value": "scatter"},
                                {"label": "Heatmap Bulan × Tahun", "value": "heatmap"},
                                {"label": "Hari Hujan Ekstrem (>50mm)", "value": "extreme"},
                            ],
                            value="monthly",
                            clearable=False,
                            style={"width": "220px", "fontSize": "13px"},
                        ),
                        dcc.RangeSlider(
                            id="year-range",
                            min=2005, max=2025, step=1,
                            value=[2005, 2025],
                            marks={y: str(y) for y in range(2005, 2026, 5)},
                            tooltip={"always_visible": False},
                        ),
                    ], style={"display": "flex", "alignItems": "center", "gap": "16px", "flexWrap": "wrap"}),
                ], style={"display": "flex", "justifyContent": "space-between",
                          "alignItems": "center", "marginBottom": "12px", "flexWrap": "wrap", "gap": "12px"}),
                dcc.Graph(id="chart-historical", config={"displayModeBar": True},
                          style={"height": "320px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "1",
            }),
        ], style={"display": "flex", "gap": "16px", "marginBottom": "16px"}),

        # ── ROW 5: STATISTIK RINGKASAN ──────────────────────────────────────
        html.Div([
            html.Div(id="stat-cards",
                     style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
        ], style={"marginBottom": "16px"}),

        # ── TELEGRAM PANEL ──────────────────────────────────────────────────
        html.Div([
            html.H3("📨 Kirim Notifikasi Telegram Manual",
                    style={"color": "#38bdf8", "margin": "0 0 12px", "fontSize": "15px", "fontWeight": "600"}),
            html.Div([
                dcc.Textarea(
                    id="telegram-msg",
                    placeholder="Tulis pesan notifikasi...",
                    value="",
                    style={"width": "100%", "height": "80px", "background": "#0f172a",
                           "color": "#f1f5f9", "border": "1px solid #1e40af", "borderRadius": "8px",
                           "padding": "10px", "resize": "vertical"},
                ),
                html.Div([
                    html.Button("Kirim ke Telegram 📨", id="btn-send-telegram",
                                style={"background": "#1d4ed8", "color": "white",
                                       "border": "none", "borderRadius": "8px",
                                       "padding": "10px 20px", "cursor": "pointer",
                                       "fontWeight": "600", "fontSize": "13px"}),
                    html.Button("Tes Koneksi Telegram ✅", id="btn-test-telegram",
                                style={"background": "#065f46", "color": "white",
                                       "border": "none", "borderRadius": "8px",
                                       "padding": "10px 20px", "cursor": "pointer",
                                       "fontWeight": "600", "fontSize": "13px"}),
                    html.Span(id="telegram-status", style={"fontSize": "13px", "color": "#94a3b8"}),
                ], style={"display": "flex", "gap": "10px", "alignItems": "center",
                          "marginTop": "10px", "flexWrap": "wrap"}),
            ]),
        ], style={
            "background": "linear-gradient(135deg, #1e293b, #0f172a)",
            "border": "1px solid #1e40af33",
            "borderRadius": "12px",
            "padding": "20px",
            "marginBottom": "16px",
        }),

        # FOOTER
        html.Div(
            f"Dashboard Hidrometeorologi Desa Petir © 2025 | Data CHIRPS | Diperbarui otomatis setiap 30 detik",
            style={"textAlign": "center", "fontSize": "12px", "color": "#475569", "paddingBottom": "12px"}
        ),

    ], style={"maxWidth": "1600px", "margin": "0 auto", "padding": "16px 20px"}),

], style={"background": "#020617", "minHeight": "100vh",
          "fontFamily": "'Inter', sans-serif"})


# ─── CALLBACKS ─────────────────────────────────────────────────────────────────

# 1. Update jam & cuaca dari API
@app.callback(
    Output("store-weather", "data"),
    Input("interval-weather", "n_intervals"),
)
def update_weather_store(_):
    return fetch_weather()

# 2. Update metric cards
@app.callback(
    [Output("header-datetime", "children"),
     Output("val-temp", "children"),
     Output("val-humidity", "children"),
     Output("val-rain1h", "children"),
     Output("val-wind", "children"),
     Output("val-pressure", "children"),
     Output("val-vis", "children"),
     Output("weather-description", "children"),
     Output("weather-feelslike", "children"),
     Output("alert-badge", "children"),
     Output("store-alert-log", "data"),
    ],
    [Input("interval-realtime", "n_intervals"),
     Input("store-weather", "data")],
    [State("store-alert-log", "data")],
)
def update_metrics(_, weather, alert_log):
    if not weather:
        weather = fetch_weather()
    if alert_log is None:
        alert_log = []

    now_str = now_wib().strftime("%A, %d %b %Y  %H:%M:%S WIB")
    temp    = weather["main"]["temp"]
    hum     = weather["main"]["humidity"]
    fl      = weather["main"]["feels_like"]
    pres    = weather["main"]["pressure"]
    wind    = weather["wind"]["speed"]
    vis     = round(weather.get("visibility", 10000) / 1000, 1)
    desc    = weather["weather"][0]["description"].capitalize()
    rain1h  = weather.get("rain", {}).get("1h", 0)

    # Update realtime buffer
    realtime_buffer.append({
        "time": now_wib(),
        "rainfall_mm": round(rain1h, 2),
    })

    # Alert check
    level_color = "#22c55e"
    level_text  = "NORMAL"
    if rain1h >= THRESHOLD["AWAS"]:
        level_color, level_text = "#ef4444", "⚠️ AWAS"
        check_and_alert(rain1h)
        alert_log.append({"time": now_wib().strftime("%H:%M"), "level": "AWAS", "rain": rain1h})
    elif rain1h >= THRESHOLD["SIAGA"]:
        level_color, level_text = "#f97316", "⚠️ SIAGA"
        check_and_alert(rain1h)
        alert_log.append({"time": now_wib().strftime("%H:%M"), "level": "SIAGA", "rain": rain1h})
    elif rain1h >= THRESHOLD["WASPADA"]:
        level_color, level_text = "#eab308", "⚡ WASPADA"
        check_and_alert(rain1h)
        alert_log.append({"time": now_wib().strftime("%H:%M"), "level": "WASPADA", "rain": rain1h})

    badge = html.Span(level_text, style={
        "background": level_color + "22",
        "color": level_color,
        "border": f"1px solid {level_color}",
        "borderRadius": "6px",
        "padding": "2px 10px",
        "fontSize": "12px",
        "fontWeight": "700",
    })

    return (
        now_str,
        f"{temp:.1f}", f"{hum}", f"{rain1h:.1f}", f"{wind:.1f}",
        f"{pres}", f"{vis}",
        f"🌤 {desc}",
        f"Terasa seperti {fl:.1f}°C",
        badge,
        alert_log[-50:],
    )

# 3. Alert log display
@app.callback(
    Output("alert-log-container", "children"),
    Input("store-alert-log", "data"),
)
def render_alert_log(logs):
    if not logs:
        return html.Div("Tidak ada peringatan aktif.", style={"color": "#475569"})
    colors = {"AWAS": "#ef4444", "SIAGA": "#f97316", "WASPADA": "#eab308", "NORMAL": "#22c55e"}
    rows = []
    for entry in reversed(logs[-20:]):
        c = colors.get(entry["level"], "#94a3b8")
        rows.append(html.Div([
            html.Span(entry["time"], style={"color": "#64748b", "marginRight": "8px"}),
            html.Span(entry["level"], style={"color": c, "fontWeight": "700", "marginRight": "8px"}),
            html.Span(f"{entry['rain']:.1f} mm/jam"),
        ], style={"padding": "4px 0", "borderBottom": "1px solid #1e293b"}))
    return rows

# 4. Realtime chart
@app.callback(
    Output("chart-realtime", "figure"),
    Input("interval-realtime", "n_intervals"),
)
def update_realtime(_):
    buf = list(realtime_buffer)
    times = [b["time"] for b in buf]
    vals  = [b["rainfall_mm"] for b in buf]

    fig = go.Figure()
    # Area fill
    fig.add_trace(go.Scatter(
        x=times, y=vals,
        mode="lines",
        line=dict(color="#38bdf8", width=2),
        fill="tozeroy",
        fillcolor="rgba(56,189,248,0.15)",
        name="CH (mm/jam)",
    ))
    # Threshold lines
    for label, val, color in [
        ("Waspada", THRESHOLD["WASPADA"], "#eab308"),
        ("Siaga",   THRESHOLD["SIAGA"],   "#f97316"),
        ("Awas",    THRESHOLD["AWAS"],    "#ef4444"),
    ]:
        fig.add_hline(y=val, line_dash="dash", line_color=color,
                      annotation_text=label, annotation_position="left",
                      annotation_font_color=color, line_width=1)

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=11),
        margin=dict(l=40, r=20, t=20, b=30),
        xaxis=dict(showgrid=False, tickformat="%H:%M"),
        yaxis=dict(showgrid=True, gridcolor="#1e293b", title="mm/jam"),
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
    )
    return fig

# 5. Forecast chart
@app.callback(
    Output("chart-forecast", "figure"),
    Input("store-weather", "data"),
)
def update_forecast(_):
    fc   = fetch_forecast()
    rows = []
    for item in fc.get("list", []):
        rows.append({
            "dt":   item["dt_txt"],
            "temp": item["main"]["temp"],
            "rain": item.get("rain", {}).get("3h", 0),
        })
    df_fc = pd.DataFrame(rows)
    if df_fc.empty:
        return go.Figure()
    df_fc["dt"] = pd.to_datetime(df_fc["dt"])

    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_fc["dt"], y=df_fc["rain"],
                         name="CH (mm/3jam)", marker_color="#38bdf8", opacity=0.7, yaxis="y"))
    fig.add_trace(go.Scatter(x=df_fc["dt"], y=df_fc["temp"],
                             name="Suhu (°C)", line=dict(color="#f97316", width=2), yaxis="y2"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=10),
        margin=dict(l=40, r=40, t=10, b=30),
        xaxis=dict(showgrid=False, tickformat="%d/%m %H:%M", tickangle=-30),
        yaxis=dict(showgrid=True, gridcolor="#1e293b", title="mm"),
        yaxis2=dict(overlaying="y", side="right", showgrid=False, title="°C"),
        barmode="overlay",
        legend=dict(orientation="h", y=-0.35),
        hovermode="x unified",
    )
    return fig

# 6. Historical chart
@app.callback(
    Output("chart-historical", "figure"),
    [Input("hist-view", "value"),
     Input("year-range", "value")],
)
def update_historical(view, year_range):
    df = df_hist[(df_hist["year"] >= year_range[0]) & (df_hist["year"] <= year_range[1])].copy()

    DARK = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#94a3b8", size=11),
                margin=dict(l=50, r=20, t=30, b=40),
                hovermode="x unified")

    if view == "monthly":
        grp = df.groupby("month")["rainfall"].mean().reset_index()
        months = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
        grp["month_str"] = grp["month"].apply(lambda x: months[x-1])
        fig = go.Figure(go.Bar(
            x=grp["month_str"], y=grp["rainfall"].round(2),
            marker=dict(color=grp["rainfall"],
                        colorscale="Blues",
                        showscale=True,
                        colorbar=dict(title="mm")),
            text=grp["rainfall"].round(1), textposition="outside",
        ))
        fig.update_layout(title="Rata-rata Curah Hujan Bulanan", **DARK,
                          yaxis=dict(title="mm/hari", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False))

    elif view == "annual":
        grp = df.groupby("year")["rainfall"].sum().reset_index()
        fig = go.Figure()
        fig.add_trace(go.Bar(x=grp["year"], y=grp["rainfall"].round(0),
                             marker_color="#38bdf8", opacity=0.8, name="Total CH"))
        fig.add_trace(go.Scatter(x=grp["year"],
                                 y=grp["rainfall"].rolling(3, center=True).mean(),
                                 line=dict(color="#f97316", width=2), name="Tren 3-thn"))
        fig.update_layout(title="Total Curah Hujan Tahunan", **DARK,
                          yaxis=dict(title="mm/tahun", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False))

    elif view == "scatter":
        sample = df.sample(min(3000, len(df)))
        fig = px.scatter(sample, x="date", y="rainfall", color="rainfall",
                         color_continuous_scale="Blues",
                         labels={"rainfall": "CH (mm)", "date": "Tanggal"},
                         title="Distribusi Harian Curah Hujan")
        fig.update_layout(**DARK,
                          coloraxis_colorbar=dict(title="mm"),
                          xaxis=dict(showgrid=False),
                          yaxis=dict(showgrid=True, gridcolor="#1e293b"))

    elif view == "heatmap":
        pivot = df.pivot_table(index="month", columns="year", values="rainfall",
                               aggfunc="mean").fillna(0)
        months = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
        fig = go.Figure(go.Heatmap(
            z=pivot.values,
            x=pivot.columns.astype(str),
            y=[months[m-1] for m in pivot.index],
            colorscale="YlOrRd",
            colorbar=dict(title="mm/hari"),
        ))
        fig.update_layout(title="Heatmap Rata-rata CH (Bulan × Tahun)", **DARK,
                          yaxis=dict(autorange="reversed"))

    elif view == "extreme":
        extreme = df[df["rainfall"] >= 50].groupby("year").size().reset_index(name="count")
        fig = go.Figure(go.Bar(x=extreme["year"], y=extreme["count"],
                               marker=dict(color=extreme["count"],
                                           colorscale="Reds", showscale=True,
                                           colorbar=dict(title="Hari")),
                               text=extreme["count"], textposition="outside"))
        fig.update_layout(title="Jumlah Hari Hujan Ekstrem (>50 mm) per Tahun", **DARK,
                          yaxis=dict(title="Hari", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False))

    return fig

# 7. Stat summary cards
@app.callback(
    Output("stat-cards", "children"),
    Input("year-range", "value"),
)
def update_stat_cards(year_range):
    df = df_hist[(df_hist["year"] >= year_range[0]) & (df_hist["year"] <= year_range[1])]
    stats = [
        ("📅 Total Hari",        f"{len(df):,}",    "#3b82f6"),
        ("💧 Rata-rata Harian",  f"{df['rainfall'].mean():.2f} mm",  "#06b6d4"),
        ("🌧️ Hari Hujan",        f"{(df['rainfall'] > 0.5).sum():,}","#8b5cf6"),
        ("⛈️ Hari Ekstrem (>50mm)",f"{(df['rainfall'] > 50).sum():,}","#ef4444"),
        ("📈 Maks Harian",       f"{df['rainfall'].max():.1f} mm",   "#f59e0b"),
        ("📊 Total Periode",     f"{df['rainfall'].sum()/1000:.1f} m",  "#10b981"),
    ]
    cards = []
    for label, value, color in stats:
        cards.append(html.Div([
            html.Div(label, style={"fontSize": "11px", "color": "#94a3b8",
                                   "textTransform": "uppercase", "letterSpacing": "0.05em"}),
            html.Div(value, style={"fontSize": "20px", "fontWeight": "700", "color": "#f1f5f9",
                                   "marginTop": "4px"}),
        ], style={
            "background": "linear-gradient(135deg, #1e293b, #0f172a)",
            "border": f"1px solid {color}44",
            "borderRadius": "10px",
            "padding": "14px 18px",
            "flex": "1",
            "minWidth": "130px",
            "boxShadow": f"0 2px 12px {color}22",
        }))
    return cards

# 8. Telegram manual send
@app.callback(
    Output("telegram-status", "children"),
    [Input("btn-send-telegram", "n_clicks"),
     Input("btn-test-telegram", "n_clicks")],
    [State("telegram-msg", "value")],
    prevent_initial_call=True,
)
def handle_telegram(n_send, n_test, msg):
    ctx = callback_context
    if not ctx.triggered:
        return ""
    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]
    if btn_id == "btn-test-telegram":
        ok = send_telegram(
            f"✅ <b>Tes Koneksi Berhasil</b>\n"
            f"📍 {LOCATION_NAME}\n"
            f"🕐 {now_wib().strftime('%d %b %Y %H:%M WIB')}\n"
            f"Dashboard berfungsi normal."
        )
        return "✅ Koneksi OK!" if ok else "❌ Gagal – cek token/chat ID"
    elif btn_id == "btn-send-telegram":
        if not msg or len(msg.strip()) < 3:
            return "⚠️ Pesan kosong!"
        ok = send_telegram(f"📢 <b>Notifikasi Manual</b>\n{msg}")
        return "✅ Terkirim!" if ok else "❌ Gagal kirim"
    return ""


# ─── CALLBACK: UPDATE BMKG STORE ─────────────────────────────────────────────
@app.callback(
    Output("store-bmkg", "data"),
    Input("interval-bmkg", "n_intervals"),
)
def update_bmkg_store(_):
    return fetch_bmkg()

# ─── CALLBACK: UPDATE FUSED STORE ─────────────────────────────────────────────
@app.callback(
    Output("store-fused", "data"),
    [Input("store-weather",  "data"),
     Input("store-openmeteo","data"),
     Input("store-bmkg",     "data")],
)
def update_fused_store(owm, meteo, bmkg):
    if not owm:   owm   = fetch_weather()
    if not meteo: meteo = fetch_openmeteo()
    if not bmkg:  bmkg  = fetch_bmkg()
    return fuse_data(owm, meteo, bmkg)

# ─── CALLBACK: TAMPILKAN FUSION PANEL ─────────────────────────────────────────
def breakdown_bar(label, value, color, unit=""):
    """Buat mini bar untuk breakdown per sumber."""
    if value is None:
        return html.Div(f"{label}: N/A",
                        style={"color": "#475569", "marginBottom": "2px"})
    max_val = 100 if unit == "%" else (10 if unit == "m/s" else 40)
    pct = min(100, max(0, (value / max_val) * 100))
    return html.Div([
        html.Span(f"{label}: ", style={"color": "#64748b", "minWidth": "80px", "display": "inline-block"}),
        html.Span(f"{value:.1f}{unit} ", style={"color": color, "fontWeight": "600"}),
        html.Div(
            html.Div(style={
                "width": f"{pct}%", "height": "4px",
                "background": color, "borderRadius": "2px",
            }),
            style={"display": "inline-block", "width": "60px",
                   "background": "#1e293b", "borderRadius": "2px",
                   "verticalAlign": "middle"},
        ),
    ], style={"marginBottom": "3px", "display": "flex", "alignItems": "center", "gap": "4px"})

@app.callback(
    [Output("fused-temp",              "children"),
     Output("fused-humidity",          "children"),
     Output("fused-rain",              "children"),
     Output("fused-wind",              "children"),
     Output("fused-bmkg-desc",         "children"),
     Output("fused-temp-breakdown",    "children"),
     Output("fused-humidity-breakdown","children"),
     Output("fused-rain-breakdown",    "children"),
     Output("fused-wind-breakdown",    "children"),
     Output("fusion-sources-badge",    "children"),
    ],
    Input("store-fused", "data"),
)
def update_fusion_panel(fused):
    if not fused:
        fused = fuse_data(fetch_weather(), fetch_openmeteo(), fetch_bmkg())

    temp  = fused.get("temp",     27.5)
    hum   = fused.get("humidity", 80.0)
    rain  = fused.get("rain",     0.0)
    wind  = fused.get("wind",     2.0)
    desc  = fused.get("bmkg_desc", "-")
    bd    = fused.get("breakdown", {})
    n_ok  = fused.get("sources_ok", 0)

    # Warna badge sumber
    badge_color = "#22c55e" if n_ok == 3 else "#f59e0b" if n_ok == 2 else "#ef4444"
    badge = html.Span(
        f"✅ {n_ok}/3 Sumber Aktif",
        style={"background": badge_color + "22", "color": badge_color,
               "border": f"1px solid {badge_color}", "borderRadius": "6px",
               "padding": "2px 10px", "fontSize": "11px", "fontWeight": "700"},
    )

    # Breakdown bars
    def make_bd(param, unit, color_map):
        items = bd.get(param, {})
        return html.Div([
            breakdown_bar(src, val, color_map.get(src, "#94a3b8"), unit)
            for src, val in items.items()
        ])

    cm_temp = {"BMKG": "#f97316", "OpenWeather": "#ef4444", "Open-Meteo": "#f59e0b"}
    cm_hum  = {"BMKG": "#3b82f6", "OpenWeather": "#06b6d4", "Open-Meteo": "#8b5cf6"}
    cm_rain = {"BMKG": "#64748b", "OpenWeather": "#38bdf8", "Open-Meteo": "#0ea5e9"}
    cm_wind = {"BMKG": "#a78bfa", "OpenWeather": "#8b5cf6", "Open-Meteo": "#7c3aed"}

    return (
        f"{temp:.1f}", f"{hum:.0f}", f"{rain:.1f}", f"{wind:.1f}",
        desc,
        make_bd("temp",     "°C",   cm_temp),
        make_bd("humidity", "%",    cm_hum),
        make_bd("rain",     "mm",   cm_rain),
        make_bd("wind",     "m/s",  cm_wind),
        badge,
    )

# ─── CALLBACK: UPDATE OPEN-METEO STORE ────────────────────────────────────────
@app.callback(
    Output("store-openmeteo", "data"),
    Input("interval-openmeteo", "n_intervals"),
)
def update_openmeteo_store(_):
    return fetch_openmeteo()

# ─── CALLBACK: UPDATE OPEN-METEO CARDS ────────────────────────────────────────
@app.callback(
    [Output("val-soil-temp-0",      "children"),
     Output("val-soil-temp-6",      "children"),
     Output("val-soil-temp-18",     "children"),
     Output("val-soil-moist-0",     "children"),
     Output("val-soil-moist-0-status","children"),
     Output("val-soil-moist-1",     "children"),
     Output("val-soil-moist-1-status","children"),
     Output("val-soil-moist-3",     "children"),
     Output("val-soil-moist-3-status","children"),
     Output("val-uv",               "children"),
     Output("val-uv-status",        "children"),
     Output("val-dewpoint",         "children"),
     Output("val-cloud",            "children"),
     Output("val-et0",              "children"),
     Output("val-wind-dir",         "children"),
     Output("val-wind-dir-label",   "children"),
    ],
    [Input("interval-openmeteo", "n_intervals"),
     Input("store-openmeteo",    "data")],
)
def update_openmeteo_cards(_, data):
    if not data:
        data = fetch_openmeteo()
    c = data.get("current", {})

    st0  = c.get("soil_temperature_0cm",  28.0)
    st6  = c.get("soil_temperature_6cm",  26.0)
    st18 = c.get("soil_temperature_18cm", 25.0)
    sm0  = c.get("soil_moisture_0_to_1cm", 0.30)
    sm1  = c.get("soil_moisture_1_to_3cm", 0.32)
    sm3  = c.get("soil_moisture_3_to_9cm", 0.35)
    uv   = c.get("uv_index",       3.5)
    dew  = c.get("dew_point_2m",   22.0)
    cld  = c.get("cloud_cover",    75)
    wdir = c.get("wind_direction_10m", 180)

    # Evapotranspirasi dari daily hari ini
    daily = data.get("daily", {})
    et0_list = daily.get("et0_fao_evapotranspiration", [4.0])
    et0 = et0_list[0] if et0_list else 4.0

    # Status kelembaban tanah
    sm0_txt,  sm0_clr  = soil_moisture_status(sm0)
    sm1_txt,  sm1_clr  = soil_moisture_status(sm1)
    sm3_txt,  sm3_clr  = soil_moisture_status(sm3)
    uv_txt,   uv_clr   = uv_status(uv)

    # Arah angin dalam teks
    dirs = ["U","TL","T","TG","S","BD","B","BL"]
    wind_label = dirs[int((wdir + 22.5) / 45) % 8]

    def sm_span(txt, clr):
        return html.Span(txt, style={"color": clr, "fontWeight": "600"})

    return (
        f"{st0:.1f}", f"{st6:.1f}", f"{st18:.1f}",
        f"{sm0:.2f}", sm_span(sm0_txt, sm0_clr),
        f"{sm1:.2f}", sm_span(sm1_txt, sm1_clr),
        f"{sm3:.2f}", sm_span(sm3_txt, sm3_clr),
        f"{uv:.1f}",  html.Span(uv_txt, style={"color": uv_clr, "fontWeight": "600"}),
        f"{dew:.1f}", f"{cld}",
        f"{et0:.1f}", f"{wdir:.0f}°",
        html.Span(f"({wind_label})", style={"color": "#64748b"}),
    )

# ─── CALLBACK: GRAFIK PRAKIRAAN 7 HARI ────────────────────────────────────────
@app.callback(
    Output("chart-openmeteo-daily", "figure"),
    Input("store-openmeteo", "data"),
)
def update_openmeteo_daily(data):
    if not data:
        data = fetch_openmeteo()
    daily = data.get("daily", {})
    times = daily.get("time", [])
    ch    = daily.get("precipitation_sum", [])
    tmax  = daily.get("temperature_2m_max", [])
    tmin  = daily.get("temperature_2m_min", [])
    uv    = daily.get("uv_index_max", [])

    fig = go.Figure()
    fig.add_trace(go.Bar(x=times, y=ch, name="CH (mm)", marker_color="#38bdf8",
                         opacity=0.8, yaxis="y"))
    fig.add_trace(go.Scatter(x=times, y=tmax, name="Suhu Maks °C",
                             line=dict(color="#ef4444", width=2), yaxis="y2"))
    fig.add_trace(go.Scatter(x=times, y=tmin, name="Suhu Min °C",
                             line=dict(color="#3b82f6", width=2, dash="dot"), yaxis="y2"))
    fig.add_trace(go.Scatter(x=times, y=uv, name="UV Maks",
                             line=dict(color="#eab308", width=1.5, dash="dash"), yaxis="y3"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=10),
        margin=dict(l=40, r=60, t=10, b=40),
        xaxis=dict(showgrid=False),
        yaxis=dict(title="mm", showgrid=True, gridcolor="#1e293b"),
        yaxis2=dict(overlaying="y", side="right", showgrid=False, title="°C"),
        yaxis3=dict(overlaying="y", side="right", showgrid=False,
                    position=0.95, title="UV"),
        barmode="overlay",
        legend=dict(orientation="h", y=-0.3),
        hovermode="x unified",
    )
    return fig

# ─── CALLBACK: GRAFIK KELEMBABAN TANAH ────────────────────────────────────────
# Buffer kelembaban tanah (simulasi tren 24 jam)
soil_buffer = deque(maxlen=48)
def _seed_soil():
    for i in range(48, 0, -1):
        t = now_wib() - timedelta(minutes=i * 30)
        soil_buffer.append({
            "time": t,
            "sm0":  round(max(0.1, min(0.5, 0.35 + np.random.normal(0, 0.02))), 3),
            "sm1":  round(max(0.1, min(0.5, 0.38 + np.random.normal(0, 0.02))), 3),
            "sm3":  round(max(0.1, min(0.5, 0.40 + np.random.normal(0, 0.01))), 3),
        })
_seed_soil()

@app.callback(
    Output("chart-soil-moisture", "figure"),
    Input("interval-openmeteo", "n_intervals"),
)
def update_soil_chart(_):
    buf   = list(soil_buffer)
    times = [b["time"] for b in buf]
    sm0   = [b["sm0"]  for b in buf]
    sm1   = [b["sm1"]  for b in buf]
    sm3   = [b["sm3"]  for b in buf]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=sm0, name="0–1 cm",
                             line=dict(color="#38bdf8", width=2), fill="tozeroy",
                             fillcolor="rgba(56,189,248,0.1)"))
    fig.add_trace(go.Scatter(x=times, y=sm1, name="1–3 cm",
                             line=dict(color="#06b6d4", width=2)))
    fig.add_trace(go.Scatter(x=times, y=sm3, name="3–9 cm",
                             line=dict(color="#3b82f6", width=2)))
    # Garis batas jenuh
    fig.add_hline(y=0.40, line_dash="dash", line_color="#ef4444",
                  annotation_text="Jenuh", annotation_font_color="#ef4444", line_width=1)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=10),
        margin=dict(l=40, r=20, t=10, b=40),
        xaxis=dict(showgrid=False, tickformat="%H:%M"),
        yaxis=dict(title="m³/m³", showgrid=True, gridcolor="#1e293b",
                   range=[0, 0.55]),
        legend=dict(orientation="h", y=-0.3),
        hovermode="x unified",
    )
    return fig

# ─── SERVER EXPORT (wajib untuk Gunicorn / Render.com) ────────────────────────
server = app.server   # <── baris ini yang dibaca Gunicorn

# ─── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8050)

# ─── TELEGRAM WEBHOOK ROUTE ────────────────────────────────────────────────────
from flask import request as flask_request, jsonify

def _tg_send(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram send error: {e}")

def _tg_get_weather():
    try:
        url = (f"https://api.openweathermap.org/data/2.5/weather"
               f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_API_KEY}&units=metric&lang=id")
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def _handle_tg_command(chat_id, text):
    text = text.strip().lower().split("@")[0]
    if text in ("/start", "/help"):
        _tg_send(chat_id,
            "🌧️ <b>Bot Hidrometeorologi – Desa Petir</b>\n\n"
            "/status  – Status cuaca &amp; level peringatan\n"
            "/cuaca   – Info cuaca lengkap\n"
            "/hujan   – Curah hujan hari ini\n"
            "/ekstrem – 5 event hujan terbesar\n"
            "/tren    – Tren tahunan ringkasan\n"
            "/help    – Tampilkan menu ini"
        )
    elif text == "/status":
        w = _tg_get_weather()
        if not w:
            _tg_send(chat_id, "❌ Gagal mengambil data cuaca.")
            return
        temp  = w["main"]["temp"]
        hum   = w["main"]["humidity"]
        desc  = w["weather"][0]["description"].capitalize()
        rain  = w.get("rain", {}).get("1h", 0)
        now   = now_wib().strftime("%d %b %Y %H:%M WIB")
        level, emoji = "NORMAL", "🟢"
        if rain >= 150:   level, emoji = "AWAS",    "🔴"
        elif rain >= 100: level, emoji = "SIAGA",   "🟠"
        elif rain >= 50:  level, emoji = "WASPADA", "🟡"
        _tg_send(chat_id,
            f"{emoji} <b>Status: {level}</b>\n📍 {LOCATION_NAME}\n🕐 {now}\n"
            f"🌡️ {temp:.1f}°C | 💧 {hum}%\n🌤️ {desc}\n🌧️ CH: <b>{rain:.1f} mm/jam</b>"
        )
    elif text == "/cuaca":
        w = _tg_get_weather()
        if not w:
            _tg_send(chat_id, "❌ Gagal mengambil data cuaca.")
            return
        _tg_send(chat_id,
            f"🌤 <b>Cuaca Lengkap – Desa Petir</b>\n━━━━━━━━━━━━━━━━\n"
            f"🌡️ Suhu        : {w['main']['temp']:.1f}°C\n"
            f"🤔 Terasa      : {w['main']['feels_like']:.1f}°C\n"
            f"💧 Kelembapan  : {w['main']['humidity']}%\n"
            f"🌬️ Angin       : {w['wind']['speed']:.1f} m/s\n"
            f"🔵 Tekanan     : {w['main']['pressure']} hPa\n"
            f"👁️ Visibilitas : {w.get('visibility',10000)/1000:.1f} km\n"
            f"🌧️ CH 1 jam    : {w.get('rain',{}).get('1h',0):.1f} mm\n"
            f"🌤️ Kondisi     : {w['weather'][0]['description'].capitalize()}"
        )
    elif text == "/hujan":
        today = now_wib().date()
        today_data = df_hist[df_hist["date"].dt.date == today]
        if today_data.empty:
            last = df_hist.iloc[-1]
            _tg_send(chat_id,
                f"📅 Data hari ini belum tersedia.\n"
                f"Data terakhir ({last['date'].strftime('%d %b %Y')}): {last['rainfall']:.1f} mm"
            )
        else:
            _tg_send(chat_id, f"🌧️ Curah hujan hari ini: <b>{today_data['rainfall'].sum():.1f} mm</b>")
    elif text == "/ekstrem":
        top5 = df_hist.nlargest(5, "rainfall")[["date", "rainfall"]]
        rows = ["⛈️ <b>5 Event Hujan Terbesar (2005–2025)</b>", "━━━━━━━━━━━━━━━━"]
        for i, row in enumerate(top5.itertuples(), 1):
            rows.append(f"{i}. {row.date.strftime('%d %b %Y')} – <b>{row.rainfall:.1f} mm</b>")
        _tg_send(chat_id, "\n".join(rows))
    elif text == "/tren":
        annual = df_hist.groupby(df_hist["date"].dt.year)["rainfall"].agg(["sum","max","mean"])
        rows = ["📊 <b>Tren CH Tahunan (5 tahun terakhir)</b>", "━━━━━━━━━━━━━━━━"]
        for yr, row in annual.tail(5).iterrows():
            rows.append(f"📅 {yr} | Total: {row['sum']:.0f}mm | Maks: {row['max']:.0f}mm | Avg: {row['mean']:.1f}mm")
        _tg_send(chat_id, "\n".join(rows))
    else:
        _tg_send(chat_id, "❓ Perintah tidak dikenali. Ketik /help untuk daftar perintah.")

@server.route("/telegram", methods=["POST"])
def telegram_webhook():
    try:
        data    = flask_request.get_json(force=True)
        msg_obj = data.get("message", {})
        chat_id = msg_obj.get("chat", {}).get("id")
        text    = msg_obj.get("text", "")
        if chat_id and text:
            _handle_tg_command(chat_id, text)
    except Exception as e:
        print(f"Webhook error: {e}")
    return jsonify({"ok": True})

@server.route("/ping")
def ping():
    return "pong", 200
