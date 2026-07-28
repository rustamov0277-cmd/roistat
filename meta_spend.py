"""
META қатлами — Campaign / Ad set / Ad даражасида кунлик рақамлар.
Business Manager'даги БАРЧА кабинетни ўзи топади.

Кэш: /root/roistat/meta_spend.json
  {"2026-07-25":[{"acc":"...","camp":"...","adset":"...","ad":"...",
                  "spend":12.5,"impr":4200,"reach":3100,"clicks":130,"leads":8}]}

Синаш:
  python3 meta_spend.py --accounts          # кабинетлар топиладими
  python3 meta_spend.py --days 3 --show ad  # креативлар ва нархлари
  python3 meta_spend.py --days 40           # кэшни тўлдириш
"""

import os, sys, json, time, argparse, logging
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

TZ = timezone(timedelta(hours=5))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TOKEN      = os.environ.get("META_ACCESS_TOKEN", "") or os.environ.get("RS_META_TOKEN", "")
API_VER    = os.environ.get("RS_META_API", "v21.0")
CACHE_FILE = os.environ.get("RS_META_CACHE", "/root/roistat/meta_spend.json")
BM_ID      = os.environ.get("RS_META_BUSINESS_ID", "").strip()
CHUNK_DAYS = int(os.environ.get("RS_META_CHUNK", "7"))   # сўровни неча кунга бўлиш

# ══════════════════════════════ HTTP ══════════════════════════════════════
def _get(url, params=None, tries=4):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "roistat-meta/2.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            # 17 / 80000 = rate limit, 613 = too many calls
            if (e.code in (429, 500, 503) or '"code":17' in body or '"code":613' in body) \
               and attempt < tries - 1:
                wait = 30 * (attempt + 1)
                log.warning("Лимит/хато %s — %d сония кутаман", e.code, wait)
                time.sleep(wait)
                continue
            raise RuntimeError("HTTP %s: %s" % (e.code, body[:400]))
        except Exception:
            if attempt < tries - 1:
                time.sleep(15)
                continue
            raise
    return {}

def _paged(url, params, cap=200):
    out = []
    data = _get(url, params)
    pages = 0
    while True:
        out += data.get("data", [])
        nxt = (data.get("paging") or {}).get("next")
        pages += 1
        if not nxt or pages >= cap:
            break
        data = _get(nxt)
    return out

# ══════════════════════════ 1. Кабинетлар ════════════════════════════════
def discover_accounts():
    """Business Manager'даги барча реклама кабинети."""
    if BM_ID:
        url = "https://graph.facebook.com/%s/%s/owned_ad_accounts" % (API_VER, BM_ID)
    else:
        url = "https://graph.facebook.com/%s/me/adaccounts" % API_VER
    rows = _paged(url, {"access_token": TOKEN,
                        "fields": "id,name,account_status,currency",
                        "limit": 200})
    accs = [{"id": a.get("id"), "name": (a.get("name") or "").strip(),
             "status": a.get("account_status"), "cur": a.get("currency", "USD")}
            for a in rows if a.get("id")]
    log.info("Топилган кабинетлар: %d", len(accs))
    for a in accs:
        mark = "" if a["cur"] == "USD" else "  ⚠️ USD эмас!"
        log.info("   %-22s %-4s %s%s", a["id"], a["cur"], a["name"][:40], mark)
    bad = [a for a in accs if a["cur"] != "USD"]
    if bad:
        log.warning("⚠️ %d кабинет USD'да эмас — уларнинг харажати нотўғри "
                    "қўшилади, айтинг курс қўшамиз", len(bad))
    return accs

# ══════════════════════ 2. Кунлик × Ad даражаси ══════════════════════════
FIELDS = ("date_start,campaign_name,adset_name,ad_name,"
          "spend,impressions,reach,clicks,actions")

def _daterange_chunks(since, until, n):
    a = datetime.strptime(since, "%Y-%m-%d").date()
    b = datetime.strptime(until, "%Y-%m-%d").date()
    while a <= b:
        c = min(a + timedelta(days=n - 1), b)
        yield a.strftime("%Y-%m-%d"), c.strftime("%Y-%m-%d")
        a = c + timedelta(days=1)

def fetch_account(acc, since, until):
    url = "https://graph.facebook.com/%s/%s/insights" % (API_VER, acc["id"])
    out = []
    for s, u in _daterange_chunks(since, until, CHUNK_DAYS):
        rows = _paged(url, {
            "access_token": TOKEN,
            "time_range": json.dumps({"since": s, "until": u}),
            "time_increment": 1,
            "level": "ad",
            "fields": FIELDS,
            "limit": 500,
        })
        for r in rows:
            leads = 0
            for a in (r.get("actions") or []):
                if a.get("action_type") == "lead":
                    leads += int(float(a.get("value", 0)))
            ad = (r.get("ad_name") or "").strip()
            if not r.get("date_start") or not ad:
                continue
            out.append({
                "date":   r["date_start"],
                "acc":    acc["name"],
                "camp":   (r.get("campaign_name") or "—").strip(),
                "adset":  (r.get("adset_name") or "—").strip(),
                "ad":     ad,
                "spend":  float(r.get("spend") or 0),
                "impr":   int(r.get("impressions") or 0),
                "reach":  int(r.get("reach") or 0),
                "clicks": int(r.get("clicks") or 0),
                "leads":  leads,
            })
    return out

def fetch_range(since, until):
    if not TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN ўрнатилмаган (start.sh)")
    accs = discover_accounts()
    by_date = defaultdict(list)
    ok = 0
    for a in accs:
        try:
            rows = fetch_account(a, since, until)
        except Exception as e:
            log.error("   ❌ %-28s %s", a["name"][:28], str(e)[:180])
            continue
        for r in rows:
            by_date[r["date"]].append(r)
        log.info("   ✅ %-28s %d ёзув", a["name"][:28], len(rows))
        ok += 1
    log.info("Кабинет: %d/%d ўқилди · %d кун", ok, len(accs), len(by_date))
    return dict(by_date)

# ══════════════════════════════ 3. Кэш ════════════════════════════════════
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error("Кэш ўқилмади: %s", e)
    return {}

def save_cache(d):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE_FILE)
    n = sum(len(v) for v in d.values())
    log.info("Кэш сақланди: %d кун · %d ёзув", len(d), n)

def refresh(days=7, min_date=None):
    """Охирги N кунни Meta'дан қайта олади, эскиси кэшда қолади.
    Meta ишламаса — эски кэш қайтарилади (дашборд бузилмайди)."""
    cache = load_cache()
    today = datetime.now(TZ).date()
    since = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    if min_date and since < min_date:
        since = min_date
    until = today.strftime("%Y-%m-%d")
    log.info("Meta: %s — %s олинмоқда", since, until)
    try:
        fresh = fetch_range(since, until)
    except Exception as e:
        log.error("⚠️ Meta олинмади (%s) — эски кэш ишлатилади", str(e)[:200])
        return cache
    if not fresh:
        log.warning("⚠️ Meta бўш қайтди — эски кэш сақланади")
        return cache
    cache.update(fresh)
    if min_date:
        cache = {k: v for k, v in cache.items() if k >= min_date}
    save_cache(cache)
    return cache

# ═══════════════════ 4. roistat.py учун: ном → харажат ═══════════════════
def spend_by_ad(cache):
    """{(сана, ad_name_lower): {spend, impr, reach, clicks, leads}}"""
    out = {}
    for d, rows in cache.items():
        for r in rows:
            k = (d, r["ad"].strip().lower())
            m = out.setdefault(k, {"spend": 0.0, "impr": 0, "reach": 0,
                                   "clicks": 0, "leads": 0})
            m["spend"]  += r["spend"]
            m["impr"]   += r["impr"]
            m["reach"]  += r["reach"]
            m["clicks"] += r["clicks"]
            m["leads"]  += r["leads"]
    return out

# ══════════════════════════════ CLI ═══════════════════════════════════════
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-date", default=os.environ.get("RS_MIN_DATE", "").strip() or None)
    ap.add_argument("--show", choices=["ad", "adset", "camp"], help="рўйхатни кўрсат")
    ap.add_argument("--accounts", action="store_true", help="фақат кабинетлар")
    args = ap.parse_args()

    if not TOKEN:
        sys.exit("❌ META_ACCESS_TOKEN ўрнатилмаган (start.sh га қўшинг)")

    if args.accounts:
        discover_accounts()
        sys.exit(0)

    data = refresh(args.days, args.min_date)
    if not data:
        sys.exit("❌ Маълумот йўқ")

    field = {"ad": "ad", "adset": "adset", "camp": "camp"}.get(args.show or "ad")
    agg = defaultdict(lambda: {"spend": 0.0, "impr": 0, "clicks": 0, "leads": 0})
    for d, rows in data.items():
        for r in rows:
            a = agg[r[field]]
            a["spend"]  += r["spend"]
            a["impr"]   += r["impr"]
            a["clicks"] += r["clicks"]
            a["leads"]  += r["leads"]

    total = sum(a["spend"] for a in agg.values())
    log.info("Жами: %d кун · %d %s · $%.2f", len(data), len(agg), field, total)

    if args.show:
        print("\n%-44s %10s %8s %9s %8s %7s" %
              ("NAME", "SPEND $", "IMPR", "CLICKS", "LEADS", "CPL $"))
        print("-" * 92)
        for k, m in sorted(agg.items(), key=lambda x: -x[1]["spend"])[:60]:
            cpl = m["spend"] / m["leads"] if m["leads"] else 0
            print("%-44s %10.2f %8d %9d %8d %7.2f" %
                  (k[:44], m["spend"], m["impr"], m["clicks"], m["leads"], cpl))