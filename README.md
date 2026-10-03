# Gold-Signalsystem XAU/USD (OANDA)

Ein Python-Programm, das für Gold (XAU_USD) über die OANDA-API **Kauf- und Verkaufssignale** erzeugt,
dich bei guten Gelegenheiten **aufs Handy benachrichtigt**, zu jedem Signal eine **prüfbare These** speichert
und **dokumentiert, wie oft die These aufgegangen ist**. Mehrere Strategien laufen parallel und werden
**ehrlich verglichen** – gegen Zufallseinstiege, gegen „einfach halten“ und auf Daten, die beim Entwickeln
nicht benutzt wurden.

> **Kein automatisches Handeln.** Das Programm sendet keine Orders, es liest nur Daten.
> Signale sind Wahrscheinlichkeiten, keine Garantien. Das System dient zum Lernen und Prüfen
> von Strategien und ist **keine Anlageberatung**. Starte mit dem **OANDA-Demokonto**.

---

## Inhalt

1. [Schnellstart](#schnellstart)
2. [Befehle](#befehle)
3. [Was das Programm macht](#was-das-programm-macht)
4. [Strategien und Filter](#strategien-und-filter)
5. [These pro Signal](#these-pro-signal)
6. [Risiko-Hilfe für kleine Konten](#risiko-hilfe-für-kleine-konten)
7. [Benachrichtigungen](#benachrichtigungen)
8. [Bewertung: Backtest, Zufall, Walk-Forward](#bewertung-backtest-zufall-walk-forward)
9. [Dauerbetrieb (PC, Server, Raspberry Pi)](#dauerbetrieb-pc-server-raspberry-pi)
10. [Datenquellen und Ausfälle](#datenquellen-und-ausfälle)
11. [Methoden kurz erklärt](#methoden-kurz-erklärt)
12. [Projektaufbau](#projektaufbau)

---

## Schnellstart

```bash
git clone <dieses Repo> && cd Claude-anf-nger
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # danach .env ausfüllen
```

**Ohne OANDA-Zugang ausprobieren** (synthetische Kurse, eigene Demo-Datenbank):

```bash
python -m goldsignal backtest --demo
python -m goldsignal scan --demo --dry-run
python -m goldsignal report --demo           # -> reports/report_demo.html
```

**Mit OANDA-Demokonto:**

1. Bei OANDA ein *Demo-/Practice*-Konto eröffnen.
2. Im Kundenbereich unter „API-Zugang verwalten“ einen Token erzeugen.
3. In `.env` eintragen: `OANDA_TOKEN`, `OANDA_ACCOUNT_ID`, `OANDA_ENV=practice`.
4. Push-Kanal einrichten (siehe [Benachrichtigungen](#benachrichtigungen)).

```bash
python -m goldsignal check          # prüft alle Datenquellen einzeln
python -m goldsignal test-notify    # Testnachricht aufs Handy
python -m goldsignal backtest       # lädt ~1 Jahr Kerzen (wird zwischengespeichert)
python -m goldsignal loop           # Dauerbetrieb
```

---

## Befehle

| Befehl | Zweck |
|---|---|
| `backtest [--days N] [--demo]` | Alle 7 Strategien × 7 Filter auf historischen Daten testen, Rangliste ausgeben, Report schreiben |
| `scan [--dry-run] [--demo]` | Einmal die letzte abgeschlossene Kerze prüfen, Thesen speichern, ggf. melden |
| `evaluate` | Offene Thesen mit 1-Minuten-Kerzen auswerten: Ziel, Stop oder abgelaufen |
| `report [--out DATEI]` | HTML-Report mit Kalender erzeugen (`reports/report.html`) |
| `loop` | Dauerbetrieb: scannt nach jedem Kerzenschluss, wertet aus, Tageszusammenfassung, Fehlermeldungen |
| `size --stop 8.5 [--entry 3800] [--account 800] [--risk 1]` | Positionsgrößenrechner |
| `summary [--send]` | Tageszusammenfassung anzeigen bzw. senden |
| `test-notify` | Testnachricht senden |
| `check` | Alle Datenquellen (OANDA, News, Kalender, COT) prüfen |

Allgemeine Optionen: `--env pfad/zur/.env`, `-v` (ausführliches Log).

---

## Was das Programm macht

```
 OANDA-Kerzen M15/H1/H4 ─┐
 EUR/USD (Dollar)        ├─► Merkmale je Kerze ─► 7 Strategien × 7 Filter ─► These + Positionsgröße
 US-Anleihe (Zinsen)     │   (Trend, Phase,          │                             │
 Positionsbuch           │    Session, Termine,      │                             ▼
 News 7 Tage             │    Marken, Dollar, …)     │                    SQLite-Journal
 Kalender, COT          ─┘                           │                             │
                                                     ▼                             ▼
                                         Melde-Regeln (Stärke,          Auswertung (Ziel/Stop/
                                         Session, Pause, Tageslimit)     abgelaufen, in R)
                                                     │                             │
                                                     ▼                             ▼
                                              Push aufs Handy              HTML-Report + Kalender
```

* **Jede** Strategie-Variante, die feuert, wird als These im Journal gespeichert – auch wenn sie nicht gemeldet wird.
  So lässt sich später ehrlich vergleichen, welche Variante live funktioniert.
* **Gemeldet** wird nur das beste Signal einer Kerze, wenn die Melde-Regeln erfüllt sind.

---

## Strategien und Filter

| Name | Art | Regel |
|---|---|---|
| `ma_20_50` | Trendfolge | SMA 20 kreuzt SMA 50 |
| `ma_50_200` | Trendfolge | SMA 50 kreuzt SMA 200 |
| `donchian_20` | Ausbruch | Schluss über Hoch / unter Tief der letzten 20 Kerzen |
| `rsi_reversion` | Gegenbewegung | RSI(14) steigt über 30 (Kauf) / fällt unter 70 (Verkauf) |
| `bollinger_reversion` | Gegenbewegung | Kurs schließt von außerhalb wieder ins Bollinger-Band (20, 2) |
| `macd_trend` | Trendfolge | MACD kreuzt Signallinie, nur in Richtung EMA 200 |
| `combo` | Kombi | 4 Technik-Teilsignale einig **und** Dollar **und** (falls verfügbar) Zinsen und News in dieselbe Richtung |

Jede Strategie wird **mit und ohne** Filter getestet (zusammen 49 Varianten, z. B. `donchian_20|htf`):

| Filter | Bedeutung |
|---|---|
| `basis` | ohne Filter |
| `htf` | Trend auf H1 **und** H4 (Kurs über/unter EMA 50) zeigt in Signalrichtung |
| `phase` | Marktphase passt: Trend-/Ausbruchsstrategien nur bei ADX ≥ 22, Gegenbewegung nur im Seitwärtsmarkt |
| `session` | nur London/New-York-Überschneidung |
| `termine` | Sperre ±1,5 Std. um FOMC, CPI, NFP & Co. |
| `marken` | Trend/Ausbruch: keine Vortages-/Vorwochen-Marke oder runde Zahl (50 $) zwischen Einstieg und Ziel. Gegenbewegung: Einstieg nahe einer solchen Marke |
| `alle` | alle Filter zusammen |

**Einstieg** zum aktuellen Kurs nach Kerzenschluss (Kauf zum Ask, Verkauf zum Bid),
**Stop** 1,5 × ATR, **Ziel** 2,0 × ATR, **Verfall** nach 4 Stunden – alles in `.env` einstellbar.
Signale, bei denen der Spread mehr als 20 % des Stop-Abstands ausmacht, werden weder im Backtest
gezählt noch gemeldet – bei kurzen Trades frisst der Spread sonst den Vorteil.

---

## These pro Signal

Zu jedem Signal wird in der Datenbank gespeichert:

* Richtung, Einstieg, Stop, Ziel (ATR-basiert), Verfallszeit, Positionsgröße
* **Dafür / Dagegen**: welche Faktoren passen (H1/H4-Trend, Marktphase, Dollar, Zinsen, News, COT, Positionsbuch, Session, Marken, Termine)
* **Scheitert, wenn …**: Stop, typischer Fehlschlag der Strategie (z. B. Fehlausbruch), Zeitablauf, anstehender Termin
* **Kontext**: Marktphase, Session, News-Stimmung, Dollar- und Zinsrichtung, Termine der nächsten 24 Std., COT-Lage
* **Risiko-Hinweise**: Rollover (~23 Uhr MEZ) im Haltezeitraum, nahe Wochenschluss (Gap-Risiko), zu hoher Spread

Optional formuliert **Claude** die These zusätzlich in natürlicher Sprache (`CLAUDE_THESIS=true`,
`pip install anthropic`, `ANTHROPIC_API_KEY` setzen). Fällt die API aus, bleibt der Standardtext.

Die Auswertung läuft automatisch: **Ziel erreicht**, **Stop erreicht** oder **abgelaufen** –
das Ergebnis in **R** (Vielfaches des Risikos), mit Bid/Ask, also **inklusive Spread**.
Werden Stop und Ziel in derselben Kerze berührt, zählt konservativ der Stop.

---

## Risiko-Hilfe für kleine Konten

Für ein Konto unter 1.000 € gelten diese Vorgaben (alle in `.env`):

* **Risiko pro Trade** standardmäßig 1 %, höchstens 2 % (`RISK_PCT`, `MAX_RISK_PCT`)
* **Mindestgröße**: XAU_USD wird bei OANDA in Unzen gehandelt; die Mindestgröße wird live abgefragt
  (Fallback `MIN_TRADE_UNITS=1`). Ist der Stop so weit, dass schon die Mindestgröße mehr als das
  erlaubte Risiko kostet, wird das Signal als **„zu groß für Konto“** markiert –
  die Größe wird **nicht** aufgerundet und das Risiko **nicht** erhöht.
* **Hebel**: Margin mit mindestens 5 % (EU-Privatkunden: Gold max. 1:20), höchstens 50 % des Kontos als Margin.
* **Tagesverlust-Grenze**: Erreichen die gemeldeten Signale des Tages (bei empfohlener Größe) −3 %,
  werden bis Mitternacht keine weiteren Signale gesendet.

Beispiel: Konto 800 €, Risiko 1 % = 8 €, Gold 3.800 $, EUR/USD 1,10:

```bash
python -m goldsignal size --stop 8 --entry 3800 --account 800 --offline
# -> 1 Unze, Risiko 7,27 €, Margin ~173 €
python -m goldsignal size --stop 12 --entry 3800 --account 800 --offline
# -> zu groß für Konto: 1 Unze würde 10,91 € riskieren; größter passender Stop 8,80 $
```

Gut zu wissen: Auf M15 liegt der ATR bei Gold oft bei 5–15 $. Mit 1,5 × ATR Stop sind viele
Signale für ein sehr kleines Konto zu groß. Optionen: `ENTRY_TF=M5` (engere Stops, aber relativ teurerer Spread),
kleinerer `STOP_ATR` oder einfach nur die passenden Signale nehmen. Das Programm entscheidet das nicht für dich.

---

## Benachrichtigungen

**ntfy** (einfachste Variante): App „ntfy“ installieren, ein schwer erratbares Thema wählen (z. B. `gold-x7k2p9q`),
in der App abonnieren und in `.env` als `NTFY_TOPIC` eintragen.

**Telegram**: Bei `@BotFather` einen Bot anlegen → `TELEGRAM_BOT_TOKEN`; dem Bot schreiben und die
Chat-ID (z. B. über `@userinfobot`) als `TELEGRAM_CHAT_ID` eintragen. Beide Kanäle können gleichzeitig aktiv sein.

Eine Signalnachricht sieht so aus:

```
🟢 KAUF Gold 3.812,40 | Stärke 6/9
Einstieg: 3.812,40 (Ask, jetzt, Market-Order)
Stop: 3.801,90 (10,50 $)
Ziel: 3.826,40 (14,00 $) | CRV 1,33 vor Spread, Spread 0,35 $
Größe: 1 Unze(n) = 1 Einheiten XAU_USD | Risiko 9,55 EUR (1,19 %) | Margin ~173 EUR
Gültig bis: 16:45 Uhr – danach These verfallen
Strategie: Ausbruch Donchian 20 [alle] + 3 weitere Variante(n) gleicher Richtung
Bisher: Live 9/17 Treffer (53 %, Ø +0,12 R, noch nicht belastbar) | Backtest OOS 46 %, Ø +0,05 R (n=81)
Session: London/NY-Überschneidung – Stufe normal
Dafür: H1-Trend in Signalrichtung; H4-Trend in Signalrichtung; Marktphase passt (trend, ADX 27); …
Dagegen: COT: Hedgefonds bereits einseitig in Signalrichtung
Scheitert, wenn: Stop 3.801,90 wird erreicht; der Kurs zurück in den alten Kanal fällt …
```

**Melde-Regeln**

* **Qualitätsstufe nach Session**: London/New York → mindestens `MIN_STRENGTH_MAIN` (3) Faktoren dafür;
  Asien/Nacht → mindestens `MIN_STRENGTH_OFF` (5). Außerdem müssen mehr Faktoren dafür als dagegen sprechen.
* **Pause** nach einem Signal (`COOLDOWN_MINUTES`, Standard 60) – kein Spam.
* **Ruhezeiten** optional (`QUIET_HOURS=23:00-07:00`). Thesen werden trotzdem gespeichert.
* **Nur bestimmte Varianten melden**: `NOTIFY_VARIANTS=combo|*,donchian_20|alle` – sinnvoll, sobald
  Backtest und Live-Journal eine Variante als belastbar zeigen.
* **Tageszusammenfassung** um `DAILY_SUMMARY_TIME` (Mo–Fr).
* **Störungen**: Meldung bei Absturz, bei Fehlern im Durchlauf (höchstens alle 30 Min.) und wenn
  während der Handelszeit keine frischen Kerzen kommen (höchstens alle 2 Std.).

---

## Bewertung: Backtest, Zufall, Walk-Forward

`python -m goldsignal backtest` zeigt **Backtest und Live-Journal getrennt** (auch im Report).

* **Kennzahlen**: Trefferquote, Ø R pro Trade, Profit-Faktor, maximaler Verlust in Folge, Anzahl Trades – auch getrennt nach Kauf und Verkauf.
* **In-Sample / Out-of-Sample**: Die ersten 70 % des Zeitraums gelten als „Entwicklungsdaten“, die letzten 30 % werden
  nur zur Prüfung benutzt. Die Rangliste sortiert nach dem **Out-of-Sample**-Ergebnis.
* **Zufallsvergleich**: Für jede Variante werden 300-mal gleich viele Käufe und Verkäufe zu zufälligen Zeitpunkten
  im selben Zeitraum mit denselben Stop/Ziel-Regeln simuliert. *p* = Anteil der Zufallsserien, die mindestens so gut waren.
  Weil Gold oft stark steigt, ist das fairer als ein Vergleich mit null.
* **Einfach halten**: Kauf zu Beginn, Verkauf am Ende jedes Abschnitts (in %). Zum Vergleich: bei 1 % Risiko pro Trade entspricht +10 R etwa +10 %.
* **Walk-Forward**: Der Zeitraum wird in 6 Abschnitte geteilt. Für jeden Abschnitt wird die **bis dahin beste** Variante
  gewählt und **nur im folgenden** Abschnitt gemessen – so zeigt sich, ob „die Beste auswählen“ überhaupt funktioniert.
* **Belastbarkeit**: unter 30 Trades wird jedes Ergebnis als **nicht belastbar** markiert.
* **Mehrfachtest-Problem**: Bei 49 Varianten sind rein zufällig 2–3 „signifikante“ Treffer zu erwarten.
  Das Urteil unterscheidet daher „besser als Zufall“ von „besser als Zufall auch nach strenger (Bonferroni-)Korrektur“.
* **Ehrliches Fazit**: Schlägt keine Variante den Zufall, steht das im Report ausdrücklich so da.

Hinweise zum Backtest: Die News-Stimmung und das Positionsbuch gibt es nur live (keine historischen Daten) –
im Backtest zählen sie als neutral. Der COT-Report wird erst ab seiner Veröffentlichung (Freitag) verwendet,
höhere Zeitrahmen erst nach Kerzenschluss – es gibt keinen Blick in die Zukunft.

---

## Dauerbetrieb (PC, Server, Raspberry Pi)

Das Programm braucht wenig Leistung; ein Raspberry Pi 3/4/5 reicht. Es scannt nur Mo–Fr während der Gold-Handelszeit.

**Linux / Raspberry Pi mit systemd** (empfohlen – startet nach Absturz oder Neustart automatisch):

```bash
sudo apt install python3-venv git
git clone <dieses Repo> ~/Claude-anf-nger && cd ~/Claude-anf-nger
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
.venv/bin/python -m goldsignal check

# Pfade/Benutzer in deploy/goldsignal.service anpassen, dann:
sudo cp deploy/goldsignal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now goldsignal
journalctl -u goldsignal -f          # Log ansehen
```

**Alternative cron**: siehe `deploy/crontab.example` (scan/evaluate alle 15 Min., Report stündlich, Backtest samstags).

**Windows**: `deploy\start_windows.bat` über die Aufgabenplanung „Bei Anmeldung“ starten. Der PC darf nicht in den Ruhezustand gehen.

**Report ansehen**: `reports/report.html` im Browser öffnen. Auf dem Pi z. B. mit
`python3 -m http.server -d reports 8080` im Heimnetz erreichbar machen.

**Datensicherung**: Alles Wichtige steht in `data/goldsignal.db` (SQLite) – diese Datei regelmäßig sichern.

---

## Datenquellen und Ausfälle

| Quelle | Wofür | Wenn sie ausfällt |
|---|---|---|
| OANDA Kerzen XAU_USD (M15, H1, H4) | Signale, Auswertung | Scan fällt aus; nach 3 Fehlversuchen Push-Meldung |
| OANDA Preis, Konto, Instrument | Einstieg, Kontostand, Mindestgröße, Margin | letzte Kerze / `ACCOUNT_SIZE` / Standardwerte |
| OANDA EUR_USD | Dollarstärke | Faktor entfällt, `combo` feuert nicht |
| OANDA `USB10Y_USD` | Zinsrichtung (nicht überall verfügbar) | Faktor entfällt, neuer Versuch nach 12 Std. |
| OANDA Positionsbuch | Kontra-Indikator (≥ 60 % long = bärisch) | Faktor entfällt |
| Google-News-RSS (7 Tage) | News-Stimmung bullisch/bärisch | Faktor „unbekannt“ |
| faireconomy.media (ForexFactory-Export) | Wochenkalender USD, hohe Wichtigkeit | eingebaute FOMC-Termine + NFP-Regel + `data/events.csv` |
| CFTC COT (Managed Money, Gold 088691) | Positionierung der Hedgefonds | Faktor entfällt |

Die Live-Kalendertermine werden in der Datenbank gespeichert, damit spätere Backtests sie kennen.
Eigene Termine (z. B. CPI-Daten) kannst du in `data/events.csv` eintragen – Vorlage: `data/events.example.csv`.
Gold-ETF-Zuflüsse sind als optionale Quelle noch nicht angebunden (kein frei zugänglicher, stabiler Datendienst);
Schlagzeilen zu ETF-Zu-/Abflüssen fließen aber in die News-Stimmung ein.

---

## Methoden kurz erklärt

* **SMA/EMA** – gleitende Durchschnitte. Kreuzt der schnelle den langsamen nach oben, deutet das auf einen beginnenden Aufwärtstrend.
* **Donchian-Kanal** – höchstes Hoch / tiefstes Tief der letzten 20 Kerzen. Ein Schluss darüber gilt als Ausbruch.
* **RSI** – misst, wie stark die letzten Bewegungen nach oben vs. unten waren (0–100). Unter 30 „überverkauft“, über 70 „überkauft“.
* **Bollinger-Bänder** – Durchschnitt ± 2 Standardabweichungen. Rückkehr ins Band = mögliche Gegenbewegung.
* **MACD** – Differenz zweier EMAs (12/26) und deren Signallinie (9). Kreuzungen zeigen Momentumwechsel.
* **ATR** – durchschnittliche Schwankungsbreite je Kerze. Stop und Ziel passen sich so der aktuellen Volatilität an.
* **ADX** – Trendstärke. Über ~22 Trendphase, darunter Seitwärtsphase.
* **R-Vielfaches** – Ergebnis geteilt durch das Risiko. −1 R = Stop, +1,33 R = Ziel (bei 2,0/1,5 ATR, vor Spread).
* **Profit-Faktor** – Summe der Gewinne / Summe der Verluste. Über 1 = profitabel.
* **Kontra-Indikatoren** – Sind sehr viele Privatkunden (Positionsbuch) oder Hedgefonds (COT) bereits auf einer Seite, ist das eher ein Warnsignal für diese Seite.
* **Out-of-Sample / Walk-Forward / Zufallsvergleich** – siehe [Bewertung](#bewertung-backtest-zufall-walk-forward).

---

## Projektaufbau

```
goldsignal/
  cli.py          Befehle (backtest, scan, evaluate, report, loop, size, …)
  config.py       Einstellungen aus .env
  sources/        OANDA, News, Wirtschaftskalender, COT
  indicators.py   SMA, EMA, RSI, ATR, ADX, Bollinger, MACD, Donchian
  features.py     Kontext je Kerze, Filter, Faktoren (dafür/dagegen)
  strategies.py   die 7 Strategien
  sim.py          Trade-Simulation mit Bid/Ask (Spread inklusive)
  backtest.py     IS/OOS, Zufallsvergleich, Buy & Hold, Walk-Forward, Urteil
  scanner.py      Live-Scan, Melde-Regeln, Auswertung offener Thesen, Tageszusammenfassung
  thesis.py       These-Text (optional mit Claude)
  risk.py         Positionsgröße, Mindestgröße, Hebel, Tageslimit
  notify.py       ntfy / Telegram
  report.py       HTML-Report mit Kalender
  runner.py       Dauerbetrieb
  db.py           SQLite
tests/            pytest – `python -m pytest`
deploy/           systemd-Dienst, cron-Beispiel, Windows-Start
```

Tests ausführen: `pip install pytest && python -m pytest`.

---

**Haftungsausschluss:** Der Handel mit CFDs auf Gold ist mit hohem Risiko verbunden; ein großer Teil der
Privatanleger verliert dabei Geld. Dieses Programm gibt keine Anlageempfehlungen. Nutze es zum Lernen,
zuerst mit dem Demokonto, und triff deine Entscheidungen selbst.
