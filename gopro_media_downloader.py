#!/usr/bin/env python3
"""
Scarica automaticamente foto/video dalla GoPro Media Library, usando le
stesse chiamate che fa il sito web nel browser. I cookie di sessione
vengono letti automaticamente dal browser che scegli all'avvio (Brave,
Chrome, Edge, Firefox, Safari, Opera, Vivaldi, Arc): nessun campo da
editare a mano nel file.

⚠️ ATTENZIONE: GoPro non ha un'API pubblica ufficiale. Questo script usa
endpoint "reverse-engineered" osservati da vari progetti open source
(itsankoff/gopro-plus, dustin/gopro, ecc.).

REQUISITI:
    pip3 install requests browser-cookie3 --break-system-packages

AUTENTICAZIONE:
    Ad ogni avvio lo script chiede da quale browser leggere i cookie e
    conferma che tu abbia già fatto login su gopro.com lì dentro. Legge
    poi i cookie gp_access_token e gp_user_id direttamente dal DB del
    browser scelto. Se non li trova (o sono vuoti), si ferma subito e
    NON fa nessuna chiamata all'API GoPro.

    Per uso automatizzato (cron ecc.) puoi saltare la domanda impostando
    la variabile d'ambiente GOPRO_BROWSER (es. GOPRO_BROWSER=chrome).

    Al primo avvio macOS potrebbe chiedere la password del Keychain
    (es. voce "Brave Safe Storage" o "Chrome Safe Storage"): è normale,
    serve per decifrare i cookie. Su Safari serve anche dare a
    Terminal/iTerm l'accesso a "Full Disk Access" in Impostazioni di
    Sistema > Privacy e sicurezza. Se ottieni errori di "database
    locked", chiudi il browser scelto e riprova.

USO:
    # 1) Modalità test: stampa la risposta grezza della prima pagina
    ./gopro_downloader.py test

    # 2) Modalità test-download: stampa la risposta grezza per un media_id
    ./gopro_downloader.py test-download <media_id>

    # 3) Modalità ricerca per nome file: ignora le date, cerca nella
    #    libreria un file (o parte del nome) e lo scarica direttamente.
    #    Di default si ferma alla prima pagina con un match (veloce);
    #    aggiungendo "tutti" cerca in tutta la libreria (piu lento, ma
    #    trova anche duplicati su pagine diverse).
    ./gopro_downloader.py file NOME_O_PARTE_DEL_NOME [tutti]

    # 4) Modalità download per range di date, formato AAAAMMGG (o AAAA-MM-GG).
    #    I file finiscono in una sottocartella DOWNLOAD_DIR/AAAAMMGG_AAAAMMGG.
    #    Ultimo argomento opzionale: foto | video | tutti (default: tutti)
    ./gopro_downloader.py date 20260619 20260719 [foto|video|tutti]

NOTA MACOS: la prima volta rendi lo script eseguibile con:
    chmod +x gopro_downloader.py
poi lancialo con ./gopro_downloader.py invece di python3 gopro_downloader.py

INTERFACCIA A SCHERMO INTERO (opzionale):
    Per un'interfaccia con tab, menu e barra di progresso invece del
    prompt testuale, usa gopro_tui.py (stessa logica, stessa cartella):
        pip3 install textual --break-system-packages
        ./gopro_tui.py
"""

__version__ = "20260830090000"  # formato: AAAAMMGGHHMMSS, aggiornato ad ogni modifica

import warnings

# IMPORTANTISSIMO: questi filtri vanno registrati PRIMA di qualunque import
# che tocchi urllib3 (quindi anche prima di "import requests"), perché il
# warning viene emesso durante l'IMPORT del modulo urllib3 stesso.
warnings.filterwarnings("ignore", message=r".*urllib3 v2 only supports OpenSSL.*")
warnings.filterwarnings("ignore", module="urllib3")

import sys
import os
import json
import time
import atexit
from datetime import datetime

import requests

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

# ============ CONFIGURAZIONE (solo parametri tecnici, non credenziali) ============

DOWNLOAD_DIR = os.path.expanduser("~/Downloads/GoProDownload")
PER_PAGE = 30
MAX_PAGES_FILE_SEARCH = 200  # tetto di sicurezza per non girare all'infinito

# File di debug dove viene salvata una copia dei cookie estratti (non
# viene mai riletto: la sessione usa sempre i valori appena estratti)
COOKIE_DEBUG_FILE = "gopro_cookies.txt"

# File di log dove il test (modalità 3) scrive la risposta grezza invece di
# stamparla a video. Viene cancellato automaticamente all'uscita dallo script.
TEST_LOG_FILE = "gopro_test_debug.log"

# ============ FINE CONFIGURAZIONE ============

def _cancella_log_test():
    """Cancella il log del test all'uscita dallo script (qualunque motivo di
    uscita: scelta 0, fine normale, errore)."""
    try:
        if os.path.exists(TEST_LOG_FILE):
            os.remove(TEST_LOG_FILE)
    except OSError:
        pass


atexit.register(_cancella_log_test)

BASE_URL = "https://api.gopro.com"

TARGET_COOKIES = ["gp_access_token", "gp_user_id"]

# Range di date attivo (impostato solo dal comando "date")
DATE_FROM = None
DATE_TO = None

# Hook di progresso opzionale per interfacce esterne (es. gopro_tui.py):
# se impostato via set_progress_hook(fn), download_file lo chiama ad ogni
# chunk scritto con (dest_path, byte_scaricati, byte_totali_attesi).
# Lasciato a None non cambia nulla per l'uso da riga di comando.
_progress_hook = None


def set_progress_hook(fn):
    global _progress_hook
    _progress_hook = fn


# ============ SELEZIONE BROWSER ED ESTRAZIONE COOKIE ============

# browser_key -> (etichetta leggibile, funzione browser_cookie3, nota extra)
BROWSER_OPZIONI = {
    "1": ("brave", "Brave", None),
    "2": ("chrome", "Google Chrome", None),
    "3": ("edge", "Microsoft Edge", None),
    "4": ("firefox", "Firefox", None),
    "5": ("safari", "Safari", "su macOS serve dare a Terminal/iTerm accesso a 'Full Disk Access' in Impostazioni di Sistema > Privacy"),
    "6": ("opera", "Opera", None),
    "7": ("vivaldi", "Vivaldi", None),
    "8": ("arc", "Arc", None),
}


def _browser_funcs():
    """Mappa browser_key -> funzione browser_cookie3 corrispondente.
    Costruita a runtime così l'errore su browser_cookie3 mancante viene
    gestito in un punto solo (chiama_funzione_browser)."""
    return {
        "brave": browser_cookie3.brave,
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "safari": browser_cookie3.safari,
        "opera": browser_cookie3.opera,
        "vivaldi": browser_cookie3.vivaldi,
        "arc": browser_cookie3.arc,
    }


def scegli_browser():
    """Chiede quale browser usare per leggere i cookie di sessione GoPro e
    conferma che il login sia già stato fatto lì dentro. Può essere saltato
    per uso automatizzato (cron ecc.) impostando la variabile d'ambiente
    GOPRO_BROWSER con uno dei nomi validi (es. GOPRO_BROWSER=chrome)."""
    env_browser = os.environ.get("GOPRO_BROWSER", "").strip().lower()
    if env_browser:
        if env_browser in _browser_funcs():
            print(f"Browser impostato da GOPRO_BROWSER={env_browser} (nessuna domanda a schermo).")
            return env_browser
        print(f"Attenzione: GOPRO_BROWSER='{env_browser}' non riconosciuto, chiedo a schermo.\n")

    print("Da quale browser leggo i cookie di sessione GoPro?")
    for k, (_, label, _nota) in BROWSER_OPZIONI.items():
        print(f"  {k}) {label}")
    scelta = input("> [1] ").strip() or "1"

    if scelta not in BROWSER_OPZIONI:
        print(f"Scelta non valida: '{scelta}'.")
        sys.exit(1)

    browser_key, label, nota = BROWSER_OPZIONI[scelta]
    if nota:
        print(f"Nota per {label}: {nota}")

    conferma = input(f"Hai già fatto login su gopro.com dentro {label}? [S/n] ").strip().lower() or "s"
    if conferma not in ("s", "si", "sì", "y", "yes"):
        print(f"\nFai prima il login su https://gopro.com dentro {label}, poi rilancia lo script.")
        sys.exit(1)

    return browser_key


def estrai_cookie(browser_key):
    """Cerca nei cookie del browser scelto quelli di dominio gopro e isola
    gp_access_token e gp_user_id (case-insensitive sul nome)."""
    if browser_cookie3 is None:
        print("ERRORE: manca la libreria 'browser-cookie3'.")
        print('Installala con: pip3 install browser-cookie3 --break-system-packages')
        sys.exit(1)

    func = _browser_funcs().get(browser_key)
    if func is None:
        print(f"ERRORE: browser '{browser_key}' non supportato.")
        sys.exit(1)

    try:
        cj = func()
    except Exception as e:
        print(f"ERRORE nell'accesso ai cookie di {browser_key}: {e}")
        print(
            "Verifica di aver installato 'browser-cookie3', che il browser "
            "non sia aperto con il DB cookie bloccato, e di aver concesso "
            "l'accesso al Keychain (o, su Safari, a Full Disk Access)."
        )
        sys.exit(1)

    target_lower = {name.lower(): name for name in TARGET_COOKIES}
    found = {}
    domains_seen = set()

    for cookie in cj:
        if "gopro" in cookie.domain.lower():
            domains_seen.add(cookie.domain)
            name_lower = cookie.name.lower()
            if name_lower in target_lower:
                canonical_name = target_lower[name_lower]
                found[canonical_name] = cookie.value

    if domains_seen:
        print(f"Domini gopro trovati nei cookie di {browser_key}:")
        for d in sorted(domains_seen):
            print(f"  - {d}")
    else:
        print(f"Nessun cookie con dominio 'gopro' trovato in {browser_key}.")

#    try:
#        with open(COOKIE_DEBUG_FILE, "w") as f:
#            for name in TARGET_COOKIES:
#                f.write(f'{name} = "{found.get(name, "")}"\n')
#    except OSError:
#        pass  # non bloccante: è solo un file di debug

    return {name: found.get(name, "") for name in TARGET_COOKIES}


def verifica_e_ottieni_cookie(browser_key):
    """Estrae i cookie dal browser scelto e, se uno dei due manca o è
    vuoto, stampa un avviso chiaro ed esce SUBITO (nessuna chiamata
    all'API GoPro viene fatta)."""
    cookies = estrai_cookie(browser_key)
    access_token = cookies.get("gp_access_token", "")
    user_id = cookies.get("gp_user_id", "")

    mancanti = []
    if not access_token:
        mancanti.append("gp_access_token")
    if not user_id:
        mancanti.append("gp_user_id")

    if mancanti:
        print("\n--- IMPOSSIBILE PROCEDERE ---")
        print(f"Cookie mancanti o vuoti: {', '.join(mancanti)}")
        print(
            f"Assicurati di aver effettuato il login su gopro.com dentro "
            f"{browser_key} e riprova. Lo script si ferma qui, nessuna "
            f"chiamata all'API è stata effettuata."
        )
        sys.exit(1)

    print("\nCookie trovati correttamente, procedo.\n")
    return access_token, user_id


# ============ SESSIONE HTTP ============

session = requests.Session()
session.headers.update({
    "Accept": "application/vnd.gopro.jk.media.search+json; version=2.0.0",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gopro.com/",
    "Origin": "https://gopro.com",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
})

SEARCH_FIELDS = (
    "camera_model,captured_at,composition,content_title,content_type,created_at,"
    "gopro_user_id,gopro_media,filename,file_extension,file_size,firmware_version,"
    "height,fov,id,item_count,mce_type,moments_count,on_public_profile,orientation,"
    "play_as,ready_to_edit,ready_to_view,resolution,source_duration,token,type,"
    "updated_at,width,stabilized,source_gumi,submitted_at,thumbnail_available,"
    "captured_at_timezone,available_labels,ai_training_opt_out,export_ids,expires_at"
)
SEARCH_TYPES = "Burst,BurstVideo,Continuous,LoopedVideo,Photo,TimeLapse,TimeLapseVideo,Video,MultiClipEdit,Edit"

# Classificazione tipi media per il filtro foto/video/tutti (modalità "date")
PHOTO_TYPES = {"Photo", "Burst", "TimeLapse"}
VIDEO_TYPES = {"Video", "BurstVideo", "Continuous", "LoopedVideo", "TimeLapseVideo", "MultiClipEdit", "Edit"}


def media_type_ok(item_type, media_type):
    """media_type: 'foto' | 'video' | 'tutti'."""
    if media_type == "foto":
        return item_type in PHOTO_TYPES
    if media_type == "video":
        return item_type in VIDEO_TYPES
    return True

DOWNLOAD_ACCEPT = "application/vnd.gopro.jk.media+json; version=2.0.0"


def debug_error(resp):
    print(f"\n--- ERRORE HTTP {resp.status_code} ---")
    print("Headers risposta:", dict(resp.headers))
    print("Corpo risposta (primi 1000 caratteri):")
    print(resp.text[:1000])
    print("--- fine errore ---\n")


MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1  # secondi, raddoppia ad ogni tentativo


def http_get(url, **kwargs):
    """GET con retry/backoff su errori di rete ed errori server (5xx)
    transitori. Su 401 esce subito: il token di sessione non è più valido
    e insistere non serve a nulla (serve rifare login su gopro.com)."""
    backoff = RETRY_BACKOFF_BASE
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, **kwargs)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                print(f"  ⚠️ errore di rete ({e}), riprovo tra {backoff}s (tentativo {attempt}/{MAX_RETRIES})...")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise

        if resp.status_code == 401:
            print("\n--- TOKEN SCADUTO ---")
            print("Il cookie di sessione GoPro non è più valido.")
            print("Rifai il login su gopro.com dentro Brave e riprova.")
            sys.exit(1)

        if resp.status_code >= 500 and attempt < MAX_RETRIES:
            print(f"  ⚠️ errore server {resp.status_code}, riprovo tra {backoff}s (tentativo {attempt}/{MAX_RETRIES})...")
            time.sleep(backoff)
            backoff *= 2
            continue

        return resp

    raise last_exc


def date_in_range(captured_at_str):
    """captured_at arriva tipicamente come ISO 8601, es. 2024-11-15T10:32:07Z"""
    if not captured_at_str:
        return False
    try:
        d = datetime.fromisoformat(captured_at_str.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    df = datetime.strptime(DATE_FROM, "%Y-%m-%d").date()
    dt = datetime.strptime(DATE_TO, "%Y-%m-%d").date()
    return df <= d <= dt


def fetch_page(page):
    url = f"{BASE_URL}/media/search"
    params = {
        "processing_states": "rendering,pretranscoding,transcoding,stabilizing,ready,failure",
        "fields": SEARCH_FIELDS,
        "type": SEARCH_TYPES,
        "page": page,
        "per_page": PER_PAGE,
    }
    resp = http_get(url, params=params)
    if not resp.ok:
        debug_error(resp)
    resp.raise_for_status()
    return resp.json()


def get_download_url(media_id):
    """Chiede al backend gli URL firmati (CloudFront/S3) per scaricare l'originale."""
    url = f"{BASE_URL}/media/{media_id}/download"
    resp = http_get(url, headers={"Accept": DOWNLOAD_ACCEPT})
    if not resp.ok:
        debug_error(resp)
    resp.raise_for_status()
    data = resp.json()

    embedded = data.get("_embedded", {})

    # "variations" può contenere una entry "source" (l'originale, qualità
    # piena) insieme a vari proxy più leggeri (edit_proxy, high_res_proxy_mp4,
    # audio_proxy...). Va cercata PRIMA di "files": "files" è la lista dei
    # capitoli/segmenti del file così com'è (usata per video multi-file), ma
    # se la variation "source" esiste è quella la qualità giusta da
    # scaricare, altrimenti si scarica per sbaglio un proxy a bassa
    # risoluzione con size diversa da file_size (mismatch a valle).
    variations = embedded.get("variations")
    if variations:
        for v in variations:
            if v.get("label") == "source":
                return v.get("url")

    files = embedded.get("files")
    if files:
        return files[0].get("url")

    if variations:
        return variations[0].get("url")

    if "url" in data:
        return data["url"]
    if "urls" in data and data["urls"]:
        return data["urls"][0]
    return None


def mode_test_download(media_id):
    print(f"Chiamo /media/{media_id}/download e stampo la risposta grezza...\n")
    url = f"{BASE_URL}/media/{media_id}/download"
    resp = http_get(url, headers={"Accept": DOWNLOAD_ACCEPT})
    if not resp.ok:
        debug_error(resp)
    resp.raise_for_status()
    print(json.dumps(resp.json(), indent=2)[:3000])


def download_file(url, dest_path, expected_size=None):
    """Scarica su file temporaneo .part e lo rinomina solo a fine scarico
    riuscito. Così, se la connessione cade a metà, non resta un file
    corrotto scambiato per "già scaricato" al run successivo. Se
    expected_size è noto (file_size dalla ricerca media), verifica anche
    che la dimensione combaci.

    A differenza di http_get (che riprova solo sulla connessione iniziale),
    qui il retry copre anche lo streaming: se il server chiude la
    connessione a metà scarico (troncamento silenzioso, senza eccezione),
    l'unico modo per accorgersene è il controllo dimensione a fine file, e
    l'unico modo per recuperare è riscaricare da capo."""
    tmp_path = dest_path + ".part"
    backoff = RETRY_BACKOFF_BASE
    last_exc = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            downloaded = 0
            with http_get(url, stream=True) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if _progress_hook:
                            try:
                                _progress_hook(dest_path, downloaded, expected_size)
                            except Exception:
                                pass  # un hook UI difettoso non deve far fallire il download

            if expected_size:
                actual_size = os.path.getsize(tmp_path)
                if actual_size != expected_size:
                    os.remove(tmp_path)
                    raise IOError(
                        f"dimensione non corrispondente (atteso {expected_size}, ottenuto {actual_size})"
                    )

            os.replace(tmp_path, dest_path)
            return
        except (requests.exceptions.RequestException, IOError) as e:
            last_exc = e
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < MAX_RETRIES:
                print(f"  ⚠️ scarico incompleto ({e}), riprovo tra {backoff}s (tentativo {attempt}/{MAX_RETRIES})...")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise last_exc


def mode_test():
    """Chiama la prima pagina di /media/search. La risposta grezza NON va a
    video: finisce in TEST_LOG_FILE (cancellato automaticamente all'uscita
    dallo script). A video solo l'esito sintetico."""
    print("Chiamo la prima pagina di /media/search...")
    data = fetch_page(1)

    try:
        with open(TEST_LOG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"⚠️ Non riesco a scrivere il log di test ({e}).")

    items = data.get("_embedded", {}).get("media", data.get("media", []))
    campi_attesi = ("id", "filename", "captured_at")
    ok = bool(items) and all(c in items[0] for c in campi_attesi)

    if ok:
        print(f"✅ Test superato: risposta valida, {len(items)} elementi, campi {', '.join(campi_attesi)} presenti.")
    else:
        print("⚠️ Test fallito: struttura della risposta diversa da quella attesa.")
        print(f"Dettaglio completo in '{TEST_LOG_FILE}' (lo trovi finché non esci dallo script, poi viene cancellato).")


def mode_download(media_type="tutti"):
    # Sottocartella dedicata al range richiesto: DOWNLOAD_DIR/AAAAMMGG_AAAAMMGG
    subfolder = f"{DATE_FROM.replace('-', '')}_{DATE_TO.replace('-', '')}"
    target_dir = os.path.join(DOWNLOAD_DIR, subfolder)
    os.makedirs(target_dir, exist_ok=True)
    print(f"Cartella di destinazione: {target_dir}\n")
    page = 1
    total_downloaded = 0
    total_skipped = 0
    date_from_obj = datetime.strptime(DATE_FROM, "%Y-%m-%d").date()

    while True:
        print(f"Leggo pagina {page}...")
        data = fetch_page(page)
        items = data.get("_embedded", {}).get("media", data.get("media", []))
        if not items:
            print("Nessun altro elemento, fine.")
            break

        # I risultati arrivano dal più recente al più vecchio. Se TUTTI gli
        # elementi di questa pagina sono più vecchi di DATE_FROM, possiamo
        # fermarci: tutto ciò che viene dopo sarà ancora più vecchio.
        page_dates = []
        for item in items:
            captured = item.get("captured_at") or item.get("created_at")
            if captured:
                try:
                    page_dates.append(
                        datetime.fromisoformat(captured.replace("Z", "+00:00")).date()
                    )
                except ValueError:
                    pass

        for item in items:
            captured = item.get("captured_at") or item.get("created_at")
            filename = item.get("filename", f"{item.get('id')}.bin")

            if not date_in_range(captured):
                total_skipped += 1
                continue

            if not media_type_ok(item.get("type"), media_type):
                total_skipped += 1
                continue

            dest_path = os.path.join(target_dir, filename)
            if os.path.exists(dest_path):
                print(f"  già presente, salto: {filename}")
                continue

            try:
                dl_url = get_download_url(item["id"])
                if not dl_url:
                    print(f"  ⚠️ nessun URL di download trovato per {filename}")
                    continue
                print(f"  scarico: {filename} (media_id={item['id']})")
                download_file(dl_url, dest_path, expected_size=item.get("file_size"))
                total_downloaded += 1
                time.sleep(0.3)  # non martellare l'API
            except (requests.HTTPError, IOError) as e:
                print(f"  ⚠️ errore su {filename}: {e}")

        if page_dates and max(page_dates) < date_from_obj:
            print("Tutti gli elementi di questa pagina sono più vecchi del range richiesto. Mi fermo qui.")
            break

        page += 1

    print(f"\nFatto. Scaricati: {total_downloaded}, fuori range/saltati: {total_skipped}")


def mode_download_by_filename(query, scan_completo=False):
    """Ignora il range di date: scorre la libreria pagina per pagina e
    scarica ogni elemento il cui filename contiene 'query' (case-insensitive).

    Default (scan_completo=False): si ferma alla prima pagina in cui trova
    almeno un match (veloce, ma su nomi che si ripetono su più pagine ne
    trova solo un sottoinsieme). Con scan_completo=True scorre tutta la
    libreria fino a MAX_PAGES_FILE_SEARCH, trovando ogni corrispondenza ma
    più lento su librerie grandi."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    query_lower = query.lower()
    page = 1
    matches_found = 0

    print(f"Cerco file il cui nome contiene: '{query}'...\n")

    while page <= MAX_PAGES_FILE_SEARCH:
        print(f"Leggo pagina {page}...")
        data = fetch_page(page)
        items = data.get("_embedded", {}).get("media", data.get("media", []))
        if not items:
            print("Nessun altro elemento, fine ricerca.")
            break

        for item in items:
            filename = item.get("filename", "")
            if query_lower not in filename.lower():
                continue

            matches_found += 1
            dest_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.exists(dest_path):
                print(f"  già presente, salto: {filename}")
                continue

            try:
                dl_url = get_download_url(item["id"])
                if not dl_url:
                    print(f"  ⚠️ nessun URL di download trovato per {filename}")
                    continue
                print(f"  scarico: {filename} (media_id={item['id']})")
                download_file(dl_url, dest_path, expected_size=item.get("file_size"))
                time.sleep(0.3)  # non martellare l'API
            except (requests.HTTPError, IOError) as e:
                print(f"  ⚠️ errore su {filename}: {e}")

        if matches_found > 0 and not scan_completo:
            print("Trovato, mi fermo qui senza leggere altre pagine.")
            print("(usa 'tutti' come terzo argomento per cercare anche nelle pagine successive)")
            break

        page += 1

    if matches_found == 0:
        print(f"\nNessun file trovato con '{query}' nel nome.")
    else:
        print(f"\nFatto. Trovati {matches_found} file corrispondenti a '{query}'.")


def parse_cli_date(raw):
    """Accetta AAAAMMGG (es. 20260619) oppure AAAA-MM-GG (es. 2026-06-19)
    e restituisce sempre il formato AAAA-MM-GG usato internamente."""
    raw = raw.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    print(f"Data non valida: '{raw}'. Usa il formato AAAAMMGG (es. 20260619) o AAAA-MM-GG.")
    sys.exit(1)


def parse_cli_media_type(raw):
    """Accetta foto/video/tutti (case-insensitive, con qualche sinonimo)."""
    raw = raw.strip().lower()
    if raw in ("foto", "photo", "photos", "immagini"):
        return "foto"
    if raw in ("video", "videos"):
        return "video"
    if raw in ("tutti", "tutto", "entrambi", "all"):
        return "tutti"
    print(f"Tipo media non valido: '{raw}'. Usa foto, video o tutti.")
    sys.exit(1)


def menu_interattivo():
    """Chiede a schermo cosa fare e restituisce una lista di argomenti
    equivalente a quella che si passerebbe da CLI, oppure None se l'utente
    sceglie di uscire (0). Su scelta non valida richiede senza uscire."""
    print("\nCosa vuoi fare?")
    print("  1) Scarica per giornata")
    print("  2) Scarica per range di date")
    print("  3) Cerca e scarica per nome file")
    print("  4) Test: verifica risposta prima pagina")
    print("  5) Test-download: stampa risposta grezza per un media_id")
    print("  0) Esci")
    print("\nSintassi CLI:")
    print("  ./gopro_media_downloader.py test")
    print("  ./gopro_media_downloader.py test-download <media_id>")
    print("  ./gopro_media_downloader.py file <nome_file> [tutti]")
    print("  ./gopro_media_downloader.py date <da_AAAAMMGG> <a_AAAAMMGG> [foto|video|tutti]")

    while True:
        scelta = input("> ").strip()

        if scelta == "1":
            giorno = input("Data (AAAAMMGG o AAAA-MM-GG): ").strip()
            print("Cosa vuoi scaricare?")
            print("  1) Solo foto")
            print("  2) Solo video")
            print("  3) Entrambi (default)")
            tipo = input("> ").strip()
            tipo_arg = {"1": "foto", "2": "video"}.get(tipo, "tutti")
            return ["date", giorno, giorno, tipo_arg]
        elif scelta == "2":
            da = input("Data inizio (AAAAMMGG o AAAA-MM-GG): ").strip()
            a = input("Data fine (AAAAMMGG o AAAA-MM-GG): ").strip()
            print("Cosa vuoi scaricare?")
            print("  1) Solo foto")
            print("  2) Solo video")
            print("  3) Entrambi (default)")
            tipo = input("> ").strip()
            tipo_arg = {"1": "foto", "2": "video"}.get(tipo, "tutti")
            return ["date", da, a, tipo_arg]
        elif scelta == "3":
            nome = input("Nome (o parte) del file da cercare: ").strip()
            risposta = input(
                "Cercare in tutta la libreria (piu lento, trova anche duplicati "
                "su pagine diverse) o fermarsi al primo trovato (piu veloce)? [primo/tutti] "
            ).strip().lower()
            if risposta in ("tutti", "t", "all"):
                return ["file", nome, "tutti"]
            return ["file", nome]
        elif scelta == "4":
            return ["test"]
        elif scelta == "5":
            media_id = input("media_id: ").strip()
            return ["test-download", media_id]
        elif scelta == "0":
            return None
        else:
            print(f"Scelta non valida: '{scelta}'. Riprova (0 per uscire).")


def args_validi(args):
    return (
        (len(args) == 1 and args[0] == "test")
        or (len(args) == 2 and args[0] == "test-download")
        or (len(args) == 2 and args[0] == "file")
        or (len(args) == 3 and args[0] == "file" and args[2] == "tutti")
        or (len(args) == 3 and args[0] == "date")
        or (len(args) == 4 and args[0] == "date")
    )


def esegui_azione(args):
    """Dispatcher unico: usato sia dalla riga di comando sia dal loop del
    menu interattivo, così i due percorsi restano allineati."""
    global DATE_FROM, DATE_TO

    if args[0] == "test":
        mode_test()
    elif args[0] == "test-download":
        mode_test_download(args[1])
    elif args[0] == "file":
        scan_completo = len(args) == 3 and args[2] == "tutti"
        mode_download_by_filename(args[1], scan_completo=scan_completo)
    elif args[0] == "date":
        DATE_FROM = parse_cli_date(args[1])
        DATE_TO = parse_cli_date(args[2])
        if DATE_FROM > DATE_TO:
            print(f"Attenzione: la data di inizio ({DATE_FROM}) è dopo quella di fine ({DATE_TO}). Le scambio.")
            DATE_FROM, DATE_TO = DATE_TO, DATE_FROM
        media_type = parse_cli_media_type(args[3]) if len(args) == 4 else "tutti"
        print(f"Range date impostato: {DATE_FROM} -> {DATE_TO} (tipo: {media_type})\n")
        mode_download(media_type=media_type)


def main():
    args = sys.argv[1:]
    print(f"gopro_downloader.py v{__version__}")

    # Uso da riga di comando: valida gli argomenti PRIMA di chiedere il
    # browser, così un comando sbagliato fallisce subito senza disturbarti
    # con la domanda sul browser.
    if args and not args_validi(args):
        print("Uso: ./gopro_downloader.py [test|test-download <media_id>|file <nome_file> [tutti]|date <da_AAAAMMGG> <a_AAAAMMGG> [foto|video|tutti]]")
        print("  'tutti' dopo il nome file forza la ricerca in tutta la libreria invece di fermarsi al primo trovato.")
        print("  foto|video|tutti dopo le date filtra il tipo di media (default: tutti).")
        sys.exit(1)

    # Scegli il browser e verifica login PRIMA di qualunque chiamata
    # all'API, una volta sola per l'intera sessione (chiesto qui, prima di
    # decidere cosa scaricare, non ad ogni azione). Se i cookie mancano o
    # sono vuoti, exit(1) qui dentro.
    browser_key = scegli_browser()
    access_token, user_id = verifica_e_ottieni_cookie(browser_key)
    session.cookies.set("gp_access_token", access_token, domain=".gopro.com")
    session.cookies.set("gp_user_id", user_id, domain=".gopro.com")

    if not args:
        # Modalità interattiva: resta in loop finché non scegli 0 (esce da
        # tutto). Ogni azione, test compreso, torna qui al termine.
        print("Nessun argomento passato: modalità interattiva.")
        while True:
            azione = menu_interattivo()
            if azione is None:
                print("Uscita.")
                break
            esegui_azione(azione)
        return

    esegui_azione(args)


if __name__ == "__main__":
    main()