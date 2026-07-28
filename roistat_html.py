"""
ROISTAT v5 — HTML қатлами (ёруғ мавзу, Montserrat, графиклар пастда).
roistat.py дан чақирилади: generate_html(...) ва push_github(...)
"""

import json, base64, ssl, logging
import urllib.request, urllib.error
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ROISTAT — Сквозная аналитика</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root{--bg:#f4f6fa;--card:#fff;--line:#e4e9f0;--line2:#eef2f7;
--txt:#0f172a;--mut:#64748b;--mut2:#94a3b8;
--blue:#2563eb;--gtx:#15803d;--gbg:#dcfce7;--atx:#b45309;--abg:#fef3c7;
--rtx:#b91c1c;--rbg:#fee2e2}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Montserrat',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
background:var(--bg);color:var(--txt);padding:20px;line-height:1.5;
-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.wrap{max-width:1640px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;
background:linear-gradient(120deg,#2563eb,#7c3aed);color:#fff;padding:18px 22px;
border-radius:18px;box-shadow:0 6px 20px rgba(37,99,235,.22)}
h1{font-size:20px;font-weight:800;letter-spacing:-.02em}
.top .s1{font-size:13px;opacity:.9;font-weight:500;margin-top:2px}
.top .s2{font-size:13px;text-align:right;opacity:.95;font-weight:500}
.top b{font-weight:700}
h2.sec{font-size:12px;letter-spacing:.07em;color:var(--mut);font-weight:700;
text-transform:uppercase;margin:26px 0 12px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:16px}
.btn{background:var(--card);border:1px solid var(--line);color:var(--mut);padding:9px 17px;
border-radius:11px;font-size:14px;cursor:pointer;font-weight:600;font-family:inherit;
transition:all .15s;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.btn:hover{border-color:#c7d2e0;color:var(--txt)}
.btn.on{background:var(--blue);color:#fff;border-color:var(--blue);
box-shadow:0 3px 10px rgba(37,99,235,.3)}
.cur{margin-left:auto;display:flex;gap:6px}
.dt{background:var(--card);border:1px solid var(--line);color:var(--txt);padding:8px 11px;
border-radius:10px;font-size:13px;font-family:inherit;font-weight:500}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(163px,1fr));gap:12px;margin-top:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px 17px;
box-shadow:0 1px 3px rgba(15,23,42,.05)}
.kpi .lab{color:var(--mut);font-size:11.5px;margin-bottom:7px;font-weight:600;
text-transform:uppercase;letter-spacing:.03em}
.kpi .val{font-size:23px;font-weight:800;letter-spacing:-.02em;word-break:break-word;line-height:1.15}
.kpi .unit{color:var(--mut);font-size:12px;margin-top:3px;font-weight:500}
.kpi .delta{font-size:12px;margin-top:6px;font-weight:700}
.up{color:var(--gtx)}.down{color:var(--rtx)}.flat{color:var(--mut2)}
.kpi.hero{background:linear-gradient(135deg,#ecfdf5,#fff);border-color:#a7f3d0}
.crumb{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:14px;font-size:13.5px;
font-weight:600}
.crumb a{color:var(--blue);cursor:pointer}.crumb a:hover{text-decoration:underline}
.crumb span{color:var(--mut2)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:2px;overflow-x:auto;margin-top:6px;box-shadow:0 1px 3px rgba(15,23,42,.05)}
table{width:100%;border-collapse:collapse;min-width:1240px}
th{text-align:right;color:var(--mut);font-size:11px;font-weight:700;padding:13px 11px;
letter-spacing:.04em;text-transform:uppercase;border-bottom:2px solid var(--line);
white-space:nowrap;cursor:pointer;user-select:none;background:#fafbfd}
th:hover{color:var(--blue)}th.srt{color:var(--blue)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
td{padding:12px 11px;text-align:right;font-size:14px;border-bottom:1px solid var(--line2);
white-space:nowrap;font-weight:500}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#f8fafc}
tbody tr.fresh td{color:var(--mut)}
tbody tr.mon td{color:var(--mut)}
tbody tr.drill{cursor:pointer}
tbody tr.drill td:nth-child(2):after{content:' \\25B8';color:var(--blue);font-weight:800}
td.name{font-weight:700}
td.rank{color:var(--mut2);font-size:12px;width:38px;text-align:center;font-weight:600}
td.pos{color:var(--gtx);font-weight:700}
tfoot td{padding:14px 11px;font-weight:800;font-size:14px;background:#f1f5f9;
border-top:2px solid var(--line);text-align:right}
tfoot td:first-child,tfoot td:nth-child(2){text-align:left}
.bd{display:inline-block;padding:3px 10px;border-radius:8px;font-size:12px;font-weight:700}
.g{background:var(--gbg);color:var(--gtx)}.a{background:var(--abg);color:var(--atx)}
.r{background:var(--rbg);color:var(--rtx)}
.charts{display:grid;grid-template-columns:1fr 1.45fr;gap:14px;margin-top:14px}
.chbox{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px;
box-shadow:0 1px 3px rgba(15,23,42,.05)}
.chbox .t{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;
margin-bottom:12px;font-weight:700}
.chwrap{position:relative;height:250px}
.note{color:var(--mut);font-size:13px;margin-top:16px;padding:13px 17px;background:#eef4ff;
border-left:4px solid var(--blue);border-radius:0 10px 10px 0;font-weight:500}
.warn{background:#fffbeb;border-left-color:#f59e0b}.warn b{color:var(--atx)}
.foot{color:var(--mut2);font-size:12px;margin-top:24px;text-align:center;font-weight:500}
.empty{color:var(--mut);text-align:center;padding:36px;font-size:14px;font-weight:600}
.sortsel{display:none}
@media(max-width:1100px){.charts{grid-template-columns:1fr}}
@media(max-width:880px){
 body{padding:12px}h1{font-size:17px}
 .top{padding:15px 17px;border-radius:15px}
 .top .s2{text-align:left}
 .kpis{grid-template-columns:1fr 1fr;gap:9px}
 .kpi{padding:12px 14px;border-radius:14px}.kpi .val{font-size:19px}
 .btn{padding:8px 14px;font-size:13px}.cur{margin-left:0}
 .sortsel{display:block;margin-top:10px}
 .panel{background:transparent;border:none;padding:0;overflow:visible;box-shadow:none}
 table{min-width:0}table,thead,tbody,tfoot,tr,td{display:block}
 thead{display:none}
 tbody tr,tfoot tr{background:var(--card);border:1px solid var(--line);border-radius:14px;
  margin-bottom:10px;padding:4px 0;box-shadow:0 1px 3px rgba(15,23,42,.05)}
 tbody tr:hover{background:var(--card)}
 tbody tr.fresh{border-color:#fcd34d}
 td,tfoot td{display:flex;justify-content:space-between;align-items:center;gap:12px;
  text-align:right;border:none;padding:8px 16px;font-size:14px;white-space:normal;background:none}
 td:before,tfoot td:before{content:attr(data-l);color:var(--mut);font-size:11.5px;
  text-align:left;flex:0 0 auto;text-transform:uppercase;letter-spacing:.03em;font-weight:600}
 td.rank{display:none}
 td.name{font-size:15px;font-weight:800;padding:12px 16px;border-bottom:1px solid var(--line);
  margin-bottom:3px;display:block;text-align:left}
 td.name:before{content:''}
 tfoot tr{background:#f1f5f9;border-color:#cbd5e1}
 tfoot td:first-child{display:none}
 tfoot td:nth-child(2){display:block;text-align:left;font-size:13px;color:var(--mut);
  border-bottom:1px solid var(--line);margin-bottom:3px}
 tfoot td:nth-child(2):before{content:''}
 .chwrap{height:270px}
}
</style></head><body><div class="wrap">

<div class="top">
 <div><h1>ROISTAT — Сквозная аналитика</h1>
  <div class="s1">Sinolife / Zextra · Колл-центр</div></div>
 <div class="s2">Обновлено: <b id="upd"></b><br>Курс: <b id="rate"></b></div>
</div>

<div class="bar">
 <button class="btn" data-r="today">Сегодня</button>
 <button class="btn on" data-r="all">Все даты</button>
 <input type="date" id="f1" class="dt"><span style="color:var(--mut)">—</span>
 <input type="date" id="f2" class="dt">
 <button class="btn" id="go">Показать</button>
 <div class="cur"><button class="btn on" id="cU">сум</button><button class="btn" id="cD">$</button></div>
</div>

<div id="fw"></div>
<h2 class="sec">Общие показатели <span id="pl" style="text-transform:none;letter-spacing:0"></span></h2>
<div class="kpis" id="kpis"></div>

<div class="bar" id="tabs"></div>
<div class="crumb" id="crumb"></div>
<h2 class="sec" id="dt"></h2>
<div class="sortsel"><select class="dt" id="ss" style="width:100%"></select></div>
<div class="panel" id="tbl"></div>

<h2 class="sec">Графики</h2>
<div class="charts">
 <div class="chbox"><div class="t">Воронка</div><div class="chwrap"><canvas id="chF"></canvas></div></div>
 <div class="chbox"><div class="t">Динамика: расход и ROAS</div><div class="chwrap"><canvas id="chD"></canvas></div></div>
</div>

<div class="note" id="hint"></div>
<div class="foot">Sheets (рабочий + архив) + Meta Ads · Выручка привязана к дате лида</div>
</div>

<script>
var D=__PAYLOAD__;
var MODE='uzs',RANGE='all',DIM='camp',CF=null,CT=null,SORT=null,SDIR=-1;
var DR={camp:null,adset:null},LAB={},CH={};
var MON=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август',
'Сентябрь','Октябрь','Ноябрь','Декабрь'];
var META={camp:1,adset:1,creative:1};
var LEADD={camp:1,adset:1,creative:1,targetolog:1,form:1,source:1,seller:1,registrator:1,days:1};
var FL=['leads','clean','kval','spend','orders','fact1','fact2','sold','newc',
'dsum','dcnt','mrev','impr','reach','clicks','mleads'];

function s2d(s){return new Date(s+'T00:00:00Z')}
function addD(s,n){var d=s2d(s);d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)}
function mSt(s){return s.slice(0,8)+'01'}
function dif(a,b){return Math.round((s2d(b)-s2d(a))/86400000)}
function ru(s){var p=s.split('-');return p[2]+'.'+p[1]+'.'+p[0]}
function md(s){var p=s.split('-');return p[2]+'.'+p[1]}
function mL(s){var p=s.split('-');return MON[parseInt(p[1],10)-1]+' '+p[0]}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function att(s){return esc(s).replace(/"/g,'&quot;')}
function cl(s){return s<D.minDate?D.minDate:s}
function z(){var o={};for(var i=0;i<FL.length;i++)o[FL[i]]=0;return o}

function per(){var t=D.today,f,to;
 if(RANGE==='today'){f=t;to=t}
 else if(RANGE==='custom'&&CF&&CT){f=CF;to=CT}
 else{f=D.minDate;to=D.maxDate}
 f=cl(f);var n=dif(f,to)+1;
 return{f:f,to:to,pf:addD(f,-n),pt:addD(f,-1)}}

function agg(dim,f,to,pf){
 var rows=D.dims[dim]||[],m={};
 for(var i=0;i<rows.length;i++){var r=rows[i];
  if(r.d<f||r.d>to)continue;
  if(pf!=null&&r.p!==pf)continue;
  var a=m[r.k]||(m[r.k]=z());
  for(var j=0;j<FL.length;j++)a[FL[j]]+=r[FL[j]]||0}
 return m}
function sum(m){var a=z();for(var k in m)for(var j=0;j<FL.length;j++)a[FL[j]]+=m[k][FL[j]];return a}

function met(a){var r=D.rate;
 return{spend:a.spend,leads:a.leads,clean:a.clean,kval:a.kval,orders:a.orders,
  fact1:a.fact1,fact2:a.fact2,sold:a.sold,newc:a.newc,mrev:a.mrev,
  impr:a.impr,clicks:a.clicks,mleads:a.mleads,
  qual:a.leads?a.clean/a.leads*100:null,
  cpl:a.leads?a.spend/a.leads:null,
  ql:a.leads?a.kval/a.leads*100:null,
  cpql:a.kval?a.spend/a.kval:null,
  buy:a.fact1?a.fact2/a.fact1*100:null,
  cpo:a.sold?a.spend/a.sold:null,
  cac:a.newc?a.spend/a.newc:null,
  avg:a.sold?a.fact2/a.sold:null,
  arpl:a.leads?a.fact2/a.leads:null,
  deal:a.dcnt?a.dsum/a.dcnt:null,
  conv:a.leads?a.sold/a.leads*100:null,
  mshare:a.fact2?a.mrev/a.fact2*100:null,
  ctr:a.impr?a.clicks/a.impr*100:null,
  cpm:a.impr?a.spend/a.impr*1000:null,
  cpc:a.clicks?a.spend/a.clicks:null,
  freq:a.reach?a.impr/a.reach:null,
  roas:a.spend>0?(a.fact2/r)/a.spend:null}}

function nf(v,d){return(v==null||isNaN(v))?'—':v.toLocaleString('ru-RU',
 {minimumFractionDigits:d,maximumFractionDigits:d})}
function n0(v){return v==null?'—':Math.round(v).toLocaleString('ru-RU')}
function mU(v){return v==null?'—':(MODE==='usd'?('$'+nf(v,2)):(n0(v*D.rate)+' сум'))}
function mS(v){return v==null?'—':(MODE==='usd'?('$'+nf(v/D.rate,2)):(n0(v)+' сум'))}
function pc(v){return v==null?'—':nf(v,1)+'%'}
function bd(v,g,a){if(v==null)return'—';var c=v>=g?'g':v>=a?'a':'r';
 return'<span class="bd '+c+'">'+nf(v,1)+'%</span>'}
function ro(v){if(v==null)return'<span class="bd r">нет расхода</span>';
 var c=v>=3?'g':v>=1.5?'a':'r';return'<span class="bd '+c+'">'+nf(v,2)+'x</span>'}
function dl(c,p){if(p==null||p===0||c==null)return'<div class="delta flat">—</div>';
 var x=(c-p)/Math.abs(p)*100;if(Math.abs(x)<0.5)return'<div class="delta flat">0%</div>';
 return'<div class="delta '+(x>0?'up':'down')+'">'+(x>0?'\\u2191':'\\u2193')+' '+
 nf(Math.abs(x),1)+'%</div>'}

function kpis(p){
 var c=met(sum(agg('days',p.f,p.to))),v=met(sum(agg('days',p.pf,p.pt)));
 function K(l,val,u,d,h){return'<div class="kpi'+(h?' hero':'')+'"><div class="lab">'+l+
  '</div><div class="val">'+val+'</div>'+(u?'<div class="unit">'+u+'</div>':'')+(d||'')+'</div>'}
 document.getElementById('kpis').innerHTML=
  K('Расход',mU(c.spend),'',dl(c.spend,v.spend))+
  K('Лиды',n0(c.leads),'чистые: '+n0(c.clean),dl(c.leads,v.leads))+
  K('CPL',mU(c.cpl),'цена лида',dl(v.cpl,c.cpl))+
  K('Квал',n0(c.kval),'QL '+pc(c.ql),dl(c.kval,v.kval))+
  K('Продажи',n0(c.sold),'выкуп '+pc(c.buy),dl(c.sold,v.sold))+
  K('CPO',mU(c.cpo),'цена продажи',dl(v.cpo,c.cpo))+
  K('CAC',mU(c.cac),'новый клиент',dl(v.cac,c.cac))+
  K('Конверсия',pc(c.conv),'лид в продажу',dl(c.conv,v.conv))+
  K('Средний чек',mS(c.avg),'',dl(c.avg,v.avg))+
  K('ARPL',mS(c.arpl),'выручка с лида',dl(c.arpl,v.arpl))+
  K('Deal Time',c.deal==null?'—':nf(c.deal,1)+' дн','лид в продажу',dl(v.deal,c.deal))+
  K('Выручка',mS(c.fact2),'ROAS '+(c.roas==null?'—':nf(c.roas,2)+'x')+
   (c.mshare==null?'':' · '+nf(c.mshare,0)+'% Meta'),dl(c.fact2,v.fact2),1);
 return c}

function cols(dim){
 var C=[],mt=!!META[dim],ld=!!LEADD[dim];
 C.push({h:'Расход',v:function(x){return x.spend},f:function(x){return mU(x.spend)}});
 if(mt)C=C.concat([
  {h:'Показы',v:function(x){return x.impr},f:function(x){return n0(x.impr)}},
  {h:'Частота',v:function(x){return x.freq},f:function(x){return x.freq==null?'—':nf(x.freq,2)}},
  {h:'Клики',v:function(x){return x.clicks},f:function(x){return n0(x.clicks)}},
  {h:'CTR',v:function(x){return x.ctr},f:function(x){return pc(x.ctr)}},
  {h:'CPM',v:function(x){return x.cpm},f:function(x){return mU(x.cpm)}},
  {h:'CPC',v:function(x){return x.cpc},f:function(x){return mU(x.cpc)}},
  {h:'Лиды Meta',v:function(x){return x.mleads},f:function(x){return n0(x.mleads)}}]);
 if(ld)C=C.concat([
  {h:'Лиды',v:function(x){return x.leads},f:function(x){return n0(x.leads)}},
  {h:'Чистые',v:function(x){return x.clean},f:function(x){return n0(x.clean)}},
  {h:'Качество',v:function(x){return x.qual},f:function(x){return bd(x.qual,80,60)}},
  {h:'CPL',v:function(x){return x.cpl},f:function(x){return mU(x.cpl)}},
  {h:'Квал',v:function(x){return x.kval},f:function(x){return n0(x.kval)}},
  {h:'QL %',v:function(x){return x.ql},f:function(x){return bd(x.ql,30,15)}},
  {h:'CPQL',v:function(x){return x.cpql},f:function(x){return mU(x.cpql)}}]);
 return C.concat([
  {h:'Заказы',v:function(x){return x.fact1},f:function(x){return mS(x.fact1)}},
  {h:'Продажи',v:function(x){return x.fact2},f:function(x){return mS(x.fact2)},c:'pos'},
  {h:'Выкуп',v:function(x){return x.buy},f:function(x){return bd(x.buy,80,60)}},
  {h:'CPO',v:function(x){return x.cpo},f:function(x){return mU(x.cpo)}},
  {h:'Ср.чек',v:function(x){return x.avg},f:function(x){return mS(x.avg)}},
  {h:'ROAS',v:function(x){return x.roas},f:function(x){return ro(x.roas)}}])}

function pfilt(){
 if(DIM==='adset'&&DR.camp)return DR.camp;
 if(DIM==='creative'&&DR.adset)return DR.adset;
 return null}

function table(p){
 var C=cols(DIM),m=agg(DIM,p.f,p.to,pfilt()),keys=Object.keys(m);
 var el=document.getElementById('tbl');
 if(!keys.length){el.innerHTML='<div class="empty">Нет данных за этот период</div>';return m}
 var isD=(DIM==='days'),canD=(DIM==='camp'||DIM==='adset'),si=(SORT==null?-1:SORT);
 if(isD&&SORT==null){keys.sort(function(a,b){return a<b?1:-1})}
 else{var kf=(si>=0&&C[si])?C[si].v:function(x){return x.fact2};
  keys.sort(function(a,b){var x=kf(met(m[a]))||0,y=kf(met(m[b]))||0;return SDIR*(y-x)})}
 var h='<table><thead><tr><th data-i="-1">#</th><th data-i="-1">'+LAB[DIM]+'</th>';
 for(var j=0;j<C.length;j++)h+='<th data-i="'+j+'" class="'+(si===j?'srt':'')+'">'+
  C[j].h+(si===j?(SDIR<0?' \\u2193':' \\u2191'):'')+'</th>';
 h+='</tr></thead><tbody>';
 for(var i=0;i<keys.length;i++){var k=keys[i],x=met(m[k]);
  var mo=isD&&k<D.dailyFrom,fr=isD&&!mo&&k>=D.freshFrom;
  var lb=isD?(mo?mL(k)+' (мес.)':ru(k)):esc(k);
  h+='<tr class="'+(fr?'fresh':(mo?'mon':''))+(canD?' drill':'')+'" data-k="'+att(k)+'">'+
   '<td class="rank">'+(i+1)+'</td><td class="name">'+lb+(fr?' \\u23F3':'')+'</td>';
  for(var j=0;j<C.length;j++)h+='<td data-l="'+C[j].h+'"'+(C[j].c?' class="'+C[j].c+'"':'')+
   '>'+C[j].f(x)+'</td>';
  h+='</tr>'}
 var t=met(sum(m));
 h+='</tbody><tfoot><tr><td></td><td>ИТОГО</td>';
 for(var j=0;j<C.length;j++)h+='<td data-l="'+C[j].h+'">'+C[j].f(t)+'</td>';
 h+='</tr></tfoot></table>';
 el.innerHTML=h;
 el.querySelectorAll('th[data-i]').forEach(function(th){th.onclick=function(){
  var i=parseInt(th.getAttribute('data-i'));if(i<0)return;
  if(SORT===i){SDIR=-SDIR}else{SORT=i;SDIR=-1}render()}});
 if(canD)el.querySelectorAll('tbody tr.drill').forEach(function(tr){tr.onclick=function(){
  var k=tr.getAttribute('data-k');
  if(DIM==='camp'){DR.camp=k;DR.adset=null;DIM='adset'}
  else{DR.adset=k;DIM='creative'}
  SORT=null;SDIR=-1;render()}});
 var ss=document.getElementById('ss'),o='';
 for(var j=0;j<C.length;j++)o+='<option value="'+j+'"'+(si===j?' selected':'')+
  '>Сортировка: '+C[j].h+'</option>';
 ss.innerHTML=o;
 ss.onchange=function(){SORT=parseInt(ss.value);SDIR=-1;render()};
 return m}

function goDim(d){
 if(d==='camp'){DR.camp=null;DR.adset=null}
 if(d==='adset'){DR.adset=null}
 DIM=d;SORT=null;SDIR=-1;render()}

function crumb(){
 var el=document.getElementById('crumb');
 if(DIM!=='adset'&&DIM!=='creative'){el.innerHTML='';return}
 var h='<a data-g="camp">Кампании</a>';
 if(DR.camp)h+='<span>/</span><a data-g="adset">'+esc(DR.camp)+'</a>';
 if(DIM==='creative'&&DR.adset)h+='<span>/</span><span>'+esc(DR.adset)+'</span>';
 el.innerHTML=h;
 el.querySelectorAll('a[data-g]').forEach(function(a){
  a.onclick=function(){goDim(a.getAttribute('data-g'))}})}

var GR='#eef2f7',TC='#64748b';
function draw(id,cfg){if(!window.Chart)return;
 if(CH[id]){CH[id].destroy();CH[id]=null}
 var e=document.getElementById(id);if(e)CH[id]=new Chart(e,cfg)}
function bo(x){var o={responsive:true,maintainAspectRatio:false,
 plugins:{legend:{labels:{color:TC,font:{size:11,family:'Montserrat',weight:'600'},boxWidth:12}}},
 scales:{x:{ticks:{color:TC,font:{size:10,family:'Montserrat'}},grid:{color:GR}},
  y:{ticks:{color:TC,font:{size:10,family:'Montserrat'}},grid:{color:GR}}}};
 if(x)for(var k in x)o[k]=x[k];return o}

function funnel(c){draw('chF',{type:'bar',
 data:{labels:['Лиды','Чистые','Квал','Заказы','Продажи'],
  datasets:[{data:[c.leads,c.clean,c.kval,c.orders,c.sold],
   backgroundColor:['#3b82f6','#06b6d4','#f59e0b','#f97316','#22c55e'],
   borderRadius:8,borderSkipped:false}]},
 options:bo({indexAxis:'y',plugins:{legend:{display:false}}})})}

function dayCh(p){
 var r=(D.dims.days||[]).filter(function(x){return x.d>=p.f&&x.d<=p.to});
 r.sort(function(a,b){return a.d<b.d?-1:1});
 if(r.length>62)r=r.slice(-62);
 draw('chD',{data:{labels:r.map(function(x){return x.d<D.dailyFrom?mL(x.d):md(x.d)}),
  datasets:[
   {type:'bar',label:MODE==='usd'?'Расход, $':'Расход, сум',
    data:r.map(function(x){return MODE==='usd'?x.spend:x.spend*D.rate}),
    backgroundColor:'#93c5fd',borderColor:'#2563eb',yAxisID:'y',borderRadius:5},
   {type:'line',label:'ROAS',
    data:r.map(function(x){return x.spend>0?+((x.fact2/D.rate)/x.spend).toFixed(2):null}),
    borderColor:'#16a34a',backgroundColor:'#16a34a',yAxisID:'y1',borderWidth:2.5,
    tension:.3,pointRadius:2,spanGaps:true}]},
  options:bo({scales:{
   x:{ticks:{color:TC,font:{size:9,family:'Montserrat'},maxRotation:0,autoSkip:true},grid:{color:GR}},
   y:{position:'left',ticks:{color:'#2563eb',font:{size:9,family:'Montserrat'}},grid:{color:GR}},
   y1:{position:'right',ticks:{color:'#16a34a',font:{size:9,family:'Montserrat'}},grid:{display:false}}}})})}

function tabs(){var el=document.getElementById('tabs'),h='';
 for(var i=0;i<D.tabs.length;i++){var t=D.tabs[i];LAB[t.id]=t.label;
  h+='<button class="btn'+(t.id===DIM?' on':'')+'" data-d="'+t.id+'">'+t.label+'</button>'}
 el.innerHTML=h;
 el.querySelectorAll('button').forEach(function(b){b.onclick=function(){
  var d=b.getAttribute('data-d');
  if(d==='camp'){DR.camp=null;DR.adset=null}
  DIM=d;SORT=null;SDIR=-1;render()}})}

function render(){var p=per();
 document.getElementById('pl').innerHTML='<span style="color:var(--mut);font-size:13px">· '+
  ru(p.f)+' — '+ru(p.to)+'</span>';
 document.getElementById('dt').textContent='Анализ по: '+LAB[DIM];
 document.getElementById('fw').innerHTML=p.to>=D.freshFrom?
  '<div class="note warn">\\u23F3 <b>Последние 7 дней ещё не полные</b> — продажи и кассы '+
  'закрываются позже, низкий ROAS в эти дни нормален.</div>':'';
 document.getElementById('hint').innerHTML=
  'Качество = чистые / лиды · QL% = квал / лиды · Выкуп = Продажи / Заказы · '+
  'ROAS = Выручка / Расход · CAC = расход / новые клиенты · '+
  '<span style="color:var(--mut2)">До '+ru(D.dailyFrom)+' — помесячно.</span>';
 document.querySelectorAll('#tabs .btn').forEach(function(b){
  b.classList.toggle('on',b.getAttribute('data-d')===DIM)});
 crumb();
 var c=kpis(p);table(p);
 funnel(c);dayCh(p)}

document.querySelectorAll('.bar .btn[data-r]').forEach(function(b){b.onclick=function(){
 RANGE=b.getAttribute('data-r');CF=CT=null;
 document.querySelectorAll('.btn[data-r]').forEach(function(x){x.classList.remove('on')});
 b.classList.add('on');render()}});
document.getElementById('go').onclick=function(){
 var a=document.getElementById('f1').value,b=document.getElementById('f2').value;
 if(!a||!b){alert('Выберите обе даты');return}
 if(a>b){var t=a;a=b;b=t}
 CF=a;CT=b;RANGE='custom';
 document.querySelectorAll('.btn[data-r]').forEach(function(x){x.classList.remove('on')});
 render()};
document.getElementById('cU').onclick=function(){MODE='uzs';this.classList.add('on');
 document.getElementById('cD').classList.remove('on');render()};
document.getElementById('cD').onclick=function(){MODE='usd';this.classList.add('on');
 document.getElementById('cU').classList.remove('on');render()};

document.getElementById('upd').textContent=D.updated;
document.getElementById('rate').textContent=n0(D.rate)+' сум ('+D.rateDate+')';
document.getElementById('f1').min=D.minDate;
document.getElementById('f2').min=D.minDate;
document.getElementById('f1').value=cl(mSt(D.today));
document.getElementById('f2').value=D.today;
tabs();render();
setTimeout(function(){location.reload()},900000);
</script></body></html>"""


def generate_html(dims, tabs, rate, rate_date, min_d, max_d, daily_from, fresh_days, tz):
    today = datetime.now(tz).strftime("%Y-%m-%d")
    payload = {
        "dims": dims,
        "tabs": tabs,
        "rate": rate,
        "rateDate": rate_date,
        "updated": datetime.now(tz).strftime("%d.%m.%Y %H:%M"),
        "today": today,
        "minDate": min_d or today,
        "maxDate": max_d or today,
        "dailyFrom": daily_from,
        "freshFrom": (datetime.now(tz) - timedelta(days=fresh_days - 1)).strftime("%Y-%m-%d"),
    }
    return HTML.replace("__PAYLOAD__",
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def push_github(html, token, user, repo, path, tz):
    if not token:
        log.warning("RS_GITHUB_TOKEN йўқ — GitHub'га юкланмади")
        return False
    api = "https://api.github.com/repos/%s/%s/contents/%s" % (user, repo, path)
    hd = {"Authorization": "token " + token,
          "Accept": "application/vnd.github.v3+json",
          "User-Agent": "roistat"}
    ctx = ssl._create_unverified_context()
    sha = None
    try:
        req = urllib.request.Request(api, headers=hd)
        with urllib.request.urlopen(req, context=ctx) as r:
            sha = json.loads(r.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.error("SHA хато: %s", e)
    pl = {"message": "roistat " + datetime.now(tz).strftime("%d.%m %H:%M"),
          "content": base64.b64encode(html.encode()).decode()}
    if sha:
        pl["sha"] = sha
    try:
        data = json.dumps(pl).encode()
        req = urllib.request.Request(api, data=data, headers=hd, method="PUT")
        with urllib.request.urlopen(req, context=ctx) as r:
            log.info("GitHub push OK: %s", r.status)
            return True
    except Exception as e:
        log.error("push хато: %s", e)
        return False