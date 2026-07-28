"""
ROISTAT v5 — Sinolife / Zextra сквозная аналитика
Sheets (лид + hisobot, черновик+архив) + Meta Ads → HTML → GitHub Pages

Файллар: meta_spend.py · roistat.py · roistat_html.py
Ишга тушириш:
    cd /root/roistat && source start.sh && python3 roistat.py
"""

import os, sys, json, time, logging
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

try:
    import meta_spend as MS
except Exception:
    MS = None

TZ = timezone(timedelta(hours=5))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ══════════════════════════ КОНФИГ (start.sh) ════════════════════════════
SA_JSON       = os.environ.get("RS_SA_JSON", "/root/roistat/service_account.json")
SH_LEADS      = os.environ.get("RS_SHEET_LEADS", "")
SH_LEADS_ARC  = os.environ.get("RS_SHEET_LEADS_ARCHIVE", "")
SH_ORDERS     = os.environ.get("RS_SHEET_ORDERS", "")
SH_ORDERS_ARC = os.environ.get("RS_SHEET_ORDERS_ARCHIVE", "")
SH_BUDGET     = os.environ.get("RS_SHEET_BUDGET", "")

LEAD_TABS     = [t.strip() for t in os.environ.get(
                  "RS_LEAD_TABS", "CollagenMarine,Zextra,Сммщик ии,Веб сайт").split(",") if t.strip()]
LEAD_TABS_ARC = [t.strip() for t in os.environ.get(
                  "RS_LEAD_TABS_ARCHIVE", "").split(",") if t.strip()] or LEAD_TABS
ORDERS_TAB    = os.environ.get("RS_ORDERS_TAB", "").strip()
BUDGET_TAB    = os.environ.get("RS_BUDGET_TAB", "").strip()

GH_TOKEN = os.environ.get("RS_GITHUB_TOKEN", "")
GH_USER  = os.environ.get("RS_GITHUB_USER", "rustamov0277-cmd")
GH_REPO  = os.environ.get("RS_GITHUB_REPO", "roistat")
GH_FILE  = "index.html"

TG_TOKEN  = os.environ.get("RS_TELEGRAM_TOKEN", "")
TG_ADMINS = [x.strip() for x in os.environ.get("RS_ADMIN_IDS", "").split(",") if x.strip()]

USD_FALLBACK  = float(os.environ.get("RS_USD_FALLBACK", "12650"))
SNAPSHOT_FILE = "/root/roistat/snapshot.json"
OUT_FILE      = "/root/roistat/index.html"
FRESH_DAYS    = 7
DAILY_DAYS    = int(os.environ.get("RS_DAILY_DAYS", "180"))
MIN_DATE      = os.environ.get("RS_MIN_DATE", "").strip()
META_ON       = os.environ.get("RS_META_ENABLE", "1") == "1"
META_DAYS     = int(os.environ.get("RS_META_DAYS", "7"))

NA = "— не указано —"

# ══════════════════════════ ЁРДАМЧИЛАР ══════════════════════════════════
def norm(s):
    return str(s or "").strip().lower()

def _num(v):
    if v is None:
        return 0.0
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace("$", "")
    s = s.replace("\u2212", "-")
    if not s or "DIV" in s or "REF" in s or s in ("-", "—"):
        return 0.0
    if s.count(",") and s.count("."):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def parse_date(v):
    s = str(v or "").strip()
    if not s:
        return None
    s = s.split(" ")[0]
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

def days_between(a, b):
    try:
        d1 = datetime.strptime(a, "%Y-%m-%d").date()
        d2 = datetime.strptime(b, "%Y-%m-%d").date()
        n = (d2 - d1).days
        return n if 0 <= n <= 120 else None
    except Exception:
        return None

def header_map(row):
    m = {}
    for i, c in enumerate(row):
        k = norm(c)
        if k and k not in m:
            m[k] = i
    return m

def cell(row, idx):
    if idx is None or idx >= len(row):
        return ""
    return row[idx]

def pick(hmap, *names):
    for n in names:
        if norm(n) in hmap:
            return hmap[norm(n)]
    return None

def txt(row, idx, default):
    v = (cell(row, idx) or "").strip()
    return v if v else default

def phone_key(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-9:] if len(d) >= 9 else ""

DIR_MAP = {}
for _p in os.environ.get("RS_DIR_MAP", "").split(","):
    if "=" in _p:
        _a, _b = _p.split("=", 1)
        DIR_MAP[norm(_a)] = norm(_b)

def map_dir(d):
    n = norm(d)
    return DIR_MAP.get(n, n)

def map_targ(t):
    n = norm(t)
    return "organic" if (not n or n == norm(NA) or n in ("-", "—")) else n

# ══════════════════════════ GOOGLE SHEETS ═══════════════════════════════
_GC = None

def _gc():
    global _GC
    if _GC is None:
        creds = Credentials.from_service_account_file(
            SA_JSON, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        _GC = gspread.authorize(creds)
    return _GC

def _find_ws(book, tab):
    if not tab:
        return book.get_worksheet(0)
    want = norm(tab).replace(" ", "")
    for ws in book.worksheets():
        if norm(ws.title).replace(" ", "") == want:
            return ws
    log.error("❌ Варақ топилмади: '%s' | Мавжудлари: %s",
              tab, [w.title for w in book.worksheets()])
    return None

def read_tab(sheet_id, tab=None):
    if not sheet_id:
        return []
    for attempt in range(3):
        try:
            ws = _find_ws(_gc().open_by_key(sheet_id), tab)
            if ws is None:
                return []
            vals = ws.get_all_values()
            if sum(1 for r in vals[:50] for c in r if "#REF" in str(c)) > 5:
                log.warning("⚠️ '%s' да #REF! кўп — IMPORTRANGE рухсати текширилсин", tab)
            return vals
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                log.warning("429 — 30 сония кутаман (%s)", tab)
                time.sleep(30)
                continue
            log.error("Ўқиб бўлмади %s / %s: %s", str(sheet_id)[:8], tab, e)
            return []
    return []

# ══════════════════════════ ЛИДЛАР ══════════════════════════════════════
def parse_leads(values, direction):
    if len(values) < 2:
        return []
    h = header_map(values[0])
    ix = {
        "date":     pick(h, "дата", "сана"),
        "creative": pick(h, "креатив"),
        "plat":     pick(h, "плат", "платформа"),
        "sell":     pick(h, "продажник", "сотувчи"),
        "stat":     pick(h, "статус"),
        "reg":      pick(h, "регистратор"),
        "targ":     pick(h, "таргетолог"),
        "form":     pick(h, "форма"),
    }
    out = []
    for r in values[1:]:
        d = parse_date(cell(r, ix["date"]))
        if not d:
            continue
        st = norm(cell(r, ix["stat"]))
        out.append({
            "date": d, "direction": direction,
            "creative":    txt(r, ix["creative"], NA),
            "source":      txt(r, ix["plat"], NA),
            "seller":      txt(r, ix["sell"], "— не передан —"),
            "registrator": txt(r, ix["reg"], NA),
            "targetolog":  txt(r, ix["targ"], NA),
            "form":        txt(r, ix["form"], NA),
            "is_kval":  st.startswith("успеш"),
            "is_dirty": st.startswith("дубл") or st.startswith("некач"),
            "spend": 0.0, "from_meta": 0,
            "camp": None, "adset": None,
        })
    return out

def load_leads():
    rows = []
    for i, tab in enumerate(LEAD_TABS):
        hot = parse_leads(read_tab(SH_LEADS, tab), tab)
        log.info("  лид/черновик '%s': %d", tab, len(hot))
        hd = {r["date"] for r in hot}
        atab = LEAD_TABS_ARC[i] if i < len(LEAD_TABS_ARC) else tab
        arc = parse_leads(read_tab(SH_LEADS_ARC, atab), tab)
        kept = [r for r in arc if r["date"] not in hd]
        if arc:
            log.info("  лид/архив   '%s': %d (олинди %d)", atab, len(arc), len(kept))
        rows += hot + kept
    log.info("Лидлар жами: %d", len(rows))
    return rows

# ══════════════════════════ БУЮРТМАЛАР ══════════════════════════════════
def parse_orders(values):
    if len(values) < 2:
        return []
    h = header_map(values[0])
    ix = {
        "date":     pick(h, "дата", "сана"),
        "creative": pick(h, "креатив"),
        "dir":      pick(h, "проект эски", "проект"),
        "sell":     pick(h, "сотувчи", "продажник"),
        "prod":     pick(h, "товар"),
        "sum":      pick(h, "сумма"),
        "reg":      pick(h, "регион"),
        "stat":     pick(h, "статус"),
        "src":      pick(h, "источник"),
        "targ":     pick(h, "таргетолог"),
        "rop":      pick(h, "роплар", "проект роп"),
        "form":     pick(h, "форма"),
        "phone":    pick(h, "телефон"),
        "dsale":    pick(h, "дата продаж"),
    }
    out = []
    for r in values[1:]:
        d = parse_date(cell(r, ix["date"]))
        if not d:
            continue
        amount = _num(cell(r, ix["sum"]))
        if amount == 0 and not (cell(r, ix["prod"]) or "").strip():
            continue
        sold = norm(cell(r, ix["stat"])).startswith("успеш")
        ds = parse_date(cell(r, ix["dsale"]))
        out.append({
            "date": d,
            "direction":  txt(r, ix["dir"], NA),
            "creative":   txt(r, ix["creative"], NA),
            "seller":     txt(r, ix["sell"], NA),
            "product":    txt(r, ix["prod"], NA),
            "region":     txt(r, ix["reg"], NA),
            "source":     txt(r, ix["src"], NA),
            "targetolog": txt(r, ix["targ"], NA),
            "rop":        txt(r, ix["rop"], NA),
            "form":       txt(r, ix["form"], NA),
            "phone": phone_key(cell(r, ix["phone"])),
            "deal_days": days_between(d, ds) if (sold and ds) else None,
            "fact1": amount,
            "fact2": amount if sold else 0.0,
            "sold": 1 if sold else 0,
            "is_new": 0, "from_meta": 0,
            "camp": None, "adset": None,
        })
    return out

def load_orders():
    hot = parse_orders(read_tab(SH_ORDERS, ORDERS_TAB or None))
    hd = {r["date"] for r in hot}
    arc = [r for r in parse_orders(read_tab(SH_ORDERS_ARC, ORDERS_TAB or None))
           if r["date"] not in hd]
    log.info("Буюртмалар: черновик=%d, архив=%d", len(hot), len(arc))
    rows = hot + arc
    seen = set()
    for r in sorted(rows, key=lambda x: x["date"]):
        if not r["phone"]:
            r["is_new"] = 1
        elif r["phone"] not in seen:
            seen.add(r["phone"])
            r["is_new"] = 1
    return rows

# ══════════════════════════ БЮДЖЕТ ══════════════════════════════════════
def load_budget():
    v = read_tab(SH_BUDGET, BUDGET_TAB or None)
    if len(v) < 3:
        log.warning("Бюджет шитси бўш")
        return {}
    groups, cur = [], ""
    for c in v[0]:
        if str(c).strip():
            cur = str(c).strip()
        groups.append(cur)
    targs = v[1]
    out = defaultdict(float)
    for r in v[2:]:
        d = parse_date(cell(r, 0))
        if not d:
            continue
        for i in range(1, len(r)):
            t = norm(cell(targs, i))
            g = map_dir(groups[i]) if i < len(groups) else ""
            if t and g:
                val = _num(r[i])
                if val:
                    out[(d, g, t)] += val
    log.info("Бюджет: %d ёзув, жами $%.2f", len(out), sum(out.values()))
    return dict(out)

# ══════════════════════════ META ════════════════════════════════════════
def load_meta():
    """{(сана, ad_lower): метрика} ва {(сана, ad_lower): (кампания, адсет)}"""
    if not META_ON or MS is None:
        log.info("Meta ўчирилган ёки модул топилмади")
        return {}, {}
    try:
        cache = MS.refresh(META_DAYS, MIN_DATE or None)
    except Exception as e:
        log.error("Meta хато: %s", str(e)[:200])
        try:
            cache = MS.load_cache()
        except Exception:
            cache = {}
    m, hier = {}, {}
    for d, rows in (cache or {}).items():
        if MIN_DATE and d < MIN_DATE:
            continue
        for r in rows:
            k = (d, norm(r.get("ad")))
            a = m.setdefault(k, {"spend": 0.0, "impr": 0, "reach": 0,
                                 "clicks": 0, "mleads": 0})
            a["spend"]  += r.get("spend", 0)
            a["impr"]   += r.get("impr", 0)
            a["reach"]  += r.get("reach", 0)
            a["clicks"] += r.get("clicks", 0)
            a["mleads"] += r.get("leads", 0)
            prev = hier.get(k)
            if prev is None or r.get("spend", 0) > prev[2]:
                hier[k] = (r.get("camp", "—"), r.get("adset", "—"), r.get("spend", 0))
    log.info("Meta: %d кун-креатив, жами $%.2f",
             len(m), sum(x["spend"] for x in m.values()))
    return m, {k: (v[0], v[1]) for k, v in hier.items()}

def enrich(rows, hier):
    """Ҳар қаторга Meta иерархиясини (кампания / адсет) қўшади."""
    by_name = {}
    for (d, ad), v in hier.items():
        by_name.setdefault(ad, v)
    n = 0
    for r in rows:
        c = norm(r["creative"])
        v = hier.get((r["date"], c)) or by_name.get(c)
        if v:
            r["camp"], r["adset"] = v
            n += 1
        else:
            r["camp"] = "— вне Meta —"
            r["adset"] = "— вне Meta —"
    return n

def allocate_spend(leads, budget, meta):
    """1) Meta'да ном мос келса — аниқ харажат. 2) Акс ҳолда бюджет шитси."""
    by_ad = defaultdict(list)
    for L in leads:
        by_ad[(L["date"], norm(L["creative"]))].append(L)

    meta_used, matched = 0.0, 0
    for k, rows in by_ad.items():
        m = meta.get(k)
        if m and m["spend"] > 0:
            matched += 1
            meta_used += m["spend"]
            per = m["spend"] / len(rows)
            for L in rows:
                L["spend"] = per
                L["from_meta"] = 1

    grp = defaultdict(list)
    for L in leads:
        if not L["from_meta"]:
            grp[(L["date"], map_dir(L["direction"]), map_targ(L["targetolog"]))].append(L)
    bud_used = 0.0
    for key, rows in grp.items():
        sp = budget.get(key, 0.0)
        if sp:
            bud_used += sp
            per = sp / len(rows)
            for L in rows:
                L["spend"] = per

    meta_total = sum(v["spend"] for v in meta.values())
    log.info("Харажат: Meta $%.2f / $%.2f (%d кун-креатив) + бюджет $%.2f",
             meta_used, meta_total, matched, bud_used)
    if meta_total > 0:
        pct = meta_used / meta_total * 100
        log.info("Meta мослиги: %.1f%%", pct)
        if pct < 70:
            miss = defaultdict(float)
            for (d, ad), v in meta.items():
                if (d, ad) not in by_ad:
                    miss[ad] += v["spend"]
            log.warning("⚠️ Мосланмаган Meta креативлари (энг катталари):")
            for ad, s in sorted(miss.items(), key=lambda x: -x[1])[:15]:
                log.warning("   $%8.2f  %s", s, ad)
    return leads

# ══════════════════════════ КУРС ════════════════════════════════════════
def usd_rate():
    try:
        req = urllib.request.Request("https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/",
                                     headers={"User-Agent": "roistat/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        log.info("Курс (ЦБ): %s", d[0]["Rate"])
        return float(d[0]["Rate"]), d[0].get("Date", "")
    except Exception as e:
        log.error("Курс олинмади (%s) — резерв %s", str(e)[:80], USD_FALLBACK)
        return USD_FALLBACK, "резерв"

# ══════════════════════════ КЕСИМЛАР ════════════════════════════════════
# id, ном, лид майдони, буюртма майдони, ота-кесим
DIMS = [
    ("camp",        "Кампании",    "camp",        "camp",        None),
    ("adset",       "Адсеты",      "adset",       "adset",       "camp"),
    ("creative",    "Объявления",  "creative",    "creative",    "adset"),
    ("targetolog",  "Таргетолог",  "targetolog",  "targetolog",  None),
    ("form",        "Форма",       "form",        "form",        None),
    ("source",      "Источник",    "source",      "source",      None),
    ("product",     "Товар",       None,          "product",     None),
    ("region",      "Регион",      None,          "region",      None),
    ("rop",         "РОП",         None,          "rop",         None),
    ("seller",      "Продавец",    "seller",      "seller",      None),
    ("registrator", "Регистратор", "registrator", None,          None),
]
TABS = [{"id": d, "label": l, "parent": p} for d, l, _, _, p in DIMS] + \
       [{"id": "days", "label": "Дни", "parent": None}]

F = ["leads", "clean", "kval", "spend", "orders", "fact1", "fact2", "sold",
     "newc", "dsum", "dcnt", "mrev", "impr", "reach", "clicks", "mleads"]

def _empty():
    return dict((f, 0) for f in F)

def _row_out(acc):
    return [{"d": d, "k": k, "p": p,
             **{f: (round(v[f], 2) if isinstance(v[f], float) else v[f]) for f in F}}
            for (d, k, p), v in acc.items()]

def build_payload(leads, orders, meta, meta_hier, daily_from):
    def bkt(d):
        return d if d >= daily_from else d[:8] + "01"

    dims = {}
    for did, label, lf, of, par in DIMS:
        acc = defaultdict(_empty)
        if lf:
            for L in leads:
                p = (L.get(par) or "") if par else ""
                a = acc[(bkt(L["date"]), L[lf], p)]
                a["leads"] += 1
                if not L["is_dirty"]:
                    a["clean"] += 1
                if L["is_kval"]:
                    a["kval"] += 1
                a["spend"] += L["spend"]
        if of:
            for O in orders:
                p = (O.get(par) or "") if par else ""
                a = acc[(bkt(O["date"]), O[of], p)]
                a["orders"] += 1
                a["fact1"] += O["fact1"]
                a["fact2"] += O["fact2"]
                a["sold"]  += O["sold"]
                if O["sold"]:
                    a["newc"] += O["is_new"]
                    if O["from_meta"]:
                        a["mrev"] += O["fact2"]
                if O["deal_days"] is not None:
                    a["dsum"] += O["deal_days"]
                    a["dcnt"] += 1
        if did in ("camp", "adset", "creative"):
            lvl = {"camp": 0, "adset": 1, "creative": 2}[did]
            for (d, ad), m in meta.items():
                h = meta_hier.get((d, ad))
                if not h:
                    continue
                key = (h[0], h[1], ad)[lvl]
                pk = "" if lvl == 0 else h[lvl - 1]
                a = acc[(bkt(d), key, pk)]
                a["impr"]   += m["impr"]
                a["reach"]  += m["reach"]
                a["clicks"] += m["clicks"]
                a["mleads"] += m["mleads"]
        dims[did] = _row_out(acc)

    acc = defaultdict(_empty)
    for L in leads:
        a = acc[(bkt(L["date"]), bkt(L["date"]), "")]
        a["leads"] += 1
        if not L["is_dirty"]:
            a["clean"] += 1
        if L["is_kval"]:
            a["kval"] += 1
        a["spend"] += L["spend"]
    for O in orders:
        a = acc[(bkt(O["date"]), bkt(O["date"]), "")]
        a["orders"] += 1
        a["fact1"] += O["fact1"]
        a["fact2"] += O["fact2"]
        a["sold"]  += O["sold"]
        if O["sold"]:
            a["newc"] += O["is_new"]
            if O["from_meta"]:
                a["mrev"] += O["fact2"]
        if O["deal_days"] is not None:
            a["dsum"] += O["deal_days"]
            a["dcnt"] += 1
    for (d, ad), m in meta.items():
        a = acc[(bkt(d), bkt(d), "")]
        a["impr"]   += m["impr"]
        a["reach"]  += m["reach"]
        a["clicks"] += m["clicks"]
        a["mleads"] += m["mleads"]
    dims["days"] = _row_out(acc)

    log.info("Кесим қаторлари: %d", sum(len(v) for v in dims.values()))
    return dims

# ══════════════════════════ НАЗОРАТ ═════════════════════════════════════
def guard(leads, orders):
    cur = {"leads": defaultdict(int), "orders": defaultdict(int)}
    for L in leads:
        cur["leads"][L["date"]] += 1
    for O in orders:
        cur["orders"][O["date"]] += 1
    cur = {k: dict(v) for k, v in cur.items()}

    old = {}
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            pass

    alerts = []
    for src in ("leads", "orders"):
        for d, n in (old.get(src) or {}).items():
            if MIN_DATE and d < MIN_DATE:
                continue
            if cur[src].get(d, 0) < n:
                alerts.append("%s · %s: %d → %d" % (src, d, n, cur[src].get(d, 0)))

    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
    except Exception as e:
        log.error("snapshot: %s", e)

    if alerts:
        msg = "⚠️ ROISTAT: маълумот камайди!\n\n" + "\n".join(alerts[:25])
        log.error(msg.replace("\n", " | "))
        if TG_TOKEN:
            for aid in TG_ADMINS:
                try:
                    u = "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN
                    b = urllib.parse.urlencode({"chat_id": aid, "text": msg[:4000]}).encode()
                    urllib.request.urlopen(urllib.request.Request(u, data=b), timeout=15)
                except Exception as e:
                    log.error("TG: %s", e)
    return alerts

# ══════════════════════════ MAIN ════════════════════════════════════════
if __name__ == "__main__":
    if not SH_LEADS or not SH_ORDERS:
        sys.exit("❌ RS_SHEET_LEADS ва RS_SHEET_ORDERS керак (start.sh)")

    from roistat_html import generate_html, push_github

    log.info("=== ROISTAT v5 ===")
    leads  = load_leads()
    orders = load_orders()

    if MIN_DATE:
        n1, n2 = len(leads), len(orders)
        leads  = [r for r in leads  if r["date"] >= MIN_DATE]
        orders = [r for r in orders if r["date"] >= MIN_DATE]
        log.info("Сана филтри %s: лид %d→%d, буюртма %d→%d",
                 MIN_DATE, n1, len(leads), n2, len(orders))

    meta, meta_hier = load_meta()
    enrich(leads, meta_hier)
    enrich(orders, meta_hier)

    meta_ads = set(ad for (_, ad) in meta.keys())
    for O in orders:
        O["from_meta"] = 1 if norm(O["creative"]) in meta_ads else 0

    budget = load_budget()
    if MIN_DATE:
        budget = {k: v for k, v in budget.items() if k[0] >= MIN_DATE}
    allocate_spend(leads, budget, meta)

    if not leads and not orders:
        sys.exit("❌ Маълумот ўқилмади — шитс ҳаволаси ва рухсатни текширинг")

    guard(leads, orders)

    rate, rate_date = usd_rate()
    daily_from = (datetime.now(TZ) - timedelta(days=DAILY_DAYS)).strftime("%Y-%m-%d")
    dims = build_payload(leads, orders, meta, meta_hier, daily_from)

    all_d = [r["date"] for r in leads] + [r["date"] for r in orders]
    min_d, max_d = (min(all_d), max(all_d)) if all_d else (None, None)

    html = generate_html(dims, TABS, rate, rate_date, min_d, max_d,
                         daily_from, FRESH_DAYS, TZ)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML: %d КБ", len(html) // 1024)
    push_github(html, GH_TOKEN, GH_USER, GH_REPO, GH_FILE, TZ)
    log.info("✅ Тайёр. Лид=%d, Буюртма=%d, Давр: %s — %s",
             len(leads), len(orders), min_d, max_d)