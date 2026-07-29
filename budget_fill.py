"""
БЮДЖЕТ ТЎЛДИРГИЧ — Meta'дан кунлик харажатни «Таргет» варағига ёзади.
Ҳар таргетолог ўз устунига, ҳар кун ўз қаторига.

Organic / Telegram / Аббос устунларига ТЕГМАЙДИ (уларда Meta реклама йўқ).

Ишлатиш:
    python3 budget_fill.py --dry          # фақат кўрсатади, ёзмайди
    python3 budget_fill.py                # охирги 7 кунни ёзади
    python3 budget_fill.py --days 31      # охирги 31 кунни ёзади
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

SA_JSON    = os.environ.get("RS_SA_JSON", "/root/roistat/service_account.json")
SHEET_ID   = os.environ.get("RS_SHEET_BUDGET", "")
BUDGET_TAB = (os.environ.get("RS_BUDGET_FILL_TAB", "").strip()
              or os.environ.get("RS_BUDGET_TAB", "Таргет").strip())
MIN_DATE   = os.environ.get("RS_MIN_DATE", "").strip()

# ── Кабинет → (йўналиш блоки, таргетолог устуни) ─────────────────────────
ACC_MAP = {
    # CollagenMarine блоки
    "collagen marine eldor":   ("collagenmarine", "элдор"),
    "sinolife family eldor":   ("collagenmarine", "элдор"),
    "umar 63":                 ("collagenmarine", "umar"),
    "umar - 64":               ("collagenmarine", "umar"),
    "timuro -  sinolife 32":   ("collagenmarine", "timur"),
    "timuro - sinolife  56":   ("collagenmarine", "timur"),
    "collagen sobirjon":       ("collagenmarine", "sobirjon"),
    # Zextra блоки
    "zextra eldor":            ("zextra", "элдор"),
    "zextra umar":             ("zextra", "umar"),
    "timuro  - zextra - 66":   ("zextra", "timur"),
    "zextra sobirjon":         ("zextra", "sobirjon"),
    "zextra kamron 1":         ("zextra", "kamron"),
}

def norm(s):
    return " ".join(str(s or "").strip().lower().split())

ACC_MAP = {norm(k): v for k, v in ACC_MAP.items()}

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
    """1-қатор = блок номи (бирлаштирилган), 2-қатор = таргетолог.
    Қайтаради: {(блок, таргетолог): устун_индекси} ва {сана: қатор_индекси}"""
    if len(vals) < 3:
        raise RuntimeError("Варақ жуда қисқа")
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
        if t in ("organic", "telegram"):     # Meta'да йўқ — тегмаймиз
            continue
        cols[(g, t)] = i

    rows = {}
    for r in range(2, len(vals)):
        d = parse_date(vals[r][0] if vals[r] else "")
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

def col_a1(idx):
    s, idx = "", idx + 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s

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

    data = meta_by_day(args.days)
    if not data:
        sys.exit("❌ Meta маълумоти йўқ")

    updates, skipped, total = [], set(), 0.0
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
        updates.append({"range": "%s!%s%d" % (ws.title, col_a1(c), r + 1),
                        "values": [[round(spend, 2)]]})

    log.info("Ёзиладиган катак: %d, жами $%.2f", len(updates), total)
    if skipped:
        log.warning("⚠️ Топилмади: %s", ", ".join(sorted(skipped)[:20]))

    if args.dry:
        log.info("--dry режими: ёзилмади")
        for u in updates[:40]:
            log.info("   %-16s = %s", u["range"], u["values"][0][0])
        sys.exit(0)

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        log.info("✅ Ёзилди: %d катак", len(updates))
    else:
        log.warning("Ёзиладиган нарса йўқ")
