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

# ─── TELEGRAM ──────────────────────────────────────────────────────────────────
last_alert_level = {"level": None, "time": datetime(2000, 1, 1, tzinfo=WIB)}

def send_telegram(message: str) -> bool:
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r    = requests.post(url, data=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
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
    dcc.Store(id="store-alert-log", data=[]),

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
