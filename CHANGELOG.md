# Änderungsprotokoll – ExSE Harp Mobil

## 1.0.4 – 13.10.2026

Textänderung, keine Funktionsänderung.
- Infobereich-Hinweis in der App gekürzt: „Simulation mit synthetischen PTU-Dateien – diese Version enthält noch keine
  Schnittstelle zu einer echten HydraHarp.“ (Herkunftshinweis bleibt im README).
- Service-Worker-Cache neu (`exse-harp-mobil-1.0.4-r15`).

## 1.0.3 – 13.10.2026

Umbenennung, keine Funktionsänderung.
- Neuer Name **ExSE Harp Mobil** (vorher „ExSE Harp Mobile“): Seitentitel, App-Name (Kurzname „Harp Mobil“),
  Infobereich, Anmeldeseite, `/api/health`, Skripte, README, Anleitung und PDF.
- GitHub-Repository umbenannt in `ExplorationSE/exse-harp-mobil` (alte Adresse leitet weiter);
  Download-Dateien heißen jetzt `exse-harp-mobil-…zip`.
- Service-Worker-Cache neu (`exse-harp-mobil-1.0.3-…`); ggf. die alte installierte App vom Startbildschirm entfernen
  und neu hinzufügen, damit der neue Name erscheint.

## 1.0.2 – 13.10.2026

Hinweise zu Status und Herkunft, keine Funktionsänderung.
- README: Hinweiskasten oben – noch keine Anbindung an eine echte HydraHarp (nur Simulation aus PTU-Dateien,
  snAPI-Quelle unter Windows ungetestet, Gerätequelle Platzhalter); erstellt ausschließlich aus öffentlich
  verfügbaren Informationen, keine PicoQuant-Dateien enthalten; kein Produkt der PicoQuant GmbH, Marken,
  keine Gewährleistung, nicht für sicherheitskritische Anwendungen.
- App: kurzer Hinweis und Kennzeichen „Simulation“ im Infobereich; Manifest-Beschreibung angepasst.
- Repository-Beschreibung: „Simulation, noch ohne Geräteanbindung“.
- Demodateien umbenannt, damit sie klar als synthetisch erkennbar sind (Inhalt unverändert):
  `Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu`, `Demo_Synthetisch_T2_Einzelphotonen_HBT_90s.ptu`,
  `Demo_Synthetisch_T2_Heralded_K0_HBT_60s.ptu`; Standarddatei in `config.toml` angepasst.
  Die alten Dateien `data\Demo_T3_…`, `data\Demo_T2_…` können gelöscht werden.

## 1.0.1 – 13.10.2026

Nur Dokumentation und Projektpflege, keine Funktionsänderung.
- MIT-Lizenz (`LICENSE`); Hinweis auf uPlot (MIT) bleibt in `web/vendor/LICENSE-uPlot.txt`.
- GitHub Actions: pytest bei jedem Push (Python 3.11 und 3.13), Test-Badge im README.
- README: neuer Kopf mit Zweck, Download-Link zur aktuellen Version, Abschnitt „Funktionen“,
  Screenshots, Kapitel „Lizenz“; Kapitelreihenfolge 4/4a korrigiert; „V1“ → „1.0“.
- Version 1.0.1, Service-Worker-Cache erneuert.

## 1.0.0 – 13.10.2026

Erste Version. Live-Anzeige von Messdaten einer PicoQuant HydraHarp 500 im Browser/Handy,
in dieser Version als Simulation durch Wiedergabe von PTU-Dateien.

### Server
- FastAPI + WebSocket, Frames mit Histogramm, Korrelation und Zählraten (wenige kB pro Bild).
- PTU-Leser für T2 und T3 (u. a. Generic T2/T3, HydraHarp V1/V2), Gegenprobe mit `ptufile`.
- Wiedergabe in Echtzeit oder schneller/langsamer, Akkumulieren oder gleitendes Fenster, Datei wiederholen.
- Steuerung durch genau einen Client (Übernahme/Freigabe), Zuschauer sehen live mit.
- Zugangscode mit Cookie, Origin-Prüfung, Befehlsprotokoll.
- Einzige Versionsquelle `harp/__init__.py`, abrufbar über `/api/health`.

### Messansichten
- **Histogramm** (log): T3 Mikrozeit nach Sync; T2 Zeit seit dem letzten Ereignis auf dem Referenzkanal.
- **Korrelation (g²)** (log): τ = t(Kn) − t(Kref) für T2 und T3 (T3 mit absoluter Zeit), alle Paare im
  symmetrischen Fenster, auch über Frame-Grenzen hinweg; Referenzkanal und Binbreite wählbar.
- **Zeitverlauf**: Zählraten (kcps) über der Dateizeit.
- T2: Sync-Eingang als eigener Datenkanal **K0** (wenn die Datei Sync-Ereignisse enthält).
- Startwerte für Binbreite/Fenster je nach Datei (Sync-Periode bzw. Herald-Koinzidenzen im ns-Bereich).

### Oberfläche (PWA)
- Handy-optimiert: einzeilige Kopfzeile mit Ansichtsumschalter „Histo | g² | Zeit“ und Kanaltasten K0–K4.
- Diagramme symmetrisch in der Karte zentriert; Zählratenkacheln in einer Zeile.
- Fortschrittsbalken direkt unter den Start-Tasten; Versionsanzeige im Infobereich.
- Jede PTU-Datei erscheint genau einmal in der Auswahl (Windows: keine Doppelten mehr).

### Windows-Betrieb
- `install_windows.bat`, `start_server.bat` (lokales Netz), `start_online.bat`/`stop_online.bat`
  (Internetzugriff über Cloudflare Quick Tunnel, mit QR-Code und Zugangscode), Anleitung als PDF.

### Demodaten
- `Demo_T3_Fluoreszenz_4Kanal_5min.ptu`: 4 Kanäle, Fluoreszenzabklingen, 5 min.
- `Demo_T2_Einzelphotonen_HBT_90s.ptu`: Einzelphotonenquelle, HBT, Antibunching.
- `Demo_T2_Heralded_K0_HBT_60s.ptu`: Herald-Einzelphotonen, K0 = Herald, Koinzidenzen bei +12/+15 ns.
