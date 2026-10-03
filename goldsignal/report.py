"""HTML-Report: Rangliste, Backtest vs. Live-Journal, Kalender (Tag/Woche/Monat), Kontext-Auswertung."""
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .db import Database, utcnow
from .features import FILTER_LABEL
from .metrics import fmt_pct, fmt_pf, fmt_r, summarize
from .sessions import SESSION_LABEL
from .strategies import STRATEGIES

e = html.escape

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--good:#1f8a4c;--bad:#c0392b;
--neutral:#8a8a8f;--accent:#b8860b;--goodbg:#e3f4ea;--badbg:#fbe5e2;--neutbg:#efefef}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--fg:#ececef;--muted:#9a9aa2;--line:#2e2e33;
--good:#4cc27e;--bad:#ef6b5b;--neutral:#8d8d94;--accent:#e0b44a;--goodbg:#173325;--badbg:#3a1d1a;--neutbg:#2a2a2e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0}h2{font-size:18px;margin:28px 0 8px;border-bottom:2px solid var(--accent);padding-bottom:4px}
h3{font-size:15px;margin:16px 0 6px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.muted{color:var(--muted)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{background:var(--badbg);border-color:var(--bad)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:5px 7px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);font-weight:600}td:first-child,th:first-child{text-align:left}.wrap{overflow-x:auto}
td.l,th.l{text-align:left;white-space:normal;min-width:180px}.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:12px}
.lv3{background:var(--goodbg);color:var(--good)}.lv2{background:var(--goodbg);color:var(--good)}.lv1{background:var(--neutbg);color:var(--muted)}.lv0{background:var(--badbg);color:var(--bad)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.kpi b{display:block;font-size:20px}.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}.cal div{min-height:58px;border:1px solid var(--line);border-radius:6px;padding:3px 5px;font-size:12px;background:var(--card)}
.cal .h{min-height:0;border:0;background:none;font-weight:600;text-align:center}.cal .o{opacity:.35}.cal .pos{background:var(--goodbg)}.cal .neg{background:var(--badbg)}
.months{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}select{padding:4px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px}
details summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;margin:6px 0}
@media (max-width:600px){main{padding:10px}th,td{padding:4px}}
"""


def _verdict_pill(v: dict) -> str:
    return f'<span class="pill lv{v["verdict_level"]}">{e(v["verdict"])}</span>'


def _cell_r(x) -> str:
    if x is None:
        return "<td>–</td>"
    cls = "good" if x > 0 else "bad" if x < 0 else ""
    return f'<td class="{cls}">{fmt_r(x)}</td>'


def _metric_cells(m: dict) -> str:
    rel = "" if m.get("reliable") else ' <span class="muted" title="unter 30 Trades – nicht belastbar">*</span>'
    return (f"<td>{m['n']}{rel}</td><td>{fmt_pct(m['winrate'])}</td>{_cell_r(m['avg_r'])}"
            f"<td>{fmt_pf(m['profit_factor'])}</td><td>{m['max_loss_streak']}</td>")


def _vlabel(vid: str) -> str:
    s, f = vid.split("|")
    return f"{e(STRATEGIES[s].title) if s in STRATEGIES else e(s)} <span class='muted'>· {e(FILTER_LABEL.get(f, f))}</span>"


def backtest_section(bt: dict | None, min_rel: int) -> str:
    if not bt:
        return "<h2>Backtest</h2><div class='card'>Noch kein Backtest gespeichert – <code>python -m goldsignal backtest</code> ausführen.</div>"
    r = bt["results"]
    p = r["period"]
    out = [f"<h2>Backtest (historisch) – Lauf vom {e(bt['created_at'][:16].replace('T', ' '))} UTC</h2>"]
    if r.get("demo"):
        out.append("<div class='card warn'><b>DEMO-DATEN:</b> synthetische Kurse ohne echten Vorteil. Nur zum Ausprobieren.</div>")
    out.append(f"<p class='muted'>Zeitraum {p['start'][:10]} bis {p['end'][:10]} · {p['bars']} Kerzen {e(p['entry_tf'])} · "
               f"In-Sample bis {p['split'][:10]}, danach Out-of-Sample (beim Entwickeln nicht benutzt) · "
               f"Stop {r['params']['stop_atr']}×ATR, Ziel {r['params']['target_atr']}×ATR, max. {r['params']['max_hold_hours']} Std. · "
               f"Spread über Bid/Ask eingerechnet · {r['n_tests']} Varianten getestet</p>")
    out.append("<div class='card'><h3>Ehrliche Einschätzung</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in r["summary"]) + "</ul></div>")
    for w in r.get("warnings", []):
        if w.startswith("DEMO"):
            continue
        out.append(f"<div class='card warn'>{e(w)}</div>")

    bh = r["buy_hold"]
    best = r["ranking"][0] if r["ranking"] else None
    best_oos = r["variants"][best]["oos"] if best else None
    out.append("<div class='kpis'>")
    out.append(f"<div class='kpi'><span class='muted'>Einfach halten (IS)</span><b>{bh['is'].get('pct', '–')} %</b></div>")
    out.append(f"<div class='kpi'><span class='muted'>Einfach halten (OOS)</span><b>{bh['oos'].get('pct', '–')} %</b></div>")
    if best_oos and best_oos["n"]:
        out.append(f"<div class='kpi'><span class='muted'>Rang 1 OOS bei 1 % Risiko/Trade</span><b>{best_oos['total_r']:+.1f} %</b>"
                   f"<span class='muted'>{_vlabel(best)}</span></div>")
    out.append(f"<div class='kpi'><span class='muted'>Ø Zufalls-Trade</span><b>{fmt_r(r.get('random_trade_avg_r'))}</b>"
               f"<span class='muted'>Kosten des Spreads/Zufalls</span></div></div>")

    head = ("<tr><th>#</th><th class='l'>Variante</th><th>IS n</th><th>IS Treffer</th><th>IS Ø R</th>"
            "<th>OOS n</th><th>Treffer</th><th>Ø R</th><th>PF</th><th>Verlust i. F.</th><th>Zufall Ø R</th><th>p</th>"
            "<th>Kauf Ø R</th><th>Verk. Ø R</th><th class='l'>Urteil</th></tr>")

    def row(i, vid):
        v = r["variants"][vid]
        o, s = v["oos"], v["is"]
        return (f"<tr><td>{i}</td><td class='l'>{_vlabel(vid)}</td><td>{s['n']}</td><td>{fmt_pct(s['winrate'])}</td>{_cell_r(s['avg_r'])}"
                f"{_metric_cells(o)}{_cell_r(o['random'].get('avg_r_mean'))}<td>{o['random'].get('p_value') or '–'}</td>"
                f"{_cell_r(o['long']['avg_r'])}{_cell_r(o['short']['avg_r'])}<td class='l'>{_verdict_pill(v)}</td></tr>")

    rows = [row(i + 1, vid) for i, vid in enumerate(r["ranking"])]
    out.append("<h3>Rangliste (sortiert nach Out-of-Sample Ø R, belastbare zuerst)</h3>")
    out.append(f"<div class='card wrap'><table>{head}{''.join(rows[:15])}</table>"
               f"<details><summary>Alle {len(rows)} Varianten</summary><table>{head}{''.join(rows[15:])}</table></details>"
               f"<p class='muted'>* weniger als {min_rel} Trades – nicht belastbar. p = Anteil von {r['params']['random_runs']} Zufallsserien "
               "(gleich viele Käufe/Verkäufe, zufällige Zeitpunkte) mit mindestens so gutem Ø R. PF = Profit-Faktor.</p></div>")

    wf = r["walk_forward"]
    wrows = "".join(
        f"<tr><td>{f['fold']}</td><td>{(f.get('from') or '')[:10]} – {(f.get('to') or '')[:10]}</td><td class='l'>{_vlabel(f['variant']) if f['variant'] else '–'}</td>"
        f"{_cell_r(f['train_avg_r'])}{_metric_cells(f['test'])}</tr>" for f in wf["folds"])
    c = wf["combined"]
    out.append("<h3>Walk-Forward</h3><div class='card wrap'><p class='muted'>In jedem Abschnitt wird die bis dahin beste Variante gewählt "
               "und nur im folgenden Abschnitt gemessen. So sieht man, ob 'die Beste auswählen' in neuen Daten funktioniert.</p>"
               "<table><tr><th>Abschnitt</th><th>Testzeitraum</th><th class='l'>gewählte Variante</th><th>Ø R vorher</th>"
               f"<th>n</th><th>Treffer</th><th>Ø R</th><th>PF</th><th>Verlust i. F.</th></tr>{wrows}"
               f"<tr><td><b>Gesamt</b></td><td></td><td></td><td></td>{_metric_cells(c)}</tr></table></div>")

    # Kontext (Backtest) für die Basis-Varianten
    ctx_rows = []
    for name in STRATEGIES:
        v = r["variants"].get(f"{name}|basis")
        if not v:
            continue
        for key, title in (("regime", "Marktphase"), ("session", "Session"), ("htf_align", "Trend H1/H4"), ("usd_align", "Dollar")):
            for g, m in sorted(v["context"][key].items()):
                lab = SESSION_LABEL.get(g, g)
                ctx_rows.append(f"<tr><td class='l'>{e(STRATEGIES[name].title)}</td><td class='l'>{title}: {e(lab)}</td>{_metric_cells(m)}</tr>")
    out.append("<h3>Wann funktioniert welche Strategie? (Backtest, ohne Filter, gesamter Zeitraum)</h3>"
               "<div class='card wrap'><details><summary>Tabelle anzeigen</summary><table><tr><th class='l'>Strategie</th><th class='l'>Kontext</th>"
               f"<th>n</th><th>Treffer</th><th>Ø R</th><th>PF</th><th>Verlust i. F.</th></tr>{''.join(ctx_rows)}</table></details></div>")

    # Monatsübersicht Backtest
    months: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for vid in r["ranking"][:6]:
        for t in r["variants"][vid]["trades"]:
            months[t[0][:7]][vid].append(t[2])
    if months:
        top = r["ranking"][:6]
        head = "<tr><th>Monat</th>" + "".join(f"<th class='l'>{_vlabel(v)}</th>" for v in top) + "</tr>"
        body = ""
        for mth in sorted(months):
            body += f"<tr><td>{mth}</td>" + "".join(
                (lambda rs: f"<td>{sum(1 for x in rs if x > 0)}✓ {sum(1 for x in rs if x <= 0)}✗ <span class='{'good' if sum(rs) > 0 else 'bad'}'>{sum(rs):+.1f}R</span></td>"
                 if rs else "<td>–</td>")(months[mth].get(v, [])) for v in top) + "</tr>"
        out.append(f"<h3>Backtest pro Monat (Top 6)</h3><div class='card wrap'><table>{head}{body}</table></div>")
    return "\n".join(out)


def journal_section(sig: pd.DataFrame, min_rel: int) -> str:
    out = ["<h2>Live-Journal (echte Thesen seit Start)</h2>"]
    if sig.empty:
        out.append("<div class='card'>Noch keine Live-Thesen. Starten mit <code>python -m goldsignal loop</code>.</div>")
        return "\n".join(out)
    closed = sig[sig["status"] != "open"]
    n_open = int((sig["status"] == "open").sum())
    m_all = summarize(closed["r"].tolist(), min_rel)
    notified = closed[closed["notified"] == 1]
    m_not = summarize(notified["r"].tolist(), min_rel)
    pnl = float(notified["pnl_acc"].fillna(0).sum()) if not notified.empty else 0.0
    out.append("<div class='kpis'>"
               f"<div class='kpi'><span class='muted'>Thesen gesamt</span><b>{len(sig)}</b><span class='muted'>{n_open} offen</span></div>"
               f"<div class='kpi'><span class='muted'>Abgeschlossen (alle Varianten)</span><b>{m_all['n']}</b>"
               f"<span class='muted'>Treffer {fmt_pct(m_all['winrate'])}, Ø {fmt_r(m_all['avg_r'])}</span></div>"
               f"<div class='kpi'><span class='muted'>Gemeldete Signale</span><b>{m_not['n']}</b>"
               f"<span class='muted'>Treffer {fmt_pct(m_not['winrate'])}, Ø {fmt_r(m_not['avg_r'])}</span></div>"
               f"<div class='kpi'><span class='muted'>Ergebnis gemeldeter Signale</span><b class='{'good' if pnl >= 0 else 'bad'}'>{pnl:+.2f}</b>"
               "<span class='muted'>bei empfohlener Größe (inkl. Spread)</span></div></div>")
    rows = []
    for vid, g in closed.groupby("variant"):
        m = summarize(g["r"].tolist(), min_rel)
        ml = summarize(g[g["direction"] == "long"]["r"].tolist(), min_rel)
        ms = summarize(g[g["direction"] == "short"]["r"].tolist(), min_rel)
        rows.append((m["avg_r"] or -9, f"<tr><td class='l'>{_vlabel(vid)}</td>{_metric_cells(m)}"
                     f"<td>{ml['n']}</td><td>{fmt_pct(ml['winrate'])}</td>{_cell_r(ml['avg_r'])}"
                     f"<td>{ms['n']}</td><td>{fmt_pct(ms['winrate'])}</td>{_cell_r(ms['avg_r'])}</tr>"))
    rows.sort(key=lambda x: x[0], reverse=True)
    out.append("<div class='card wrap'><table><tr><th class='l'>Variante</th><th>n</th><th>Treffer</th><th>Ø R</th><th>PF</th>"
               "<th>Verlust i. F.</th><th>Kauf n</th><th>Kauf Treffer</th><th>Kauf Ø R</th><th>Verk. n</th><th>Verk. Treffer</th><th>Verk. Ø R</th></tr>"
               + "".join(r for _, r in rows) + f"</table><p class='muted'>* unter {min_rel} Trades: noch nicht belastbar.</p></div>")

    # Kontext live
    ctx_rows = []
    for name, g in closed.groupby("strategy"):
        for key, title in (("regime", "Marktphase"), ("session", "Session"), ("news_label", "News")):
            for val, gg in g.groupby(key):
                ctx_rows.append(f"<tr><td class='l'>{e(STRATEGIES[name].title if name in STRATEGIES else name)}</td>"
                                f"<td class='l'>{title}: {e(SESSION_LABEL.get(val, str(val)))}</td>{_metric_cells(summarize(gg['r'].tolist(), min_rel))}</tr>")
    out.append("<h3>Wann funktioniert welche Strategie? (Live, alle Varianten)</h3><div class='card wrap'><table>"
               "<tr><th class='l'>Strategie</th><th class='l'>Kontext</th><th>n</th><th>Treffer</th><th>Ø R</th><th>PF</th><th>Verlust i. F.</th></tr>"
               + "".join(ctx_rows) + "</table></div>")

    # Wochen- / Monatstabellen je Strategie und Richtung (Basis-Variante = jedes Strategiesignal einmal)
    base = closed[closed["variant"].str.endswith("|basis")].copy()
    if not base.empty:
        base["t"] = pd.to_datetime(base["created_at"], utc=True)
        for label, fmt in (("Woche", "%G-KW%V"), ("Monat", "%Y-%m")):
            base["p"] = base["t"].dt.strftime(fmt)
            piv = []
            for (p, st, d), g in base.groupby(["p", "strategy", "direction"]):
                hits, miss = int((g["r"] > 0).sum()), int((g["r"] <= 0).sum())
                piv.append(f"<tr><td>{p}</td><td class='l'>{e(STRATEGIES[st].title if st in STRATEGIES else st)}</td>"
                           f"<td class='l'>{'Kauf' if d == 'long' else 'Verkauf'}</td><td class='good'>{hits}</td><td class='bad'>{miss}</td>"
                           f"{_cell_r(round(float(g['r'].sum()), 2))}</tr>")
            out.append(f"<details class='card'><summary>Treffer/Fehlschläge pro {label} je Strategie (ohne Filter)</summary><div class='wrap'><table>"
                       f"<tr><th>{label}</th><th class='l'>Strategie</th><th class='l'>Richtung</th><th>Treffer</th><th>Fehlschläge</th><th>Σ R</th></tr>"
                       + "".join(reversed(piv)) + "</table></div></details>")
    return "\n".join(out)


def calendar_section(sig: pd.DataFrame, tz: str) -> str:
    out = ["<h2>Kalender (Live-Thesen)</h2>"]
    if sig.empty:
        out.append("<div class='card muted'>Noch keine Daten.</div>")
        return "\n".join(out)
    z = ZoneInfo(tz)
    data = [{"d": pd.Timestamp(r.created_at).tz_convert(z).strftime("%Y-%m-%d"), "s": r.strategy, "v": r.variant.split("|")[1],
             "dir": r.direction, "st": r.status, "r": None if pd.isna(r.r) else float(r.r), "n": int(r.notified)}
            for r in sig.itertuples()]
    opts = "".join(f"<option value='{k}'>{e(v.title)}</option>" for k, v in STRATEGIES.items())
    fopts = "".join(f"<option value='{k}'{' selected' if k == 'basis' else ''}>{e(v)}</option>" for k, v in FILTER_LABEL.items())
    out.append(f"""<div class='card'>
<label>Strategie <select id='cs'><option value=''>alle</option>{opts}</select></label>
<label>Filter <select id='cf'><option value=''>alle</option>{fopts}</select></label>
<label>Richtung <select id='cd'><option value=''>beide</option><option value='long'>Kauf</option><option value='short'>Verkauf</option></select></label>
<label><input type='checkbox' id='cn'> nur gemeldete</label>
<p class='muted'>✓ = Ergebnis positiv (meist Ziel), ✗ = negativ (meist Stop), ⏳ = offen. Zahl in Klammern: Σ R des Tages.</p>
<div id='cal' class='months'></div></div>
<script>
const SIG={json.dumps(data)};
const WD=['Mo','Di','Mi','Do','Fr','Sa','So'];
function render(){{
 const s=document.getElementById('cs').value,f=document.getElementById('cf').value,d=document.getElementById('cd').value,n=document.getElementById('cn').checked;
 const rows=SIG.filter(x=>(!s||x.s===s)&&(!f||x.v===f)&&(!d||x.dir===d)&&(!n||x.n===1));
 const byDay={{}};rows.forEach(x=>{{(byDay[x.d]=byDay[x.d]||[]).push(x)}});
 const days=Object.keys(byDay).sort();const root=document.getElementById('cal');root.innerHTML='';
 const last=days.length?new Date(days[days.length-1]+'T12:00:00'):new Date();
 for(let k=2;k>=0;k--){{
  const m=new Date(last.getFullYear(),last.getMonth()-k,1);const box=document.createElement('div');
  let h='<h3>'+m.toLocaleDateString('de-DE',{{month:'long',year:'numeric'}})+'</h3><div class="cal">'+WD.map(w=>'<div class="h">'+w+'</div>').join('');
  const off=(m.getDay()+6)%7;for(let i=0;i<off;i++)h+='<div class="o"></div>';
  const dim=new Date(m.getFullYear(),m.getMonth()+1,0).getDate();let mw=0,ml=0,mr=0;
  for(let dd=1;dd<=dim;dd++){{
   const key=m.getFullYear()+'-'+String(m.getMonth()+1).padStart(2,'0')+'-'+String(dd).padStart(2,'0');
   const xs=byDay[key]||[];const w=xs.filter(x=>x.r!==null&&x.r>0).length,l=xs.filter(x=>x.r!==null&&x.r<=0).length,o=xs.filter(x=>x.st==='open').length;
   const r=xs.reduce((a,x)=>a+(x.r||0),0);mw+=w;ml+=l;mr+=r;
   h+='<div class="'+(xs.length?(r>0?'pos':r<0?'neg':''):'')+'"><b>'+dd+'</b><br>'+(w?'✓'+w+' ':'')+(l?'✗'+l+' ':'')+(o?'⏳'+o:'')+(w+l?'<br>('+(r>0?'+':'')+r.toFixed(1)+'R)':'')+'</div>';
  }}
  h+='</div><p class="muted">Monat: '+mw+' Treffer, '+ml+' Fehlschläge, Σ '+mr.toFixed(1)+' R</p>';box.innerHTML=h;root.appendChild(box);
 }}
}}
['cs','cf','cd','cn'].forEach(id=>document.getElementById(id).addEventListener('change',render));render();
</script>""")
    return "\n".join(out)


def recent_section(sig: pd.DataFrame, tz: str, limit: int = 25) -> str:
    if sig.empty:
        return ""
    z = ZoneInfo(tz)
    base = sig[sig["variant"].str.endswith("|basis")].tail(limit).iloc[::-1]
    items = []
    for r in base.itertuples():
        t = pd.Timestamp(r.created_at).tz_convert(z).strftime("%d.%m. %H:%M")
        res = "offen" if r.status == "open" else f"{r.status} {r.r:+.2f} R"
        cls = "" if r.status == "open" else ("good" if (r.r or 0) > 0 else "bad")
        items.append(f"<details class='card'><summary>{t} · {'Kauf' if r.direction == 'long' else 'Verkauf'} · "
                     f"{e(STRATEGIES[r.strategy].title if r.strategy in STRATEGIES else r.strategy)} · Stärke {r.strength}/{r.strength_max} · "
                     f"<span class='{cls}'>{e(res)}</span>{' · 📲' if r.notified else ''}</summary><pre>{e(r.thesis or '')}</pre></details>")
    return "<h2>Letzte Thesen</h2>" + "".join(items)


METHODS = """<h2>Methoden kurz erklärt</h2><div class='card'><ul>
<li><b>R</b>: Ergebnis als Vielfaches des Risikos. +1 R = so viel gewonnen, wie beim Stop verloren worden wäre. Kauf zum Ask, Verkauf zum Bid – der Spread ist enthalten.</li>
<li><b>Trefferquote</b>: Anteil der Trades mit positivem Ergebnis. <b>Profit-Faktor</b>: Summe Gewinne / Summe Verluste (über 1 = profitabel).</li>
<li><b>In-Sample / Out-of-Sample</b>: Der letzte Teil der Daten wird beim Entwickeln nicht angeschaut und nur zur Prüfung genutzt.</li>
<li><b>Zufallsvergleich</b>: Gleich viele Käufe und Verkäufe zu zufälligen Zeitpunkten mit denselben Stop/Ziel-Regeln. p = Anteil der Zufallsserien, die mindestens genauso gut waren. Werden viele Varianten getestet, ist ein kleines p bei einigen davon reiner Zufall (Mehrfachtest-Problem) – daher die strenge Bonferroni-Prüfung.</li>
<li><b>Walk-Forward</b>: Die bisher beste Variante wird gewählt und nur im nächsten, unbekannten Abschnitt gemessen.</li>
<li><b>ATR</b>: durchschnittliche Schwankungsbreite je Kerze; Stop und Ziel sind Vielfache davon. <b>ADX</b>: Trendstärke – ab ca. 22 Trend, darunter Seitwärtsphase.</li>
<li><b>Stärke</b>: Anzahl der Faktoren (Trend H1/H4, Marktphase, Dollar, Zinsen, News, COT, Positionsbuch, Session, Marken), die für das Signal sprechen.</li>
<li><b>Belastbar</b>: erst ab ca. 30 abgeschlossenen Trades. Darunter sind Quoten stark vom Zufall geprägt.</li>
</ul><p class='muted'>Signale sind Wahrscheinlichkeiten, keine Garantien. Das System dient zum Lernen und Prüfen von Strategien, nicht als Anlageberatung.</p></div>"""


def build(settings, db: Database, path: str | None = None) -> str:
    path = path or settings.report_path
    sig = db.signals()
    bt = db.latest_backtest()
    now = utcnow().astimezone(ZoneInfo(settings.timezone))
    body = [f"<h1>Gold-Signale XAU/USD – Auswertung</h1><p class='muted'>Erstellt {now:%d.%m.%Y %H:%M} · "
            f"Einstieg {e(settings.entry_tf)}, Trendfilter {e('/'.join(settings.trend_tfs))} · nur Signale, kein automatisches Handeln</p>",
            backtest_section(bt, settings.min_trades_reliable),
            journal_section(sig, settings.min_trades_reliable),
            calendar_section(sig, settings.timezone),
            recent_section(sig, settings.timezone),
            METHODS]
    doc = (f"<!doctype html><html lang='de'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Gold-Signale Report</title><style>{CSS}</style></head><body><main>{''.join(body)}</main></body></html>")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(doc, encoding="utf-8")
    return path
