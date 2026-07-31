"""
META қатлами — Campaign / Ad set / Ad даражасида кунлик рақамлар.
Business Manager'даги БАРЧА кабинетни ўзи топади.

ХАВФСИЗЛИК:
  • битта кабинет ишламаса — унинг ЭСКИ маълумоти кэшда сақланади
  • огоҳлантириш битта кабинет учун КУНИГА БИР МАРТА юборилади
  • тикланганда "тикланди" хабари келади

Ишлатиш:
  python3 meta_spend.py --accounts
  python3 meta_spend.py --days 3 --show ad
  python3 meta_spend.py --rebuild
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
FAIL_FILE  = os.environ.get("RS_META_FAILS", "/root/roistat/meta_fails.json")
BM_ID      = os.environ.get("RS_META_BUSINESS_ID", "").strip()
MIN_DATE   = os.environ.get("RS_MIN_DATE", "").strip()
CHUNK_DAYS = int(os.environ.get("RS_META_CHUNK", "7"))

TG_TOKEN   = os.environ.get("RS_TELEGRAM_TOKEN", "")
TG_ADMINS  = [x.strip() for x in os.environ.get("RS_ADMIN_IDS", "").split(",") if x.strip()]


def tg_send(text):
    if not TG_TOKEN or not TG_ADMINS:
        return
    for aid in TG_ADMINS:
        try:
            url = "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN
            body = urllib.parse.urlencode({"chat_id": aid, "text": text[:4000]}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=15)
        except Exception as e:
            log.error("TG: %s", e)


# ═════════════ Огоҳлантириш назорати (кунига 1 марта) ════════════════════
def _load_fails():
    try:
        with open(FAIL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_fails(d):
    try:
        with open(FAIL_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        log.error("fails файл: %s", e)


def alert_failures(failed, errors):
    """failed — ишламаган кабинет номлари.
    Хабар кунига бир марта. Тикланса — 'тикланди' хабари."""
    state = _load_fails()
    today = datetime.now(TZ).strftime("%Y-%m-%d")

    # ── тикланганлар
    healed = [n for n in state if n not in failed]
    if healed:
        for n in healed:
            state.pop(n, None)
        tg_send("✅ ROISTAT — Meta кабинети тикланди\n\n" +
                "\n".join("• " + n for n in healed))
        log.info("Тикланди: %s", ", ".join(healed))

    # ── янги ёки эски хатолар
    fresh, repeat = [], []
    for n in failed:
        rec = state.get(n) or {}
        if rec.get("date") != today:
            fresh.append(n)
            state[n] = {"date": today, "count": rec.get("count", 0) + 1}
        else:
            repeat.append(n)
            rec["count"] = rec.get("count", 0) + 1
            state[n] = rec

    if fresh:
        lines = ["⚠️ ROISTAT — Meta кабинети ўқилмади", ""]
        for n in fresh:
            days = state[n].get("count", 1)
            lines.append("• %s%s" % (n, "  (%d-марта)" % days if days > 1 else ""))
            err = errors.get(n, "")
            if err:
                lines.append("   %s" % err[:120])
        lines += ["", "Эски маълумот сақланди — рақамлар тушмайди.",
                  "Кун давомида такрорланса, бошқа хабар келмайди.",
                  "Эртага ҳам такрорланса — токенни текширинг."]
        tg_send("\n".join(lines))
        log.info("📨 Telegram: %d та кабинет ҳақида хабар", len(fresh))

    if repeat:
        log.info("Такрорий хато (хабар юборилмади): %s", ", ".join(repeat))

    _save_fails(state)


# ══════════════════════════════ HTTP ══════════════════════════════════════
def _get(url, params=None, tries=4):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "roistat-meta/5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            transient = (e.code in (429, 500, 503)
                         or '"code":17' in body
                         or '"code":613' in body
                         or '"code":2' in body)
            if transient and attempt < tries - 1:
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
    out, pages = [], 0
    data = _get(url, params)
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
        log.warning("⚠️ %d кабинет USD'да эмас — харажат нотўғри қўшилади", len(bad))
    return accs


# ══════════════════════ 2. Кунлик × Ad даражаси ══════════════════════════
FIELDS = ("date_start,campaign_id,campaign_name,adset_id,adset_name,"
          "ad_id,ad_name,spend,impressions,reach,clicks,actions")


def _chunks(since, until, n):
    a = datetime.strptime(since, "%Y-%m-%d").date()
    b = datetime.strptime(until, "%Y-%m-%d").date()
    while a <= b:
        c = min(a + timedelta(days=n - 1), b)
        yield a.strftime("%Y-%m-%d"), c.strftime("%Y-%m-%d")
        a = c + timedelta(days=1)


def fetch_account(acc, since, until):
    url = "https://graph.facebook.com/%s/%s/insights" % (API_VER, acc["id"])
    out = []
    for s, u in _chunks(since, until, CHUNK_DAYS):
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
            if not r.get("date_start") or not r.get("ad_id"):
                continue
            out.append({
                "date":     r["date_start"],
                "acc":      acc["name"],
                "camp_id":  r.get("campaign_id", ""),
                "camp":     (r.get("campaign_name") or "—").strip(),
                "adset_id": r.get("adset_id", ""),
                "adset":    (r.get("adset_name") or "—").strip(),
                "ad_id":    r.get("ad_id", ""),
                "ad":       (r.get("ad_name") or "").strip() or "— без имени —",
                "spend":    float(r.get("spend") or 0),
                "impr":     int(r.get("impressions") or 0),
                "reach":    int(r.get("reach") or 0),
                "clicks":   int(r.get("clicks") or 0),
                "leads":    leads,
            })
    return out


def fetch_range(since, until):
    """Қайтаради: ({сана: [ёзувлар]}, ўқилган кабинет номлари)"""
    if not TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN ўрнатилмаган (start.sh)")
    accs = discover_accounts()
    by_date = defaultdict(list)
    ok_names, failed, errors = set(), [], {}

    for a in accs:
        try:
            rows = fetch_account(a, since, until)
        except Exception as e:
            msg = str(e)[:180]
            log.error("   ❌ %-28s %s", a["name"][:28], msg)
            failed.append(a["name"])
            errors[a["name"]] = msg
            continue
        for r in rows:
            by_date[r["date"]].append(r)
        log.info("   ✅ %-28s %d ёзув", a["name"][:28], len(rows))
        ok_names.add(a["name"])

    log.info("Кабинет: %d/%d ўқилди · %d кун", len(ok_names), len(accs), len(by_date))
    if failed:
        log.warning("⚠️ Ўқилмади — эски маълумот сақланади: %s", ", ".join(failed))
    alert_failures(failed, errors)
    return dict(by_date), ok_names


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
    log.info("Кэш: %d кун · %d ёзув", len(d), sum(len(v) for v in d.values()))


def refresh(days=7, min_date=None, rebuild=False):
    """Кэшни КАБИНЕТ-КАБИНЕТ янгилайди.
    Ишламаган кабинетнинг эски ёзувлари ўчмайди."""
    cache = load_cache()
    today = datetime.now(TZ).date()
    min_date = min_date or MIN_DATE or None

    if rebuild:
        if not min_date:
            log.error("❌ --rebuild учун RS_MIN_DATE керак")
            return cache
        since = min_date
        log.info("♻️  REBUILD: %s дан бугунгача", since)
    else:
        since = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        if min_date and since < min_date:
            since = min_date

    until = today.strftime("%Y-%m-%d")
    log.info("Meta: %s — %s", since, until)

    try:
        fresh, ok_names = fetch_range(since, until)
    except Exception as e:
        log.error("⚠️ Meta олинмади (%s) — эски кэш ишлатилади", str(e)[:200])
        return cache

    if not ok_names:
        log.warning("⚠️ Бирорта кабинет ўқилмади — эски кэш сақланади")
        return cache

    d0 = datetime.strptime(since, "%Y-%m-%d").date()
    d1 = datetime.strptime(until, "%Y-%m-%d").date()
    kept_total = 0
    while d0 <= d1:
        day = d0.strftime("%Y-%m-%d")
        keep = [r for r in cache.get(day, []) if r.get("acc") not in ok_names]
        new = fresh.get(day, [])
        kept_total += len(keep)
        if keep or new:
            cache[day] = keep + new
        d0 += timedelta(days=1)

    if kept_total:
        log.info("Сақлаб қолинди (ўқилмаган кабинетлардан): %d ёзув", kept_total)

    if min_date:
        cache = dict((k, v) for k, v in cache.items() if k >= min_date)

    save_cache(cache)
    return cache


# ═══════════════════ 4. roistat.py учун ёрдамчилар ═══════════════════════
def spend_by_ad(cache):
    out = {}
    for d, rows in cache.items():
        for r in rows:
            k = (d, r["ad"].strip().lower())
            m = out.setdefault(k, {"spend": 0.0, "impr": 0, "reach": 0,
                                   "clicks": 0, "leads": 0})
            for f in ("spend", "impr", "reach", "clicks", "leads"):
                m[f] += r.get(f, 0)
    return out


def spend_by_id(cache):
    out = {}
    for d, rows in cache.items():
        for r in rows:
            if not r.get("ad_id"):
                continue
            k = (d, r["ad_id"])
            m = out.setdefault(k, {"spend": 0.0, "impr": 0, "reach": 0,
                                   "clicks": 0, "leads": 0, "ad": r["ad"]})
            for f in ("spend", "impr", "reach", "clicks", "leads"):
                m[f] += r.get(f, 0)
    return out


def id_to_name(cache):
    out = {}
    for d in sorted(cache):
        for r in cache[d]:
            if r.get("ad_id"):
                out[r["ad_id"]] = r["ad"]
    return out


# ══════════════════════════════ CLI ═══════════════════════════════════════
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-date", default=MIN_DATE or None)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--show", choices=["ad", "adset", "camp"])
    ap.add_argument("--accounts", action="store_true")
    ap.add_argument("--ids", action="store_true")
    args = ap.parse_args()

    if not TOKEN:
        sys.exit("❌ META_ACCESS_TOKEN ўрнатилмаган (start.sh га қўшинг)")

    if args.accounts:
        discover_accounts()
        sys.exit(0)

    if args.ids:
        for i, n in sorted(id_to_name(load_cache()).items(), key=lambda x: x[1]):
            print("%-20s %s" % (i, n))
        sys.exit(0)

    data = refresh(args.days, args.min_date, args.rebuild)
    if not data:
        sys.exit("❌ Маълумот йўқ")

    field = args.show or "ad"
    agg = defaultdict(lambda: {"spend": 0.0, "impr": 0, "clicks": 0, "leads": 0})
    for d, rows in data.items():
        for r in rows:
            a = agg[r[field]]
            for f in ("spend", "impr", "clicks", "leads"):
                a[f] += r.get(f, 0)

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