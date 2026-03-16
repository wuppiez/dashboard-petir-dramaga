"""
set_webhook.py – Daftarkan webhook Bot Telegram ke Render
Jalankan SEKALI setelah dashboard berhasil deploy di Render.

Cara pakai:
    python set_webhook.py
"""

import os
import requests

# ── ISI BAGIAN INI ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "GANTI_TOKEN_BOT_DISINI")
RENDER_URL         = "https://dashboard-petir-dramaga.onrender.com"  # ganti jika nama berbeda
# ─────────────────────────────────────────────────────────────────────────────

def set_webhook():
    webhook_url = f"{RENDER_URL}/webhook/{TELEGRAM_BOT_TOKEN}"
    api_url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"

    print(f"🔗 Mendaftarkan webhook ke:")
    print(f"   {webhook_url}\n")

    r = requests.get(api_url, params={"url": webhook_url}, timeout=10)
    result = r.json()

    if result.get("ok"):
        print("✅ Webhook berhasil didaftarkan!")
        print(f"   {result.get('description', '')}")
    else:
        print("❌ Gagal mendaftarkan webhook.")
        print(f"   Error: {result}")

def check_webhook():
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
    r       = requests.get(api_url, timeout=10)
    info    = r.json().get("result", {})

    print("\n📡 Info Webhook Saat Ini:")
    print(f"   URL    : {info.get('url', '(kosong)')}")
    print(f"   Status : {'✅ Aktif' if info.get('url') else '❌ Belum terdaftar'}")
    print(f"   Error  : {info.get('last_error_message', 'Tidak ada')}")

if __name__ == "__main__":
    set_webhook()
    check_webhook()
