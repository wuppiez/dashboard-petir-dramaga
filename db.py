"""
db.py
─────
Helper untuk membaca data dari Supabase di dashboard.
Di-import oleh app.py sebagai pengganti baca CSV statis.

Kalau Supabase tidak tersedia, otomatis fallback ke CSV lokal.
"""

import os
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL",     "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY","")
TABLE_NAME   = "rainfall_daily"
CSV_FALLBACK = "data/rainfall_historical.csv"
WIB          = timezone(timedelta(hours=7))

def _headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }

def load_from_supabase() -> pd.DataFrame:
    """Ambil semua data curah hujan dari Supabase (dengan pagination)."""
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Supabase credentials tidak ditemukan")

        # Ambil semua data dengan pagination (per 1000 baris)
        all_rows  = []
        page_size = 1000
        offset    = 0

        while True:
            url = (f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
                   f"?select=date,rainfall_mm"
                   f"&order=date.asc"
                   f"&limit={page_size}"
                   f"&offset={offset}")
            r = requests.get(url, headers=_headers(), timeout=15)

            if r.status_code != 200:
                raise Exception(f"HTTP {r.status_code}: {r.text[:100]}")

            rows = r.json()
            if not rows:
                break  # Tidak ada data lagi

            all_rows += rows
            print(f"  📦 Halaman {offset//page_size + 1}: {len(rows)} baris diambil...")

            if len(rows) < page_size:
                break  # Halaman terakhir
            offset += page_size

        if not all_rows:
            raise Exception("Data kosong di Supabase")

        rows = all_rows

        df              = pd.DataFrame(rows)
        df["date"]      = pd.to_datetime(df["date"])
        df              = df.rename(columns={"rainfall_mm": "rainfall"})
        df["month"]     = df["date"].dt.month
        df["year"]      = df["date"].dt.year
        df["doy"]       = df["date"].dt.dayofyear
        df["month_str"] = df["date"].dt.strftime("%b")
        df              = df.sort_values("date").reset_index(drop=True)

        print(f"✅ Data dari Supabase: {len(df):,} baris "
              f"({df['date'].min().strftime('%Y-%m-%d')} s/d "
              f"{df['date'].max().strftime('%Y-%m-%d')})")
        return df

    except Exception as e:
        print(f"⚠️  Supabase tidak tersedia ({e}), pakai CSV lokal...")
        return load_from_csv()

def load_from_csv() -> pd.DataFrame:
    """Fallback: baca dari CSV lokal."""
    df              = pd.read_csv(CSV_FALLBACK, parse_dates=["date"])
    df.columns      = ["date", "rainfall"]
    df              = df.sort_values("date").reset_index(drop=True)
    df["month"]     = df["date"].dt.month
    df["year"]      = df["date"].dt.year
    df["doy"]       = df["date"].dt.dayofyear
    df["month_str"] = df["date"].dt.strftime("%b")
    print(f"✅ Data dari CSV lokal: {len(df):,} baris")
    return df

def load_historical() -> pd.DataFrame:
    """
    Fungsi utama — coba Supabase dulu, fallback ke CSV.
    Dipanggil oleh app.py saat startup.
    """
    return load_from_supabase()

def get_latest_date() -> str:
    """Ambil tanggal data terbaru di Supabase."""
    try:
        url = (f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
               f"?select=date&order=date.desc&limit=1")
        r   = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]["date"]
    except Exception:
        pass
    return None

def get_data_source_info() -> dict:
    """Info sumber data untuk ditampilkan di dashboard."""
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            return {"source": "CSV Lokal", "latest": "Jun 2025", "auto_update": False}

        latest = get_latest_date()
        return {
            "source":      "Supabase + CHIRPS",
            "latest":      latest or "-",
            "auto_update": True,
        }
    except Exception:
        return {"source": "CSV Lokal", "latest": "Jun 2025", "auto_update": False}
