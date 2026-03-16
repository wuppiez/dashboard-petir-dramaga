"""
chirps_update.py
────────────────
Script otomatis download data CHIRPS harian dan simpan ke Supabase.
Dijadwalkan jalan setiap hari pukul 08.00 WIB via cron job di Render.

Cara pakai manual:
    python chirps_update.py              # download data kemarin
    python chirps_update.py --backfill   # isi data yang kosong (max 30 hari)
    python chirps_update.py --date 2026-03-14  # download tanggal tertentu
"""

import os
import sys
import requests
import argparse
import struct
import gzip
import io
import numpy as np
from datetime import datetime, timedelta, timezone

# ─── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL    = os.getenv("SUPABASE_URL",    "YOUR_SUPABASE_URL")
SUPABASE_KEY    = os.getenv("SUPABASE_ANON_KEY","YOUR_SUPABASE_ANON_KEY")
TABLE_NAME      = "rainfall_daily"

LAT, LON        = -6.6121, 106.7231
LOCATION_NAME   = "Desa Petir, Dramaga, Bogor"

WIB = timezone(timedelta(hours=7))

# CHIRPS base URL
CHIRPS_BASE = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0"
    "/global_daily/tifs/p05/{year}/"
    "chirps-v2.0.{year}.{month:02d}.{day:02d}.tif.gz"
)

# ─── SUPABASE HELPER ───────────────────────────────────────────────────────────
def sb_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }

def sb_get_existing_dates():
    """Ambil semua tanggal yang sudah ada di Supabase."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=date&order=date.desc&limit=60"
        r   = requests.get(url, headers=sb_headers(), timeout=10)
        if r.status_code == 200:
            return {row["date"] for row in r.json()}
    except Exception as e:
        print(f"  ⚠️  Supabase get error: {e}")
    return set()

def sb_insert(date_str, rainfall_mm):
    """Insert satu baris ke Supabase."""
    try:
        url  = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
        data = {"date": date_str, "rainfall_mm": round(float(rainfall_mm), 4)}
        r    = requests.post(url, headers=sb_headers(), json=data, timeout=10)
        if r.status_code in (200, 201):
            return True
        # Kalau sudah ada (conflict), update
        if r.status_code == 409:
            url2 = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?date=eq.{date_str}"
            r2   = requests.patch(url2, headers=sb_headers(), json=data, timeout=10)
            return r2.status_code in (200, 204)
        print(f"  ❌ Insert error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  ❌ Supabase insert exception: {e}")
        return False

def sb_get_all():
    """Ambil semua data dari Supabase untuk dashboard."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=date,rainfall_mm&order=date.asc"
        r   = requests.get(url, headers=sb_headers(), timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ⚠️  Supabase fetch all error: {e}")
    return []

# ─── CHIRPS DOWNLOAD ───────────────────────────────────────────────────────────
def latlon_to_chirps_index(lat, lon):
    """
    Konversi koordinat lat/lon ke index pixel CHIRPS.
    CHIRPS global: -180 to 180 lon, -50 to 50 lat, resolusi 0.05°
    """
    col = int((lon + 180.0) / 0.05)
    row = int((50.0 - lat)  / 0.05)
    return row, col

def download_chirps_tif(date: datetime):
    """
    Download file GeoTIFF CHIRPS untuk tanggal tertentu,
    ekstrak nilai CH untuk koordinat Desa Petir.
    Return: nilai CH dalam mm, atau None jika gagal.
    """
    url = CHIRPS_BASE.format(
        year=date.year, month=date.month, day=date.day
    )
    print(f"  📥 Download: {url}")
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code == 404:
            print(f"  ⚠️  Data belum tersedia untuk {date.strftime('%Y-%m-%d')}")
            return None
        if r.status_code != 200:
            print(f"  ❌ HTTP {r.status_code}")
            return None

        # Decompress gzip
        raw = gzip.decompress(r.content)

        # Parse GeoTIFF minimal (ambil nilai di koordinat target)
        # CHIRPS TIF: little-endian float32, global 0.05 deg
        row, col = latlon_to_chirps_index(LAT, LON)

        # Header GeoTIFF sederhana: cari offset data
        # Gunakan pendekatan langsung baca float32 array
        try:
            import struct
            # Cari TIFF magic
            if raw[:2] in (b'II', b'MM'):
                # Baca sebagai array float32 setelah header
                # CHIRPS global = 7200 x 2000 pixels (0.05 deg, 50S-50N)
                ncols = 7200
                nrows = 2000
                # Offset data (biasanya setelah header ~8KB untuk CHIRPS)
                # Cari IFD untuk offset data yang benar
                val = extract_tiff_value(raw, row, col, nrows, ncols)
                if val is not None and val > -9990:
                    return max(0.0, float(val))
        except Exception as te:
            print(f"  ⚠️  TIF parse error: {te}")

        # Fallback: estimasi dari rata-rata sekitar (jika parse gagal)
        print(f"  ⚠️  Gunakan nilai estimasi")
        return None

    except Exception as e:
        print(f"  ❌ Download error: {e}")
        return None

def extract_tiff_value(raw_bytes, row, col, nrows, ncols):
    """Ekstrak nilai float32 dari GeoTIFF raw bytes."""
    try:
        # Deteksi byte order
        byte_order = '<' if raw_bytes[:2] == b'II' else '>'

        # Baca offset IFD
        ifd_offset = struct.unpack_from(byte_order + 'I', raw_bytes, 4)[0]

        # Baca jumlah entry IFD
        n_entries  = struct.unpack_from(byte_order + 'H', raw_bytes, ifd_offset)[0]

        strip_offsets    = None
        strip_byte_counts = None
        bits_per_sample  = 32
        sample_format    = 3  # float

        for i in range(n_entries):
            entry_offset = ifd_offset + 2 + i * 12
            tag   = struct.unpack_from(byte_order + 'H', raw_bytes, entry_offset)[0]
            dtype = struct.unpack_from(byte_order + 'H', raw_bytes, entry_offset + 2)[0]
            count = struct.unpack_from(byte_order + 'I', raw_bytes, entry_offset + 4)[0]
            value_offset = entry_offset + 8

            if tag == 273:   # StripOffsets
                if count == 1:
                    strip_offsets = [struct.unpack_from(byte_order + 'I', raw_bytes, value_offset)[0]]
                else:
                    off = struct.unpack_from(byte_order + 'I', raw_bytes, value_offset)[0]
                    strip_offsets = list(struct.unpack_from(byte_order + f'{count}I', raw_bytes, off))
            elif tag == 279: # StripByteCounts
                if count == 1:
                    strip_byte_counts = [struct.unpack_from(byte_order + 'I', raw_bytes, value_offset)[0]]
                else:
                    off = struct.unpack_from(byte_order + 'I', raw_bytes, value_offset)[0]
                    strip_byte_counts = list(struct.unpack_from(byte_order + f'{count}I', raw_bytes, off))

        if strip_offsets is None:
            return None

        # Hitung posisi pixel
        pixel_index = row * ncols + col
        bytes_per_pixel = bits_per_sample // 8
        byte_pos = pixel_index * bytes_per_pixel

        # Cari strip yang mengandung pixel ini
        current_offset = 0
        for i, (soff, scount) in enumerate(zip(strip_offsets, strip_byte_counts)):
            strip_pixels = scount // bytes_per_pixel
            if current_offset + strip_pixels > pixel_index:
                local_offset = (pixel_index - current_offset) * bytes_per_pixel
                val = struct.unpack_from(byte_order + 'f', raw_bytes, soff + local_offset)[0]
                return val
            current_offset += strip_pixels

        return None
    except Exception as e:
        return None

# ─── ALTERNATIVE: CHIRPS via CHC API ──────────────────────────────────────────
def download_chirps_api(date: datetime):
    """
    Alternatif: Gunakan CHC data service API yang lebih mudah diparse.
    Mengembalikan nilai CH dalam mm untuk satu titik koordinat.
    """
    try:
        # Climate Engine API (alternatif gratis)
        date_str = date.strftime("%Y-%m-%d")
        url = (
            f"https://climateserv.servirglobal.net/api/submitDataRequest/"
            f"?datatype=0&begintime={date_str}&endtime={date_str}"
            f"&intervaltype=0&operationtype=5"
            f"&geometry=%7B%22type%22%3A%22Point%22%2C%22coordinates%22%3A%5B{LON}%2C{LAT}%5D%7D"
        )
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                val = data[0].get("value", -1)
                if val and val > -9990:
                    return max(0.0, float(val))
    except Exception as e:
        print(f"  ⚠️  CHC API error: {e}")

    # Fallback ke CHIRPS TIF
    return download_chirps_tif(date)

# ─── MAIN LOGIC ────────────────────────────────────────────────────────────────
def process_date(target_date: datetime, existing_dates: set) -> bool:
    """Download dan simpan data untuk satu tanggal."""
    date_str = target_date.strftime("%Y-%m-%d")

    if date_str in existing_dates:
        print(f"  ⏭️  {date_str} sudah ada, skip.")
        return True

    print(f"\n📅 Proses tanggal: {date_str}")
    rainfall = download_chirps_api(target_date)

    if rainfall is None:
        print(f"  ❌ Data tidak tersedia untuk {date_str}")
        return False

    print(f"  💧 CH = {rainfall:.2f} mm")
    ok = sb_insert(date_str, rainfall)
    if ok:
        print(f"  ✅ Tersimpan ke Supabase")
    else:
        print(f"  ❌ Gagal simpan ke Supabase")
    return ok

def run_daily():
    """Mode harian: download data kemarin."""
    print("=" * 55)
    print(f"🌧️  CHIRPS Auto-Update — {datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')}")
    print(f"📍  {LOCATION_NAME}")
    print("=" * 55)

    yesterday = datetime.now(WIB) - timedelta(days=2)  # CHIRPS delay ~2 hari
    existing  = sb_get_existing_dates()
    process_date(yesterday, existing)

def run_backfill(days=30):
    """Mode backfill: isi data yang kosong dalam N hari terakhir."""
    print("=" * 55)
    print(f"🔄  CHIRPS Backfill ({days} hari terakhir)")
    print(f"📍  {LOCATION_NAME}")
    print("=" * 55)

    existing = sb_get_existing_dates()
    now      = datetime.now(WIB)
    success, failed = 0, 0

    for i in range(days, 1, -1):
        target = now - timedelta(days=i)
        ok     = process_date(target, existing)
        if ok: success += 1
        else:  failed  += 1

    print(f"\n{'='*55}")
    print(f"✅ Berhasil: {success} | ❌ Gagal: {failed}")

def run_single_date(date_str):
    """Mode manual: download tanggal tertentu."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=WIB)
    except ValueError:
        print(f"❌ Format tanggal salah. Gunakan: YYYY-MM-DD")
        sys.exit(1)
    existing = sb_get_existing_dates()
    process_date(target, existing)

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CHIRPS Auto-Updater untuk Desa Petir")
    parser.add_argument("--backfill",  action="store_true", help="Isi data kosong 30 hari terakhir")
    parser.add_argument("--date",      type=str,            help="Download tanggal tertentu (YYYY-MM-DD)")
    parser.add_argument("--days",      type=int, default=30,help="Jumlah hari untuk backfill")
    args = parser.parse_args()

    if args.date:
        run_single_date(args.date)
    elif args.backfill:
        run_backfill(args.days)
    else:
        run_daily()
