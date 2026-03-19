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

# ─── IMPORT MAP LAYERS ────────────────────────────────────────────────────────
try:
    from map_layers import (fetch_batas_desa_petir, fetch_batas_kecamatan_dramaga,
                            fetch_inarisk_indeks, get_map_tile_layers)
    MAP_LAYERS_AVAILABLE = True
except ImportError:
    MAP_LAYERS_AVAILABLE = False

# ─── IMPORT API HEALTH CHECK ──────────────────────────────────────────────────
try:
    from api_health import check_all_apis
    API_HEALTH_AVAILABLE = True
except ImportError:
    API_HEALTH_AVAILABLE = False
    def check_all_apis(): return {"results": {}, "summary": {
        "overall_msg": "Module tidak tersedia", "overall_color": "#64748b",
        "checked_at": "-", "online": 0, "offline": 0, "error": 0, "total": 0}}

# ─── IMPORT BMKG CAP ──────────────────────────────────────────────────────────
try:
    from bmkg_cap import fetch_bmkg_cap, get_cap_status, format_cap_telegram
    BMKG_CAP_AVAILABLE = True
except ImportError:
    BMKG_CAP_AVAILABLE = False
    def fetch_bmkg_cap(): return []
    def get_cap_status(a): return {"active": False, "count": 0, "level": "NORMAL",
        "color": "#22c55e", "emoji": "✅", "message": "Module tidak tersedia",
        "alerts": [], "source": "© BMKG – bmkg.go.id",
        "data_url": "https://github.com/infoBMKG/data-cap"}
    def format_cap_telegram(a): return None

# ─── CONFIG ────────────────────────────────────────────────────────────────────
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_OPENWEATHER_API_KEY")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_TELEGRAM_CHAT_ID")

LAT, LON        = -6.6121, 106.7231
LOCATION_NAME   = "Desa Petir, Dramaga, Bogor"
DATA_FILE       = "data/rainfall_historical.csv"

# Ambang batas peringatan (mm)
# Threshold sementara untuk notifikasi Telegram (akan diupdate)
THRESHOLD_RT = {"WASPADA": 27, "SIAGA": 29, "AWAS": 42}

# ─── SUPABASE REALTIME CONFIG ────────────────────────────────────────────────
SUPABASE_REALTIME_ENABLED = False  # Diaktifkan via env var
try:
    from supabase import create_client as _sb_create
    _sb_url = os.getenv("SUPABASE_URL","")
    _sb_key = os.getenv("SUPABASE_ANON_KEY","")
    if _sb_url and _sb_key:
        SUPABASE_REALTIME_ENABLED = True
        print("✅ Supabase realtime client tersedia")
except ImportError:
    print("⚠️  supabase-py tidak terinstall — realtime tidak aktif")

# ─── LAZY LOADING SEMUA DATA ─────────────────────────────────────────────────
# Tidak ada data yang diload saat startup — semua lazy saat callback pertama kali
# Ini mencegah timeout Gunicorn saat deploy di Render free tier
import json as _json

# ── GeoJSON Batas Desa (file lokal — cepat, tidak perlu lazy) ─────────────────
def load_desa_geojson():
    try:
        with open("desapetir.json", "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception as e:
        print(f"⚠️  desapetir.json tidak ditemukan: {e}")
        return None

def load_slope_geojson():
    try:
        with open("slope_dramaga.json", "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception as e:
        print(f"⚠️  slope_dramaga.json tidak ditemukan: {e}")
        return None

DESA_GEOJSON  = load_desa_geojson()   # File lokal kecil — OK di startup
SLOPE_GEOJSON = load_slope_geojson()  # File lokal 1.6MB — OK di startup

# ── CHIRPS Historical (Supabase — LAZY, tidak diload saat startup) ─────────────
from db import load_historical

import threading as _threading
from datetime import date as _date

_df_hist_cache     = None  # Cache semua data (lazy)
_df_hist_5yr_cache = None  # Cache 5 tahun terakhir (diload saat startup)
_df_hist_lock      = _threading.Lock()

def _load_5yr():
    """Load 5 tahun terakhir dari Supabase saat startup."""
    try:
        import requests as _req
        _sb_url = os.getenv("SUPABASE_URL","")
        _sb_key = os.getenv("SUPABASE_ANON_KEY","")
        if not _sb_url or not _sb_key:
            raise ValueError("No credentials")
        from_date = (_date.today().replace(year=_date.today().year - 5)).isoformat()
        all_rows, offset = [], 0
        while True:
            url = (f"{_sb_url}/rest/v1/rainfall_daily"
                   f"?select=date,rainfall_mm&order=date.asc"
                   f"&date=gte.{from_date}&limit=1000&offset={offset}")
            r = _req.get(url, headers={
                "apikey": _sb_key, "Authorization": f"Bearer {_sb_key}"}, timeout=15)
            rows = r.json() if r.status_code == 200 else []
            if not rows: break
            all_rows += rows
            if len(rows) < 1000: break
            offset += 1000
        if all_rows:
            df = pd.DataFrame(all_rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.rename(columns={"rainfall_mm": "rainfall"})
            df["year"]  = df["date"].dt.year
            df["month"] = df["date"].dt.month
            df["month_str"] = df["date"].dt.strftime("%b")
            df["doy"]   = df["date"].dt.dayofyear
            print(f"✅ CHIRPS 5yr loaded: {len(df):,} baris ({from_date} s/d sekarang)")
            return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"⚠️  CHIRPS 5yr load error: {e}")
    return None

# Load 5 tahun saat startup (lebih kecil, tidak timeout)
_df_hist_5yr_cache = _load_5yr()

def get_hist_data(full=False):
    """
    full=False → pakai cache 5 tahun (sudah diload saat startup)
    full=True  → lazy load semua data historis
    """
    global _df_hist_cache
    if not full:
        return _df_hist_5yr_cache
    # Full historical — lazy
    if _df_hist_cache is not None:
        return _df_hist_cache
    with _df_hist_lock:
        if _df_hist_cache is not None:
            return _df_hist_cache
        try:
            print("🔄 Loading CHIRPS full data (lazy)...")
            _df_hist_cache = load_historical()
            print(f"✅ CHIRPS full loaded: {len(_df_hist_cache):,} baris")
            return _df_hist_cache
        except Exception as e:
            print(f"⚠️  CHIRPS full load error: {e}")
            return _df_hist_5yr_cache  # Fallback ke 5 tahun

# df_hist alias — gunakan 5 tahun cache untuk default
df_hist = _df_hist_5yr_cache

# ─── LOAD DATA MIKROMETEOROLOGI NASA POWER (LAZY) ────────────────────────────
# Tidak diload saat startup — diload saat pertama kali callback dipanggil
try:
    from db_micromet import load_micromet, load_micromet_recent, get_micromet_stats
    MICROMET_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  db_micromet tidak tersedia: {e}")
    MICROMET_AVAILABLE = False

df_micromet = None  # Diload lazy saat callback pertama kali

_df_micromet_lock = _threading.Lock()

def get_micromet_data():
    """Lazy load data micromet — hanya fetch sekali lalu cache (thread-safe)."""
    global df_micromet
    if df_micromet is not None:
        return df_micromet
    if not MICROMET_AVAILABLE:
        return None
    with _df_micromet_lock:
        if df_micromet is not None:
            return df_micromet
        try:
            print("🔄 Loading micromet data (lazy)...")
            df_micromet = load_micromet()
            print(f"✅ Micromet loaded: {len(df_micromet)} baris")
            return df_micromet
        except Exception as e:
            print(f"⚠️  Micromet load error: {e}")
            return None

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
        r = requests.get(url, timeout=10)
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
    for attempt in range(3):  # Retry 3x
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            break
        except requests.Timeout:
            print(f"Open-Meteo timeout (attempt {attempt+1}/3)")
            if attempt < 2:
                time.sleep(2)
        except Exception as e:
            print(f"Open-Meteo error: {e}")
            break
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
# Sumber resmi: https://data.bmkg.go.id/prakiraan-cuaca/
# Kode wilayah: Desa Petir, Kecamatan Dramaga, Kabupaten Bogor, Jawa Barat
# Ref: Kepmendagri No. 100.1.1-6117 Tahun 2022
# © BMKG – Badan Meteorologi, Klimatologi, dan Geofisika (bmkg.go.id)
BMKG_AREA_CODE = "32.01.30.2005"  # Desa Petir, Kec. Dramaga, Kab. Bogor
BMKG_API_URL   = f"https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={BMKG_AREA_CODE}"
BMKG_GITHUB    = "https://github.com/infoBMKG/data-cuaca"

def fetch_bmkg():
    """
    Ambil prakiraan cuaca BMKG untuk Desa Petir, Kec. Dramaga.
    Sumber: © BMKG – https://data.bmkg.go.id/prakiraan-cuaca/
    Kode wilayah: 32.01.30.2005 (Kepmendagri No. 100.1.1-6117 Tahun 2022)
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DashboardPetir/1.0)",
            "Accept": "application/json",
            "Referer": "https://data.bmkg.go.id/",
        }
        r = requests.get(BMKG_API_URL, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()

            # Struktur: data[0].cuaca[periode][jam]
            lokasi_list = data.get("data", [])
            if not lokasi_list:
                raise ValueError("Data lokasi kosong")

            lokasi = lokasi_list[0]
            cuaca_list = lokasi.get("cuaca", [])

            # Cari data yang paling dekat dengan waktu sekarang
            now_wib_dt = now_wib()
            best_item  = None
            min_diff   = float("inf")

            for periode in cuaca_list:
                for item in periode:
                    try:
                        # Parse waktu lokal dari BMKG
                        local_dt_str = item.get("local_datetime", "")
                        if local_dt_str:
                            local_dt = datetime.strptime(
                                local_dt_str[:19], "%Y-%m-%d %H:%M:%S"
                            ).replace(tzinfo=WIB)
                            diff = abs((local_dt - now_wib_dt).total_seconds())
                            if diff < min_diff:
                                min_diff  = diff
                                best_item = item
                    except Exception:
                        continue

            if not best_item:
                best_item = cuaca_list[0][0] if cuaca_list else {}

            # Konversi ws dari km/jam ke m/s
            ws_kmh = float(best_item.get("ws", 0) or 0)
            ws_ms  = round(ws_kmh / 3.6, 2)

            return {
                "temp":          float(best_item.get("t",   27.0) or 27.0),
                "humidity":      float(best_item.get("hu",  80.0) or 80.0),
                "wind_speed":    ws_ms,
                "wind_dir":      best_item.get("wd",  "S"),
                "weather_desc":  best_item.get("weather_desc", "Berawan"),
                "weather_desc_en": best_item.get("weather_desc_en", "Cloudy"),
                "cloud_cover":   float(best_item.get("tcc", 50) or 50),
                "visibility":    best_item.get("vs_text", "-"),
                "local_datetime": best_item.get("local_datetime", "-"),
                "analysis_date": best_item.get("analysis_date", "-"),
                "source":        "BMKG",
                "source_url":    "https://data.bmkg.go.id/prakiraan-cuaca/",
                "area_code":     BMKG_AREA_CODE,
                "ok":            True,
            }

    except Exception as e:
        print(f"BMKG API error: {e}")

    # Fallback jika API tidak tersedia
    return {
        "temp": 27.8, "humidity": 83.0,
        "wind_speed": 1.7, "wind_dir": "S",
        "weather_desc": "Data tidak tersedia",
        "weather_desc_en": "Unavailable",
        "cloud_cover": 50.0,
        "visibility": "-",
        "local_datetime": "-",
        "analysis_date": "-",
        "source": "BMKG", "source_url": "https://data.bmkg.go.id",
        "area_code": BMKG_AREA_CODE,
        "ok": False,
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

    if rainfall_1h >= THRESHOLD_RT["AWAS"]:
        level, emoji = "AWAS 🔴", "🚨"
    elif rainfall_1h >= THRESHOLD_RT["SIAGA"]:
        level, emoji = "SIAGA 🟠", "⚠️"
    elif rainfall_1h >= THRESHOLD_RT["WASPADA"]:
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
            f"{'🔴 AWAS – Potensi banjir bandang & longsor!' if rainfall_1h >= THRESHOLD_RT['AWAS'] else ''}"
            f"{'🟠 SIAGA – Bersiap evakuasi!' if THRESHOLD_RT['SIAGA'] <= rainfall_1h < THRESHOLD_RT['AWAS'] else ''}"
            f"{'🟡 WASPADA – Pantau terus kondisi!' if THRESHOLD_RT['WASPADA'] <= rainfall_1h < THRESHOLD_RT['SIAGA'] else ''}\n"
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
    dcc.Interval(id="interval-weather",  interval=300_000, n_intervals=0),  # 5 menit
    dcc.Store(id="store-weather"),
    dcc.Store(id="store-openmeteo"),
    dcc.Store(id="store-bmkg"),
    dcc.Store(id="store-cap"),
    dcc.Store(id="store-health"),
    dcc.Store(id="store-micromet"),
    dcc.Interval(id="interval-micromet", interval=3_600_000, n_intervals=0),  # 1 jam
    dcc.Interval(id="interval-realtime", interval=30_000, n_intervals=0),  # 30 detik
    # map layers pakai file lokal (tidak perlu store/interval)
    dcc.Interval(id="interval-health", interval=300_000, n_intervals=0),  # 5 menit
    dcc.Interval(id="interval-cap", interval=1_800_000, n_intervals=0),  # 30 menit
    dcc.Store(id="store-fused"),

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

    # ── BANNER PERINGATAN BMKG CAP ────────────────────────────────────────────
    html.Div(id="bmkg-cap-banner"),

    # ── MAIN CONTENT ───────────────────────────────────────────────────────────
    html.Div([

        # ── SUMBER DATA & COPYRIGHT ────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Span("📡 Sumber Data: ", style={"color": "#64748b", "fontSize": "11px"}),
                html.Span("OpenWeatherMap", style={"color": "#38bdf8", "fontSize": "11px", "fontWeight": "600"}),
                html.Span(" · ", style={"color": "#475569"}),
                html.Span("Open-Meteo", style={"color": "#10b981", "fontSize": "11px", "fontWeight": "600"}),
                html.Span(" · ", style={"color": "#475569"}),
                html.Span("© BMKG", style={"color": "#f59e0b", "fontSize": "11px", "fontWeight": "600"}),
                html.Span(" · ", style={"color": "#475569"}),
                html.Span("NASA CHIRPS", style={"color": "#8b5cf6", "fontSize": "11px", "fontWeight": "600"}),
                html.Span(" · ", style={"color": "#475569"}),
                html.Span("Supabase", style={"color": "#22c55e", "fontSize": "11px", "fontWeight": "600"}),
            ]),
            html.Div([
                html.Span("⚠️ Data peringatan dini oleh ", style={"color": "#64748b", "fontSize": "10px"}),
                html.A("© BMKG – Badan Meteorologi, Klimatologi, dan Geofisika",
                       href="https://www.bmkg.go.id", target="_blank",
                       style={"color": "#f59e0b", "fontSize": "10px", "textDecoration": "none"}),
                html.Span(" | Data CAP: ", style={"color": "#64748b", "fontSize": "10px"}),
                html.A("github.com/infoBMKG/data-cap",
                       href="https://github.com/infoBMKG/data-cap", target="_blank",
                       style={"color": "#64748b", "fontSize": "10px"}),
            ]),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "background": "#0f172a", "border": "1px solid #1e293b",
            "borderRadius": "8px", "padding": "8px 16px",
            "marginBottom": "12px", "flexWrap": "wrap", "gap": "8px",
        }),

        # ROW 1 metric individual dihapus — digabung ke panel cuaca terpadu di bawah

        # ── PANEL CUACA TERPADU (Data Fusion + Open-Meteo compact) ────────────
        html.Div([
            html.Div([
                # Header
                html.Div([
                    html.Span("🔀 Cuaca Terpadu",
                              style={"fontSize":"13px","fontWeight":"700","color":"#38bdf8"}),
                    html.Span(" — Weighted Average (BMKG 50% · OWM 30% · Open-Meteo 20%)",
                              style={"fontSize":"10px","color":"#475569"}),
                    html.Div(id="fusion-sources-badge",
                             style={"marginLeft":"auto"}),
                ], style={"display":"flex","alignItems":"center","gap":"8px",
                          "marginBottom":"14px","flexWrap":"wrap"}),

                # Cards row — 1 baris 8 parameter
                html.Div([
                    # Suhu
                    html.Div([
                        html.Div("🌡 Suhu", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Span(id="fused-temp", style={"fontSize":"24px","fontWeight":"800","color":"#ef4444"}),
                        html.Span(" °C", style={"fontSize":"11px","color":"#64748b"}),
                        html.Div(id="fused-temp-breakdown", style={"display":"none"}),
                    ], style={"flex":"1","minWidth":"100px","padding":"12px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #ef444433"}),
                    # Kelembaban
                    html.Div([
                        html.Div("💧 Kelembaban", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Span(id="fused-humidity", style={"fontSize":"24px","fontWeight":"800","color":"#3b82f6"}),
                        html.Span(" %", style={"fontSize":"11px","color":"#64748b"}),
                        html.Div(id="fused-humidity-breakdown", style={"display":"none"}),
                    ], style={"flex":"1","minWidth":"100px","padding":"12px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #3b82f633"}),
                    # CH
                    html.Div([
                        html.Div("🌧 CH", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Span(id="fused-rain", style={"fontSize":"24px","fontWeight":"800","color":"#06b6d4"}),
                        html.Span(" mm", style={"fontSize":"11px","color":"#64748b"}),
                        html.Div(id="fused-rain-breakdown", style={"display":"none"}),
                    ], style={"flex":"1","minWidth":"100px","padding":"12px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #06b6d433"}),
                    # Angin
                    html.Div([
                        html.Div("💨 Angin", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Span(id="fused-wind", style={"fontSize":"24px","fontWeight":"800","color":"#8b5cf6"}),
                        html.Span(" m/s", style={"fontSize":"11px","color":"#64748b"}),
                        html.Div(id="fused-wind-breakdown", style={"display":"none"}),
                    ], style={"flex":"1","minWidth":"100px","padding":"12px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #8b5cf633"}),
                    # Tekanan
                    html.Div([
                        html.Div("🔵 Tekanan", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Div([
                            html.Span(id="val-pressure", style={"fontSize":"22px","fontWeight":"800","color":"#f59e0b"}),
                            html.Span(" hPa", style={"fontSize":"11px","color":"#64748b"}),
                        ]),
                    ], style={"flex":"1","minWidth":"100px","padding":"10px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #f59e0b33"}),
                    # UV
                    html.Div([
                        html.Div("☀️ UV Index", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Div([
                            html.Span(id="val-uv", style={"fontSize":"22px","fontWeight":"800","color":"#eab308"}),
                        ]),
                        html.Div(id="val-uv-status", style={"fontSize":"9px","marginTop":"4px"}),
                    ], style={"flex":"1","minWidth":"100px","padding":"10px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #eab30833"}),
                    # ET0
                    html.Div([
                        html.Div("🌿 ET₀", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Div([
                            html.Span(id="val-et0", style={"fontSize":"22px","fontWeight":"800","color":"#10b981"}),
                            html.Span(" mm/hari", style={"fontSize":"11px","color":"#64748b"}),
                        ]),
                    ], style={"flex":"1","minWidth":"100px","padding":"10px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #10b98133"}),
                    # Kondisi BMKG
                    html.Div([
                        html.Div("📡 BMKG", style={"fontSize":"10px","color":"#94a3b8","marginBottom":"4px"}),
                        html.Div(id="fused-bmkg-desc",
                                 style={"fontSize":"13px","fontWeight":"700","color":"#38bdf8","lineHeight":"1.4"}),
                        html.A("© BMKG", href="https://data.bmkg.go.id/prakiraan-cuaca/",
                               target="_blank",
                               style={"fontSize":"9px","color":"#f59e0b","textDecoration":"none"}),
                    ], style={"flex":"1","minWidth":"100px","padding":"10px","background":"#0f172a",
                              "borderRadius":"10px","border":"1px solid #10b98133"}),
                ], style={"display":"flex","gap":"8px","flexWrap":"wrap"}),

                # Row 2 — Tanah compact
                html.Div([
                    html.Div([
                        html.Span("🌱 Suhu Tanah: ", style={"fontSize":"10px","color":"#64748b"}),
                        html.Span(id="val-soil-temp-0", style={"fontSize":"11px","fontWeight":"700","color":"#f97316"}),
                        html.Span(" °C (0cm) | ", style={"fontSize":"10px","color":"#334155"}),
                        html.Span(id="val-soil-temp-6", style={"fontSize":"11px","fontWeight":"700","color":"#f59e0b"}),
                        html.Span(" °C (6cm) | ", style={"fontSize":"10px","color":"#334155"}),
                        html.Span(id="val-soil-temp-18", style={"fontSize":"11px","fontWeight":"700","color":"#eab308"}),
                        html.Span(" °C (18cm)", style={"fontSize":"10px","color":"#334155"}),
                    ], style={"flex":"1"}),
                    html.Div([
                        html.Span("💦 Kelembaban Tanah: ", style={"fontSize":"10px","color":"#64748b"}),
                        html.Span(id="val-soil-moist-0", style={"fontSize":"11px","fontWeight":"700","color":"#38bdf8"}),
                        html.Span(id="val-soil-moist-0-status", style={"fontSize":"9px","color":"#475569"}),
                        html.Span(" m³/m³ (0-1cm) | ", style={"fontSize":"10px","color":"#334155"}),
                        html.Span(id="val-soil-moist-1", style={"fontSize":"11px","fontWeight":"700","color":"#06b6d4"}),
                        html.Span(id="val-soil-moist-1-status", style={"fontSize":"9px","color":"#475569"}),
                        html.Span(" m³/m³ (1-3cm) | ", style={"fontSize":"10px","color":"#334155"}),
                        html.Span(id="val-soil-moist-3", style={"fontSize":"11px","fontWeight":"700","color":"#3b82f6"}),
                        html.Span(id="val-soil-moist-3-status", style={"fontSize":"9px","color":"#475569"}),
                        html.Span(" m³/m³ (3-9cm)", style={"fontSize":"10px","color":"#334155"}),
                    ], style={"flex":"1"}),
                    html.Div([
                        html.Span("💨 Arah Angin: ", style={"fontSize":"10px","color":"#64748b"}),
                        html.Span(id="val-wind-dir", style={"fontSize":"11px","fontWeight":"700","color":"#8b5cf6"}),
                        html.Span("° ", style={"fontSize":"10px","color":"#334155"}),
                        html.Span(id="val-wind-dir-label", style={"fontSize":"10px","color":"#64748b"}),
                        html.Span(" | 🌫 Titik Embun: ", style={"fontSize":"10px","color":"#64748b"}),
                        html.Span(id="val-dewpoint", style={"fontSize":"11px","fontWeight":"700","color":"#a78bfa"}),
                        html.Span(" °C", style={"fontSize":"10px","color":"#334155"}),
                        html.Span(" | ☁️ Awan: ", style={"fontSize":"10px","color":"#64748b"}),
                        html.Span(id="val-cloud", style={"fontSize":"11px","fontWeight":"700","color":"#94a3b8"}),
                        html.Span(" %", style={"fontSize":"10px","color":"#334155"}),
                    ], style={"flex":"1"}),
                ], style={"display":"flex","gap":"16px","flexWrap":"wrap",
                          "marginTop":"10px","padding":"8px 0",
                          "borderTop":"1px solid #1e293b"}),
            ], style={
                "background":"linear-gradient(135deg, #1e293b, #0f172a)",
                "border":"2px solid #1d4ed8",
                "borderRadius":"12px","padding":"16px 20px",
                "boxShadow":"0 4px 24px rgba(29,78,216,0.2)",
            }),
        ], style={"marginBottom":"16px"}),

                # ── ROW 1C: PRAKIRAAN TERPADU ──────────────────────────────────────
        html.Div([
            html.Div([
                html.H3("📅 Prakiraan 7 Hari",
                        style={"color":"#38bdf8","margin":"0 0 10px","fontSize":"14px","fontWeight":"600"}),
                dcc.Graph(id="chart-openmeteo-daily", config={"displayModeBar": False},
                          style={"height":"200px"}),
            ], style={
                "background":"linear-gradient(135deg, #1e293b, #0f172a)",
                "border":"1px solid #1e40af33","borderRadius":"12px",
                "padding":"16px","flex":"2","minWidth":"300px",
            }),
            html.Div([
                html.H3("🌱 Kelembaban Tanah 24 Jam",
                        style={"color":"#38bdf8","margin":"0 0 10px","fontSize":"14px","fontWeight":"600"}),
                dcc.Graph(id="chart-soil-moisture", config={"displayModeBar": False},
                          style={"height":"200px"}),
            ], style={
                "background":"linear-gradient(135deg, #1e293b, #0f172a)",
                "border":"1px solid #1e40af33","borderRadius":"12px",
                "padding":"16px","flex":"1","minWidth":"260px",
            }),
        ], style={"display":"flex","gap":"12px","marginBottom":"16px","flexWrap":"wrap"}),

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
                html.Div([
                    html.H3("🗺 Peta Rawan Bencana Hidrometeorologi",
                            style={"color": "#38bdf8", "margin": "0",
                                   "fontSize": "15px", "fontWeight": "600"}),
                    # Layer toggle buttons
                    html.Div([
                        html.Button("🏔️ Longsor", id="btn-layer-longsor",
                                    n_clicks=1,
                                    style={"background": "#ef444433", "color": "#ef4444",
                                           "border": "1px solid #ef4444",
                                           "borderRadius": "6px", "padding": "3px 10px",
                                           "cursor": "pointer", "fontSize": "11px",
                                           "fontWeight": "600"}),
                        html.Button("🌊 Banjir", id="btn-layer-banjir",
                                    n_clicks=1,
                                    style={"background": "#3b82f633", "color": "#3b82f6",
                                           "border": "1px solid #3b82f6",
                                           "borderRadius": "6px", "padding": "3px 10px",
                                           "cursor": "pointer", "fontSize": "11px",
                                           "fontWeight": "600"}),
                        html.Button("⛈️ Cuaca Ekstrim", id="btn-layer-cuaca",
                                    n_clicks=0,
                                    style={"background": "#1e293b", "color": "#64748b",
                                           "border": "1px solid #334155",
                                           "borderRadius": "6px", "padding": "3px 10px",
                                           "cursor": "pointer", "fontSize": "11px"}),
                        html.Button("🏔️ Kemiringan Lereng", id="btn-layer-slope",
                                    n_clicks=1,
                                    style={"background": "#78716c33", "color": "#a8a29e",
                                           "border": "1px solid #78716c",
                                           "borderRadius": "6px", "padding": "3px 10px",
                                           "cursor": "pointer", "fontSize": "11px",
                                           "fontWeight": "600"}),
                        html.Button("🏘️ Batas Desa", id="btn-layer-desa",
                                    n_clicks=1,
                                    style={"background": "#f59e0b33", "color": "#f59e0b",
                                           "border": "1px solid #f59e0b",
                                           "borderRadius": "6px", "padding": "3px 10px",
                                           "cursor": "pointer", "fontSize": "11px",
                                           "fontWeight": "600"}),
                    ], style={"display": "flex", "gap": "6px", "flexWrap": "wrap"}),
                ], style={"display": "flex", "justifyContent": "space-between",
                          "alignItems": "center", "marginBottom": "10px",
                          "flexWrap": "wrap", "gap": "8px"}),

                dl.Map([
                    # Basemap OpenStreetMap
                    dl.TileLayer(
                        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                        attribution="© <a href='https://openstreetmap.org/copyright'>OpenStreetMap</a>",
                    ),
                    # Layer Risiko Longsor (GeoJSON dari data BNPB InaRisk)
                    dl.LayerGroup(id="layer-longsor"),
                    # Layer Risiko Banjir (GeoJSON dari data BNPB InaRisk)
                    dl.LayerGroup(id="layer-banjir"),
                    # Layer Risiko Cuaca Ekstrim (default off)
                    dl.LayerGroup(id="layer-cuaca-ekstrim"),
                    # Layer kemiringan lereng (Puslittanak 2004)
                    dl.LayerGroup(id="layer-slope"),
                    # Layer batas desa dari BIG (diupdate via callback)
                    dl.LayerGroup(id="layer-batas-desa"),
                    # Marker lokasi Desa Petir
                    dl.Marker(
                        position=[LAT, LON],
                        children=[
                            dl.Tooltip("📍 Desa Petir, Kec. Dramaga"),
                            dl.Popup(html.Div([
                                html.B("📍 Desa Petir"),
                                html.Br(),
                                html.Span("Kec. Dramaga, Kab. Bogor"),
                                html.Br(),
                                html.Span(f"Koordinat: {LAT}, {LON}",
                                          style={"fontSize": "11px", "color": "#64748b"}),
                            ])),
                        ],
                    ),
                ],
                center=[LAT, LON], zoom=13,
                style={"height": "360px", "borderRadius": "8px"},
                id="main-map"),

                # Legenda & Copyright
                html.Div([
                    html.Div([
                        html.Span("■", style={"color": "#ef4444", "marginRight": "3px"}),
                        html.Span("Longsor Tinggi", style={"marginRight": "10px"}),
                        html.Span("■", style={"color": "#3b82f6", "marginRight": "3px"}),
                        html.Span("Banjir Tinggi", style={"marginRight": "10px"}),
                        html.Span("─", style={"color": "#f59e0b", "marginRight": "3px",
                                              "fontWeight": "700"}),
                        html.Span("Batas Desa", style={"marginRight": "10px"}),
                        html.Span("■", style={"color": "#22c55e", "marginRight": "3px"}),
                        html.Span("Datar", style={"marginRight": "6px"}),
                        html.Span("■", style={"color": "#eab308", "marginRight": "3px"}),
                        html.Span("Landai", style={"marginRight": "6px"}),
                        html.Span("■", style={"color": "#f97316", "marginRight": "3px"}),
                        html.Span("Agak Curam", style={"marginRight": "6px"}),
                        html.Span("■", style={"color": "#ef4444", "marginRight": "3px"}),
                        html.Span("Curam"),
                    ], style={"fontSize": "11px", "color": "#94a3b8"}),
                    html.Div([
                        html.Span("© Sumber: ", style={"color": "#475569", "fontSize": "10px"}),
                        html.A("BNPB InaRisk",
                               href="https://inarisk.bnpb.go.id", target="_blank",
                               style={"color": "#ef4444", "fontSize": "10px",
                                      "textDecoration": "none"}),
                        html.Span(" · ", style={"color": "#334155"}),
                        html.A("BIG",
                               href="https://geoservices.big.go.id", target="_blank",
                               style={"color": "#f59e0b", "fontSize": "10px",
                                      "textDecoration": "none"}),
                        html.Span(" · ", style={"color": "#334155"}),
                        html.A("OpenStreetMap",
                               href="https://openstreetmap.org/copyright", target="_blank",
                               style={"color": "#38bdf8", "fontSize": "10px",
                                      "textDecoration": "none"}),
                    ]),
                ], style={"display": "flex", "justifyContent": "space-between",
                          "alignItems": "center", "marginTop": "8px",
                          "flexWrap": "wrap", "gap": "4px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "2",
                "minWidth": "400px",
            }),

        ], style={"display": "flex", "gap": "16px", "marginBottom": "16px", "flexWrap": "wrap"}),

        # ── ROW 4: TREN HISTORIS ────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Div([
                    html.H3(id="hist-title", children="📊 Analisis Tren Historis",
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
                            min=2005,
                            max=2026,
                            step=1,
                            value=[2021, 2026],
                            marks={y: str(y) for y in range(2005, 2027, 5)},
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

        # ── ROW NASA POWER: DATA MIKROMETEOROLOGI ──────────────────────────────
        html.Div([
            html.Div([
                html.Div([
                    html.H3("🛰️ Data Mikrometeorologi Historis – NASA POWER",
                            style={"color": "#38bdf8", "margin": "0",
                                   "fontSize": "15px", "fontWeight": "600"}),
                    html.Div([
                        dcc.Dropdown(
                            id="micromet-param",
                            options=[
                                {"label": "🌡️ Suhu Udara (Maks/Min/Rata)",  "value": "temp"},
                                {"label": "💧 Kelembaban Udara",             "value": "rh"},
                                {"label": "💨 Kecepatan Angin",              "value": "wind"},
                                {"label": "☀️ Radiasi Matahari",             "value": "rad"},
                                {"label": "🌿 Evapotranspirasi",             "value": "et0"},
                                {"label": "🔵 Tekanan Udara",                "value": "pres"},
                                {"label": "🌧️ CH NASA vs CHIRPS",            "value": "prec"},
                            ],
                            value="temp",
                            clearable=False,
                            style={"width": "240px", "fontSize": "13px"},
                        ),
                        dcc.RangeSlider(
                            id="micromet-year-range",
                            min=1981,
                            max=2026,
                            step=1,
                            value=[2005, 2026],
                            marks={y: str(y) for y in range(1981, 2027, 5)},
                            tooltip={"always_visible": False},
                        ),
                    ], style={"display": "flex", "alignItems": "center",
                              "gap": "16px", "flexWrap": "wrap"}),
                ], style={"display": "flex", "justifyContent": "space-between",
                          "alignItems": "center", "marginBottom": "12px",
                          "flexWrap": "wrap", "gap": "12px"}),
                dcc.Graph(id="chart-micromet", config={"displayModeBar": True},
                          style={"height": "300px"}),
                # Info sumber data
                html.Div([
                    html.Span("© Sumber: ", style={"fontSize": "10px", "color": "#475569"}),
                    html.A("NASA POWER – Prediction Of Worldwide Energy Resources",
                           href="https://power.larc.nasa.gov",
                           target="_blank",
                           style={"fontSize": "10px", "color": "#38bdf8",
                                  "textDecoration": "none"}),
                    html.Span(" | Resolusi: Harian | Koordinat: -6.6121°S, 106.7231°E",
                              style={"fontSize": "10px", "color": "#475569"}),
                ], style={"marginTop": "8px"}),
            ], style={
                "background": "linear-gradient(135deg, #1e293b, #0f172a)",
                "border": "1px solid #1e40af33",
                "borderRadius": "12px",
                "padding": "20px",
                "flex": "1",
            }),
        ], style={"display": "flex", "gap": "16px", "marginBottom": "16px"}),

        # ── ROW STATISTIK MICROMET ───────────────────────────────────────────
        html.Div([
            html.Div(id="micromet-stat-cards",
                     style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
        ], style={"marginBottom": "16px"}),

        # ── API HEALTH CHECK PANEL ─────────────────────────────────────────────
        html.Div([
            html.Div([
                html.H3("🔍 Status API & Koneksi",
                        style={"color": "#38bdf8", "margin": "0", "fontSize": "15px", "fontWeight": "600"}),
                html.Div(id="health-overall-badge"),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "center", "marginBottom": "14px"}),
            html.Div(id="health-cards",
                     style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}),
            html.Div(id="health-checked-at",
                     style={"fontSize": "11px", "color": "#475569", "marginTop": "10px"}),
        ], style={
            "background": "linear-gradient(135deg, #1e293b, #0f172a)",
            "border": "1px solid #1e40af33", "borderRadius": "12px",
            "padding": "20px", "marginBottom": "16px",
        }),

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

# ─── CALLBACK: UPDATE JUDUL HISTORIS DINAMIS ─────────────────────────────────
@app.callback(
    Output("hist-title", "children"),
    Input("year-range", "value"),
)
def update_hist_title(year_range):
    df = get_hist_data(full=False)
    data_range = ""
    if df is not None and len(df) > 0:
        yr_min = int(df["year"].min())
        yr_max = int(df["year"].max())
        data_range = f" [{yr_min}–{yr_max}]"
    return f"📊 Analisis Tren Historis ({year_range[0]}–{year_range[1]})"

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
    Output("chart-historical", "figure"),
    [Input("hist-view",   "value"),
     Input("year-range",  "value")],
)
def update_historical(view, year_range):
    # Coba full data dulu, fallback ke 5 tahun jika belum siap
    df = get_hist_data(full=True)
    if df is None or len(df) == 0:
        df = get_hist_data(full=False)
    if df is None or len(df) == 0:
        fig = go.Figure()
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#94a3b8"),
            annotations=[dict(text="⏳ Memuat data CHIRPS...",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=16, color="#38bdf8"))],
        )
        return fig
    df = df.copy()
    df["year"] = df["year"].astype(int)
    df = df[(df["year"] >= year_range[0]) & (df["year"] <= year_range[1])]
    if len(df) == 0:
        # Tidak ada data di range ini, tampilkan semua
        df_all = get_hist_data(full=True) or get_hist_data(full=False)
        df = df_all.copy()

    DARK = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=10),
        margin=dict(l=50, r=20, t=20, b=40),
        hovermode="x unified",
    )
    fig = go.Figure()

    if view == "monthly":
        m = df.groupby("month")["rainfall"].mean().reindex(range(1,13), fill_value=0)
        months = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
        colorscale = [f"rgba(56,189,248,{0.4 + 0.05*i})" for i in range(12)]
        fig.add_trace(go.Bar(x=months, y=m.values.round(1),
            marker_color=colorscale, name="Rata-rata CH (mm/hari)",
            text=[f"{v:.1f}" for v in m.values], textposition="outside",
            textfont=dict(size=9)))
        fig.update_layout(title="Rata-rata Curah Hujan Bulanan",
                          yaxis=dict(title="mm/hari", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif view == "annual":
        a = df.groupby("year")["rainfall"].sum()
        fig.add_trace(go.Bar(x=a.index, y=a.values.round(0),
            marker_color="#38bdf8", opacity=0.85, name="Total CH (mm/tahun)"))
        fig.update_layout(title="Total Curah Hujan Tahunan",
                          yaxis=dict(title="mm/tahun", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif view == "scatter":
        fig.add_trace(go.Scatter(x=df["date"], y=df["rainfall"],
            mode="markers", marker=dict(
                color=df["rainfall"], colorscale="Blues", size=3, opacity=0.7,
                colorbar=dict(title="mm", thickness=12)),
            name="CH Harian (mm)"))
        fig.update_layout(title="Distribusi Harian Curah Hujan",
                          yaxis=dict(title="CH (mm)", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(title="Tanggal", showgrid=False), **DARK)

    elif view == "heatmap":
        pivot = df.pivot_table(index="month", columns="year", values="rainfall", aggfunc="mean")
        fig.add_trace(go.Heatmap(
            z=pivot.values, x=pivot.columns, y=pivot.index,
            colorscale="Blues", colorbar=dict(title="mm/hari", thickness=12),
            hoverongaps=False))
        months_lbl = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
        fig.update_layout(title="Heatmap CH — Bulan × Tahun",
                          yaxis=dict(ticktext=months_lbl, tickvals=list(range(1,13))),
                          **DARK)

    elif view == "extreme":
        ex = df[df["rainfall"] >= 50].groupby("year").size()
        all_years = range(year_range[0], year_range[1]+1)
        ex = ex.reindex(all_years, fill_value=0)
        fig.add_trace(go.Bar(x=ex.index, y=ex.values,
            marker_color="#ef4444", opacity=0.85,
            name="Hari Ekstrem (≥50mm)"))
        fig.update_layout(title="Frekuensi Hari Hujan Ekstrem (≥50 mm/hari)",
                          yaxis=dict(title="Jumlah Hari", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    return fig

@app.callback(
    Output("stat-cards", "children"),
    Input("year-range", "value"),
)
def update_stat_cards(year_range):
    df = get_hist_data(full=True)
    if df is None or len(df) == 0:
        return []
    df = df[(df["year"] >= year_range[0]) & (df["year"] <= year_range[1])]

    stats = [
        ("📅", "Total Hari",       f"{len(df):,}",                    "#38bdf8"),
        ("💧", "Rata-rata Harian", f"{df['rainfall'].mean():.2f} mm", "#10b981"),
        ("🌧", "Hari Hujan",       f"{(df['rainfall'] > 0.5).sum():,}","#3b82f6"),
        ("⛈️", "Hari Ekstrem >50mm", f"{(df['rainfall'] >= 50).sum():,}", "#ef4444"),
        ("📈", "Maks Harian",      f"{df['rainfall'].max():.1f} mm",  "#f59e0b"),
        ("🗓", "Total Periode",    f"{df['rainfall'].sum()/1000:.1f} m","#8b5cf6"),
    ]
    cards = []
    for icon, label, value, color in stats:
        cards.append(html.Div([
            html.Div([html.Span(icon, style={"fontSize":"18px"}),
                      html.Span(f" {label}", style={"fontSize":"10px","color":"#94a3b8",
                                "textTransform":"uppercase","marginLeft":"4px"})],
                     style={"marginBottom":"6px"}),
            html.Div(value, style={"fontSize":"20px","fontWeight":"800",
                                   "color":"#f1f5f9","letterSpacing":"-0.5px"}),
        ], style={
            "background":"linear-gradient(135deg,#1e293b,#0f172a)",
            "border":f"1px solid {color}44","borderRadius":"10px",
            "padding":"14px 18px","flex":"1","minWidth":"120px",
            "boxShadow":f"0 2px 12px {color}22",
        }))
    return cards


@app.callback(
    [Output("fused-temp",           "children"),
     Output("fused-humidity",       "children"),
     Output("fused-rain",           "children"),
     Output("fused-wind",           "children"),
     Output("fused-bmkg-desc",      "children"),
     Output("fused-temp-breakdown", "children"),
     Output("fused-humidity-breakdown","children"),
     Output("fused-rain-breakdown", "children"),
     Output("fused-wind-breakdown", "children"),
     Output("fusion-sources-badge", "children"),
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
     Output("val-pressure",         "children"),
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

    pressure = c.get("surface_pressure", 1013.0)
    return (
        f"{st0:.1f}", f"{st6:.1f}", f"{st18:.1f}",
        f"{sm0:.3f}", sm_span(sm0_txt, sm0_clr),
        f"{sm1:.3f}", sm_span(sm1_txt, sm1_clr),
        f"{sm3:.3f}", sm_span(sm3_txt, sm3_clr),
        f"{uv:.1f}",  html.Span(uv_txt, style={"color": uv_clr, "fontWeight": "600"}),
        f"{dew:.1f}", f"{cld}",
        f"{et0:.2f}", f"{wdir:.0f}",
        html.Span(f"({wind_label})", style={"color": "#64748b"}),
        f"{pressure:.0f}",
    )

# ─── CALLBACK: GRAFIK PRAKIRAAN 7 HARI ────────────────────────────────────────
@app.callback(
    Output("chart-openmeteo-daily", "figure"),
    Input("store-bmkg", "data"),
)
def update_forecast_bmkg(bmkg_data):
    """Prakiraan 7 hari dari BMKG saja (sumber resmi, kode 32.01.30.2005)."""
    DARK = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=10),
        margin=dict(l=40, r=20, t=20, b=50),
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.4, font=dict(size=9)),
    )
    fig = go.Figure()
    times, ch, tmax, tmin = [], [], [], []

    try:
        if bmkg_data:
            cuaca_list = bmkg_data.get("cuaca", [])
            # Ambil 1 data per periode (tengah hari)
            seen_dates = set()
            for periode in cuaca_list:
                for item in (periode if isinstance(periode, list) else []):
                    try:
                        dt_str = item.get("local_datetime","")[:10]
                        if dt_str and dt_str not in seen_dates:
                            seen_dates.add(dt_str)
                            times.append(dt_str)
                            tmax.append(float(item.get("t", 28) or 28))
                            tmin.append(float(item.get("t", 24) or 24) - 4)
                            # CH dari cuaca code
                            wc = str(item.get("weather_code","0"))
                            ch_val = 5.0 if wc in ["61","63","80","81"] else                                      2.0 if wc in ["51","53"] else 0.0
                            ch.append(ch_val)
                        if len(times) >= 7:
                            break
                    except Exception:
                        continue
                if len(times) >= 7:
                    break
    except Exception:
        pass

    # Fallback jika BMKG tidak punya data
    if not times:
        om = fetch_openmeteo()
        daily = om.get("daily", {}) if om else {}
        times = daily.get("time", [])[:7]
        ch    = daily.get("precipitation_sum", [0]*7)[:7]
        tmax  = daily.get("temperature_2m_max", [30]*7)[:7]
        tmin  = daily.get("temperature_2m_min", [23]*7)[:7]

    if times:
        fig.add_trace(go.Bar(
            x=times, y=ch,
            name="CH (mm)", marker_color="#38bdf8", opacity=0.85, yaxis="y",
        ))
        fig.add_trace(go.Scatter(
            x=times, y=tmax,
            name="Suhu Maks (°C)", line=dict(color="#ef4444", width=2),
            mode="lines+markers", marker=dict(size=5), yaxis="y2",
        ))
        fig.add_trace(go.Scatter(
            x=times, y=tmin,
            name="Suhu Min (°C)",
            line=dict(color="#3b82f6", width=1.5, dash="dot"),
            mode="lines+markers", marker=dict(size=4), yaxis="y2",
        ))

    fig.update_layout(
        yaxis =dict(title="CH (mm)", showgrid=True, gridcolor="#1e293b"),
        yaxis2=dict(title="Suhu (°C)", overlaying="y", side="right",
                    showgrid=False, range=[15, 40]),
        annotations=[dict(
            text="© Sumber: BMKG – api.bmkg.go.id | Kode: 32.01.30.2005",
            xref="paper", yref="paper", x=0, y=-0.38,
            showarrow=False, font=dict(size=8, color="#475569"),
        )],
        **DARK,
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

# ─── CALLBACK: LOAD BATAS DESA (1 JAM SEKALI) ────────────────────────────────
@app.callback(
    Output("store-map-layers", "data"),
    Input("interval-map", "n_intervals"),
)
def load_map_data(_):
    # Pakai file GeoJSON lokal — tidak fetch API eksternal
    return {
        "loaded_at": datetime.now(WIB).strftime("%H:%M WIB"),
    }

# ─── CALLBACK: RENDER BATAS DESA DI PETA ──────────────────────────────────────
@app.callback(
    Output("layer-batas-desa", "children"),
    Input("btn-layer-desa", "n_clicks"),
)
def render_batas_desa(n_desa):
    """
    Render batas Desa Petir dari file GeoJSON resmi BIG.
    Sumber: © BIG – Batas Wilayah Kelurahan/Desa 10K (2017)
    Kode wilayah: 32.01.30.2005
    """
    show = (n_desa or 0) % 2 == 1
    if not show or not DESA_GEOJSON:
        return []

    children = []
    for feature in DESA_GEOJSON.get("features", []):
        props = feature.get("properties", {})
        nama  = props.get("WADMKD", "Petir")
        kec   = props.get("WADMKC", "Dramaga")
        kab   = props.get("WADMKK", "Bogor")
        luas  = props.get("LUAS",   4.27)
        kode  = props.get("KDEPUM", "32.01.30.2005")
        sumber= props.get("UUPP",   "BIG")

        children.append(
            dl.GeoJSON(
                data=feature,
                id="geojson-desa-petir",
                style={
                    "color":       "#f59e0b",
                    "weight":      3,
                    "fillColor":   "#f59e0b",
                    "fillOpacity": 0.12,
                    "dashArray":   "0",
                },
                children=[
                    dl.Tooltip(html.Div([
                        html.B(f"📍 Desa {nama}",
                               style={"color": "#f59e0b", "fontSize": "13px"}),
                        html.Br(),
                        html.Span(f"Kec. {kec}, Kab. {kab}"),
                        html.Br(),
                        html.Span(f"Luas: {luas:.2f} km²",
                                  style={"fontSize": "11px"}),
                        html.Br(),
                        html.Span(f"Kode: {kode}",
                                  style={"fontSize": "11px", "color": "#94a3b8"}),
                        html.Br(),
                        html.Span(f"© {sumber}",
                                  style={"fontSize": "10px", "color": "#64748b"}),
                    ])),
                    dl.Popup(html.Div([
                        html.H4(f"📍 Desa {nama}",
                                style={"margin": "0 0 8px", "color": "#f59e0b"}),
                        html.Table([
                            html.Tr([html.Td("Kecamatan"), html.Td(f": {kec}")]),
                            html.Tr([html.Td("Kabupaten"), html.Td(f": {kab}")]),
                            html.Tr([html.Td("Provinsi"),  html.Td(": Jawa Barat")]),
                            html.Tr([html.Td("Luas"),      html.Td(f": {luas:.2f} km²")]),
                            html.Tr([html.Td("Kode"),      html.Td(f": {kode}")]),
                        ], style={"fontSize": "12px", "borderSpacing": "4px"}),
                        html.Hr(style={"margin": "8px 0"}),
                        html.Span("© BIG – Batas Wilayah Kel/Desa 10K (2017)",
                                  style={"fontSize": "10px", "color": "#94a3b8"}),
                    ], style={"minWidth": "200px"})),
                ],
            )
        )
    return children

# ─── CALLBACK: RENDER LAYER KEMIRINGAN LERENG ────────────────────────────────
SLOPE_COLORS = {
    1: {"color": "#22c55e", "label": "Datar (0–8%)",        "fill": "#22c55e"},
    2: {"color": "#eab308", "label": "Landai (8–15%)",       "fill": "#eab308"},
    3: {"color": "#f97316", "label": "Agak Curam (15–25%)",  "fill": "#f97316"},
    4: {"color": "#ef4444", "label": "Curam (25–45%)",       "fill": "#ef4444"},
    5: {"color": "#7f1d1d", "label": "Sangat Curam (>45%)",  "fill": "#7f1d1d"},
}

@app.callback(
    [Output("layer-slope",     "children"),
     Output("btn-layer-slope", "style")],
    Input("btn-layer-slope", "n_clicks"),
)
def toggle_slope(n):
    on = (n or 0) % 2 == 1
    style = {
        "background":   "#78716c33" if on else "#1e293b",
        "color":        "#a8a29e"   if on else "#64748b",
        "border":       f"1px solid {'#78716c' if on else '#334155'}",
        "borderRadius": "6px", "padding": "3px 10px",
        "cursor":       "pointer", "fontSize": "11px",
        "fontWeight":   "600" if on else "400",
    }

    if not on or not SLOPE_GEOJSON:
        return [], style

    children = []
    seen_codes = set()

    for feature in SLOPE_GEOJSON.get("features", []):
        props   = feature.get("properties", {})
        code    = props.get("gridcode", 0)
        kelas   = props.get("Kelas_Lere", f"Kelas {code}").title()
        persen  = props.get("Kemiringan", "-")
        source  = props.get("Source", "DEM SRTM 30m")
        cfg     = SLOPE_COLORS.get(code, {"color": "#64748b", "fill": "#64748b",
                                          "label": kelas})

        # Label untuk tooltip
        label_map = {
            1: "🟢 Datar",
            2: "🟡 Landai",
            3: "🟠 Agak Curam",
            4: "🔴 Curam",
            5: "⛔ Sangat Curam",
        }
        icon_label = label_map.get(code, kelas)

        children.append(
            dl.GeoJSON(
                data=feature,
                style={
                    "color":       cfg["color"],
                    "weight":      1,
                    "fillColor":   cfg["fill"],
                    "fillOpacity": 0.45,
                },
                children=[
                    dl.Tooltip(html.Div([
                        html.B(f"{icon_label}",
                               style={"color": cfg["color"], "fontSize": "13px"}),
                        html.Br(),
                        html.Span(f"Kemiringan: {persen}",
                                  style={"fontSize": "11px"}),
                        html.Br(),
                        html.Span(f"Klasifikasi: Puslittanak Bogor (2004)",
                                  style={"fontSize": "10px", "color": "#94a3b8"}),
                        html.Br(),
                        html.Span(f"Sumber: {source}",
                                  style={"fontSize": "10px", "color": "#94a3b8"}),
                    ])),
                    dl.Popup(html.Div([
                        html.H4(f"{icon_label}",
                                style={"margin": "0 0 8px", "color": cfg["color"]}),
                        html.Table([
                            html.Tr([html.Td("Kemiringan"),
                                     html.Td(f": {persen}")]),
                            html.Tr([html.Td("Klasifikasi"),
                                     html.Td(": Puslittanak (2004)")]),
                            html.Tr([html.Td("Sumber DEM"),
                                     html.Td(f": {source}")]),
                            html.Tr([html.Td("Provider"),
                                     html.Td(": © indonesia-geospasial.com")]),
                        ], style={"fontSize": "11px", "borderSpacing": "4px"}),
                    ], style={"minWidth": "200px"})),
                ],
            )
        )
    return children, style

# ─── CALLBACK: TOGGLE LAYER LONGSOR ───────────────────────────────────────────
# ─── DATA ZONA RAWAN BERBASIS BNPB INARISK (KABUPATEN BOGOR) ─────────────────
# Zona risiko berdasarkan data indeks BNPB InaRisk untuk Kec. Dramaga
# Sumber: © BNPB InaRisk (inarisk.bnpb.go.id) – Indeks Risiko Bencana 2023
# Koordinat diperoleh dari analisis peta BNPB wilayah Kec. Dramaga, Kab. Bogor

ZONA_LONGSOR = [
    {"name": "Zona Longsor Tinggi – Lereng Barat Petir",
     "coords": [[-6.598, 106.705], [-6.605, 106.718], [-6.618, 106.712],
                [-6.612, 106.700], [-6.598, 106.705]],
     "risk": "Tinggi", "color": "#dc2626",
     "source": "BNPB InaRisk – Indeks Risiko Tanah Longsor"},
    {"name": "Zona Longsor Tinggi – Perbukitan Selatan",
     "coords": [[-6.625, 106.708], [-6.632, 106.722], [-6.640, 106.715],
                [-6.633, 106.703], [-6.625, 106.708]],
     "risk": "Tinggi", "color": "#dc2626",
     "source": "BNPB InaRisk – Indeks Risiko Tanah Longsor"},
    {"name": "Zona Longsor Sedang – Lereng Timur",
     "coords": [[-6.605, 106.722], [-6.610, 106.732], [-6.620, 106.728],
                [-6.615, 106.718], [-6.605, 106.722]],
     "risk": "Sedang", "color": "#f97316",
     "source": "BNPB InaRisk – Indeks Risiko Tanah Longsor"},
]

ZONA_BANJIR = [
    {"name": "Zona Banjir Tinggi – DAS Cianten",
     "coords": [[-6.608, 106.728], [-6.613, 106.742], [-6.622, 106.738],
                [-6.617, 106.724], [-6.608, 106.728]],
     "risk": "Tinggi", "color": "#1d4ed8",
     "source": "BNPB InaRisk – Indeks Risiko Banjir"},
    {"name": "Zona Banjir Sedang – Lembah Barat Daya",
     "coords": [[-6.618, 106.698], [-6.626, 106.710], [-6.632, 106.704],
                [-6.624, 106.693], [-6.618, 106.698]],
     "risk": "Sedang", "color": "#3b82f6",
     "source": "BNPB InaRisk – Indeks Risiko Banjir"},
]

ZONA_CUACA = [
    {"name": "Zona Cuaca Ekstrim – Dataran Tengah",
     "coords": [[-6.608, 106.710], [-6.614, 106.722], [-6.622, 106.718],
                [-6.616, 106.706], [-6.608, 106.710]],
     "risk": "Sedang", "color": "#7c3aed",
     "source": "BNPB InaRisk – Indeks Risiko Cuaca Ekstrim"},
]

def _make_zone_layer(zones, visible=True):
    """Buat layer GeoJSON dari zona risiko."""
    if not visible:
        return []
    children = []
    for z in zones:
        children.append(
            dl.Polygon(
                positions=z["coords"],
                color=z["color"],
                fillColor=z["color"],
                fillOpacity=0.35,
                weight=2,
                children=[
                    dl.Tooltip(html.Div([
                        html.B(z["name"]),
                        html.Br(),
                        html.Span(f"Risiko: {z['risk']}",
                                  style={"color": z["color"], "fontWeight": "600"}),
                        html.Br(),
                        html.Span(z["source"],
                                  style={"fontSize": "10px", "color": "#94a3b8"}),
                    ])),
                ],
            )
        )
    return children

@app.callback(
    [Output("layer-longsor",     "children"),
     Output("btn-layer-longsor", "style")],
    Input("btn-layer-longsor", "n_clicks"),
)
def toggle_longsor(n):
    on = (n or 0) % 2 == 1
    style = {
        "background":   "#ef444433" if on else "#1e293b",
        "color":        "#ef4444"   if on else "#64748b",
        "border":       f"1px solid {'#ef4444' if on else '#334155'}",
        "borderRadius": "6px", "padding": "3px 10px",
        "cursor":       "pointer", "fontSize": "11px",
        "fontWeight":   "600" if on else "400",
    }
    return _make_zone_layer(ZONA_LONGSOR, on), style

@app.callback(
    [Output("layer-banjir",     "children"),
     Output("btn-layer-banjir", "style")],
    Input("btn-layer-banjir", "n_clicks"),
)
def toggle_banjir(n):
    on = (n or 0) % 2 == 1
    style = {
        "background":   "#3b82f633" if on else "#1e293b",
        "color":        "#3b82f6"   if on else "#64748b",
        "border":       f"1px solid {'#3b82f6' if on else '#334155'}",
        "borderRadius": "6px", "padding": "3px 10px",
        "cursor":       "pointer", "fontSize": "11px",
        "fontWeight":   "600" if on else "400",
    }
    return _make_zone_layer(ZONA_BANJIR, on), style

@app.callback(
    [Output("layer-cuaca-ekstrim",  "children"),
     Output("btn-layer-cuaca",      "style")],
    Input("btn-layer-cuaca", "n_clicks"),
)
def toggle_cuaca(n):
    on = (n or 0) % 2 == 1
    style = {
        "background":   "#8b5cf633" if on else "#1e293b",
        "color":        "#8b5cf6"   if on else "#64748b",
        "border":       f"1px solid {'#8b5cf6' if on else '#334155'}",
        "borderRadius": "6px", "padding": "3px 10px",
        "cursor":       "pointer", "fontSize": "11px",
        "fontWeight":   "600" if on else "400",
    }
    return _make_zone_layer(ZONA_CUACA, on), style


@app.callback(
    Output("store-micromet", "data"),
    Input("interval-micromet", "n_intervals"),
)
def update_micromet_store(_):
    if not MICROMET_AVAILABLE:
        return {"available": False}
    stats = get_micromet_stats()
    return {"available": True, "stats": stats}

# ─── CALLBACK: GRAFIK MIKROMETEOROLOGI ────────────────────────────────────────
@app.callback(
    Output("chart-micromet", "figure"),
    [Input("micromet-param",      "value"),
     Input("micromet-year-range", "value")],
)
def update_micromet_chart(param, year_range):
    DARK = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=11),
        margin=dict(l=50, r=20, t=30, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.3),
    )

    # Lazy load data micromet
    df_micromet = get_micromet_data()

    # Cek data tersedia
    if df_micromet is None or len(df_micromet) == 0:
        fig = go.Figure()
        fig.add_annotation(
            text="⚠️ Data NASA POWER belum tersedia.<br>"
                 "Jalankan: python nasa_power_update.py --historical",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=13, color="#94a3b8"),
        )
        fig.update_layout(**DARK)
        return fig

    # Filter tahun
    df = df_micromet[
        (df_micromet["year"] >= year_range[0]) &
        (df_micromet["year"] <= year_range[1])
    ].copy()

    if len(df) == 0:
        fig = go.Figure()
        fig.add_annotation(
            text="Tidak ada data untuk rentang tahun ini",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(color="#94a3b8"))
        fig.update_layout(**DARK)
        return fig

    # Resample bulanan untuk grafik lebih bersih
    df_m = df.set_index("date").resample("ME").mean(numeric_only=True).reset_index()

    fig = go.Figure()

    if param == "temp":
        if "t2m_max" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["t2m_max"],
                name="Suhu Maks (°C)", line=dict(color="#ef4444", width=1.5)))
        if "t2m" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["t2m"],
                name="Suhu Rata (°C)", line=dict(color="#f97316", width=2)))
        if "t2m_min" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["t2m_min"],
                name="Suhu Min (°C)", line=dict(color="#3b82f6", width=1.5)))
        fig.update_layout(title="Tren Suhu Udara Bulanan (°C)",
                          yaxis=dict(title="°C", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif param == "rh":
        if "rh2m" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["rh2m"],
                name="Kelembaban Udara (%)",
                line=dict(color="#38bdf8", width=2),
                fill="tozeroy", fillcolor="rgba(56,189,248,0.1)"))
        fig.update_layout(title="Tren Kelembaban Udara Bulanan (%)",
                          yaxis=dict(title="%", showgrid=True, gridcolor="#1e293b",
                                     range=[50, 100]),
                          xaxis=dict(showgrid=False), **DARK)

    elif param == "wind":
        if "ws2m" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["ws2m"],
                name="Kecepatan Angin (m/s)",
                line=dict(color="#8b5cf6", width=2)))
        fig.update_layout(title="Tren Kecepatan Angin Bulanan (m/s)",
                          yaxis=dict(title="m/s", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif param == "rad":
        if "radiation" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["radiation"],
                name="Radiasi Matahari (MJ/m²)",
                line=dict(color="#eab308", width=2),
                fill="tozeroy", fillcolor="rgba(234,179,8,0.1)"))
        fig.update_layout(title="Tren Radiasi Matahari Bulanan (MJ/m²/hari)",
                          yaxis=dict(title="W/m²", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif param == "et0":
        if "et0" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["et0"],
                name="Evapotranspirasi (mm/hari)",
                line=dict(color="#10b981", width=2),
                fill="tozeroy", fillcolor="rgba(16,185,129,0.1)"))
        fig.update_layout(title="Tren Evapotranspirasi Bulanan (mm/hari)",
                          yaxis=dict(title="mm/hari", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif param == "pres":
        if "pressure" in df_m.columns:
            fig.add_trace(go.Scatter(x=df_m["date"], y=df_m["pressure"],
                name="Tekanan Udara (kPa)",
                line=dict(color="#06b6d4", width=2)))
        fig.update_layout(title="Tren Tekanan Udara Bulanan (kPa)",
                          yaxis=dict(title="kPa", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), **DARK)

    elif param == "prec":
        # Bandingkan CH NASA POWER vs CHIRPS
        if "prec_nasa" in df_m.columns:
            fig.add_trace(go.Bar(x=df_m["date"], y=df_m["prec_nasa"],
                name="CH NASA POWER (mm)", marker_color="#38bdf8", opacity=0.7))

        # Overlay CHIRPS jika tersedia
        try:
            df_chirps = df_hist[
                (df_hist["year"] >= year_range[0]) &
                (df_hist["year"] <= year_range[1])
            ].set_index("date").resample("ME")["rainfall"].sum().reset_index()
            # Konversi total bulanan ke rata-rata harian
            df_chirps["rainfall_daily"] = df_chirps["rainfall"] / 30
            fig.add_trace(go.Scatter(x=df_chirps["date"], y=df_chirps["rainfall_daily"],
                name="CH CHIRPS (mm/hari rata)", line=dict(color="#f97316", width=2)))
        except Exception:
            pass

        fig.update_layout(title="Perbandingan CH NASA POWER vs CHIRPS (mm/hari)",
                          yaxis=dict(title="mm/hari", showgrid=True, gridcolor="#1e293b"),
                          xaxis=dict(showgrid=False), barmode="overlay", **DARK)

    return fig

# ─── CALLBACK: STATISTIK MICROMET ─────────────────────────────────────────────
@app.callback(
    Output("micromet-stat-cards", "children"),
    Input("store-micromet", "data"),
)
def update_micromet_stats(data):
    df_micromet = get_micromet_data()
    if not MICROMET_AVAILABLE or df_micromet is None or len(df_micromet) == 0:
        return [html.Div(
            "⚠️ Data NASA POWER belum tersedia. Jalankan nasa_power_update.py --historical",
            style={"color": "#f59e0b", "fontSize": "12px", "padding": "10px",
                   "background": "#1e293b", "borderRadius": "8px"}
        )]

    stats_data = [
        ("🌡️ Suhu Rata-rata",  f"{df_micromet['t2m'].mean():.1f}°C",     "#ef4444"),
        ("💧 Kelembaban Rata", f"{df_micromet['rh2m'].mean():.1f}%",      "#38bdf8"),
        ("💨 Angin Rata-rata", f"{df_micromet['ws2m'].mean():.1f} m/s",   "#8b5cf6"),
        ("☀️ Radiasi Rata",    f"{df_micromet['radiation'].mean():.1f} MJ",  "#eab308"),
        ("🌿 ET0 Rata-rata",   f"{df_micromet['et0'].mean():.1f} mm/hr",  "#10b981"),
        ("📅 Rentang Data",
         f"{df_micromet['date'].min().year}–{df_micromet['date'].max().year}",
         "#06b6d4"),
    ]

    cards = []
    for label, value, color in stats_data:
        cards.append(html.Div([
            html.Div(label, style={"fontSize": "11px", "color": "#94a3b8",
                                   "textTransform": "uppercase"}),
            html.Div(value, style={"fontSize": "18px", "fontWeight": "700",
                                   "color": "#f1f5f9", "marginTop": "4px"}),
            html.Div("© NASA POWER", style={"fontSize": "9px", "color": "#475569",
                                            "marginTop": "4px"}),
        ], style={
            "background": "linear-gradient(135deg, #1e293b, #0f172a)",
            "border":      f"1px solid {color}44",
            "borderRadius": "10px",
            "padding":     "14px 18px",
            "flex":        "1",
            "minWidth":    "130px",
            "boxShadow":   f"0 2px 12px {color}22",
        }))
    return cards

# ─── CALLBACK: SUPABASE REALTIME TRIGGER ─────────────────────────────────────

# ─── CALLBACK: UPDATE HEALTH STORE ───────────────────────────────────────────
@app.callback(
    Output("store-health", "data"),
    Input("interval-health", "n_intervals"),
)
def update_health_store(_):
    return check_all_apis()

# ─── CALLBACK: TAMPILKAN HEALTH CHECK PANEL ───────────────────────────────────
@app.callback(
    [Output("health-cards",        "children"),
     Output("health-overall-badge","children"),
     Output("health-checked-at",   "children")],
    Input("store-health", "data"),
)
def update_health_panel(data):
    if not data:
        data = check_all_apis()

    summary = data.get("summary", {})
    results = data.get("results", {})

    # Badge overall
    oc = summary.get("overall_color", "#64748b")
    badge = html.Span(
        summary.get("overall_msg", "-"),
        style={"background": oc + "22", "color": oc,
               "border": f"1px solid {oc}", "borderRadius": "6px",
               "padding": "3px 10px", "fontSize": "11px", "fontWeight": "700"},
    )

    # Konfigurasi tampilan tiap API
    api_config = {
        "openweathermap": {"icon": "🌤️", "label": "OpenWeatherMap",  "desc": "Cuaca real-time"},
        "openmeteo":      {"icon": "🌱", "label": "Open-Meteo",      "desc": "Data tanah & UV"},
        "bmkg_prakiraan": {"icon": "📡", "label": "BMKG Prakiraan",  "desc": "Prakiraan lokal"},
        "bmkg_cap":       {"icon": "⚠️", "label": "BMKG CAP",        "desc": "Peringatan dini"},
        "chirps":         {"icon": "🛰️", "label": "NASA CHIRPS",     "desc": "Data CH harian"},
        "supabase":       {"icon": "🗄️", "label": "Supabase",        "desc": "Database"},
        "telegram":       {"icon": "📨", "label": "Telegram Bot",    "desc": "Notifikasi"},
    }

    STATUS_COLOR = {
        "online":  "#22c55e",
        "offline": "#ef4444",
        "error":   "#f59e0b",
        "unknown": "#64748b",
    }
    STATUS_ICON = {
        "online":  "●",
        "offline": "●",
        "error":   "●",
        "unknown": "○",
    }

    cards = []
    for key, cfg in api_config.items():
        r     = results.get(key, {})
        st    = r.get("status",      "unknown")
        lbl   = r.get("label",       "-")
        ms    = r.get("response_ms", 0)
        color = STATUS_COLOR.get(st, "#64748b")
        dot   = STATUS_ICON.get(st, "○")

        # Warna response time
        ms_color = "#22c55e" if ms < 500 else "#f59e0b" if ms < 1500 else "#ef4444"

        cards.append(html.Div([
            # Header
            html.Div([
                html.Span(cfg["icon"], style={"fontSize": "18px"}),
                html.Span(dot, style={"color": color, "fontSize": "10px",
                                      "marginLeft": "4px", "verticalAlign": "middle"}),
            ], style={"marginBottom": "6px"}),
            # Nama API
            html.Div(cfg["label"],
                     style={"fontSize": "11px", "fontWeight": "700",
                            "color": "#f1f5f9", "marginBottom": "2px"}),
            # Deskripsi
            html.Div(cfg["desc"],
                     style={"fontSize": "10px", "color": "#475569", "marginBottom": "6px"}),
            # Status
            html.Div(lbl[:25],
                     style={"fontSize": "10px", "color": color,
                            "fontWeight": "600", "marginBottom": "4px"}),
            # Response time
            html.Div(
                f"{ms}ms" if ms > 0 else "-",
                style={"fontSize": "10px", "color": ms_color,
                       "fontWeight": "600" if ms > 0 else "400"},
            ),
        ], style={
            "background":  "#0f172a",
            "border":      f"1px solid {color}44",
            "borderTop":   f"3px solid {color}",
            "borderRadius":"10px",
            "padding":     "12px",
            "minWidth":    "110px",
            "flex":        "1",
            "boxShadow":   f"0 2px 8px {color}11",
        }))

    checked = f"🕐 Terakhir dicek: {summary.get('checked_at', '-')} | Update otomatis setiap 5 menit"
    return cards, badge, checked

# ─── CALLBACK: UPDATE CAP STORE ───────────────────────────────────────────────
@app.callback(
    Output("store-cap", "data"),
    Input("interval-cap", "n_intervals"),
)
def update_cap_store(_):
    alerts = fetch_bmkg_cap()
    status = get_cap_status(alerts)
    # Kirim Telegram jika ada peringatan baru
    if status["active"] and BMKG_CAP_AVAILABLE:
        msg = format_cap_telegram(alerts)
        if msg:
            send_telegram(msg)
    return status

# ─── CALLBACK: TAMPILKAN BANNER CAP ───────────────────────────────────────────
@app.callback(
    Output("bmkg-cap-banner", "children"),
    Input("store-cap", "data"),
)
def update_cap_banner(cap_data):
    if not cap_data:
        cap_data = get_cap_status([])

    active  = cap_data.get("active", False)
    level   = cap_data.get("level",   "NORMAL")
    color   = cap_data.get("color",   "#22c55e")
    emoji   = cap_data.get("emoji",   "✅")
    message = cap_data.get("message", "Tidak ada peringatan aktif dari BMKG")
    count   = cap_data.get("count",   0)
    alerts  = cap_data.get("alerts",  [])

    if not active:
        # Banner hijau kecil — tidak ada peringatan
        return html.Div([
            html.Div([
                html.Span("✅ ", style={"fontSize": "14px"}),
                html.Span("Tidak ada peringatan aktif BMKG untuk Kecamatan Dramaga",
                          style={"fontSize": "12px", "color": "#22c55e"}),
                html.Span(" | ", style={"color": "#1e293b", "margin": "0 8px"}),
                html.Span("© Sumber: BMKG – bmkg.go.id",
                          style={"fontSize": "11px", "color": "#475569"}),
            ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap"}),
        ], style={
            "background": "#052e16",
            "border":     "1px solid #22c55e33",
            "borderLeft": "4px solid #22c55e",
            "padding":    "8px 20px",
            "marginBottom": "0",
        })

    # Banner merah/oranye/kuning — ada peringatan aktif
    alert_items = []
    for a in alerts[:3]:  # Tampilkan max 3
        alert_items.append(html.Div([
            html.Span(f"{a['emoji']} ", style={"fontSize": "14px"}),
            html.Span(f"{a['event']}", style={"fontWeight": "700", "color": color}),
            html.Span(f" — {a['headline'][:80]}",
                      style={"fontSize": "12px", "color": "#f1f5f9"}),
            html.Span(f" (Berlaku: {a['expires_str']})",
                      style={"fontSize": "11px", "color": "#94a3b8", "marginLeft": "8px"}),
        ], style={"marginBottom": "4px"}))

    return html.Div([
        html.Div([
            # Judul banner
            html.Div([
                html.Span(f"{emoji} PERINGATAN RESMI BMKG ",
                          style={"fontWeight": "800", "fontSize": "14px",
                                 "color": color, "letterSpacing": "0.05em"}),
                html.Span(f"— {count} Peringatan Aktif untuk Kec. Dramaga",
                          style={"fontSize": "12px", "color": "#f1f5f9"}),
            ], style={"marginBottom": "6px"}),
            # List peringatan
            html.Div(alert_items),
            # Footer copyright
            html.Div([
                html.Span("© Sumber: ", style={"color": "#94a3b8", "fontSize": "10px"}),
                html.A("BMKG – Badan Meteorologi, Klimatologi, dan Geofisika",
                       href="https://www.bmkg.go.id", target="_blank",
                       style={"color": "#f59e0b", "fontSize": "10px",
                              "textDecoration": "none", "fontWeight": "600"}),
                html.Span(" | Data CAP: ", style={"color": "#475569", "fontSize": "10px"}),
                html.A("github.com/infoBMKG/data-cap",
                       href="https://github.com/infoBMKG/data-cap", target="_blank",
                       style={"color": "#64748b", "fontSize": "10px"}),
            ], style={"marginTop": "6px"}),
        ], style={"maxWidth": "1600px", "margin": "0 auto"}),
    ], style={
        "background":   f"linear-gradient(90deg, {color}22 0%, #0f172a 100%)",
        "border":       f"1px solid {color}44",
        "borderLeft":   f"5px solid {color}",
        "borderRadius": "0",
        "padding":      "12px 24px",
        "marginBottom": "0",
        "animation":    "pulse 2s infinite" if level == "AWAS" else "none",
    })

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
    """Handle Telegram commands — data sama dengan yang tampil di dashboard."""
    text = text.strip().lower().split("@")[0]
    if text in ("/start", "/help"):
        _tg_send(chat_id,
            "🌧️ <b>Bot Informasi Cuaca Desa Petir</b>\n\n"
            "/status  – Status cuaca sekarang\n"
            "/cuaca   – Parameter cuaca lengkap\n"
            "/hujan   – Curah hujan hari ini\n"
            "/ekstrem – 5 event hujan terbesar\n"
            "/tren    – Tren 5 tahun terakhir\n"
            "/help    – Tampilkan menu ini"
        )
    elif text == "/status":
        # Gunakan fused data — sama dengan dashboard
        try:
            owm   = fetch_weather()
            meteo = fetch_openmeteo()
            bmkg  = fetch_bmkg()
            fused = fuse_data(owm, meteo, bmkg)
            temp  = fused.get("temp", 27.0)
            hum   = fused.get("humidity", 80.0)
            rain  = fused.get("rain", 0.0)
            wind  = fused.get("wind", 2.0)
            desc  = fused.get("bmkg_desc", "Berawan")
            now   = now_wib().strftime("%d %b %Y %H:%M WIB")
            # Level peringatan berdasarkan CH harian CHIRPS terbaru
            ch_thr = THRESHOLD_RT
            level, emoji = "NORMAL", "🟢"
            if rain >= ch_thr["AWAS"]:    level, emoji = "AWAS",    "🔴"
            elif rain >= ch_thr["SIAGA"]: level, emoji = "SIAGA",   "🟠"
            elif rain >= ch_thr["WASPADA"]: level, emoji = "WASPADA", "🟡"
            _tg_send(chat_id,
                f"{emoji} <b>Status: {level}</b>\n"
                f"📍 {LOCATION_NAME}\n"
                f"🕐 {now}\n━━━━━━━━━━━━━━━━\n"
                f"🌡️ Suhu     : <b>{temp:.1f}°C</b>\n"
                f"💧 Kelembaban: <b>{hum:.0f}%</b>\n"
                f"🌧️ CH       : <b>{rain:.1f} mm</b>\n"
                f"💨 Angin    : <b>{wind:.1f} m/s</b>\n"
                f"🌤️ BMKG     : {desc}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"<i>Fused: BMKG 50% · OWM 30% · Open-Meteo 20%</i>"
            )
        except Exception as e:
            _tg_send(chat_id, f"❌ Gagal mengambil data: {e}")
    elif text == "/cuaca":
        try:
            owm   = fetch_weather()
            meteo = fetch_openmeteo()
            bmkg  = fetch_bmkg()
            fused = fuse_data(owm, meteo, bmkg)
            curr  = meteo.get("current", {}) if meteo else {}
            et0   = (meteo.get("daily",{}).get("et0_fao_evapotranspiration",[4.0]) or [4.0])[0]
            uv    = curr.get("uv_index", 0)
            dew   = curr.get("dew_point_2m", 22.0)
            cld   = curr.get("cloud_cover", 50)
            pres  = curr.get("surface_pressure", 1013)
            _tg_send(chat_id,
                f"🌤 <b>Cuaca Terpadu – Desa Petir</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🌡️ Suhu        : <b>{fused.get('temp',27):.1f}°C</b>\n"
                f"💧 Kelembaban   : <b>{fused.get('humidity',80):.0f}%</b>\n"
                f"🌧️ CH          : <b>{fused.get('rain',0):.1f} mm</b>\n"
                f"💨 Angin       : <b>{fused.get('wind',2):.1f} m/s</b>\n"
                f"🔵 Tekanan     : <b>{pres:.0f} hPa</b>\n"
                f"☀️ UV Index    : <b>{uv:.1f}</b>\n"
                f"🌿 ET₀        : <b>{et0:.2f} mm/hari</b>\n"
                f"💦 Titik Embun : <b>{dew:.1f}°C</b>\n"
                f"☁️ Awan        : <b>{cld}%</b>\n"
                f"📡 BMKG        : {fused.get('bmkg_desc','Berawan')}"
            )
        except Exception as e:
            _tg_send(chat_id, f"❌ Gagal mengambil data: {e}")
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
        rows = [f"⛈️ <b>5 Event Hujan Terbesar ({int(df_hist['year'].min())}–{int(df_hist['year'].max())})</b>", "━━━━━━━━━━━━━━━━"]
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
