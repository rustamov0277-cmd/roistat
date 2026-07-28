"""
ROISTAT — Sinolife / Zextra сквозная аналитика
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

LEAD_TABS  = [t.strip() for t in os.environ.get(
                "RS_LEAD_TABS", "collagen,zextra,ии,веб сайт").split(",") if t.strip()]
ORDERS_TAB = os.environ.get("RS_ORDERS_TAB", "").strip()
BUDGET_TAB = os.environ.get("RS_BUDGET_TAB", "").strip()

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
    """01.07.2026 / 2026-07-01 / 01/07/2026 → '2026-07-01'"""
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
    """Устун номи → индекс. Такрорланган ном бўлса БИРИНЧИСИ олинади."""
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

# ══════════════════════════════ GOOGLE SHEETS ═════════════════════════════
_GC = None

def _gc():
    global _GC
    if _GC is None:
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(SA_JSON, scopes=scopes)
        _GC = gspread.authorize(creds)
    return _GC

def read_tab(sheet_id, tab=None):
    """Варақни ўқийди. tab бўш бўлса — биринчи варақ. 429 да қайта уринади."""
    if not sheet_id:
        return []
    for attempt in range(3):
        try:
            book = _gc().open_by_key(sheet_id)
            ws = book.worksheet(tab) if tab else book.get_worksheet(0)
            return ws.get_all_values()
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
    i_age   = pick(h, "кассалик", "ёш оралиғи", "ёш оралиги", "ёш")
    NA = "— белгиланмаган —"
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
            "seller":      txt(r, i_sell,  "— берилмаган —"),
            "registrator": txt(r, i_reg,   NA),
            "targetolog":  txt(r, i_targ,  NA),
            "form":        txt(r, i_form,  NA),
            "age":         txt(r, i_age,   "—"),
            "is_kval":     st.startswith("успеш"),
            "is_dirty":    st.startswith("дубл") or st.startswith("некач"),
            "spend":       0.0,
        })
    return out

def load_leads():
    hot = []
    for tab in LEAD_TABS:
        rows = parse_leads(read_tab(SH_LEADS, tab), tab)
        log.info("  лид/черновик '%s': %d", tab, len(rows))
        hot += rows
    hot_dates = {r["date"] for r in hot}
    arc = []
    for tab in LEAD_TABS:
        for r in parse_leads(read_tab(SH_LEADS_ARC, tab), tab):
            if r["date"] not in hot_dates:
                arc.append(r)
    log.info("Лидлар жами: черновик=%d, архив=%d", len(hot), len(arc))
    return hot + arc

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
    NA = "— белгиланмаган —"
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
    """Икки қаторли сарлавҳа: 1-қатор = йўналиш (Collagen/Zextra),
    2-қатор = таргетолог, 3-қатордан = сана × қиймат ($)."""
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
            g = norm(groups[i]) if i < len(groups) else ""
            if not t or not g:
                continue
            val = _num(r[i])
            if val:
                out[(d, g, t)] += val
    log.info("Бюджет: %d ёзув, жами $%.2f", len(out), sum(out.values()))
    return dict(out)

def allocate_spend(leads, budget):
    """(сана, йўналиш, таргетолог) харажати ўша гуруҳ лидларига тенг тақсимланади."""
    groups = defaultdict(list)
    for L in leads:
        groups[(L["date"], norm(L["direction"]), norm(L["targetolog"]))].append(L)
    matched = 0
    for key, rows in groups.items():
        spend = budget.get(key, 0.0)
        if spend:
            matched += 1
            per = spend / len(rows)
            for L in rows:
                L["spend"] = per
    log.info("Бюджет мосланди: %d / %d гуруҳ", matched, len(groups))
    if groups and matched == 0:
        log.error("⚠️ БИРОРТА ГУРУҲ МОСЛАНМАДИ — таргетолог/йўналиш номлари фарқ қиляпти!")
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
        return USD_FALLBACK, "заҳира"

# ══════════════════════════════ КЕСИМЛАР ══════════════════════════════════
# (id, кўринадиган ном, лид майдони, буюртма майдони)
DIMS = [
    ("targetolog",  "Таргетолог",  "targetolog",  "targetolog"),
    ("creative",    "Креатив",     "creative",    "creative"),
    ("form",        "Форма",       "form",        "form"),
    ("source",      "Источник",    "source",      "source"),
    ("direction",   "Йўналиш",     "direction",   "direction"),
    ("product",     "Товар",       None,          "product"),
    ("region",      "Регион",      None,          "region"),
    ("rop",         "РОП",         None,          "rop"),
    ("seller",      "Сотувчи",     "seller",      "seller"),
    ("registrator", "Регистратор", "registrator", None),
    ("age",         "Ёш оралиғи",  "age",         None),
]

TABS = [{"id": d, "label": lab} for d, lab, _, _ in DIMS] + \
       [{"id": "days", "label": "Кунлар"}]

def _empty():
    return {"leads": 0, "clean": 0, "kval": 0, "spend": 0.0,
            "orders": 0, "fact1": 0.0, "fact2": 0.0, "sold": 0}

def build_payload(leads, orders):
    dims = {}
    for did, label, lf, of in DIMS:
        acc = defaultdict(_empty)
        if lf:
            for L in leads:
                a = acc[(L["date"], L[lf])]
                a["leads"] += 1
                if not L["is_dirty"]:
                    a["clean"] += 1
                if L["is_kval"]:
                    a["kval"] += 1
                a["spend"] += L["spend"]
        if of:
            for O in orders:
                a = acc[(O["date"], O[of])]
                a["orders"] += 1
                a["fact1"]  += O["fact1"]
                a["fact2"]  += O["fact2"]
                a["sold"]   += O["sold"]
        dims[did] = [{"d": d, "k": k, **{kk: round(vv, 2) for kk, vv in m.items()}}
                     for (d, k), m in acc.items()]

    # кунлик кесим ("Кунлар" вкладкаси)
    acc = defaultdict(_empty)
    for L in leads:
        a = acc[L["date"]]
        a["leads"] += 1
        if not L["is_dirty"]:
            a["clean"] += 1
        if L["is_kval"]:
            a["kval"] += 1
        a["spend"] += L["spend"]
    for O in orders:
        a = acc[O["date"]]
        a["orders"] += 1
        a["fact1"]  += O["fact1"]
        a["fact2"]  += O["fact2"]
        a["sold"]   += O["sold"]
    dims["days"] = [{"d": d, "k": d, **{kk: round(vv, 2) for kk, vv in m.items()}}
                    for d, m in acc.items()]
    return dims

# ══════════════════════════════ НАЗОРАТ ═══════════════════════════════════
def guard(leads, orders):
    """Ҳар кун бўйича қатор сонини эслаб қолади. Камайса — Telegram огоҳлантириш."""
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
HTML = """<!DOCTYPE html><html lang="uz"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ROISTAT — Сквозная аналитика</title>
<style>
:root{--bg:#0a0a0a;--card:#151515;--line:#262626;--txt:#f5f5f5;--mut:#888;--mut2:#555;
--green:#22c55e;--greenbg:#14331f;--greentx:#86efac;--amber:#eab308;--amberbg:#3a2f0a;
--ambertx:#fde68a;--red:#ef4444;--redbg:#3a1414;--redtx:#fca5a5;--accent:#3b82f6}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--txt);padding:20px;line-height:1.45;-webkit-font-smoothing:antialiased}
.wrap{max-width:1500px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px}
h1{font-size:19px;font-weight:700;letter-spacing:-.02em}
.sub{color:var(--mut);font-size:13px}
.sub b{color:#bbb;font-weight:600}
h2.sec{font-size:12px;letter-spacing:.08em;color:var(--mut);font-weight:600;
text-transform:uppercase;margin:24px 0 12px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:14px}
.btn{background:var(--card);border:1px solid var(--line);color:var(--mut);padding:9px 16px;
border-radius:10px;font-size:14px;cursor:pointer;transition:all .15s;font-weight:500}
.btn:hover{border-color:#3a3a3a;color:#bbb}
.btn.on{background:#fff;color:#0a0a0a;border-color:#fff;font-weight:600}
.cur{margin-left:auto;display:flex;gap:6px}
.dt{background:var(--card);border:1px solid var(--line);color:var(--txt);padding:8px 10px;
border-radius:9px;font-size:13px;color-scheme:dark}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-top:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:16px 18px}
.kpi .lab{color:var(--mut);font-size:12px;margin-bottom:7px}
.kpi .val{font-size:26px;font-weight:700;letter-spacing:-.02em}
.kpi .unit{color:var(--mut);font-size:12px;margin-top:2px}
.kpi .delta{font-size:12px;margin-top:5px;font-weight:600}
.up{color:var(--greentx)}.down{color:var(--redtx)}.flat{color:var(--mut2)}
.kpi.hero{border-color:#1f3a26}
.panel{background:var(--card);border:1px solid var(--line);border-radius:15px;
padding:4px 2px;overflow-x:auto;margin-top:6px}
table{width:100%;border-collapse:collapse;min-width:1180px}
th{text-align:right;color:var(--mut);font-size:11px;font-weight:600;padding:12px;
letter-spacing:.03em;text-transform:uppercase;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
td{padding:12px;text-align:right;font-size:14px;border-bottom:1px solid #1d1d1d;white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr{transition:background .12s}
tbody tr:hover{background:#1a1a1a}
tbody tr.fresh td{opacity:.55}
td.name{font-weight:600}
td.rank{color:var(--mut2);font-size:12px;width:38px;text-align:center}
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
.foot{color:var(--mut2);font-size:12px;margin-top:24px;text-align:center}
.empty{color:var(--mut);text-align:center;padding:36px;font-size:14px}
</style></head><body><div class="wrap">

<div class="top">
  <div>
    <h1>📊 ROISTAT — Сквозная аналитика</h1>
    <div class="sub">Sinolife / Zextra · БАД колл-центр</div>
  </div>
  <div class="sub" style="text-align:right">
    🔄 Янгиланди: <b id="upd"></b><br>
    💱 Курс: <b id="rate"></b>
  </div>
</div>

<div class="bar">
  <button class="btn" data-r="today">Бугун</button>
  <button class="btn" data-r="yesterday">Кеча</button>
  <button class="btn" data-r="week">7 кун</button>
  <button class="btn on" data-r="month">Бу ой</button>
  <button class="btn" data-r="prevmonth">Ўтган ой</button>
  <button class="btn" data-r="all">Барча давр</button>
  <input type="date" id="f1" class="dt"><span class="sub">—</span>
  <input type="date" id="f2" class="dt">
  <button class="btn" id="goCustom">Кўрсатиш</button>
  <div class="cur">
    <button class="btn on" id="cUZS">сўм</button>
    <button class="btn" id="cUSD">$</button>
  </div>
</div>

<div id="freshWarn"></div>
<h2 class="sec">Умумий кўрсаткичлар <span id="periodLab" style="text-transform:none;letter-spacing:0"></span></h2>
<div class="kpis" id="kpis"></div>

<div class="bar" id="tabs"></div>
<h2 class="sec" id="dimTitle"></h2>
<div class="panel" id="tbl"></div>
<div class="note" id="hint"></div>

<div class="foot">Манба: Google Sheets (черновик + архив) · Харажат бюджет шитсидан ·
Даромад лид санасига боғланган</div>
</div>

<script>
var DATA = __PAYLOAD__;
var MODE='uzs', RANGE='month', DIM='targetolog', CF=null, CT=null;
var DIMLAB={};
var HAS_LEAD={targetolog:1,creative:1,form:1,source:1,direction:1,seller:1,
              registrator:1,age:1,days:1};

function s2d(s){return new Date(s+'T00:00:00Z')}
function d2s(d){return d.toISOString().slice(0,10)}
function addD(s,n){var d=s2d(s);d.setUTCDate(d.getUTCDate()+n);return d2s(d)}
function mStart(s){return s.slice(0,8)+'01'}
function mEnd(s){var d=s2d(s);return d2s(new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)))}
function diffD(a,b){return Math.round((s2d(b)-s2d(a))/86400000)}
function ru(s){var p=s.split('-');return p[2]+'.'+p[1]+'.'+p[0]}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function period(){
  var t=DATA.today,f,to;
  if(RANGE==='today'){f=t;to=t}
  else if(RANGE==='yesterday'){f=addD(t,-1);to=f}
  else if(RANGE==='week'){f=addD(t,-6);to=t}
  else if(RANGE==='month'){f=mStart(t);to=t}
  else if(RANGE==='prevmonth'){var p=addD(mStart(t),-1);f=mStart(p);to=mEnd(p)}
  else if(RANGE==='custom'&&CF&&CT){f=CF;to=CT}
  else{f=DATA.minDate;to=DATA.maxDate}
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
  return MODE==='usd'?('$'+nf(v,2)):(n0(v*DATA.rate)+' сўм')}
function mS(v){if(v==null)return '—';
  return MODE==='usd'?('$'+nf(v/DATA.rate,2)):(n0(v)+' сўм')}
function pc(v){return v==null?'—':nf(v,1)+'%'}
function bdg(v,g,a){if(v==null)return '—';
  var c=v>=g?'b-green':v>=a?'b-amber':'b-red';
  return '<span class="badge '+c+'">'+nf(v,1)+'%</span>'}
function romiTx(v){if(v==null)return '<span class="badge b-red">харажат йўқ</span>';
  var c=v>=3?'b-green':v>=1.5?'b-amber':'b-red';
  return '<span class="badge '+c+'">'+nf(v,2)+'×</span>'}
function delta(cur,prev){
  if(prev==null||prev===0||cur==null)return '<div class="delta flat">—</div>';
  var p=(cur-prev)/Math.abs(prev)*100;
  if(Math.abs(p)<0.5)return '<div class="delta flat">0%</div>';
  var cls=p>0?'up':'down',ar=p>0?'↑':'↓';
  return '<div class="delta '+cls+'">'+ar+' '+nf(Math.abs(p),1)+'%</div>';
}

function kpis(p){
  var c=met(sumAll(agg('targetolog',p.f,p.to)));
  var v=met(sumAll(agg('targetolog',p.pf,p.pt)));
  function card(lab,val,unit,d,hero){
    return '<div class="kpi'+(hero?' hero':'')+'"><div class="lab">'+lab+'</div>'+
      '<div class="val">'+val+'</div>'+(unit?'<div class="unit">'+unit+'</div>':'')+(d||'')+'</div>'}
  document.getElementById('kpis').innerHTML=
    card('Харажат',mU(c.spend),'',delta(c.spend,v.spend))+
    card('Жами лид',n0(c.leads),'тоза: '+n0(c.clean),delta(c.leads,v.leads))+
    card('CPL',mU(c.cpl),'1 лид нархи',delta(v.cpl,c.cpl))+
    card('Квал',n0(c.kval),'конв: '+pc(c.convK),delta(c.kval,v.kval))+
    card('Сотув',n0(c.sold),'выкуп: '+pc(c.buyout),delta(c.sold,v.sold))+
    card('CPO',mU(c.cpo),'1 сотув нархи',delta(v.cpo,c.cpo))+
    card('Ўртача чек',mS(c.avg),'',delta(c.avg,v.avg))+
    card('Даромад',mS(c.fact2),'ROMI '+(c.romi==null?'—':nf(c.romi,2)+'×'),
         delta(c.fact2,v.fact2),true);
}

function table(p){
  var m=agg(DIM,p.f,p.to),keys=Object.keys(m);
  var el=document.getElementById('tbl');
  if(!keys.length){el.innerHTML='<div class="empty">Бу давр учун маълумот йўқ</div>';return}
  var lead=!!HAS_LEAD[DIM],isDays=(DIM==='days');
  keys.sort(isDays?function(a,b){return a<b?1:-1}
                 :function(a,b){return m[b].fact2-m[a].fact2});
  var h='<table><thead><tr><th>#</th><th>'+DIMLAB[DIM]+'</th><th>Харажат</th>';
  if(lead)h+='<th>Лид</th><th>Тоза</th><th>Сифат</th><th>CPL</th><th>Квал</th>';
  h+='<th>Заказ Ф1</th><th>Сотув Ф2</th><th>Выкуп</th><th>CPO</th><th>Ўрт.чек</th><th>ROMI</th></tr></thead><tbody>';
  for(var i=0;i<keys.length;i++){
    var k=keys[i],x=met(m[k]),fr=isDays&&k>=DATA.freshFrom;
    h+='<tr'+(fr?' class="fresh"':'')+'><td class="rank">'+(i+1)+'</td>'+
       '<td class="name">'+(isDays?ru(k):esc(k))+(fr?' ⏳':'')+'</td>'+
       '<td>'+mU(x.spend)+'</td>';
    if(lead)h+='<td>'+n0(x.leads)+'</td><td>'+n0(x.clean)+'</td><td>'+bdg(x.quality,80,60)+'</td>'+
               '<td>'+mU(x.cpl)+'</td><td>'+n0(x.kval)+'</td>';
    h+='<td>'+mS(x.fact1)+'</td><td style="color:var(--greentx)">'+mS(x.fact2)+'</td>'+
       '<td>'+bdg(x.buyout,80,60)+'</td><td>'+mU(x.cpo)+'</td><td>'+mS(x.avg)+'</td>'+
       '<td>'+romiTx(x.romi)+'</td></tr>';
  }
  var t=met(sumAll(m));
  h+='</tbody><tfoot><tr><td></td><td>ЖАМИ</td><td>'+mU(t.spend)+'</td>';
  if(lead)h+='<td>'+n0(t.leads)+'</td><td>'+n0(t.clean)+'</td><td>'+pc(t.quality)+'</td>'+
             '<td>'+mU(t.cpl)+'</td><td>'+n0(t.kval)+'</td>';
  h+='<td>'+mS(t.fact1)+'</td><td>'+mS(t.fact2)+'</td><td>'+pc(t.buyout)+'</td>'+
     '<td>'+mU(t.cpo)+'</td><td>'+mS(t.avg)+'</td><td>'+
     (t.romi==null?'—':nf(t.romi,2)+'×')+'</td></tr></tfoot></table>';
  el.innerHTML=h;
}

function tabs(){
  var el=document.getElementById('tabs'),h='';
  for(var i=0;i<DATA.tabs.length;i++){var t=DATA.tabs[i];DIMLAB[t.id]=t.label;
    h+='<button class="btn'+(t.id===DIM?' on':'')+'" data-d="'+t.id+'">'+t.label+'</button>'}
  el.innerHTML=h;
  el.querySelectorAll('button').forEach(function(b){
    b.onclick=function(){DIM=b.dataset.d;render()}});
}

function render(){
  var p=period();
  document.getElementById('periodLab').innerHTML=
    '<span style="color:var(--mut);font-size:13px">· '+ru(p.f)+' — '+ru(p.to)+'</span>';
  document.getElementById('dimTitle').textContent=DIMLAB[DIM]+' бўйича таҳлил';
  document.getElementById('freshWarn').innerHTML = p.to>=DATA.freshFrom
    ? '<div class="note warn">⏳ <b>Охирги 7 кун ҳали тўлиқ эмас</b> — сотувлар ва кассалар ёпилмоқда, шунинг учун бу кунларда ROMI паст кўриниши нормал ҳолат.</div>'
    : '';
  document.getElementById('hint').innerHTML=
    'Сифат = тоза лид ÷ жами лид · Выкуп = Факт2 ÷ Факт1 · ROMI = Факт2 ÷ харажат · '+
    'CPL жами лиддан, конверсия тоза лиддан ҳисобланади.';
  document.querySelectorAll('#tabs .btn').forEach(function(b){
    b.classList.toggle('on',b.dataset.d===DIM)});
  kpis(p);table(p);
}

document.querySelectorAll('.bar .btn[data-r]').forEach(function(b){
  b.onclick=function(){RANGE=b.dataset.r;CF=CT=null;
    document.querySelectorAll('.btn[data-r]').forEach(function(x){x.classList.remove('on')});
    b.classList.add('on');render()}});
document.getElementById('goCustom').onclick=function(){
  var a=document.getElementById('f1').value,b=document.getElementById('f2').value;
  if(!a||!b){alert('Иккала санани танланг');return}
  if(a>b){var t=a;a=b;b=t}
  CF=a;CT=b;RANGE='custom';
  document.querySelectorAll('.btn[data-r]').forEach(function(x){x.classList.remove('on')});
  render()};
document.getElementById('cUZS').onclick=function(){MODE='uzs';
  this.classList.add('on');document.getElementById('cUSD').classList.remove('on');render()};
document.getElementById('cUSD').onclick=function(){MODE='usd';
  this.classList.add('on');document.getElementById('cUZS').classList.remove('on');render()};

document.getElementById('upd').textContent=DATA.updated;
document.getElementById('rate').textContent=n0(DATA.rate)+' сўм ('+DATA.rateDate+')';
document.getElementById('f1').value=mStart(DATA.today);
document.getElementById('f2').value=DATA.today;
tabs();render();
setTimeout(function(){location.reload()},900000);
</script></body></html>"""

def generate_html(dims, rate, rate_date, min_d, max_d):
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
        "freshFrom": (datetime.now(TZ) - timedelta(days=FRESH_DAYS - 1)).strftime("%Y-%m-%d"),
    }
    return HTML.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))

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
    budget = load_budget()
    leads  = allocate_spend(leads, budget)

    if not leads and not orders:
        sys.exit("❌ Маълумот ўқилмади — шитс ҳаволаси ва сервис аккаунт рухсатини текширинг")

    guard(leads, orders)

    rate, rate_date = usd_rate()
    dims = build_payload(leads, orders)

    all_d = [r["date"] for r in leads] + [r["date"] for r in orders]
    min_d, max_d = (min(all_d), max(all_d)) if all_d else (None, None)

    html = generate_html(dims, rate, rate_date, min_d, max_d)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML: %s (%d КБ)", OUT_FILE, len(html) // 1024)
    push_github(html)
    log.info("✅ Тайёр. Лид=%d, Буюртма=%d, Давр: %s — %s",
             len(leads), len(orders), min_d, max_d)