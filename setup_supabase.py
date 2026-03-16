"""
setup_supabase.py
─────────────────
Script untuk:
1. Buat tabel di Supabase
2. Import data historis dari CSV ke Supabase
3. Verifikasi data

Jalankan SEKALI saat setup awal.

Cara pakai:
    python setup_supabase.py --create-table   # buat tabel
    python setup_supabase.py --import-csv     # import CSV historis
    python setup_supabase.py --verify         # cek data
    python setup_supabase.py --all            # lakukan semua
"""

import os
import sys
import json
import requests
import argparse
import pandas as pd
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL",     "YOUR_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY","YOUR_SUPABASE_ANON_KEY")
TABLE_NAME   = "rainfall_daily"
CSV_FILE     = "data/rainfall_historical.csv"

# ─── HEADERS ───────────────────────────────────────────────────────────────────
def headers(prefer="return=representation"):
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        prefer,
    }

# ─── 1. BUAT TABEL ─────────────────────────────────────────────────────────────
def create_table():
    """
    Buat tabel rainfall_daily di Supabase.
    Catatan: Eksekusi SQL via Supabase REST (butuh service_role key)
    atau buat manual via Supabase Dashboard.
    """
    print("\n📋 LANGKAH: Buat Tabel di Supabase")
    print("─" * 50)
    print("Karena Supabase tidak izinkan CREATE TABLE via REST API,")
    print("buat tabel secara manual di Supabase Dashboard:\n")
    print("1. Buka: app.supabase.com → project Anda")
    print("2. Klik menu 'SQL Editor' di sidebar kiri")
    print("3. Klik 'New Query'")
    print("4. Salin dan jalankan SQL berikut:\n")
    print("─" * 50)
    sql = """
-- Buat tabel utama curah hujan harian
CREATE TABLE IF NOT EXISTS rainfall_daily (
    id          BIGSERIAL PRIMARY KEY,
    date        DATE NOT NULL UNIQUE,
    rainfall_mm FLOAT NOT NULL DEFAULT 0,
    source      VARCHAR(20) DEFAULT 'CHIRPS',
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index untuk query cepat berdasarkan tanggal
CREATE INDEX IF NOT EXISTS idx_rainfall_date
    ON rainfall_daily(date);

-- Index untuk query per tahun/bulan
CREATE INDEX IF NOT EXISTS idx_rainfall_year
    ON rainfall_daily(EXTRACT(YEAR FROM date));

-- Aktifkan Row Level Security (RLS)
ALTER TABLE rainfall_daily ENABLE ROW LEVEL SECURITY;

-- Izinkan semua orang baca data (untuk dashboard publik)
CREATE POLICY "Allow public read"
    ON rainfall_daily FOR SELECT
    USING (true);

-- Izinkan insert/update dengan API key
CREATE POLICY "Allow anon insert"
    ON rainfall_daily FOR INSERT
    WITH CHECK (true);

CREATE POLICY "Allow anon update"
    ON rainfall_daily FOR UPDATE
    USING (true);
"""
    print(sql)
    print("─" * 50)
    print("5. Klik tombol 'Run' (▶️)")
    print("6. Pastikan muncul pesan 'Success'")
    print("\n✅ Tabel siap digunakan!")

# ─── 2. IMPORT CSV ─────────────────────────────────────────────────────────────
def import_csv(batch_size=500):
    """Import data historis dari CSV ke Supabase dalam batch."""
    print("\n📥 LANGKAH: Import Data Historis CSV → Supabase")
    print("─" * 50)

    # Baca CSV
    if not os.path.exists(CSV_FILE):
        print(f"❌ File tidak ditemukan: {CSV_FILE}")
        sys.exit(1)

    df = pd.read_csv(CSV_FILE, parse_dates=["date"])
    df.columns = ["date", "rainfall"]
    df = df.sort_values("date").reset_index(drop=True)
    df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")

    total = len(df)
    print(f"📊 Total data: {total:,} baris")
    print(f"📅 Periode  : {df['date_str'].min()} s/d {df['date_str'].max()}")
    print(f"🔢 Batch    : {batch_size} baris per request\n")

    # Import dalam batch
    url       = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
    success   = 0
    failed    = 0
    n_batches = (total + batch_size - 1) // batch_size

    for i in range(n_batches):
        start = i * batch_size
        end   = min(start + batch_size, total)
        batch = df.iloc[start:end]

        rows = [
            {
                "date":        row["date_str"],
                "rainfall_mm": round(float(row["rainfall"]), 4),
                "source":      "CHIRPS_HISTORICAL",
            }
            for _, row in batch.iterrows()
        ]

        try:
            h = headers("resolution=merge-duplicates")
            r = requests.post(url, headers=h, json=rows, timeout=30)
            if r.status_code in (200, 201):
                success += len(rows)
                print(f"  ✅ Batch {i+1}/{n_batches}: {start+1}–{end} ({len(rows)} baris)")
            else:
                failed += len(rows)
                print(f"  ❌ Batch {i+1}/{n_batches} error {r.status_code}: {r.text[:100]}")
        except Exception as e:
            failed += len(rows)
            print(f"  ❌ Batch {i+1}/{n_batches} exception: {e}")

    print(f"\n{'─'*50}")
    print(f"✅ Berhasil : {success:,} baris")
    print(f"❌ Gagal    : {failed:,} baris")
    if failed == 0:
        print("🎉 Semua data berhasil diimport!")

# ─── 3. VERIFIKASI ─────────────────────────────────────────────────────────────
def verify():
    """Cek jumlah data dan statistik di Supabase."""
    print("\n🔍 LANGKAH: Verifikasi Data di Supabase")
    print("─" * 50)

    try:
        # Hitung total
        url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=count"
        h   = {**headers(), "Prefer": "count=exact"}
        r   = requests.get(url, headers=h, timeout=10)

        if r.status_code == 200:
            count = int(r.headers.get("content-range", "0/0").split("/")[-1])
            print(f"📊 Total baris  : {count:,}")

        # Ambil data terbaru
        url2 = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=date,rainfall_mm&order=date.desc&limit=5"
        r2   = requests.get(url2, headers=headers(), timeout=10)
        if r2.status_code == 200:
            rows = r2.json()
            print(f"\n📅 5 Data Terbaru:")
            for row in rows:
                print(f"   {row['date']} → {row['rainfall_mm']:.2f} mm")

        # Ambil data tertua
        url3 = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=date,rainfall_mm&order=date.asc&limit=3"
        r3   = requests.get(url3, headers=headers(), timeout=10)
        if r3.status_code == 200:
            rows3 = r3.json()
            print(f"\n📅 3 Data Tertua:")
            for row in rows3:
                print(f"   {row['date']} → {row['rainfall_mm']:.2f} mm")

        print(f"\n✅ Koneksi ke Supabase: OK")
        print(f"✅ Data siap digunakan di dashboard!")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("Pastikan SUPABASE_URL dan SUPABASE_ANON_KEY sudah benar")

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup Supabase untuk Dashboard Petir")
    parser.add_argument("--create-table", action="store_true", help="Tampilkan SQL untuk buat tabel")
    parser.add_argument("--import-csv",   action="store_true", help="Import CSV historis ke Supabase")
    parser.add_argument("--verify",       action="store_true", help="Verifikasi data di Supabase")
    parser.add_argument("--all",          action="store_true", help="Jalankan semua langkah")
    parser.add_argument("--batch",        type=int, default=500, help="Ukuran batch import (default: 500)")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(0)

    print("=" * 55)
    print("🗄️  Setup Supabase — Dashboard Petir Dramaga")
    print("=" * 55)

    if args.create_table or args.all:
        create_table()
    if args.import_csv or args.all:
        import_csv(args.batch)
    if args.verify or args.all:
        verify()
