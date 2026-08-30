# GoPro Media Downloader

Script Python per scaricare i propri media dal cloud GoPro (gopro.com/media-library)
usando le stesse chiamate API del sito web, autenticandosi tramite i cookie di
sessione già presenti nel browser (Brave, Chrome, Safari, Firefox, ...).

Non richiede credenziali hardcoded: legge `gp_access_token` e `gp_user_id`
direttamente dal database cookie del browser scelto, previo login già effettuato
su gopro.com nel browser stesso.

## Requisiti

```bash
pip3 install requests browser-cookie3 --break-system-packages
```

Interfaccia grafica opzionale (full-screen TUI):

```bash
pip3 install textual --break-system-packages
```

## Uso

Modalità interattiva (menu guidato):

```bash
./gopro_media_downloader.py
```

Sintassi CLI diretta:

```bash
./gopro_media_downloader.py test
./gopro_media_downloader.py test-download <media_id>
./gopro_media_downloader.py file <nome_file> [tutti]
./gopro_media_downloader.py date <da_AAAAMMGG> <a_AAAAMMGG> [foto|video|tutti]
```

Interfaccia a schermo intero:

```bash
./gopro_tui.py
```

## Note

- Serve aver fatto login su gopro.com nel browser scelto prima di lanciare lo script.
- Su macOS il primo avvio può chiedere la password del Keychain per decifrare i cookie.
- Nessun dato personale o credenziale è salvato nel codice: tutto viene letto a runtime dal browser.
