"""
bmkg_cap.py
───────────
Integrasi Data Peringatan Dini BMKG berbasis Common Alerting Protocol (CAP)
untuk Kecamatan Dramaga, Kabupaten Bogor, Jawa Barat.

SUMBER DATA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
© BMKG – Badan Meteorologi, Klimatologi, dan Geofisika Republik Indonesia
   Website  : https://www.bmkg.go.id
   Data CAP : https://github.com/infoBMKG/data-cap
   Portal   : https://data.bmkg.go.id/peringatan-dini-cuaca/

CATATAN WAJIB (MANDATORY ATTRIBUTION):
   Sesuai ketentuan BMKG, penggunaan data ini WAJIB mencantumkan
   BMKG sebagai sumber data dan menampilkannya pada aplikasi/sistem.
   Ref: https://data.bmkg.go.id/peringatan-dini-cuaca/
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# ─── KODE WILAYAH ──────────────────────────────────────────────────────────────
# Struktur kode: Provinsi + Kabupaten + Kecamatan
# Jawa Barat    : 32
# Kab. Bogor    : 32.01  → folder: 3201
# Kec. Dramaga  : 32.01.21 → folder: 320121
PROV_CODE  = "32"           # Jawa Barat
KAB_CODE   = "3201"         # Kabupaten Bogor
KEC_CODE   = "320121"       # Kecamatan Dramaga
AREA_NAME  = "Kecamatan Dramaga, Kabupaten Bogor"

# URL raw GitHub BMKG data-cap
CAP_BASE_URL = (
    "https://raw.githubusercontent.com/infoBMKG/data-cap/main"
    f"/{PROV_CODE}/{KAB_CODE}/{KEC_CODE}.xml"
)

# Namespace XML CAP standard
CAP_NS = {
    "cap": "urn:oasis:names:tc:emergency:cap:1.2",
    "":    "urn:oasis:names:tc:emergency:cap:1.2",
}

WIB = timezone(timedelta(hours=7))

# ─── LEVEL PERINGATAN ──────────────────────────────────────────────────────────
SEVERITY_MAP = {
    "Extreme":  {"level": "AWAS",    "color": "#ef4444", "emoji": "🔴"},
    "Severe":   {"level": "SIAGA",   "color": "#f97316", "emoji": "🟠"},
    "Moderate": {"level": "WASPADA", "color": "#eab308", "emoji": "🟡"},
    "Minor":    {"level": "RINGAN",  "color": "#22c55e", "emoji": "🟢"},
    "Unknown":  {"level": "INFO",    "color": "#38bdf8", "emoji": "🔵"},
}

# ─── FETCH & PARSE CAP ─────────────────────────────────────────────────────────
def fetch_bmkg_cap():
    """
    Ambil dan parse data CAP BMKG untuk Kecamatan Dramaga.
    Return: list of alert dict, atau list kosong jika tidak ada peringatan.

    Sumber: © BMKG (https://github.com/infoBMKG/data-cap)
    """
    try:
        r = requests.get(CAP_BASE_URL, timeout=10)

        # 404 = tidak ada peringatan aktif saat ini (normal)
        if r.status_code == 404:
            return []

        if r.status_code != 200:
            print(f"⚠️  BMKG CAP HTTP {r.status_code}")
            return []

        content = r.text.strip()
        if not content or len(content) < 50:
            return []

        return _parse_cap_xml(content)

    except Exception as e:
        print(f"⚠️  BMKG CAP fetch error: {e}")
        return []

def _parse_cap_xml(xml_text: str) -> list:
    """Parse XML CAP dan return list peringatan."""
    alerts = []
    try:
        root = ET.fromstring(xml_text)

        # Handle namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        def find(el, tag):
            return el.find(f"{ns}{tag}")

        def findtext(el, tag, default=""):
            t = el.find(f"{ns}{tag}")
            return t.text.strip() if t is not None and t.text else default

        # Satu file bisa berisi beberapa <alert>
        # Cek apakah root adalah <alert> atau wrapper
        if root.tag.endswith("alert"):
            alert_elements = [root]
        else:
            alert_elements = root.findall(f"{ns}alert")

        for alert_el in alert_elements:
            identifier  = findtext(alert_el, "identifier")
            sender      = findtext(alert_el, "sender", "BMKG")
            sent_str    = findtext(alert_el, "sent")
            status      = findtext(alert_el, "status", "Actual")
            msg_type    = findtext(alert_el, "msgType", "Alert")

            # Skip jika bukan actual alert
            if status not in ("Actual", "Exercise"):
                continue

            # Parse info blocks
            for info_el in alert_el.findall(f"{ns}info"):
                language    = findtext(info_el, "language", "id-ID")
                category    = findtext(info_el, "category", "Met")
                event       = findtext(info_el, "event", "Peringatan Cuaca")
                urgency     = findtext(info_el, "urgency", "Unknown")
                severity    = findtext(info_el, "severity", "Unknown")
                certainty   = findtext(info_el, "certainty", "Unknown")
                headline    = findtext(info_el, "headline", event)
                description = findtext(info_el, "description", "")
                instruction = findtext(info_el, "instruction", "")
                effective   = findtext(info_el, "effective", sent_str)
                expires_str = findtext(info_el, "expires", "")

                # Ambil info area
                area_desc = AREA_NAME
                area_el   = info_el.find(f"{ns}area")
                if area_el is not None:
                    area_desc = findtext(area_el, "areaDesc", AREA_NAME)

                # Parse waktu
                sent_wib    = _parse_cap_time(sent_str)
                expires_wib = _parse_cap_time(expires_str)

                # Cek apakah masih aktif
                now = datetime.now(WIB)
                if expires_wib and expires_wib < now:
                    continue  # Peringatan sudah kadaluarsa

                sev_info = SEVERITY_MAP.get(severity, SEVERITY_MAP["Unknown"])

                alerts.append({
                    "identifier":   identifier,
                    "event":        event,
                    "headline":     headline,
                    "description":  description,
                    "instruction":  instruction,
                    "severity":     severity,
                    "urgency":      urgency,
                    "certainty":    certainty,
                    "category":     category,
                    "area":         area_desc,
                    "sent":         sent_wib,
                    "expires":      expires_wib,
                    "sent_str":     sent_wib.strftime("%d %b %Y %H:%M WIB") if sent_wib else "-",
                    "expires_str":  expires_wib.strftime("%d %b %Y %H:%M WIB") if expires_wib else "-",
                    "level":        sev_info["level"],
                    "color":        sev_info["color"],
                    "emoji":        sev_info["emoji"],
                    "source":       "BMKG",
                    "source_url":   "https://www.bmkg.go.id",
                    "data_url":     CAP_BASE_URL,
                })

    except ET.ParseError as e:
        print(f"⚠️  BMKG CAP XML parse error: {e}")

    return alerts

def _parse_cap_time(time_str: str):
    """Parse waktu CAP ke datetime WIB."""
    if not time_str:
        return None
    try:
        # Format CAP: 2026-03-17T10:00:00+07:00
        if "+" in time_str or time_str.endswith("Z"):
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            return dt.astimezone(WIB)
        else:
            dt = datetime.strptime(time_str[:19], "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=WIB)
    except Exception:
        return None

# ─── FORMAT TELEGRAM ───────────────────────────────────────────────────────────
def format_cap_telegram(alerts: list) -> str:
    """Format peringatan CAP untuk dikirim ke Telegram."""
    if not alerts:
        return None

    lines = []
    for a in alerts:
        lines.append(
            f"{a['emoji']} <b>PERINGATAN RESMI BMKG</b> {a['emoji']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 {a['area']}\n"
            f"🕐 {a['sent_str']}\n"
            f"⚠️ Status: <b>{a['level']}</b>\n"
            f"🌩️ Kejadian: <b>{a['event']}</b>\n"
            f"📋 {a['headline']}\n"
        )
        if a["description"]:
            lines.append(f"📝 {a['description'][:200]}\n")
        if a["instruction"]:
            lines.append(f"💡 Instruksi: {a['instruction'][:150]}\n")
        lines.append(
            f"⏰ Berlaku hingga: {a['expires_str']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"© Sumber: <b>BMKG</b> (bmkg.go.id)\n"
            f"Data: github.com/infoBMKG/data-cap"
        )
    return "\n".join(lines)

# ─── STATUS SUMMARY ────────────────────────────────────────────────────────────
def get_cap_status(alerts: list) -> dict:
    """
    Ringkasan status CAP untuk ditampilkan di dashboard.
    Return dict dengan info level tertinggi.
    """
    if not alerts:
        return {
            "active":    False,
            "count":     0,
            "level":     "NORMAL",
            "color":     "#22c55e",
            "emoji":     "✅",
            "message":   "Tidak ada peringatan aktif dari BMKG",
            "alerts":    [],
            "source":    "© BMKG – bmkg.go.id",
            "data_url":  "https://github.com/infoBMKG/data-cap",
        }

    # Ambil level tertinggi
    priority = {"AWAS": 4, "SIAGA": 3, "WASPADA": 2, "RINGAN": 1, "INFO": 0}
    highest  = max(alerts, key=lambda a: priority.get(a["level"], 0))

    return {
        "active":   True,
        "count":    len(alerts),
        "level":    highest["level"],
        "color":    highest["color"],
        "emoji":    highest["emoji"],
        "message":  highest["headline"],
        "alerts":   alerts,
        "source":   "© BMKG – bmkg.go.id",
        "data_url": "https://github.com/infoBMKG/data-cap",
    }
