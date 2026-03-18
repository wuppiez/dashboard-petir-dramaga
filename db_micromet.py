"""
db_micromet.py
──────────────
Helper untuk membaca data mikrometeorologi dari Supabase.
Di-import oleh app.py untuk grafik dan indeks risiko.

Sumber data: © NASA POWER (power.larc.nasa.gov)
"""

import os
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL",     "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY","")
TABLE_NAME   = "micromet_daily"
WIB          = timezone(timedelta(hours=7))

def _headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }

def load_micromet() -> pd.DataFrame:
    """
    Ambil semua data mikrometeorologi dari Supabase dengan pagination.
    Return DataFrame dengan semua parameter harian.
    """
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Supabase credentials tidak ditemukan")

        all_rows  = []
        page_size = 1000
        offset    = 0

        while True:
            url = (
                f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
                f"?select=date,t2m,t2m_max,t2m_min,rh2m,ws2m,wd2m,"
                f"radiation,pressure,et0,prec_nasa,qv2m,t2m_dew"
                f"&order=date.asc"
                f"&limit={page_size}"
                f"&offset={offset}"
            )
            r = requests.get(url, headers=_headers(), timeout=15)
            if r.status_code != 200:
                raise Exception(f"HTTP {r.status_code}: {r.text[:100]}")

            rows = r.json()
            if not rows:
                break

            all_rows += rows
            print(f"  📦 Micromet halaman {offset//page_size+1}: {len(rows)} baris")

            if len(rows) < page_size:
                break
            offset += page_size

        if not all_rows:
            raise Exception("Data micromet kosong")

        df           = pd.DataFrame(all_rows)
        df["date"]   = pd.to_datetime(df["date"])
        df["year"]   = df["date"].dt.year
        df["month"]  = df["date"].dt.month
        df           = df.sort_values("date").reset_index(drop=True)

        print(f"✅ Micromet dari Supabase: {len(df):,} baris "
              f"({df['date'].min().strftime('%Y-%m-%d')} s/d "
              f"{df['date'].max().strftime('%Y-%m-%d')})")
        return df

    except Exception as e:
        print(f"⚠️  Micromet Supabase error ({e}), return DataFrame kosong")
        return pd.DataFrame()

def load_micromet_recent(days: int = 365) -> pd.DataFrame:
    """Ambil data N hari terakhir untuk grafik real-time."""
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            return pd.DataFrame()

        from_date = (datetime.now(WIB) - timedelta(days=days)).strftime("%Y-%m-%d")
        url = (
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
            f"?select=date,t2m,t2m_max,t2m_min,rh2m,ws2m,radiation,et0,prec_nasa"
            f"&order=date.asc"
            f"&date=gte.{from_date}"
        )
        r = requests.get(url, headers=_headers(), timeout=15)
        if r.status_code == 200:
            df = pd.DataFrame(r.json())
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception as e:
        print(f"⚠️  Micromet recent error: {e}")
    return pd.DataFrame()

def get_micromet_stats() -> dict:
    """Statistik ringkasan data mikrometeorologi."""
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
            f"?select=count&limit=1"
        )
        h = {**_headers(), "Prefer": "count=exact"}
        r = requests.get(url, headers=h, timeout=10)
        count = int(r.headers.get("content-range", "0/0").split("/")[-1])

        # Data terbaru
        url2 = (
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
            f"?select=date,t2m,rh2m,ws2m&order=date.desc&limit=1"
        )
        r2 = requests.get(url2, headers=_headers(), timeout=10)
        latest = r2.json()[0] if r2.status_code == 200 and r2.json() else {}

        # Data tertua
        url3 = (
            f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
            f"?select=date&order=date.asc&limit=1"
        )
        r3   = requests.get(url3, headers=_headers(), timeout=10)
        oldest = r3.json()[0] if r3.status_code == 200 and r3.json() else {}

        return {
            "total":      count,
            "latest":     latest.get("date", "-"),
            "oldest":     oldest.get("date", "-"),
            "latest_t2m": latest.get("t2m"),
            "latest_rh":  latest.get("rh2m"),
            "latest_ws":  latest.get("ws2m"),
            "source":     "© NASA POWER (power.larc.nasa.gov)",
        }
    except Exception as e:
        print(f"⚠️  Micromet stats error: {e}")
        return {"total": 0, "source": "NASA POWER"}
