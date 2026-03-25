"""
nasa_power_update.py
────────────────────
Script otomatis download data mikrometeorologi harian dari NASA POWER
untuk Desa Petir, Dramaga, Bogor dan simpan ke Supabase.

Sumber data:
© NASA POWER Project – Prediction Of Worldwide Energy Resources
  Website : https://power.larc.nasa.gov
  API     : https://power.larc.nasa.gov/api/temporal/daily/point
  Lisensi : NASA Open Data (https://www.nasa.gov/open/data.html)

Cara pakai:
    python nasa_power_update.py              # download kemarin
    python nasa_power_update.py --backfill   # isi data kosong 30 hari
    python nasa_power_update.py --historical # download semua 1981-sekarang
    python nasa_power_update.py --date 2024-01-15  # tanggal tertentu
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timedelta, timezone, date

# ─── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL",     "YOUR_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY","YOUR_SUPABASE_ANON_KEY")
TABLE_NAME   = "micromet_daily"

LAT, LON     = -6.6121, 106.7231
WIB          = timezone(timedelta(hours=7))

# Parameter NASA POWER yang didownload
PARAMETERS = (
    "T2M,T2M_MAX,T2M_MIN,RH2M,WS2M,WD2M,"
    "ALLSKY_SFC_SW_DWN,PS,EVPTRNS,PRECTOTCORR,QV2M,T2MDEW"
)

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# ─── SUPABASE HELPERS ─────────────────────────────────────────────────────────
def sb_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }

def sb_get_existing_dates():
    """Ambil semua tanggal yang sudah ada di tabel micromet_daily."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=date&order=date.desc&limit=3000"
        r   = requests.get(url, headers=sb_headers(), timeout=15)
        if r.status_code == 200:
            return {row["date"] for row in r.json()}
    except Exception as e:
        print(f"  ⚠️  Supabase get dates error: {e}")
    return set()

def sb_insert_batch(rows: list) -> bool:
    """Insert batch data ke Supabase."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
        r   = requests.post(url, headers=sb_headers(),
                            json=rows, timeout=30)
        if r.status_code in (200, 201):
            return True
        print(f"  ❌ Insert error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  ❌ Insert exception: {e}")
        return False

# ─── NASA POWER API ───────────────────────────────────────────────────────────
def fetch_nasa_power(start_date: str, end_date: str) -> dict:
    """
    Download data NASA POWER untuk rentang tanggal tertentu.
    Format tanggal: YYYYMMDD
    Sumber: © NASA POWER (power.larc.nasa.gov)
    """
    params = {
        "parameters": PARAMETERS,
        "community":  "RE",
        "longitude":  LON,
        "latitude":   LAT,
        "start":      start_date,
        "end":        end_date,
        "format":     "JSON",
    }
    try:
        r = requests.get(NASA_POWER_URL, params=params, timeout=60)
        if r.status_code == 200:
            data = r.json()
            return data.get("properties", {}).get("parameter", {})
        print(f"  ❌ NASA POWER HTTP {r.status_code}")
        return {}
    except Exception as e:
        print(f"  ❌ NASA POWER error: {e}")
        return {}

def parse_nasa_to_rows(param_data: dict, existing_dates: set) -> list:
    """
    Parse response NASA POWER ke list of dicts untuk Supabase.
    Format tanggal NASA POWER: YYYYMMDD (string)
    """
    rows = []
    if not param_data:
        return rows

    # Ambil semua tanggal dari parameter pertama
    first_param = list(param_data.values())[0]
    dates = sorted(first_param.keys())

    for date_str in dates:
        # Konversi YYYYMMDD → YYYY-MM-DD
        try:
            d = datetime.strptime(date_str, "%Y%m%d")
            iso_date = d.strftime("%Y-%m-%d")
        except ValueError:
            continue

        if iso_date in existing_dates:
            continue

        def get_val(param, default=None):
            v = param_data.get(param, {}).get(date_str)
            # NASA POWER pakai -999 untuk missing value
            if v is None or v == -999 or v <= -999:
                return default
            return round(float(v), 4)

        row = {
            "date":       iso_date,
            "t2m":        get_val("T2M"),
            "t2m_max":    get_val("T2M_MAX"),
            "t2m_min":    get_val("T2M_MIN"),
            "rh2m":       get_val("RH2M"),
            "ws2m":       get_val("WS2M"),
            "wd2m":       get_val("WD2M"),
            "radiation":  get_val("ALLSKY_SFC_SW_DWN"),
            "pressure":   get_val("PS"),
            "et0":        get_val("EVPTRNS"),
            "prec_nasa":  get_val("PRECTOTCORR"),
            "qv2m":       get_val("QV2M"),
            "t2m_dew":    get_val("T2MDEW"),
            "source":     "NASA_POWER",
        }

      if row["t2m"] is None and row["prec_nasa"] is None:
            continue
        
        rows.append(row)

    return rows

# ─── DOWNLOAD MODES ───────────────────────────────────────────────────────────
def run_daily():
    """Download data 2-3 hari terakhir (NASA POWER delay ~2-3 hari)."""
    print("=" * 60)
    print(f"🛰️  NASA POWER Daily Update")
    print(f"📍  Desa Petir, Dramaga ({LAT}, {LON})")
    print(f"🕐  {datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')}")
    print("=" * 60)

    existing = sb_get_existing_dates()
    now      = datetime.now(WIB)

    # Download 3 hari terakhir (antisipasi delay NASA POWER)
    success = 0
    for days_back in range(3, 0, -1):
        target   = now - timedelta(days=days_back)
        date_str = target.strftime("%Y%m%d")
        iso_date = target.strftime("%Y-%m-%d")

        if iso_date in existing:
            print(f"  ⏭️  {iso_date} sudah ada, skip.")
            continue

        print(f"\n  📥 Download {iso_date}...")
        param_data = fetch_nasa_power(date_str, date_str)
        rows = parse_nasa_to_rows(param_data, existing)

        if rows:
            if sb_insert_batch(rows):
                print(f"  ✅ {iso_date} tersimpan ({len(rows)} baris)")
                success += len(rows)
        else:
            print(f"  ⚠️  {iso_date} belum tersedia di NASA POWER")

    print(f"\n✅ Selesai: {success} baris baru")

def run_backfill(days: int = 30):
    """Isi data yang kosong dalam N hari terakhir."""
    print("=" * 60)
    print(f"🔄  NASA POWER Backfill ({days} hari)")
    print("=" * 60)

    existing = sb_get_existing_dates()
    now      = datetime.now(WIB)

    # Download per batch 1 tahun untuk efisiensi
    end_dt   = now - timedelta(days=2)
    start_dt = now - timedelta(days=days)

    start_str = start_dt.strftime("%Y%m%d")
    end_str   = end_dt.strftime("%Y%m%d")

    print(f"  📥 Download {start_str} s/d {end_str}...")
    param_data = fetch_nasa_power(start_str, end_str)
    rows = parse_nasa_to_rows(param_data, existing)

    if rows:
        # Insert per batch 500
        total, saved = len(rows), 0
        for i in range(0, total, 500):
            batch = rows[i:i+500]
            if sb_insert_batch(batch):
                saved += len(batch)
                print(f"  ✅ Batch {i//500+1}: {len(batch)} baris")
        print(f"\n✅ Total tersimpan: {saved}/{total} baris")
    else:
        print("  ⚠️  Tidak ada data baru")

def run_historical():
    """
    Download semua data historis NASA POWER 1981–sekarang.
    Download per tahun untuk menghindari timeout.
    """
    print("=" * 60)
    print("📚  NASA POWER Historical Download (1981–sekarang)")
    print("⚠️   Proses ini membutuhkan ~15-30 menit")
    print("=" * 60)

    existing  = sb_get_existing_dates()
    now       = datetime.now(WIB)
    end_year  = now.year - 1  # Sampai tahun lalu (tahun ini pakai backfill)
    total_saved = 0

    for year in range(1981, end_year + 1):
        start_str = f"{year}0101"
        end_str   = f"{year}1231"

        # Cek apakah tahun ini sudah ada
        year_dates = {d for d in existing if d.startswith(str(year))}
        if len(year_dates) >= 360:  # Hampir lengkap
            print(f"  ⏭️  Tahun {year} sudah lengkap ({len(year_dates)} hari), skip.")
            continue

        print(f"\n  📥 Download tahun {year}...")
        param_data = fetch_nasa_power(start_str, end_str)
        rows = parse_nasa_to_rows(param_data, existing)

        if rows:
            # Update existing setelah insert
            for row in rows:
                existing.add(row["date"])

            for i in range(0, len(rows), 500):
                batch = rows[i:i+500]
                if sb_insert_batch(batch):
                    total_saved += len(batch)

            print(f"  ✅ Tahun {year}: {len(rows)} hari baru tersimpan")
        else:
            print(f"  ⚠️  Tahun {year}: tidak ada data baru")

        # Jeda antar request agar tidak overload API
        time.sleep(1)

    print(f"\n{'='*60}")
    print(f"✅ Total tersimpan: {total_saved:,} baris")

def run_single_date(date_str: str):
    """Download tanggal tertentu."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print("❌ Format tanggal salah. Gunakan: YYYY-MM-DD")
        sys.exit(1)

    nasa_str = d.strftime("%Y%m%d")
    existing = sb_get_existing_dates()

    print(f"📥 Download {date_str}...")
    param_data = fetch_nasa_power(nasa_str, nasa_str)
    rows = parse_nasa_to_rows(param_data, existing)

    if rows:
        if sb_insert_batch(rows):
            print(f"✅ {date_str} tersimpan ({len(rows)} baris)")
        else:
            print(f"❌ Gagal menyimpan {date_str}")
    else:
        print(f"⚠️  {date_str} tidak tersedia atau sudah ada")

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NASA POWER Mikrometeorologi Updater — Desa Petir")
    parser.add_argument("--daily",      action="store_true",
                        help="Download 3 hari terakhir (default)")
    parser.add_argument("--backfill",   action="store_true",
                        help="Isi data 30 hari terakhir yang kosong")
    parser.add_argument("--historical", action="store_true",
                        help="Download semua data historis 1981-sekarang")
    parser.add_argument("--date",       type=str,
                        help="Download tanggal tertentu (YYYY-MM-DD)")
    parser.add_argument("--days",       type=int, default=30,
                        help="Jumlah hari untuk backfill (default: 30)")
    args = parser.parse_args()

    if args.date:
        run_single_date(args.date)
    elif args.backfill:
        run_backfill(args.days)
    elif args.historical:
        run_historical()
    else:
        run_daily()
