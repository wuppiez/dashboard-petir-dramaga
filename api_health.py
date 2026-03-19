"""
api_health.py
─────────────
Monitor status kesehatan semua API yang digunakan dashboard.
Cek apakah setiap API aktif, berapa response time-nya, dan
kapan terakhir berhasil.

API yang dipantau:
  1. OpenWeatherMap   – Data cuaca real-time
  2. Open-Meteo       – Data tanah & lingkungan
  3. BMKG Prakiraan   – Prakiraan cuaca lokal
  4. BMKG CAP         – Peringatan dini bencana
  5. NASA CHIRPS      – Data curah hujan historis
  6. Supabase         – Database penyimpanan
  7. Telegram Bot     – Notifikasi
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

# ─── CONFIG ────────────────────────────────────────────────────────────────────
LAT, LON = -6.6121, 106.7231
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN",  "")
SUPABASE_URL        = os.getenv("SUPABASE_URL",         "")
SUPABASE_KEY        = os.getenv("SUPABASE_ANON_KEY",    "")

# ─── HELPER ────────────────────────────────────────────────────────────────────
def _check(name, url, headers=None, timeout=8, method="GET",
           expected_status=200, label_ok="Online", label_fail="Offline"):
    """Cek satu endpoint — return dict status."""
    start = time.time()
    try:
        r = requests.request(method, url, headers=headers,
                             timeout=timeout, allow_redirects=True)
        elapsed = round((time.time() - start) * 1000)  # ms
        ok      = r.status_code == expected_status

        return {
            "name":        name,
            "status":      "online" if ok else "error",
            "label":       label_ok if ok else f"HTTP {r.status_code}",
            "response_ms": elapsed,
            "checked_at":  datetime.now(WIB).strftime("%H:%M:%S WIB"),
            "status_code": r.status_code,
            "error":       None,
        }
    except requests.Timeout:
        return _err(name, "Timeout", time.time() - start)
    except requests.ConnectionError:
        return _err(name, "Connection Error", time.time() - start)
    except Exception as e:
        return _err(name, str(e)[:50], time.time() - start)

def _err(name, msg, elapsed=0):
    return {
        "name":        name,
        "status":      "offline",
        "label":       msg,
        "response_ms": round(elapsed * 1000),
        "checked_at":  datetime.now(WIB).strftime("%H:%M:%S WIB"),
        "status_code": None,
        "error":       msg,
    }

def _skip(name, reason):
    return {
        "name":        name,
        "status":      "unknown",
        "label":       f"Tidak dikonfigurasi ({reason})",
        "response_ms": 0,
        "checked_at":  datetime.now(WIB).strftime("%H:%M:%S WIB"),
        "status_code": None,
        "error":       None,
    }

# ─── CEK TIAP API ──────────────────────────────────────────────────────────────
def check_openweathermap():
    if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == "YOUR_OPENWEATHER_API_KEY":
        return _skip("OpenWeatherMap", "API Key belum diset")
    url = (f"https://api.openweathermap.org/data/2.5/weather"
           f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_API_KEY}&units=metric")
    return _check("OpenWeatherMap", url)

def check_openmeteo():
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={LAT}&longitude={LON}&current=temperature_2m")
    return _check("Open-Meteo", url)

def check_bmkg_prakiraan():
    url = "https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=32.01.30.2005"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DashboardPetir/1.0)",
        "Accept":     "application/json",
        "Referer":    "https://data.bmkg.go.id/",
    }
    return _check("BMKG Prakiraan", url, headers=headers)

def check_bmkg_cap():
    url = ("https://raw.githubusercontent.com/infoBMKG/data-cap/main"
           "/32/3201/320121.xml")
    r = _check("BMKG CAP", url, expected_status=200)
    # 404 = tidak ada peringatan aktif (normal, bukan error)
    if r["status_code"] == 404:
        r["status"] = "online"
        r["label"]  = "Online (Tidak ada peringatan aktif)"
    return r

def check_chirps():
    # Cek data CHIRPS via Supabase (data sudah tersimpan di DB lokal)
    if not SUPABASE_URL or SUPABASE_URL == "YOUR_SUPABASE_URL":
        return _skip("NASA CHIRPS", "Supabase URL belum diset")
    url = (f"{SUPABASE_URL}/rest/v1/rainfall_daily"
           f"?select=date,rainfall_mm&order=date.desc&limit=1")
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    r = _check("NASA CHIRPS", url, timeout=10, headers=headers)
    r["desc"] = "Data CH harian"
    return r

def check_supabase():
    if not SUPABASE_URL or SUPABASE_URL == "YOUR_SUPABASE_URL":
        return _skip("Supabase", "URL belum diset")
    url = f"{SUPABASE_URL}/rest/v1/rainfall_daily?select=count&limit=1"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    return _check("Supabase", url, headers=headers)

def check_telegram():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return _skip("Telegram Bot", "Token belum diset")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    return _check("Telegram Bot", url)

# ─── CEK SEMUA SEKALIGUS ───────────────────────────────────────────────────────
def check_all_apis() -> dict:
    """
    Cek semua API sekaligus dan return ringkasan status.
    Dijalankan secara berurutan (bukan parallel) agar tidak overload.
    """
    results = {
        "openweathermap": check_openweathermap(),
        "openmeteo":      check_openmeteo(),
        "bmkg_prakiraan": check_bmkg_prakiraan(),
        "bmkg_cap":       check_bmkg_cap(),
        "chirps":         check_chirps(),
        "supabase":       check_supabase(),
        "telegram":       check_telegram(),
    }

    # Hitung ringkasan
    total   = len(results)
    online  = sum(1 for r in results.values() if r["status"] == "online")
    offline = sum(1 for r in results.values() if r["status"] == "offline")
    error   = sum(1 for r in results.values() if r["status"] == "error")
    unknown = sum(1 for r in results.values() if r["status"] == "unknown")

    # Overall status
    if offline + error == 0:
        overall = "all_online"
        overall_color = "#22c55e"
        overall_msg   = f"Semua {online} API online ✅"
    elif online == 0:
        overall = "all_offline"
        overall_color = "#ef4444"
        overall_msg   = "Semua API tidak dapat diakses ❌"
    else:
        overall = "partial"
        overall_color = "#f59e0b"
        overall_msg   = f"{online} Online · {offline+error} Bermasalah ⚠️"

    return {
        "results":       results,
        "summary": {
            "total":         total,
            "online":        online,
            "offline":       offline,
            "error":         error,
            "unknown":       unknown,
            "overall":       overall,
            "overall_color": overall_color,
            "overall_msg":   overall_msg,
            "checked_at":    datetime.now(WIB).strftime("%d %b %Y %H:%M:%S WIB"),
        }
    }

# ─── TEST MANUAL ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("🔍 API Health Check — Dashboard Petir Dramaga")
    print("=" * 55)

    results = check_all_apis()
    summary = results["summary"]

    STATUS_ICON = {
        "online":  "✅",
        "offline": "❌",
        "error":   "⚠️",
        "unknown": "⬜",
    }

    for key, r in results["results"].items():
        icon = STATUS_ICON.get(r["status"], "❓")
        ms   = f"{r['response_ms']}ms" if r["response_ms"] > 0 else "-"
        print(f"  {icon} {r['name']:<20} {r['label']:<35} {ms}")

    print(f"\n{'─'*55}")
    print(f"  {summary['overall_msg']}")
    print(f"  Dicek: {summary['checked_at']}")
