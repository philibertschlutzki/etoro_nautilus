Offen ist noch das hier (Stand der letzten Meldungen von gestern Abend; ob du heute schon etwas erledigt hast, sehe ich nicht):

**1. Jetzt, solange die US-Börse offen ist (bis 20:00 UTC)**
- [ ] Die 8 fremden Aktien (vom 15:30-Lauf, je ca. 2.000 USD) schließen. Wie: eToro-App auf Demo-Modus, Portfolio, Position antippen, Schließen. Nicht anfassen: die Bot-Positionen und die 5 Stage-C-Testpositionen (CAT, Aktien-Short, 2× FX, Krypto), die laufen für die Finanzierungsmessung bis Montag.
- [ ] Prüfen, ob #1355, #1356 und #1357 gemergt sind (#1358 ist es). Wie: auf GitHub die PR-Liste öffnen, bei grünen Checks mit Delete-Branch mergen. #1357 bringt den Freitagsbericht in main.

**2. Eine Antwort von dir**
- [ ] Krypto aus dem Paper-Bot nehmen? Antworte hier mit „Ja, Krypto-PR“ oder „Nein“. Ich lasse dann einen Draft-PR bauen, du mergst und startest nichts neu außer einer Kette, die ich dir dann gebe.

**3. Täglich, ohne dein Zutun**
- Um 06:50 UTC läuft der Stage-C-Monitor und um 06:55 UTC der Gesundheitscheck. Lesen musst du nur den Bericht, wenn du magst: `cat ~/etoro_nautilus/results/daily/$(date -u +%F).md`. Bei Auffälligkeiten (Neustart, ERROR, halber Abstand zum Stopp) steht ein Befund drin; schick mir ihn dann einfach.

**4. Mittwoch 14.10. Abend, nichts zu tun**
- Der Hypothesen-Thread rechnet B+ mit den gemessenen Spreads neu. Die Spread-Messung läuft bis Fr 16.10.

**5. Freitag 16.10., ab 21:30 UTC**
- [ ] Schreib im Thread „Hypothesen H4 bis H8 prüfen“ „Wochenbericht“. Die Gerätesession liest `results/weekly/` selbst und sagt dir, ob der Aktienspread im Bot (jetzt 7 bps, gemessen 26 bps) angehoben werden muss und ob B+ in den Bot soll.

**6. Montag 19.10., nach 14:00 UTC (16:00 Uhr bei dir)**
- [ ] Diese eine Kette ausführen und die Ausgabe in den Thread „Hypothesen H1 bis H3 prüfen“ schicken (sie schließt die Stage-C-Testpositionen und wertet aus):

`cd ~/ebc_stagec && P=~/etoro_nautilus/venv/bin/python3 && S=experiments/ebc/stage_c/run_stage_c.py && $P $S monitor --env demo && $P $S closeall --env demo --live --confirm-account demo && $P experiments/ebc/stage_c/evaluate_stage_c.py`

**7. Wenn es passt, ohne Frist**
- [ ] 1000-USD-Gebührenprobe wiederholen: im Thread H1 bis H3 „feeprobe jetzt“ schreiben, zwischen 13:30 und 19:30 UTC und nicht kurz vor der vollen Stunde (sonst 429 mit dem Bot).
- [ ] Alte Testposition (RBLX-Short / Instrument 6961) im alten TestUSD1-Konto in der App prüfen und schließen.
- [ ] Die alten eToro-Schlüssel, die du im Klartext gepostet hattest, in eToro erneuern.
- [ ] Sag mir, ob die Issues #1274, #1276, #1277 neu eröffnet werden sollen und ob aus der Log-Review Issues entstehen sollen. Der Krypto-Katalog-Rebuild (`--reset-catalog`) erübrigt sich, falls Krypto rausfliegt.

Bis Ende November läuft nur noch der Forward-Shadow (täglich 22:45 UTC) von allein. Parameter im Bot ändern wir diese Woche bewusst nicht, damit die Messreihen vergleichbar bleiben, außer beim Spread nach dem Freitagsbericht.
