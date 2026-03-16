"""
cron_runner.py
──────────────
Scheduler cron job untuk Render.com.
Menjalankan chirps_update.py setiap hari pukul 08.00 WIB.

Cara deploy di Render:
- Buat service baru di Render → pilih "Cron Job"
- Build Command : pip install -r requirements.txt
- Schedule      : 0 1 * * *  (jam 01.00 UTC = 08.00 WIB)
- Command       : python cron_runner.py
"""

import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

def main():
    print("=" * 55)
    print(f"⏰ Cron Job Dimulai")
    print(f"🕐 Waktu: {datetime.now(WIB).strftime('%d %b %Y %H:%M:%S WIB')}")
    print("=" * 55)

    # Cek environment variables
    required = ["SUPABASE_URL", "SUPABASE_ANON_KEY"]
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ Environment variable belum diset: {', '.join(missing)}")
        sys.exit(1)

    try:
        # Import dan jalankan updater
        from chirps_update import run_daily
        run_daily()
        print(f"\n✅ Cron job selesai: {datetime.now(WIB).strftime('%H:%M:%S WIB')}")
    except Exception as e:
        print(f"\n❌ Cron job error: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
