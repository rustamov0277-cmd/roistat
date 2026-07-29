"""
БЮДЖЕТ ТЎЛДИРГИЧ — Meta'дан кунлик харажатни «Target» варағига ёзади.
Ҳар таргетолог ўз устунига, ҳар кун ўз қаторига.

Organic / Telegram устунларига ТЕГМАЙДИ (уларда Meta реклама йўқ).

Ишлатиш:
    python3 budget_fill.py --dry          # фақат кўрсатади, ёзмайди
    python3 budget_fill.py                # охирги 7 кунни Meta'дан янгилаб ёзади
    python3 budget_fill.py --days 31      # бутун ойни Meta'дан янгилаб ёзади
"""

import os, sys, argparse, logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

import meta_spend as MS

TZ = timezone(timedelta(hours=5))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

SA_JSON  = os.environ.get("RS_SA_JSON", "/root/roistat/service_account.json")
SHEET_ID = os.environ.get("RS_SHEET_BUDGET", "")
MIN_DATE = os.environ.get("RS_MIN_DATE", "").strip()

# Ёзиладиган варақ: RS_BUDGET_FILL_TAB устун, бўлмаса RS_BUDGET_TAB
BUDGET_TAB = (os.environ.get("RS_BUDGET_FILL_TAB", "").strip()
              or os.environ.get("RS_BUDGET_TAB", "Target").strip())

# ── Кабинет → (йўналиш блоки, таргетолог устуни) ─────────────────────────
ACC_MAP_RAW = {
    # CollagenMarine блоки
    "collagen marine eldor":   ("collagenmarine", "элдор"),
    "sinolife family eldor":   ("collagenmarine", "элдор"),
    "umar 63":                 ("collagenmarine", "umar"),
    "umar - 64":               ("collagenmarine", "umar"),
    "timuro - sinolife 32":    ("collagenmarine", "timur"),
    "timuro - sinolife 56":    ("collagenmarine", "timur"),
    "collagen sobirjon":       ("collagenmarine", "sobirjon"),
    # Zextra блоки
    "zextra eldor":            ("zextra", "элдор"),
    "zextra umar":             ("zextra", "umar"),
    "timuro - zextra - 66":    ("zextra", "timur"),
    "zextra sobirjon":         ("zextra", "sobirjon"),
    "zextra kamron 1":         ("zextra", "kamron"),
}


def norm(s):
    """Кичик ҳарф + ортиқча бўшлиқларни бир бўшлиққа келтиради."""
    return " ".join(str(s or "").strip().lower().split())


ACC_MAP = dict((norm(k), v) for k, v in ACC_MAP_RAW.items())


def parse_date(v):
    s = str(v or "").strip().split(" ")[0]
    if not s:
        return None
    for sep in (".", "/", "-"):
        if sep in s:
            p = s.split(sep)
            if len(p) != 3:
                continue
            try:
                if len(p[0]) == 4:
                    y, m, d = int(p[0]), int(p[1]), int(p[2])
                else:
                    d, m, y = int(p[0]), int(p[1]), int(p[2])
                if y < 100:
                    y += 2000
                if not (1 <= m <= 12 and 1 <= d <= 31):
                    continue
                return "%04d-%02d-%02d" % (y, m, d)
            except ValueError:
                continue
    return None


def col_a1(idx):
    """0 → A, 1 → B, 25 → Z, 26 → AA"""
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _book():
    creds = Credentials.from_service_account_file(
        SA_JSON, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def find_ws(book, tab):
    want = norm(tab).replace(" ", "")
    for ws in book.worksheets():
        if norm(ws.title).replace(" ", "") == want:
            return ws
    log.error("❌ Варақ топилмади: '%s' | Мавжудлари: %s",
              tab, [w.title for w in book.worksheets()])
    return None


def build_layout(vals):
    """1-қатор = блок номи (бирлаштирилган), 2-қатор = таргетолог, A устуни = сана.
    Қайтаради: {(блок, таргетолог): устун_индекси}, {сана: қатор_индекси}"""
    if len(vals) < 3:
        raise RuntimeError("Варақ жуда қисқа — сарлавҳа қаторлари йўқ")

    groups, cur = [], ""
    for c in vals[0]:
        if str(c).strip():
            cur = str(c).strip()
        groups.append(cur)

    targs = vals[1]
    cols = {}
    for i in range(len(targs)):
        t = norm(targs[i])
        g = norm(groups[i]) if i < len(groups) else ""
        if not t or not g:
            continue
        if t in ("organic", "telegram"):
            continue
        cols[(g, t)] = i

    rows = {}
    for r in range(2, len(vals)):
        row = vals[r] if vals[r] else []
        d = parse_date(row[0] if row else "")
        if d:
            rows[d] = r

    return cols, rows


def meta_by_day(days):
    """{(сана, блок, таргетолог): харажат}"""
    cache = MS.refresh(days, MIN_DATE or None)
    out = defaultdict(float)
    unknown = defaultdict(float)
    for d, items in (cache or {}).items():
        for r in items:
            key = ACC_MAP.get(norm(r.get("acc")))
            if key:
                out[(d, key[0], key[1])] += r.get("spend", 0)
            else:
                unknown[r.get("acc", "?")] += r.get("spend", 0)
    if unknown:
        log.warning("⚠️ ACC_MAP'да йўқ кабинетлар (ёзилмади):")
        for a, s in sorted(unknown.items(), key=lambda x: -x[1]):
            log.warning("   $%9.2f  %s", s, a)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry", action="store_true", help="ёзмайди, фақат кўрсатади")
    args = ap.parse_args()

    if not SHEET_ID:
        sys.exit("❌ RS_SHEET_BUDGET ўрнатилмаган (start.sh)")

    book = _book()
    ws = find_ws(book, BUDGET_TAB)
    if ws is None:
        sys.exit(1)

    vals = ws.get_all_values()
    cols, rows = build_layout(vals)
    log.info("Варақ '%s': %d устун, %d сана қатори", ws.title, len(cols), len(rows))
    for (g, t), i in sorted(cols.items()):
        log.info("   %-16s %-10s → устун %s", g, t, col_a1(i))

    if not cols:
        sys.exit("❌ Устун топилмади — 1 ва 2-қаторда сарлавҳа борми?")
    if not rows:
        sys.exit("❌ Сана қатори топилмади — A устунида саналар борми?")

    data = meta_by_day(args.days)
    if not data:
        sys.exit("❌ Meta маълумоти йўқ")

    updates = []
    skipped = set()
    total = 0.0
    for (d, g, t), spend in sorted(data.items()):
        r = rows.get(d)
        c = cols.get((g, t))
        if r is None:
            skipped.add("сана " + d)
            continue
        if c is None:
            skipped.add("устун " + g + "/" + t)
            continue
        total += spend
        cell = col_a1(c) + str(r + 1)
        updates.append({"range": cell, "values": [[round(spend, 2)]]})

    log.info("Ёзиладиган катак: %d, жами $%.2f", len(updates), total)
    if skipped:
        log.warning("⚠️ Топилмади: %s", ", ".join(sorted(skipped)[:20]))

    if args.dry:
        log.info("--dry режими: ёзилмади")
        for u in updates[:40]:
            log.info("   %-8s = %s", u["range"], u["values"][0][0])
        sys.exit(0)

    if not updates:
        log.warning("Ёзиладиган нарса йўқ")
        sys.exit(0)

    ws.batch_update(updates, value_input_option="USER_ENTERED")
    log.info("✅ Ёзилди: %d катак, жами $%.2f", len(updates), total)