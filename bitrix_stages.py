"""
Bitrix стадия аналитикаси — кунлик A → B.
  A = C6:NEW   (Подготовка товара)  — шу кун стадияга ТУШГАН сделкалар
                                      (кейин бошқа стадияга ўтса ҳам саналади)
  B = C6:WON   (Доставлено)         — улардан кейинчалик етказилганлари

Натижа: дата база шитсидаги «Стадия» варағи.
  • A ўтган кун учун ҚУЛФЛАНАДИ — бошқа ўзгармайди
  • B охирги 30 кун учун ҳар соат қайта ҳисобланади

    cd /root/roistat && source start.sh && python3 bitrix_stages.py
"""

import os, sys, json, time, logging
import urllib.request, urllib.error, urllib.parse
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
CATEGORY  = os.environ.get("RS_STAGE_CATEGORY", "6")
RECALC_B  = int(os.environ.get("RS_STAGE_RECALC", "30"))
PAGE_PAUSE = float(os.environ.get("RS_STAGE_PAUSE", "0.4"))

TG_TOKEN  = os.environ.get("RS_TELEGRAM_TOKEN", "")
TG_ADMINS = [x.strip() for x in os.environ.get("RS_ADMIN_IDS", "").split(",") if x.strip()]

HEADERS = ["Sana", "A (kirgan)", "B (yetkazilgan)", "Konversiya %", "Yangilandi"]


def tg(text):
    if not TG_TOKEN or not TG_ADMINS:
        return
    for aid in TG_ADMINS:
        try:
            u = "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN
            b = urllib.parse.urlencode({"chat_id": aid, "text": text[:4000]}).encode()
            urllib.request.urlopen(urllib.request.Request(u, data=b), timeout=15)
        except Exception as e:
            log.error("TG: %s", e)


# ══════════════════════════ Bitrix ═══════════════════════════════════════
def bx(method, params=None, tries=4):
    """TLS баъзан қотиб қолади — қайта уринади (20 → 40 → 60 сония)."""
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
    """Стадия тарихи — ЯНГИСИДАН эскисига, since'га етганда тўхтайди.
    (Bitrix CREATED_TIME филтрини эътиборга олмайди, шунинг учун шундай.)"""
    out, start, page, seen = [], 0, 0, 0
    stop = False
    flt = {}
    if CATEGORY:
        try:
            flt["CATEGORY_ID"] = int(CATEGORY)
        except ValueError:
            pass

    while not stop:
        resp = bx("crm.stagehistory.list", {
            "entityTypeId": 2,
            "order": {"ID": "DESC"},
            "filter": flt,
            "select": ["ID", "OWNER_ID", "CREATED_TIME", "STAGE_ID", "CATEGORY_ID"],
            "start": start,
        })
        if "error" in resp:
            raise RuntimeError(resp.get("error_description", resp["error"]))
        res = resp.get("result")
        items = res.get("items", []) if isinstance(res, dict) else (res or [])
        if not items:
            break

        for it in items:
            seen += 1
            d = (it.get("CREATED_TIME") or "")[:10]
            if not d:
                continue
            if d < since:                     # эскисига етдик
                stop = True
                break
            st = it.get("STAGE_ID") or ""
            if st in (STAGE_A, STAGE_B):
                out.append({"owner": str(it.get("OWNER_ID")), "stage": st, "date": d})

        page += 1
        if page % 10 == 0:
            log.info("   ... %d саҳифа · кўрилди %d · керакли %d", page, seen, len(out))
        nxt = resp.get("next")
        if not nxt or stop:
            break
        start = nxt
        time.sleep(PAGE_PAUSE)                # Bitrix чекловига урилмаслик учун

    log.info("Тарих: %d керакли ёзув (%d кўрилди, %d саҳифа)", len(out), seen, page)
    return out


def compute(hist):
    """{сана: A}, {сана: B} — B сделканинг A санасига ёзилади."""
    first_a = {}
    reached_b = set()
    for h in hist:
        if h["stage"] == STAGE_A:
            o = h["owner"]
            if o not in first_a or h["date"] < first_a[o]:
                first_a[o] = h["date"]
        else:
            reached_b.add(h["owner"])

    a_cnt, b_cnt = defaultdict(int), defaultdict(int)
    for o, d in first_a.items():
        a_cnt[d] += 1
        if o in reached_b:
            b_cnt[d] += 1

    ta, tb = sum(a_cnt.values()), sum(b_cnt.values())
    log.info("Кунлар: %d · жами A=%d · B=%d (%.1f%%)",
             len(a_cnt), ta, tb, (tb / ta * 100) if ta else 0)
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
    if len(s) >= 10 and s[4] == "-":
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


# ══════════════════════════ MAIN ═════════════════════════════════════════
if __name__ == "__main__":
    if not os.environ.get("BITRIX_WEBHOOK"):
        sys.exit("❌ BITRIX_WEBHOOK ўрнатилмаган (start.sh)")
    if not SHEET_ID:
        sys.exit("❌ RS_STAGE_SHEET ёки RS_SHEET_BUDGET ўрнатилмаган")

    log.info("=== Стадия: %s → %s · %s дан ===", STAGE_A, STAGE_B, MIN_DATE)
    t0 = time.time()

    try:
        hist = fetch_history(MIN_DATE)
    except Exception as e:
        msg = "⚠️ Стадия: Bitrix'дан олинмади — " + str(e)[:200]
        log.error(msg)
        tg(msg + "\n\nЭски маълумот сақланиб қолди.")
        sys.exit(1)

    if not hist:
        log.warning("⚠️ Тарих бўш — эски маълумот сақланади")
        sys.exit(0)

    a_new, b_new = compute(hist)

    book = _book()
    ws = ensure_ws(book)
    vals = ws.get_all_values()

    old = {}
    for r in vals[1:]:
        d = iso(r[0] if r else "")
        if d:
            old[d] = [_num(r[1] if len(r) > 1 else ""),
                      _num(r[2] if len(r) > 2 else "")]

    today  = datetime.now(TZ).strftime("%Y-%m-%d")
    b_from = (datetime.now(TZ) - timedelta(days=RECALC_B)).strftime("%Y-%m-%d")

    dates = sorted(set(list(a_new.keys()) + list(old.keys())))
    dates = [d for d in dates if d >= MIN_DATE]

    rows, froze_a, upd_b = [], 0, 0
    now_s = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
    for d in dates:
        o_a, o_b = old.get(d, [None, None])

        # ── A: ўтган кун учун ҚУЛФЛАНГАН
        if d < today and o_a is not None:
            a = o_a
            froze_a += 1
        else:
            a = a_new.get(d, o_a if o_a is not None else 0)

        # ── B: охирги RECALC_B кун қайта ҳисобланади, эскиси қотади
        if d >= b_from:
            b = b_new.get(d, 0)
            if o_b is not None and b != o_b:
                upd_b += 1
        else:
            b = o_b if o_b is not None else b_new.get(d, 0)

        conv = round(b / a * 100, 1) if a else 0
        rows.append([ru(d), a, b, conv, now_s])

    rows.sort(key=lambda r: iso(r[0]), reverse=True)   # янгиси юқорида

    ws.clear()
    ws.append_row(HEADERS)
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    ta = sum(r[1] for r in rows)
    tb = sum(r[2] for r in rows)
    log.info("✅ Ёзилди: %d кун · A=%d · B=%d (%.1f%%) · қулфланган A: %d · "
             "янгиланган B: %d · %.0f сония",
             len(rows), ta, tb, (tb / ta * 100) if ta else 0,
             froze_a, upd_b, time.time() - t0)