"""
map_layers.py
─────────────
Modul peta terintegrasi untuk Dashboard Hidrometeorologi Desa Petir.

Sumber data resmi:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. BNPB InaRisk
   © Badan Nasional Penanggulangan Bencana (BNPB)
   Portal  : https://inarisk.bnpb.go.id
   GeoServer: https://gis.bnpb.go.id/server/rest/services/inarisk/

2. BIG – Badan Informasi Geospasial
   © Badan Informasi Geospasial (BIG)
   Portal  : https://geoservices.big.go.id
   Data    : Batas Wilayah Kelurahan/Desa Skala 1:10.000

3. OpenStreetMap
   © OpenStreetMap Contributors (openstreetmap.org/copyright)
   Lisensi : ODbL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

# ─── KOORDINAT DESA PETIR ─────────────────────────────────────────────────────
LAT, LON   = -6.6121, 106.7231
DESA_NAME  = "Desa Petir"
KEC_NAME   = "Kecamatan Dramaga"
KAB_NAME   = "Kabupaten Bogor"

# ─── URL SUMBER DATA ──────────────────────────────────────────────────────────

# BNPB InaRisk GeoServer — Layer risiko bencana
BNPB_BASE  = "https://gis.bnpb.go.id/server/rest/services/inarisk"
BNPB_LAYERS = {
    "longsor": f"{BNPB_BASE}/INDEKS_RISIKO_TANAH_LONGSOR/MapServer",
    "banjir":  f"{BNPB_BASE}/INDEKS_RISIKO_BANJIR/MapServer",
    "cuaca":   f"{BNPB_BASE}/INDEKS_RISIKO_CUACA_EKSTRIM/MapServer",
}

# BIG GeoServices — Batas Desa
BIG_DESA_URL = (
    "https://geoservices.big.go.id/rbi/rest/services/BATASWILAYAH"
    "/Administrasi_AR_KelDesa_10K/MapServer/0/query"
)

# ─── FETCH BATAS DESA DARI BIG ────────────────────────────────────────────────
def fetch_batas_desa_petir():
    """
    Ambil polygon batas Desa Petir dari BIG GeoServices.
    Sumber: © Badan Informasi Geospasial (geoservices.big.go.id)
    Return: GeoJSON dict atau None jika gagal.
    """
    try:
        params = {
            "where":       "WADMKD LIKE '%PETIR%' AND WIADKC LIKE '%DRAMAGA%'",
            "outFields":   "WADMKD,WADMKK,WADMPR,WIADKC,LUASWH",
            "f":           "geojson",
            "outSR":       "4326",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DashboardPetir/1.0)",
            "Accept":     "application/json",
        }
        r = requests.get(BIG_DESA_URL, params=params,
                         headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            features = data.get("features", [])
            if features:
                print(f"✅ BIG: Batas Desa Petir ditemukan "
                      f"({len(features)} feature)")
                return data
            print("⚠️  BIG: Tidak ada data untuk Desa Petir")
    except Exception as e:
        print(f"⚠️  BIG fetch error: {e}")
    return None

def fetch_batas_kecamatan_dramaga():
    """
    Ambil polygon batas Kecamatan Dramaga dari BIG.
    Digunakan sebagai konteks sekitar Desa Petir.
    """
    try:
        params = {
            "where":       "WIADKC LIKE '%DRAMAGA%' AND WADMKK LIKE '%BOGOR%'",
            "outFields":   "WADMKD,WIADKC,WADMKK",
            "f":           "geojson",
            "outSR":       "4326",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DashboardPetir/1.0)",
            "Accept":     "application/json",
        }
        r = requests.get(BIG_DESA_URL, params=params,
                         headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            features = data.get("features", [])
            if features:
                print(f"✅ BIG: Kec. Dramaga ditemukan ({len(features)} desa)")
                return data
    except Exception as e:
        print(f"⚠️  BIG kecamatan fetch error: {e}")
    return None

# ─── FETCH INARISK BNPB ───────────────────────────────────────────────────────
def fetch_inarisk_layer(layer_name: str):
    """
    Ambil data InaRisk BNPB sebagai WMS tile URL untuk Leaflet.
    Layer tersedia: longsor, banjir, cuaca
    Sumber: © BNPB (inarisk.bnpb.go.id)
    """
    base_url = BNPB_LAYERS.get(layer_name)
    if not base_url:
        return None

    # Return WMS tile URL untuk dipakai langsung di dash-leaflet
    tile_url = (
        f"{base_url}/tile/{{z}}/{{y}}/{{x}}"
    )
    return {
        "name":       layer_name,
        "tile_url":   tile_url,
        "attribution": "© BNPB InaRisk (inarisk.bnpb.go.id)",
        "opacity":    0.6,
    }

def fetch_inarisk_indeks(lat: float, lon: float):
    """
    Ambil indeks risiko bencana untuk titik koordinat tertentu.
    Mengembalikan nilai indeks untuk longsor, banjir, dan cuaca ekstrim.
    """
    results = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DashboardPetir/1.0)",
        "Accept":     "application/json",
    }

    for bencana, base_url in BNPB_LAYERS.items():
        try:
            # Query titik koordinat Desa Petir
            query_url = f"{base_url}/0/query"
            params = {
                "geometry":     f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "spatialRel":   "esriSpatialRelIntersects",
                "outFields":    "*",
                "f":            "json",
            }
            r = requests.get(query_url, params=params,
                             headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                features = data.get("features", [])
                if features:
                    attrs = features[0].get("attributes", {})
                    # Cari field indeks risiko
                    indeks = None
                    for key, val in attrs.items():
                        if "INDEKS" in key.upper() or "RISIKO" in key.upper():
                            indeks = val
                            break
                    results[bencana] = {
                        "indeks":  indeks,
                        "attrs":   attrs,
                        "status":  "ok",
                    }
                else:
                    results[bencana] = {"indeks": None, "status": "no_data"}
        except Exception as e:
            results[bencana] = {"indeks": None, "status": f"error: {e}"}

    return results

# ─── LAYER TILES UNTUK LEAFLET ────────────────────────────────────────────────
def get_map_tile_layers():
    """
    Return list konfigurasi tile layer untuk dash-leaflet.
    Semua layer siap dipakai langsung tanpa fetch tambahan.
    """
    return {
        # Basemap OpenStreetMap
        "osm": {
            "url":         "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "© <a href='https://openstreetmap.org/copyright'>OpenStreetMap</a> Contributors",
            "name":        "OpenStreetMap",
        },
        # BNPB InaRisk — Risiko Tanah Longsor
        "longsor": {
            "url": (
                "https://gis.bnpb.go.id/server/rest/services/inarisk"
                "/INDEKS_RISIKO_TANAH_LONGSOR/MapServer/tile/{z}/{y}/{x}"
            ),
            "attribution": "© <a href='https://inarisk.bnpb.go.id'>BNPB InaRisk</a>",
            "name":        "Risiko Longsor",
            "opacity":     0.65,
        },
        # BNPB InaRisk — Risiko Banjir
        "banjir": {
            "url": (
                "https://gis.bnpb.go.id/server/rest/services/inarisk"
                "/INDEKS_RISIKO_BANJIR/MapServer/tile/{z}/{y}/{x}"
            ),
            "attribution": "© <a href='https://inarisk.bnpb.go.id'>BNPB InaRisk</a>",
            "name":        "Risiko Banjir",
            "opacity":     0.65,
        },
        # BNPB InaRisk — Risiko Cuaca Ekstrim
        "cuaca_ekstrim": {
            "url": (
                "https://gis.bnpb.go.id/server/rest/services/inarisk"
                "/INDEKS_RISIKO_CUACA_EKSTRIM/MapServer/tile/{z}/{y}/{x}"
            ),
            "attribution": "© <a href='https://inarisk.bnpb.go.id'>BNPB InaRisk</a>",
            "name":        "Risiko Cuaca Ekstrim",
            "opacity":     0.65,
        },
    }

# ─── TEST ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("🗺️  Map Layers Test — Dashboard Petir Dramaga")
    print("=" * 55)

    print("\n1. Test BIG — Batas Desa Petir:")
    desa = fetch_batas_desa_petir()
    if desa:
        for f in desa.get("features", []):
            p = f.get("properties", {})
            print(f"   ✅ {p.get('WADMKD')} — {p.get('WIADKC')} — {p.get('WADMKK')}")
    else:
        print("   ❌ Tidak dapat mengambil data")

    print("\n2. Test BIG — Desa se-Kecamatan Dramaga:")
    kec = fetch_batas_kecamatan_dramaga()
    if kec:
        for f in kec.get("features", [])[:5]:
            p = f.get("properties", {})
            print(f"   ✅ {p.get('WADMKD')}")
    else:
        print("   ❌ Tidak dapat mengambil data")

    print("\n3. Test InaRisk BNPB — Indeks Risiko Desa Petir:")
    indeks = fetch_inarisk_indeks(LAT, LON)
    for bencana, data in indeks.items():
        status = "✅" if data["status"] == "ok" else "❌"
        print(f"   {status} {bencana}: {data.get('indeks', 'N/A')} "
              f"({data['status']})")

    print("\n4. Tile Layers (siap dipakai di Leaflet):")
    layers = get_map_tile_layers()
    for name, cfg in layers.items():
        print(f"   ✅ {cfg['name']}: {cfg['url'][:60]}...")
