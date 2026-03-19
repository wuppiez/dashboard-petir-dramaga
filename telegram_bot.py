"""
telegram_bot.py – Webhook handler Bot Telegram
Jalankan terpisah: python telegram_bot.py
Atau integrasikan dengan Flask/FastAPI di server yang sama.

Perintah yang didukung:
  /status      – Status cuaca terkini
  /cuaca       – Info cuaca lengkap
  /hujan       – Curah hujan hari ini
  /ekstrem     – 5 hari hujan terbanyak (historis)
  /help        – Daftar perintah
"""

import os
import requests
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_OPENWEATHER_API_KEY")
LAT, LON = -6.6121, 106.7231

# Load historical data
df_hist = pd.read_csv("data/rainfall_historical.csv", parse_dates=["date"])

flask_app = Flask(__name__)

# ── Telegram helper ──────────────────────────────────────────────────────────
def send_message(chat_id, text):
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    requests.post(url, data=data, timeout=10)

def get_weather():
    try:
        url = (f"https://api.openweathermap.org/data/2.5/weather"
               f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_API_KEY}&units=metric&lang=id")
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# ── Command handlers ─────────────────────────────────────────────────────────
def cmd_help(chat_id):
    msg = (
        "🌧️ <b>Bot Informasi Cuaca Desa Petir</b>\n\n"
        "Perintah yang tersedia:\n"
        "/status  – Status cuaca singkat\n"
        "/cuaca   – Info cuaca lengkap\n"
        "/hujan   – Curah hujan hari ini\n"
        "/ekstrem – 5 event hujan terbesar\n"
        "/tren    – Tren tahunan ringkasan\n"
        "/help    – Tampilkan menu ini"
    )
    send_message(chat_id, msg)

def cmd_status(chat_id):
    w = get_weather()
    if not w:
        send_message(chat_id, "❌ Gagal mengambil data cuaca.")
        return
    temp  = w["main"]["temp"]
    hum   = w["main"]["humidity"]
    desc  = w["weather"][0]["description"].capitalize()
    rain  = w.get("rain", {}).get("1h", 0)
    now   = datetime.now().strftime("%d %b %Y %H:%M WIB")

    status_emoji = "🟢"
    status_text  = "NORMAL"
    if rain >= 150:   status_emoji, status_text = "🔴", "AWAS"
    elif rain >= 100: status_emoji, status_text = "🟠", "SIAGA"
    elif rain >= 50:  status_emoji, status_text = "🟡", "WASPADA"

    msg = (
        f"{status_emoji} <b>Status: {status_text}</b>\n"
        f"📍 Desa Petir, Dramaga\n"
        f"🕐 {now}\n"
        f"🌡️ Suhu: {temp:.1f}°C | Kelembapan: {hum}%\n"
        f"🌤️ Kondisi: {desc}\n"
        f"🌧️ CH: <b>{rain:.1f} mm/jam</b>"
    )
    send_message(chat_id, msg)

def cmd_cuaca(chat_id):
    w = get_weather()
    if not w:
        send_message(chat_id, "❌ Gagal mengambil data cuaca.")
        return
    msg = (
        f"🌤 <b>Cuaca Lengkap – Desa Petir</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🌡️ Suhu        : {w['main']['temp']:.1f}°C\n"
        f"🤔 Terasa      : {w['main']['feels_like']:.1f}°C\n"
        f"💧 Kelembapan  : {w['main']['humidity']}%\n"
        f"🌬️ Angin       : {w['wind']['speed']:.1f} m/s\n"
        f"🔵 Tekanan     : {w['main']['pressure']} hPa\n"
        f"👁️ Visibilitas : {w.get('visibility',10000)/1000:.1f} km\n"
        f"🌧️ CH 1 jam    : {w.get('rain',{}).get('1h',0):.1f} mm\n"
        f"🌤️ Kondisi     : {w['weather'][0]['description'].capitalize()}"
    )
    send_message(chat_id, msg)

def cmd_hujan(chat_id):
    today = datetime.now().date()
    today_data = df_hist[df_hist["date"].dt.date == today]
    if today_data.empty:
        last = df_hist.iloc[-1]
        msg = (f"📅 Data hari ini belum tersedia.\n"
               f"Data terakhir ({last['date'].strftime('%d %b %Y')}): "
               f"{last['rainfall']:.1f} mm")
    else:
        val = today_data["rainfall"].sum()
        msg = f"🌧️ Curah hujan hari ini: <b>{val:.1f} mm</b>"
    send_message(chat_id, msg)

def cmd_ekstrem(chat_id):
    top5 = df_hist.nlargest(5, "rainfall")[["date", "rainfall"]]
    rows = ["⛈️ <b>5 Event Hujan Terbesar (2005–2025)</b>", "━━━━━━━━━━━━━━━━"]
    for i, row in enumerate(top5.itertuples(), 1):
        rows.append(f"{i}. {row.date.strftime('%d %b %Y')} – <b>{row.rainfall:.1f} mm</b>")
    send_message(chat_id, "\n".join(rows))

def cmd_tren(chat_id):
    annual = df_hist.groupby(df_hist["date"].dt.year)["rainfall"].agg(["sum", "max", "mean"])
    rows = ["📊 <b>Tren CH Tahunan (5 tahun terakhir)</b>", "━━━━━━━━━━━━━━━━"]
    for yr, row in annual.tail(5).iterrows():
        rows.append(f"📅 {yr} | Total: {row['sum']:.0f}mm | Maks: {row['max']:.0f}mm | Avg: {row['mean']:.1f}mm")
    send_message(chat_id, "\n".join(rows))

# ── Webhook route ────────────────────────────────────────────────────────────
@flask_app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"ok": True})

    msg_obj = data["message"]
    chat_id  = msg_obj["chat"]["id"]
    text     = msg_obj.get("text", "").strip().lower()

    dispatch = {
        "/start":  cmd_help,
        "/help":   cmd_help,
        "/status": cmd_status,
        "/cuaca":  cmd_cuaca,
        "/hujan":  cmd_hujan,
        "/ekstrem":cmd_ekstrem,
        "/tren":   cmd_tren,
    }
    handler = dispatch.get(text.split("@")[0])
    if handler:
        handler(chat_id)
    else:
        send_message(chat_id, "❓ Perintah tidak dikenali. Ketik /help untuk daftar perintah.")
    return jsonify({"ok": True})

@flask_app.route("/set_webhook")
def set_webhook():
    """Panggil sekali untuk mendaftarkan webhook ke Telegram."""
    server_url = os.getenv("SERVER_URL", "https://yourdomain.com")
    url = (f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
           f"?url={server_url}/webhook/{TELEGRAM_BOT_TOKEN}")
    r = requests.get(url)
    return jsonify(r.json())

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=5000, debug=True)
