# Satisfactory Printer

Macht aus einem 3D-Modell (`.stl` / `.obj`) eine Statue aus **lackierten Trägern** in
Satisfactory. Am besten als **Blueprint**, den du mit einem Klick platzierst. Als Reserve
gibt es einen Tastatur-Modus, der die Träger im Spiel eintippt.

## Start

```bash
pip install -r requirements.txt
npm install --prefix satisfactory_printer/blueprint_writer   # einmalig, braucht Node.js (nodejs.org)
python -m satisfactory_printer
```

Der Browser öffnet die App. Dort kannst du:

- **Modell** auswählen oder hochladen. Du kannst die Datei auch einfach ins Fenster ziehen.
  Hochgeladene Modelle landen in `models/`. Der Ordner wird nie zu GitHub hochgeladen, er
  ist also auch für Modelle geeignet, die du aus Spielen extrahiert hast.
- **Sockel entfernen**: Einzelteile, die komplett im unteren Teil der Höhe liegen, fallen
  weg (z. B. 16 %).
- **Form** einstellen:
  - Größe (längste Seite, 5–200 m);
  - Detail (Toleranz): kleiner heißt genauer, aber mehr Träger;
  - Trägerabstand: 1 m ergibt eine geschlossene Oberfläche.
- **Lack-Finish** wählen: Caterium (gold), Kupfer, Chrom oder Carbonstahl. Das Finish muss
  im AWESOME-Shop freigeschaltet sein.
- **Ausgabe** wählen:
  - **Blueprint**: „Blueprint erstellen“ schreibt die Dateien und bietet ein Zip zum
    Download an.
  - **Tastatur**: tippt die Träger im Spiel ein und zeigt dabei live, welche Träger schon
    gesetzt sind.

Die 3D-Ansicht zeigt jede Änderung sofort. Der Baufortschritts-Regler zeigt gesetzte
Träger fest, den nächsten Träger rot und die noch fehlenden blass.

## So entstehen die Träger

- **Flache Flächen**, also von scharfen Kanten begrenzte Flächen in jeder Neigung, werden
  mit parallelen langen Trägern belegt. Auf schrägen Flächen (Dach, Rampe) laufen sie die
  Schräge hinunter. So wird die Schräge glatt statt treppig.
- **Gewölbte Teile** werden mit **Dauben** belegt: Träger, die nebeneinander entlang der
  Form laufen, also einen Raketenrumpf hoch oder einen verdrehten Streifen entlang.
- **Scharfe Kanten** bekommen eigene Träger.
- Ein **Lückenfüller** sucht danach jede sichtbare Lücke und schließt sie mit einem flach
  aufliegenden Träger. Das Ergebnis ist 100 % bedeckte Oberfläche.
- Jeder Träger wird so **gedreht**, dass eine flache Seite auf der Oberfläche liegt.
- **Größe und Rechenzeit:** Träger sind höchstens 40 m lang. Gerechnet wird auf allen
  CPU-Kernen.

## Blueprints

- **Genauigkeit:** Ein Blueprint speichert für jeden Träger die exakte Position, Drehung und
  Länge. Es gibt kein 1-m-Raster. Die Träger sitzen eine halbe Trägerbreite nach innen
  versetzt, damit ihre Außenseite genau die Modelloberfläche bildet.
- **Größe:** Der Designer im Spiel geht bis 6×6×6 Fundamente (Mk.3, 48 m). Die Größe steht
  aber nur als Zahl in der Datei, und das Spiel nimmt auch größere Blueprints an.
  Designer-Mods gehen bis 12×12×12, und ihre Blueprints funktionieren ohne den Mod.
  „Automatisch“ macht deshalb einen Blueprint für das ganze Modell. Alternativ teilt die
  App das Modell in 12er-, Mk.3-, Mk.2- oder Mk.1-Teile; `PLACEMENT.txt` sagt dann, wohin
  jedes Teil kommt.
- **Installieren:** Kopiere die `.sbp`- und `.sbpcfg`-Dateien nach
  `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\blueprints\<Name deiner Welt>\` und lade das
  Spiel neu.
- **Material:** Zum Platzieren braucht man das ganze Material auf einmal. Die Kostenliste
  im Blueprint ist eine Schätzung: Stahlträger pro Meter, einstellbar.
- **Im Spiel noch nicht getestet.** Das Dateiformat stammt aus der Bibliothek
  [satisfactory-file-parser](https://github.com/etothepii4/satisfactory-file-parser) (MIT,
  Spielversion 1.0–1.2). Wie ein Träger gespeichert ist (Ankerpunkt am Anfang, Ausrichtung
  entlang der lokalen X-Achse, Länge in `mLength`, Finish), ist aus echten Spielständen
  übernommen. Am sichersten ist es, im Spiel einen Blueprint mit einem lackierten Träger zu
  speichern und ihn mit `--template` zu übergeben. Dann werden genau dieser Träger und
  deine Spielversion verwendet.

## Tastatur (Reserve)

Für den Fall, dass Blueprints nicht gehen: Das Programm tippt jeden Träger ein, auf dem
1-m-Raster. Du stehst still, das Fadenkreuz liegt auf einem festen Punkt, dem **Anker**.
Pro Träger läuft dann:

1. `H` sperrt das Hologramm. Der Trägeranfang wird um `Anfang − Ende` verschoben (Pfeile =
   waagerecht, Bild↑/Bild↓ = senkrecht, 1 Druck = 1 m).
2. Linksklick, ohne die Maus zu bewegen. Der Träger spannt sich vom Anfang bis zum Anker.
3. Mit dem Mausrad wird er gedreht: 6 Rasten = 90°, Rad hoch = gegen den Uhrzeigersinn,
   von der Trägerspitze aus gesehen.
4. `H` noch einmal, dann wird der ganze Träger an seinen Platz geschoben.
5. Linksklick baut ihn.

**Bedienung in der App:**
- **Starten:** Reiter „Tastatur“ öffnen, Tippgeschwindigkeit einstellen und „Start“
  drücken. Nach dem Countdown zu Satisfactory wechseln.
- **Pause und Stopp** greifen erst nach dem aktuellen Träger, damit nie einer halb
  gesetzt bleibt. Im Spiel pausiert F8, F9 bricht sofort ab.
- **Simulieren** probt das Timing, ohne Tasten zu drücken.
- **Einrichten:** Die App muss auf dem Spiele-PC laufen. Mit
  `pip install pydirectinput pynput` installierst du die Eingabe-Bibliotheken.

**Einmal in einer Testwelt kalibrieren**, mit einer Kopie von `config.example.json` und `-c`:
- **Achsen:** Kommt das Modell gespiegelt oder gedreht heraus, tausche die Einträge unter
  `keys`.
- **Ablauf:** Braucht dein Spiel eine andere Reihenfolge, passe `beam_macro` an. Die
  Schritte sind `key:lock`, `nudge:shape`, `click`, `roll`, `nudge:place` und `wait:0.5`.
- **Tastendrücke gehen verloren:** Senke die Tippgeschwindigkeit oder erhöhe
  `click_delay` bzw. `beam_delay`.
- **Drehung falsch herum:** Setze `"roll_direction": -1`.
- **Sichtlinie:** Kein gebauter Träger darf zwischen Kamera und Anker liegen. Das Modell
  beginnt deshalb 2 m hinter dem Anker (`auto_offset_gap`).

## Kommandozeile

Alles aus der App geht auch ohne Browser:

```bash
# Blueprint
python -m satisfactory_printer blueprint models/statue.stl --size 70 --drop-base 0.16 --finish caterium -o out_bp
# Tastatur-Plan, dann eintippen
python -m satisfactory_printer keys models/statue.stl --size 40 -o out_keys
python -m satisfactory_printer run out_keys/plan.json --start-at 120
```

Weitere Optionen:

| Option | Wofür |
|---|---|
| `--tolerance`, `--spacing` | Detailgrad und Trägerabstand |
| `--designer auto\|6\|12\|AxBxC` | Größe der Blueprint-Teile |
| `--inset` | wie weit die Träger nach innen versetzt werden |
| `--cost-per-m` | Stahlträger pro Meter für die Kostenliste |
| `--template datei.sbp` | eigener Vorlage-Blueprint |
| `--kps` | Tippgeschwindigkeit |
| `--no-roll` | Träger nicht mit dem Mausrad drehen |
| `-c config.json` | eigene Tastatur-Konfiguration |
| `-j N` | Anzahl der CPU-Kerne |

Mit `--help` zeigt jeder Befehl alle Optionen.

## Beispiele und Tests

`examples/` enthält Haus, Rampe, Kugel und Rakete (`python examples/make_examples.py`).
Die Tests startest du mit `python -m pytest tests`.
