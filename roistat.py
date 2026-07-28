"""
ROISTAT — Sinolife / Zextra сквозная аналитика  (v4)
Sheets (лидлар черновик+архив, hisobot черновик+архив, бюджет) → HTML → GitHub Pages

Ишга тушириш:
    cd /root/roistat && source start.sh && python3 roistat.py
"""

import os, sys, json, base64, ssl, time, logging
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

TZ = timezone(timedelta(hours=5))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ══════════════════════════════ КОНФИГ (start.sh) ═════════════════════════
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

# ══════════════════════════════ ЁРДАМЧИЛАР ════════════════════════════════
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

NA = "— не указано —"

DIR_MAP = {}
for _pair in os.environ.get("RS_DIR_MAP", "").split(","):
    if "=" in _pair:
        _a, _b = _pair.split("=", 1)
        DIR_MAP[norm(_a)] = norm(_b)

def map_dir(d):
    n = norm(d)
    return DIR_MAP.get(n, n)

def map_targ(t):
    n = norm(t)
    if not n or n == norm(NA) or n in ("-", "—"):
        return "organic"
    return n

# ══════════════════════════════ GOOGLE SHEETS ═════════════════════════════
_GC = None

def _gc():
    global _GC
    if _GC is None:
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
        _GC = gspread.authorize(creds)
    return _GC

def _find_ws(book, tab):
    if not tab:
        return book.get_worksheet(0)
    want = norm(tab).replace(" ", "")
    sheets = book.worksheets()
    for ws in sheets:
        if norm(ws.title).replace(" ", "") == want:
            return ws
    log.error("❌ Варақ топилмади: '%s' | Мавжудлари: %s",
              tab, [w.title for w in sheets])
    return None

def read_tab(sheet_id, tab=None):
    if not sheet_id:
        return []
    for attempt in range(3):
        try:
            book = _gc().open_by_key(sheet_id)
            ws = _find_ws(book, tab)
            if ws is None:
                return []
            vals = ws.get_all_values()
            bad = sum(1 for r in vals[:50] for c in r if "#REF" in str(c))
            if bad > 5:
                log.warning("⚠️ '%s' да #REF! кўп — IMPORTRANGE рухсати "
                            "берилмаган бўлиши мумкин", tab)
            return vals
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                log.warning("429 — 30 сония кутаман (%s / %s)", sheet_id[:8], tab)
                time.sleep(30)
                continue
            log.error("Ўқиб бўлмади %s / %s: %s", sheet_id[:8], tab, e)
            return []
    return []

# ══════════════════════════════ ЛИДЛАР ════════════════════════════════════
def parse_leads(values, direction):
    if len(values) < 2:
        return []
    h = header_map(values[0])
    i_date  = pick(h, "дата", "сана")
    i_creat = pick(h, "креатив")
    i_plat  = pick(h, "плат", "платформа")
    i_sell  = pick(h, "продажник", "сотувчи")
    i_stat  = pick(h, "статус")
    i_reg   = pick(h, "регистратор")
    i_targ  = pick(h, "таргетолог")
    i_form  = pick(h, "форма")
    out = []
    for r in values[1:]:
        d = parse_date(cell(r, i_date))
        if not d:
            continue
        st = norm(cell(r, i_stat))
        out.append({
            "date":        d,
            "direction":   direction,
            "creative":    txt(r, i_creat, NA),
            "source":      txt(r, i_plat,  NA),
            "seller":      txt(r, i_sell,  "— не передан —"),
            "registrator": txt(r, i_reg,   NA),
            "targetolog":  txt(r, i_targ,  NA),
            "form":        txt(r, i_form,  NA),
            "is_kval":     st.startswith("успеш"),
            "is_dirty":    st.startswith("дубл") or st.startswith("некач"),
            "spend":       0.0,
        })
    return out

def load_leads():
    """Ҳар варақ АЛОҲИДА бирлаштирилади: черновикда бор сана ўша варақнинг
    архивидан олинмайди (варақлар бир-бирига аралашмайди)."""
    all_rows = []
    for i, tab in enumerate(LEAD_TABS):
        hot = parse_leads(read_tab(SH_LEADS, tab), tab)
        log.info("  лид/черновик '%s': %d", tab, len(hot))
        hot_dates = {r["date"] for r in hot}

        arc_tab = LEAD_TABS_ARC[i] if i < len(LEAD_TABS_ARC) else tab
        arc = parse_leads(read_tab(SH_LEADS_ARC, arc_tab), tab)
        kept = [r for r in arc if r["date"] not in hot_dates]
        if arc:
            log.info("  лид/архив   '%s': %d (олинди %d)", arc_tab, len(arc), len(kept))
        all_rows += hot + kept

    log.info("Лидлар жами: %d", len(all_rows))
    return all_rows

# ══════════════════════════════ БУЮРТМАЛАР ════════════════════════════════
def parse_orders(values):
    if len(values) < 2:
        return []
    h = header_map(values[0])
    i_date  = pick(h, "дата", "сана")
    i_creat = pick(h, "креатив")
    i_dir   = pick(h, "проект эски", "проект")
    i_sell  = pick(h, "сотувчи", "продажник")
    i_prod  = pick(h, "товар")
    i_qty   = pick(h, "кол", "сони")
    i_sum   = pick(h, "сумма")
    i_reg   = pick(h, "регион")
    i_stat  = pick(h, "статус")
    i_src   = pick(h, "источник")
    i_targ  = pick(h, "таргетолог")
    i_rop   = pick(h, "роплар", "проект роп")
    i_form  = pick(h, "форма")
    out = []
    for r in values[1:]:
        d = parse_date(cell(r, i_date))
        if not d:
            continue
        amount = _num(cell(r, i_sum))
        if amount == 0 and not (cell(r, i_prod) or "").strip():
            continue
        sold = norm(cell(r, i_stat)).startswith("успеш")
        out.append({
            "date":       d,
            "direction":  txt(r, i_dir,   NA),
            "creative":   txt(r, i_creat, NA),
            "seller":     txt(r, i_sell,  NA),
            "product":    txt(r, i_prod,  NA),
            "qty":        _num(cell(r, i_qty)) or 1,
            "region":     txt(r, i_reg,   NA),
            "source":     txt(r, i_src,   NA),
            "targetolog": txt(r, i_targ,  NA),
            "rop":        txt(r, i_rop,   NA),
            "form":       txt(r, i_form,  NA),
            "fact1":      amount,
            "fact2":      amount if sold else 0.0,
            "sold":       1 if sold else 0,
        })
    return out

def load_orders():
    hot = parse_orders(read_tab(SH_ORDERS, ORDERS_TAB or None))
    hot_dates = {r["date"] for r in hot}
    arc = [r for r in parse_orders(read_tab(SH_ORDERS_ARC, ORDERS_TAB or None))
           if r["date"] not in hot_dates]
    log.info("Буюртмалар: черновик=%d, архив=%d", len(hot), len(arc))
    return hot + arc

# ══════════════════════════════ БЮДЖЕТ ════════════════════════════════════
def load_budget():
    v = read_tab(SH_BUDGET, BUDGET_TAB or None)
    if len(v) < 3:
        log.warning("Бюджет шитси бўш ёки топилмади")
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
            if not t or not g:
                continue
            val = _num(r[i])
            if val:
                out[(d, g, t)] += val
    log.info("Бюджет: %d ёзув, жами $%.2f", len(out), sum(out.values()))
    return dict(out)

def allocate_spend(leads, budget):
    if not budget:
        return leads
    groups = defaultdict(list)
    for L in leads:
        groups[(L["date"], map_dir(L["direction"]), map_targ(L["targetolog"]))].append(L)

    matched, used = 0, 0.0
    for key, rows in groups.items():
        spend = budget.get(key, 0.0)
        if spend:
            matched += 1
            used += spend
            per = spend / len(rows)
            for L in rows:
                L["spend"] = per

    total = sum(budget.values())
    pct = (used / total * 100) if total else 0
    log.info("Бюджет тақсимланди: $%.2f / $%.2f (%.1f%%) · гуруҳ %d/%d",
             used, total, pct, matched, len(groups))

    if pct < 80:
        lost = sorted(((v, k) for k, v in budget.items() if k not in groups),
                      reverse=True)[:10]
        log.warning("⚠️ Бюджетнинг %.1f%% и мосланмади. Энг катталари:", 100 - pct)
        for v, k in lost:
            log.warning("   %s | %s | %s → $%.2f", k[0], k[1], k[2], v)
        log.warning("  ЛИД йўналишлари   : %s",
                    sorted({map_dir(L["direction"]) for L in leads}))
        log.warning("  БЮДЖЕТ йўналишлари: %s", sorted({g for _, g, _ in budget}))
    return leads

# ══════════════════════════════ КУРС ══════════════════════════════════════
def usd_rate():
    try:
        req = urllib.request.Request(
            "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/",
            headers={"User-Agent": "roistat/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        rate = float(data[0]["Rate"])
        log.info("Курс (ЦБ): %s", rate)
        return rate, data[0].get("Date", "")
    except Exception as e:
        log.error("Курс олинмади (%s) — заҳира: %s", e, USD_FALLBACK)
        return USD_FALLBACK, "резерв"

# ══════════════════════════════ КЕСИМЛАР ══════════════════════════════════
# (id, ном, лид майдони, буюртма майдони)
DIMS = [
    ("targetolog",  "Таргетолог",  "targetolog",  "targetolog"),
    ("creative",    "Креатив",     "creative",    "creative"),
    ("form",        "Форма",       "form",        "form"),
    ("source",      "Источник",    "source",      "source"),
    ("product",     "Товар",       None,          "product"),
    ("region",      "Регион",      None,          "region"),
    ("rop",         "РОП",         None,          "rop"),
    ("seller",      "Продавец",    "seller",      "seller"),
    ("registrator", "Регистратор", "registrator", None),
]

TABS = [{"id": d, "label": lab} for d, lab, _, _ in DIMS] + \
       [{"id": "days", "label": "Дни"}]

def _empty():
    return {"leads": 0, "clean": 0, "kval": 0, "spend": 0.0,
            "orders": 0, "fact1": 0.0, "fact2": 0.0, "sold": 0}

def build_payload(leads, orders, daily_from):
    def bkt(d):
        return d if d >= daily_from else d[:8] + "01"

    dims = {}
    for did, label, lf, of in DIMS:
        acc = defaultdict(_empty)
        if lf:
            for L in leads:
                a = acc[(bkt(L["date"]), L[lf])]
                a["leads"] += 1
                if not L["is_dirty"]:
                    a["clean"] += 1
                if L["is_kval"]:
                    a["kval"] += 1
                a["spend"] += L["spend"]
        if of:
            for O in orders:
                a = acc[(bkt(O["date"]), O[of])]
                a["orders"] += 1
                a["fact1"]  += O["fact1"]
                a["fact2"]  += O["fact2"]
                a["sold"]   += O["sold"]
        dims[did] = [{"d": d, "k": k, **{kk: round(vv, 2) for kk, vv in m.items()}}
                     for (d, k), m in acc.items()]

    acc = defaultdict(_empty)
    for L in leads:
        a = acc[bkt(L["date"])]
        a["leads"] += 1
        if not L["is_dirty"]:
            a["clean"] += 1
        if L["is_kval"]:
            a["kval"] += 1
        a["spend"] += L["spend"]
    for O in orders:
        a = acc[bkt(O["date"])]
        a["orders"] += 1
        a["fact1"]  += O["fact1"]
        a["fact2"]  += O["fact2"]
        a["sold"]   += O["sold"]
    dims["days"] = [{"d": d, "k": d, **{kk: round(vv, 2) for kk, vv in m.items()}}
                    for d, m in acc.items()]

    log.info("Кесим қаторлари: %d", sum(len(v) for v in dims.values()))
    return dims

# ══════════════════════════════ НАЗОРАТ ═══════════════════════════════════
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
            old = {}

    alerts = []
    for src in ("leads", "orders"):
        for d, n_old in (old.get(src) or {}).items():
            if MIN_DATE and d < MIN_DATE:
                continue
            n_new = cur[src].get(d, 0)
            if n_new < n_old:
                alerts.append("%s · %s: %d → %d" % (src, d, n_old, n_new))

    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
    except Exception as e:
        log.error("snapshot ёзилмади: %s", e)

    if alerts:
        msg = "⚠️ ROISTAT: маълумот камайди!\n\n" + "\n".join(alerts[:25])
        log.error(msg.replace("\n", " | "))
        if TG_TOKEN:
            for aid in TG_ADMINS:
                try:
                    url = "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN
                    body = urllib.parse.urlencode(
                        {"chat_id": aid, "text": msg[:4000]}).encode()
                    urllib.request.urlopen(
                        urllib.request.Request(url, data=body), timeout=15)
                except Exception as e:
                    log.error("TG огоҳлантириш: %s", e)
    return alerts

# ══════════════════════════════ HTML ══════════════════════════════════════
HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ROISTAT — Сквозная аналитика</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0a0a;--card:#151515;--line:#262626;--txt:#f5f5f5;--mut:#888;--mut2:#555;
--green:#22c55e;--greenbg:#14331f;--greentx:#86efac;--amber:#eab308;--amberbg:#3a2f0a;
--ambertx:#fde68a;--red:#ef4444;--redbg:#3a1414;--redtx:#fca5a5;--accent:#3b82f6}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--txt);padding:18px;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{max-width:1500px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px}
h1{font-size:19px;font-weight:700;letter-spacing:-.02em}
.sub{color:var(--mut);font-size:13px}
.sub b{color:#bbb;font-weight:600}
h2.sec{font-size:12px;letter-spacing:.08em;color:var(--mut);font-weight:600;
text-transform:uppercase;margin:22px 0 12px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:14px}
.btn{background:var(--card);border:1px solid var(--line);color:var(--mut);padding:9px 16px;
border-radius:10px;font-size:14px;cursor:pointer;transition:all .15s;font-weight:500}
.btn:hover{border-color:#3a3a3a;color:#bbb}
.btn.on{background:#fff;color:#0a0a0a;border-color:#fff;font-weight:600}
.cur{margin-left:auto;display:flex;gap:6px}
.dt{background:var(--card);border:1px solid var(--line);color:var(--txt);padding:8px 10px;
border-radius:9px;font-size:13px;color-scheme:dark}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin-top:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 17px}
.kpi .lab{color:var(--mut);font-size:12px;margin-bottom:6px}
.kpi .val{font-size:25px;font-weight:700;letter-spacing:-.02em;word-break:break-word}
.kpi .unit{color:var(--mut);font-size:12px;margin-top:2px}
.kpi .delta{font-size:12px;margin-top:5px;font-weight:600}
.up{color:var(--greentx)}.down{color:var(--redtx)}.flat{color:var(--mut2)}
.kpi.hero{border-color:#1f3a26}
.charts{display:grid;grid-template-columns:1fr 1.4fr;gap:12px;margin-top:14px}
.chbox{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:14px 16px}
.chbox .t{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}
.chwrap{position:relative;height:230px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:15px;
padding:4px 2px;overflow-x:auto;margin-top:6px}
table{width:100%;border-collapse:collapse;min-width:1120px}
th{text-align:right;color:var(--mut);font-size:11px;font-weight:600;padding:12px;
letter-spacing:.03em;text-transform:uppercase;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
td{padding:12px;text-align:right;font-size:14px;border-bottom:1px solid #1d1d1d;white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr{transition:background .12s}
tbody tr:hover{background:#1a1a1a}
tbody tr.fresh td{opacity:.55}
tbody tr.mon td{color:#9aa}
td.name{font-weight:600}
td.rank{color:var(--mut2);font-size:12px;width:38px;text-align:center}
td.pos{color:var(--greentx)}
tfoot td{padding:13px 12px;font-weight:700;font-size:14px;background:#101010;
border-top:2px solid var(--line);text-align:right}
tfoot td:first-child,tfoot td:nth-child(2){text-align:left}
.badge{display:inline-block;padding:3px 10px;border-radius:7px;font-size:12px;font-weight:600}
.b-green{background:var(--greenbg);color:var(--greentx)}
.b-amber{background:var(--amberbg);color:var(--ambertx)}
.b-red{background:var(--redbg);color:var(--redtx)}
.note{color:var(--mut);font-size:13px;margin-top:14px;padding:12px 16px;background:#121212;
border-left:3px solid var(--accent);border-radius:0 8px 8px 0}
.warn{border-left-color:var(--amber)}
.warn b{color:var(--ambertx)}
.foot{color:var(--mut2);font-size:12px;margin-top:22px;text-align:center}
.empty{color:var(--mut);text-align:center;padding:34px;font-size:14px}

/* ─── АДАПТИВ ─── */
@media(max-width:1100px){.charts{grid-template-columns:1fr}}
@media(max-width:860px){
  body{padding:12px}
  h1{font-size:17px}
  .kpis{grid-template-columns:1fr 1fr;gap:9px}
  .kpi{padding:12px 13px;border-radius:13px}
  .kpi .val{font-size:20px}
  .btn{padding:8px 13px;font-size:13px}
  .cur{margin-left:0}
  .chwrap{height:250px}
  .panel{background:transparent;border:none;padding:0;overflow:visible}
  table{min-width:0}
  table,thead,tbody,tfoot,tr,td{display:block}
  thead{display:none}
  tbody tr,tfoot tr{background:var(--card);border:1px solid var(--line);
    border-radius:13px;margin-bottom:10px;padding:4px 0}
  tbody tr:hover{background:var(--card)}
  tbody tr.fresh{opacity:1;border-color:#3a2f0a}
  tbody tr.fresh td{opacity:.7}
  td,tfoot td{display:flex;justify-content:space-between;align-items:center;gap:12px;
    text-align:right;border:none;padding:8px 15px;font-size:14px;white-space:normal;background:none}
  td:before,tfoot td:before{content:attr(data-l);color:var(--mut);font-size:12px;
    text-align:left;flex:0 0 auto;text-transform:uppercase;letter-spacing:.03em}
  td.rank{display:none}
  td.name{font-size:15px;font-weight:700;padding:11px 15px;
    border-bottom:1px solid var(--line);margin-bottom:3px;display:block;text-align:left}
  td.name:before{content:''}
  tfoot tr{border-color:#1f3a26}
  tfoot td:first-child{display:none}
  tfoot td:nth-child(2){display:block;text-align:left;font-size:13px;color:var(--mut);
    border-bottom:1px solid var(--line);margin-bottom:3px}
  tfoot td:nth-child(2):before{content:''}
}
</style></head><body><div class="wrap">

<div class="top">
  <div>
    <h1>📊 ROISTAT — Сквозная аналитика</h1>
    <div class="sub">Sinolife / Zextra · Колл-центр</div>
  </div>
  <div class="sub" style="text-align:right">
    🔄 Обновлено: <b id="upd"></b><br>
    💱 Курс: <b id="rate"></b>
  </div>
</div>

<div class="bar">
  <button class="btn" data-r="today">Сегодня</button>
  <button class="btn on" data-r="all">Все даты</button>
  <input type="date" id="f1" class="dt"><span class="sub">—</span>
  <input type="date" id="f2" class="dt">
  <button class="btn" id="goCustom">Показать</button>
  <div class="cur">
    <button class="btn on" id="cUZS">сум</button>
    <button class="btn" id="cUSD">$</button>
  </div>
</div>

<div id="freshWarn"></div>
<h2 class="sec">Общие показатели <span id="periodLab" style="text-transform:none;letter-spacing:0"></span></h2>
<div class="kpis" id="kpis"></div>

<div class="charts">
  <div class="chbox"><div class="t">Воронка</div>
    <div class="chwrap"><canvas id="chFun"></canvas></div></div>
  <div class="chbox"><div class="t">Динамика: расход и ROMI</div>
    <div class="chwrap"><canvas id="chDay"></canvas></div></div>
</div>

<div class="bar" id="tabs"></div>
<h2 class="sec" id="dimTitle"></h2>
<div class="panel" id="tbl"></div>

<div class="chbox" style="margin-top:12px"><div class="t" id="topT">Топ-5 по выручке</div>
  <div class="chwrap"><canvas id="chTop"></canvas></div></div>

<div class="note" id="hint"></div>
<div class="foot">Источник: Google Sheets (рабочий + архив) · Расход из бюджетного листа ·
Выручка привязана к дате лида</div>
</div>

<script>
var DATA = __PAYLOAD__;
var MODE='uzs', RANGE='all', DIM='targetolog', CF=null, CT=null;
var DIMLAB={}, CH={};
var HAS_LEAD={targetolog:1,creative:1,form:1,source:1,seller:1,registrator:1,days:1};
var MON=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август',
         'Сентябрь','Октябрь','Ноябрь','Декабрь'];

function s2d(s){return new Date(s+'T00:00:00Z')}
function d2s(d){return d.toISOString().slice(0,10)}
function addD(s,n){var d=s2d(s);d.setUTCDate(d.getUTCDate()+n);return d2s(d)}
function mStart(s){return s.slice(0,8)+'01'}
function diffD(a,b){return Math.round((s2d(b)-s2d(a))/86400000)}
function ru(s){var p=s.split('-');return p[2]+'.'+p[1]+'.'+p[0]}
function md(s){var p=s.split('-');return p[2]+'.'+p[1]}
function monLab(s){var p=s.split('-');return MON[parseInt(p[1],10)-1]+' '+p[0]}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function clamp(s){return s<DATA.minDate?DATA.minDate:s}

function period(){
  var t=DATA.today,f,to;
  if(RANGE==='today'){f=t;to=t}
  else if(RANGE==='custom'&&CF&&CT){f=CF;to=CT}
  else{f=DATA.minDate;to=DATA.maxDate}
  f=clamp(f);
  var len=diffD(f,to)+1;
  return {f:f,to:to,pf:addD(f,-len),pt:addD(f,-1)};
}

function agg(dim,f,to){
  var rows=DATA.dims[dim]||[],m={};
  for(var i=0;i<rows.length;i++){var r=rows[i];
    if(r.d<f||r.d>to)continue;
    var a=m[r.k]||(m[r.k]={leads:0,clean:0,kval:0,spend:0,orders:0,fact1:0,fact2:0,sold:0});
    a.leads+=r.leads;a.clean+=r.clean;a.kval+=r.kval;a.spend+=r.spend;
    a.orders+=r.orders;a.fact1+=r.fact1;a.fact2+=r.fact2;a.sold+=r.sold}
  return m;
}
function sumAll(m){
  var a={leads:0,clean:0,kval:0,spend:0,orders:0,fact1:0,fact2:0,sold:0};
  for(var k in m){for(var p in a)a[p]+=m[k][p]}
  return a;
}
function met(a){
  return {spend:a.spend,leads:a.leads,clean:a.clean,kval:a.kval,orders:a.orders,
    fact1:a.fact1,fact2:a.fact2,sold:a.sold,
    quality:a.leads?a.clean/a.leads*100:null,
    cpl:a.leads?a.spend/a.leads:null,
    convK:a.clean?a.kval/a.clean*100:null,
    convS:a.kval?a.sold/a.kval*100:null,
    buyout:a.fact1?a.fact2/a.fact1*100:null,
    cpo:a.sold?a.spend/a.sold:null,
    avg:a.sold?a.fact2/a.sold:null,
    romi:a.spend>0?(a.fact2/DATA.rate)/a.spend:null};
}

function nf(v,d){if(v==null||isNaN(v))return '—';
  return v.toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d})}
function n0(v){return v==null?'—':Math.round(v).toLocaleString('ru-RU')}
function mU(v){if(v==null)return '—';
  return MODE==='usd'?('$'+nf(v,2)):(n0(v*DATA.rate)+' сум')}
function mS(v){if(v==null)return '—';
  return MODE==='usd'?('$'+nf(v/DATA.rate,2)):(n0(v)+' сум')}
function pc(v){return v==null?'—':nf(v,1)+'%'}
function bdg(v,g,a){if(v==null)return '—';
  var c=v>=g?'b-green':v>=a?'b-amber':'b-red';
  return '<span class="badge '+c+'">'+nf(v,1)+'%</span>'}
function romiTx(v){if(v==null)return '<span class="badge b-red">нет расхода</span>';
  var c=v>=3?'b-green':v>=1.5?'b-amber':'b-red';
  return '<span class="badge '+c+'">'+nf(v,2)+'×</span>'}
function delta(cur,prev){
  if(prev==null||prev===0||cur==null)return '<div class="delta flat">—</div>';
  var p=(cur-prev)/Math.abs(prev)*100;
  if(Math.abs(p)<0.5)return '<div class="delta flat">0%</div>';
  var cls=p>0?'up':'down',ar=p>0?'↑':'↓';
  return '<div class="delta '+cls+'">'+ar+' '+nf(Math.abs(p),1)+'%</div>';
}

/* ── KPI ── */
function kpis(p){
  var c=met(sumAll(agg('targetolog',p.f,p.to)));
  var v=met(sumAll(agg('targetolog',p.pf,p.pt)));
  function card(lab,val,unit,d,hero){
    return '<div class="kpi'+(hero?' hero':'')+'"><div class="lab">'+lab+'</div>'+
      '<div class="val">'+val+'</div>'+(unit?'<div class="unit">'+unit+'</div>':'')+(d||'')+'</div>'}
  document.getElementById('kpis').innerHTML=
    card('Расход',mU(c.spend),'',delta(c.spend,v.spend))+
    card('Лиды',n0(c.leads),'чистые: '+n0(c.clean),delta(c.leads,v.leads))+
    card('CPL',mU(c.cpl),'цена лида',delta(v.cpl,c.cpl))+
    card('Квал',n0(c.kval),'конв: '+pc(c.convK),delta(c.kval,v.kval))+
    card('Продажи',n0(c.sold),'выкуп: '+pc(c.buyout),delta(c.sold,v.sold))+
    card('CPO',mU(c.cpo),'цена продажи',delta(v.cpo,c.cpo))+
    card('Средний чек',mS(c.avg),'',delta(c.avg,v.avg))+
    card('Выручка',mS(c.fact2),'ROMI '+(c.romi==null?'—':nf(c.romi,2)+'×'),
         delta(c.fact2,v.fact2),true);
  return c;
}

/* ── Устунлар ── */
function cols(lead){
  var c=[{h:'Расход',f:function(x){return mU(x.spend)}}];
  if(lead)c=c.concat([
    {h:'Лиды',    f:function(x){return n0(x.leads)}},
    {h:'Чистые',  f:function(x){return n0(x.clean)}},
    {h:'Качество',f:function(x){return bdg(x.quality,80,60)}},
    {h:'CPL',     f:function(x){return mU(x.cpl)}},
    {h:'Квал',    f:function(x){return n0(x.kval)}}]);
  return c.concat([
    {h:'Заказы Ф1', f:function(x){return mS(x.fact1)}},
    {h:'Продажи Ф2',f:function(x){return mS(x.fact2)},cls:'pos'},
    {h:'Выкуп',     f:function(x){return bdg(x.buyout,80,60)}},
    {h:'CPO',       f:function(x){return mU(x.cpo)}},
    {h:'Ср.чек',    f:function(x){return mS(x.avg)}},
    {h:'ROMI',      f:function(x){return romiTx(x.romi)}}]);
}

function table(p){
  var m=agg(DIM,p.f,p.to),keys=Object.keys(m);
  var el=document.getElementById('tbl');
  if(!keys.length){el.innerHTML='<div class="empty">Нет данных за этот период</div>';return m}
  var lead=!!HAS_LEAD[DIM],isDays=(DIM==='days'),C=cols(lead);
  keys.sort(isDays?function(a,b){return a<b?1:-1}
                 :function(a,b){return m[b].fact2-m[a].fact2});
  var h='<table><thead><tr><th>#</th><th>'+DIMLAB[DIM]+'</th>';
  for(var j=0;j<C.length;j++)h+='<th>'+C[j].h+'</th>';
  h+='</tr></thead><tbody>';
  for(var i=0;i<keys.length;i++){
    var k=keys[i],x=met(m[k]);
    var isMon=isDays&&k<DATA.dailyFrom;
    var fr=isDays&&!isMon&&k>=DATA.freshFrom;
    var lbl=isDays?(isMon?monLab(k)+' (мес.)':ru(k)):esc(k);
    h+='<tr'+(fr?' class="fresh"':(isMon?' class="mon"':''))+'>'+
       '<td class="rank">'+(i+1)+'</td>'+
       '<td class="name">'+lbl+(fr?' ⏳':'')+'</td>';
    for(var j=0;j<C.length;j++)
      h+='<td data-l="'+C[j].h+'"'+(C[j].cls?' class="'+C[j].cls+'"':'')+'>'+C[j].f(x)+'</td>';
    h+='</tr>';
  }
  var t=met(sumAll(m));
  h+='</tbody><tfoot><tr><td></td><td>ИТОГО</td>';
  for(var j=0;j<C.length;j++){
    var vv=C[j].h==='Качество'?pc(t.quality)
          :C[j].h==='Выкуп'?pc(t.buyout)
          :C[j].h==='ROMI'?(t.romi==null?'—':nf(t.romi,2)+'×')
          :C[j].f(t);
    h+='<td data-l="'+C[j].h+'">'+vv+'</td>';
  }
  h+='</tr></tfoot></table>';
  el.innerHTML=h;
  return m;
}

/* ── Диаграммалар ── */
var GRID='#1d1d1d', TICK='#8a8a8a';
function draw(id,cfg){
  if(!window.Chart)return;
  if(CH[id]){CH[id].destroy();CH[id]=null}
  var el=document.getElementById(id);
  if(el)CH[id]=new Chart(el,cfg);
}
function baseOpts(extra){
  var o={responsive:true,maintainAspectRatio:false,
    plugins:{legend:{labels:{color:TICK,font:{size:11},boxWidth:12}}},
    scales:{x:{ticks:{color:TICK,font:{size:10}},grid:{color:GRID}},
            y:{ticks:{color:TICK,font:{size:10}},grid:{color:GRID}}}};
  if(extra)for(var k in extra)o[k]=extra[k];
  return o;
}

function funnel(c){
  draw('chFun',{type:'bar',
    data:{labels:['Лиды','Чистые','Квал','Заказы','Продажи'],
      datasets:[{data:[c.leads,c.clean,c.kval,c.orders,c.sold],
        backgroundColor:['#3b82f6cc','#06b6d4cc','#eab308cc','#f59e0bcc','#22c55ecc'],
        borderRadius:6,borderSkipped:false}]},
    options:baseOpts({indexAxis:'y',plugins:{legend:{display:false}}})});
}

function dayChart(p){
  var rows=(DATA.dims.days||[]).filter(function(r){return r.d>=p.f&&r.d<=p.to});
  rows.sort(function(a,b){return a.d<b.d?-1:1});
  if(rows.length>62)rows=rows.slice(-62);
  var lab=rows.map(function(r){return r.d<DATA.dailyFrom?monLab(r.d):md(r.d)});
  var sp=rows.map(function(r){return MODE==='usd'?r.spend:r.spend*DATA.rate});
  var rm=rows.map(function(r){return r.spend>0?+((r.fact2/DATA.rate)/r.spend).toFixed(2):null});
  draw('chDay',{data:{labels:lab,datasets:[
      {type:'bar',label:MODE==='usd'?'Расход, $':'Расход, сум',data:sp,
       backgroundColor:'#3b82f688',borderColor:'#3b82f6',yAxisID:'y',borderRadius:4},
      {type:'line',label:'ROMI',data:rm,borderColor:'#22c55e',backgroundColor:'#22c55e',
       yAxisID:'y1',tension:.3,pointRadius:2,spanGaps:true}]},
    options:baseOpts({scales:{
      x:{ticks:{color:TICK,font:{size:9},maxRotation:0,autoSkip:true},grid:{color:GRID}},
      y:{position:'left',ticks:{color:'#7aa7f0',font:{size:9}},grid:{color:GRID}},
      y1:{position:'right',ticks:{color:'#86efac',font:{size:9}},grid:{display:false}}}})});
}

function topChart(m){
  var arr=Object.keys(m).map(function(k){return [k,m[k].fact2]})
            .filter(function(a){return a[1]>0})
            .sort(function(a,b){return b[1]-a[1]}).slice(0,5);
  document.getElementById('topT').textContent='Топ-5 по выручке · '+DIMLAB[DIM];
  if(!arr.length){draw('chTop',{type:'doughnut',data:{labels:[],datasets:[]}});return}
  draw('chTop',{type:'doughnut',
    data:{labels:arr.map(function(a){return a[0].length>26?a[0].slice(0,26)+'…':a[0]}),
      datasets:[{data:arr.map(function(a){return MODE==='usd'?+(a[1]/DATA.rate).toFixed(2):a[1]}),
        backgroundColor:['#22c55e','#06b6d4','#3b82f6','#eab308','#f97316'],
        borderColor:'#151515',borderWidth:2}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{position:'right',labels:{color:TICK,font:{size:11},boxWidth:12}}}}});
}

/* ── Вкладкалар ── */
function tabs(){
  var el=document.getElementById('tabs'),h='';
  for(var i=0;i<DATA.tabs.length;i++){var t=DATA.tabs[i];DIMLAB[t.id]=t.label;
    h+='<button class="btn'+(t.id===DIM?' on':'')+'" data-d="'+t.id+'">'+t.label+'</button>'}
  el.innerHTML=h;
  el.querySelectorAll('button').forEach(function(b){
    b.onclick=function(){DIM=b.dataset.d;render()}});
}

/* ── Рендер ── */
function render(){
  var p=period();
  document.getElementById('periodLab').innerHTML=
    '<span style="color:var(--mut);font-size:13px">· '+ru(p.f)+' — '+ru(p.to)+'</span>';
  document.getElementById('dimTitle').textContent='Анализ по: '+DIMLAB[DIM];
  document.getElementById('freshWarn').innerHTML = p.to>=DATA.freshFrom
    ? '<div class="note warn">⏳ <b>Последние 7 дней ещё не полные</b> — продажи и кассы '+
      'закрываются позже, поэтому низкий ROMI в эти дни — нормально.</div>'
    : '';
  document.getElementById('hint').innerHTML=
    'Качество = чистые ÷ все лиды · Выкуп = Факт2 ÷ Факт1 · ROMI = Факт2 ÷ расход · '+
    'CPL считается от всех лидов, конверсия — от чистых. · '+
    '<span style="color:#9aa">Периоды до '+ru(DATA.dailyFrom)+' сгруппированы по месяцам.</span>';
  document.querySelectorAll('#tabs .btn').forEach(function(b){
    b.classList.toggle('on',b.dataset.d===DIM)});
  var c=kpis(p);
  var m=table(p);
  funnel(c);dayChart(p);topChart(m||{});
}

/* ── Воқеалар ── */
document.querySelectorAll('.bar .btn[data-r]').forEach(function(b){
  b.onclick=function(){RANGE=b.dataset.r;CF=CT=null;
    document.querySelectorAll('.btn[data-r]').forEach(function(x){x.classList.remove('on')});
    b.classList.add('on');render()}});
document.getElementById('goCustom').onclick=function(){
  var a=document.getElementById('f1').value,b=document.getElementById('f2').value;
  if(!a||!b){alert('Выберите обе даты');return}
  if(a>b){var t=a;a=b;b=t}
  CF=a;CT=b;RANGE='custom';
  document.querySelectorAll('.btn[data-r]').forEach(function(x){x.classList.remove('on')});
  render()};
document.getElementById('cUZS').onclick=function(){MODE='uzs';
  this.classList.add('on');document.getElementById('cUSD').classList.remove('on');render()};
document.getElementById('cUSD').onclick=function(){MODE='usd';
  this.classList.add('on');document.getElementById('cUZS').classList.remove('on');render()};

document.getElementById('upd').textContent=DATA.updated;
document.getElementById('rate').textContent=n0(DATA.rate)+' сум ('+DATA.rateDate+')';
document.getElementById('f1').min=DATA.minDate;
document.getElementById('f2').min=DATA.minDate;
document.getElementById('f1').value=clamp(mStart(DATA.today));
document.getElementById('f2').value=DATA.today;
tabs();render();
setTimeout(function(){location.reload()},900000);
</script></body></html>"""

def generate_html(dims, rate, rate_date, min_d, max_d, daily_from):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    payload = {
        "dims":      dims,
        "tabs":      TABS,
        "rate":      rate,
        "rateDate":  rate_date,
        "updated":   datetime.now(TZ).strftime("%d.%m.%Y %H:%M"),
        "today":     today,
        "minDate":   min_d or today,
        "maxDate":   max_d or today,
        "dailyFrom": daily_from,
        "freshFrom": (datetime.now(TZ) - timedelta(days=FRESH_DAYS - 1)).strftime("%Y-%m-%d"),
    }
    return HTML.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False,
                                                  separators=(",", ":")))

# ══════════════════════════════ GITHUB PAGES ══════════════════════════════
def push_github(html):
    if not GH_TOKEN:
        log.warning("RS_GITHUB_TOKEN йўқ — GitHub'га юкланмади")
        return False
    api = "https://api.github.com/repos/%s/%s/contents/%s" % (GH_USER, GH_REPO, GH_FILE)
    headers = {"Authorization": "token " + GH_TOKEN,
               "Accept": "application/vnd.github.v3+json",
               "User-Agent": "roistat"}
    ctx = ssl._create_unverified_context()
    sha = None
    try:
        req = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(req, context=ctx) as r:
            sha = json.loads(r.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.error("SHA хато: %s", e)
    payload = {"message": "roistat " + datetime.now(TZ).strftime("%d.%m %H:%M"),
               "content": base64.b64encode(html.encode()).decode()}
    if sha:
        payload["sha"] = sha
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(api, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, context=ctx) as r:
            log.info("GitHub push OK: %s", r.status)
            return True
    except Exception as e:
        log.error("push хато: %s", e)
        return False

# ══════════════════════════════ MAIN ══════════════════════════════════════
if __name__ == "__main__":
    if not SH_LEADS or not SH_ORDERS:
        sys.exit("❌ RS_SHEET_LEADS ва RS_SHEET_ORDERS керак (start.sh)")

    log.info("=== ROISTAT бошланди ===")
    leads  = load_leads()
    orders = load_orders()

    if MIN_DATE:
        n1, n2 = len(leads), len(orders)
        leads  = [r for r in leads  if r["date"] >= MIN_DATE]
        orders = [r for r in orders if r["date"] >= MIN_DATE]
        log.info("Сана филтри %s: лид %d→%d, буюртма %d→%d",
                 MIN_DATE, n1, len(leads), n2, len(orders))

    budget = load_budget()
    if MIN_DATE:
        budget = {k: v for k, v in budget.items() if k[0] >= MIN_DATE}
    leads = allocate_spend(leads, budget)

    if not leads and not orders:
        sys.exit("❌ Маълумот ўқилмади — шитс ҳаволаси ва сервис аккаунт рухсатини текширинг")

    guard(leads, orders)

    rate, rate_date = usd_rate()
    daily_from = (datetime.now(TZ) - timedelta(days=DAILY_DAYS)).strftime("%Y-%m-%d")
    dims = build_payload(leads, orders, daily_from)

    all_d = [r["date"] for r in leads] + [r["date"] for r in orders]
    min_d, max_d = (min(all_d), max(all_d)) if all_d else (None, None)

    html = generate_html(dims, rate, rate_date, min_d, max_d, daily_from)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML: %s (%d КБ)", OUT_FILE, len(html) // 1024)
    push_github(html)
    log.info("✅ Тайёр. Лид=%d, Буюртма=%d, Давр: %s — %s",
             len(leads), len(orders), min_d, max_d)