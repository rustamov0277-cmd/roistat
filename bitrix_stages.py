"""
Bitrix стадия аналитикаси — кунлик A → B.
  A = C6:NEW   (Подготовка товара)  — шу кун воронкага кирган сделкалар
  B = C6:WON   (Доставлено)         — улардан кейинчалик етказилганлари

Натижа: дата база шитсидаги «Стадия» варағи.
A ўтган кун учун ҚУЛФЛАНАДИ, B охирги 30 кун учун ҳар соат қайта ҳисобланади.

    cd /root/roistat && source start.sh && python3 bitrix_stages.py
"""

import os, sys, json, time, logging
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

TZ = timezone(timedelta(hours=5))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

WEBHOOK   = os.environ.get("BITRIX_WEBHOOK", "").rstrip("/") + "/"
SA_JSON   = os.environ.get("RS_SA_JSON", "/root/roistat/service_account.json")
SHEET_ID  = os.environ.get("RS_STAGE_SHEET", "") or os.environ.get("RS_SHEET_BUDGET", "")
TAB       = os.environ.get("RS_STAGE_TAB", "Стадия")
MIN_DATE  = os.environ.get("RS_MIN_DATE", "").strip() or "2026-07-01"

STAGE_A   = os.environ.get("RS_STAGE_A", "C6:NEW")
STAGE_B   = os.environ.get("RS_STAGE_B", "C6:WON")
RECALC_B  = int(os.environ.get("RS_STAGE_RECALC", "30"))   # B неча кун қайта ҳисобланади

TG_TOKEN  = os.environ.get("RS_TELEGRAM_TOKEN", "")
TG_ADMINS = [x.strip() for x in os.environ.get("RS_ADMIN_IDS", "").split(",") if x.strip()]

HEADERS = ["Sana", "A (kirgan)", "B (yetkazilgan)", "Konversiya %", "Yangilandi"]


def tg(text):
    if not TG_TOKEN or not TG_ADMINS:
        return
    import urllib.parse
    for aid in TG_ADMINS:
        try:
            u = "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN
            b = urllib.parse.urlencode({"chat_id": aid, "text": text[:4000]}).encode()
            urllib.request.urlopen(urllib.request.Request(u, data=b), timeout=15)
        except Exception as e:
            log.error("TG: %s", e)


# ══════════════════════════ Bitrix ═══════════════════════════════════════
def bx(method, params=None, tries=4):
    """TLS баъзан қотиб қолади — қайта уринади."""
    url = WEBHOOK + method + ".json"
    data = json.dumps(params or {}).encode("utf-8")
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt < tries - 1:
                wait = 20 * (attempt + 1)
                log.warning("Bitrix хато (%s) — %d сония кутаман", str(e)[:80], wait)
                time.sleep(wait)
                continue
            raise
    return {}


def fetch_history(since):
    """Стадия тарихини ўқийди: [{owner, stage, date}]"""
    out, start, page = [], 0, 0
    while True:
        resp = bx("crm.stagehistory.list", {
            "entityTypeId": 2,
            "order": {"ID": "ASC"},
            "filter": {">=CREATED_TIME": since + "T00:00:00"},
            "select": ["ID", "OWNER_ID", "CREATED_TIME", "STAGE_ID", "CATEGORY_ID"],
            "start": start,
        })
        if "error" in resp:
            raise RuntimeError(resp.get("error_description", resp["error"]))
        res = resp.get("result")
        items = res.get("items", []) if isinstance(res, dict) else (res or [])
        for it in items:
            st = it.get("STAGE_ID") or ""
            if st not in (STAGE_A, STAGE_B):
                continue
            d = (it.get("CREATED_TIME") or "")[:10]
            if not d:
                continue
            out.append({"owner": str(it.get("OWNER_ID")), "stage": st, "date": d})
        nxt = resp.get("next")
        page += 1
        if not nxt:
            break
        start = nxt
        if page % 20 == 0:
            log.info("   ... %d саҳифа, %d ёзув", page, len(out))
    log.info("Тарих: %d ёзув (%s ва %s)", len(out), STAGE_A, STAGE_B)
    return out


def compute(hist):
    """{сана: (A, B)} — B сделканинг A санасига ёзилади."""
    first_a = {}                    # owner → A га биринчи тушган сана
    reached_b = set()               # B га етган owner'лар
    for h in hist:
        if h["stage"] == STAGE_A:
            o = h["owner"]
            if o not in first_a or h["date"] < first_a[o]:
                first_a[o] = h["date"]
        else:
            reached_b.add(h["owner"])

    a_cnt = defaultdict(int)
    b_cnt = defaultdict(int)
    for o, d in first_a.items():
        a_cnt[d] += 1
        if o in reached_b:
            b_cnt[d] += 1
    log.info("Кунлар: %d · жами A=%d · B=%d",
             len(a_cnt), sum(a_cnt.values()), sum(b_cnt.values()))
    return a_cnt, b_cnt


# ══════════════════════════ Sheets ═══════════════════════════════════════
def _book():
    creds = Credentials.from_service_account_file(
        SA_JSON, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def ensure_ws(book):
    for ws in book.worksheets():
        if ws.title.strip().lower() == TAB.strip().lower():
            return ws
    ws = book.add_worksheet(title=TAB, rows=1000, cols=len(HEADERS) + 2)
    ws.append_row(HEADERS)
    log.info("Янги варақ яратилди: %s", TAB)
    return ws


def ru(d):
    p = d.split("-")
    return "%s.%s.%s" % (p[2], p[1], p[0])


def iso(s):
    s = str(s or "").strip()
    if not s:
        return None
    if "-" in s and len(s) >= 10 and s[4] == "-":
        return s[:10]
    p = s.split(".")
    if len(p) == 3:
        try:
            return "%04d-%02d-%02d" % (int(p[2]), int(p[1]), int(p[0]))
        except ValueError:
            return None
    return None


def _num(v):
    s = str(v or "").strip().replace(" ", "")
    return int(s) if s.isdigit() else None


if __name__ == "__main__":
    if not os.environ.get("BITRIX_WEBHOOK"):
        sys.exit("❌ BITRIX_WEBHOOK ўрнатилмаган (start.sh)")
    if not SHEET_ID:
        sys.exit("❌ RS_STAGE_SHEET ёки RS_SHEET_BUDGET ўрнатилмаган")

    log.info("=== Стадия аналитикаси: %s → %s ===", STAGE_A, STAGE_B)
    try:
        hist = fetch_history(MIN_DATE)
    except Exception as e:
        msg = "⚠️ Стадия: Bitrix'дан олинмади — " + str(e)[:200]
        log.error(msg)
        tg(msg + "\n\nЭски маълумот сақланди.")
        sys.exit(1)

    a_new, b_new = compute(hist)

    book = _book()
    ws = ensure_ws(book)
    vals = ws.get_all_values()

    # мавжуд қаторлар: {сана: [A, B]}
    old = {}
    for r in vals[1:]:
        d = iso(r[0] if r else "")
        if d:
            old[d] = [_num(r[1] if len(r) > 1 else ""),
                      _num(r[2] if len(r) > 2 else "")]

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    b_from = (datetime.now(TZ) - timedelta(days=RECALC_B)).strftime("%Y-%m-%d")

    dates = sorted(set(list(a_new.keys()) + list(old.keys())))
    dates = [d for d in dates if d >= MIN_DATE]

    rows, froze_a, upd_b = [], 0, 0
    for d in dates:
        o_a, o_b = old.get(d, [None, None])

        # ── A: ўтган кун учун ҚУЛФЛАНГАН
        if d < today and o_a is not None:
            a = o_a
            froze_a += 1
        else:
            a = a_new.get(d, o_a or 0)

        # ── B: охирги RECALC_B кун қайта ҳисобланади, эскиси қотади
        if d >= b_from:
            b = b_new.get(d, 0)
            if o_b is not None and b != o_b:
                upd_b += 1
        else:
            b = o_b if o_b is not None else b_new.get(d, 0)

        conv = round(b / a * 100, 1) if a else 0
        rows.append([ru(d), a, b, conv,
                     datetime.now(TZ).strftime("%d.%m.%Y %H:%M")])

    ws.clear()
    ws.append_row(HEADERS)
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    tot_a = sum(r[1] for r in rows)
    tot_b = sum(r[2] for r in rows)
    log.info("✅ Ёзилди: %d кун · A=%d · B=%d (%.1f%%) · қулфланган A: %d · янгиланган B: %d",
             len(rows), tot_a, tot_b, (tot_b / tot_a * 100) if tot_a else 0,
             froze_a, upd_b)