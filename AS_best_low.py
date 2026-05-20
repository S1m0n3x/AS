import os
import re
import time
import json
import traceback
import requests
import difflib
import unicodedata
import random
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
import undetected_chromedriver as uc

# ==========================================
# ⚙️ CONFIGURAZIONE UTENTE
# ==========================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = "168349527" 

# Debug: salva HTML widget per analisi (utile se parsing fallisce)
DEBUG_SAVE_HTML = False  # Imposta True per salvare HTML dei widget
DEBUG_FOLDER = os.path.join(os.getcwd(), "DEBUG")

LEAGUES_TO_MONITOR = [
    "https://www.betmonitor.com/odds-comparison/football/england-premier-league/10000070",
    "https://www.betmonitor.com/odds-comparison/football/italy-serie-a/10000124",
    "https://www.betmonitor.com/odds-comparison/football/france-ligue-1/10000080",
    "https://www.betmonitor.com/odds-comparison/football/germany-bundesliga/10000090",
    "https://www.betmonitor.com/odds-comparison/football/spain-la-liga/10000184",
    #"https://www.betmonitor.com/odds-comparison/football/england-efl-cup/10001679",
    "https://www.betmonitor.com/odds-comparison/football/germany-dfb-pokal/10000375",
    #"https://www.betmonitor.com/odds-comparison/football/spain-copa-del-rey/10016610",
    #"https://www.betmonitor.com/odds-comparison/football/italy-cup/10000230",
    #"https://www.betmonitor.com/odds-comparison/football/brazil-serie-a/10001637",
    "https://www.betmonitor.com/odds-comparison/football/uefa-champions-league/10000315",
    "https://www.betmonitor.com/odds-comparison/football/uefa-europa-league/10000314",
    "https://www.betmonitor.com/odds-comparison/football/uefa-conference-league/10018549"
    #"https://www.betmonitor.com/odds-comparison/football/england-efl-championship/10000066",
    #"https://www.betmonitor.com/odds-comparison/football/portugal-primeira-liga/10000161",
    #"https://www.betmonitor.com/odds-comparison/football/england-fa-cup/10000225",
    #"https://www.betmonitor.com/odds-comparison/football/fifa-world-cup-qual---europe/10001133",
    #"https://www.betmonitor.com/odds-comparison/football/friendly-internationals/10000404"
]

OUTPUT_FOLDER = os.path.join(os.getcwd(), "DATI_CARTELLI")
WAIT_MINUTES_BETWEEN_LOOPS = 5

# ==========================================
# 🌐 CONFIGURAZIONE ANTI-BAN
# ==========================================

# Versione major di Chrome installata (es. 147).
# Se None, il bot prova auto-detect/retry dinamico in base all'errore di avvio.
CHROME_VERSION = 147

# True = usa proxy gratuiti per ruotare IP ad ogni avvio (lento, raramente utile con betmonitor)
# False = usa solo stealth mode (consigliato)
USE_PROXY_ROTATION = False
PROXY_TEST_TIMEOUT = 5    # secondi per testare ogni proxy
PROXY_MAX_ATTEMPTS = 30   # max proxy da testare prima di rinunciare

# Pool di User-Agent realistici — uno casuale ad ogni avvio
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
]


class ProxyRotator:
    """Scarica proxy gratuiti, li testa e restituisce il primo funzionante."""

    PROXY_SOURCES = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=elite,anonymous",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ]

    @staticmethod
    def fetch_proxies():
        proxies = []
        for url in ProxyRotator.PROXY_SOURCES:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    lines = resp.text.strip().splitlines()
                    new = [p.strip() for p in lines if re.match(r'\d+\.\d+\.\d+\.\d+:\d+', p.strip())]
                    proxies.extend(new)
                    print(f"   🌐 Proxy source OK: {len(new)} da {url.split('/')[2]}")
            except Exception as e:
                print(f"   ⚠️ Proxy source fallita: {url.split('/')[2]} ({e})")
        return list(set(proxies))

    @staticmethod
    def test_proxy(proxy, timeout=PROXY_TEST_TIMEOUT):
        """Testa il proxy direttamente su betmonitor (HTTPS) per escludere proxy che non raggiungono il sito target."""
        try:
            resp = requests.get(
                "https://www.betmonitor.com/",
                proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                timeout=timeout,
                allow_redirects=True
            )
            return resp.status_code < 500  # 200, 301, 403 vanno bene — 5xx no
        except:
            return False

    @staticmethod
    def get_working_proxy():
        print("🔄 Ricerca proxy funzionante...")
        proxies = ProxyRotator.fetch_proxies()
        if not proxies:
            print("⚠️ Nessun proxy scaricato, uso IP diretto.")
            return None
        random.shuffle(proxies)
        for i, proxy in enumerate(proxies[:PROXY_MAX_ATTEMPTS], 1):
            if ProxyRotator.test_proxy(proxy):
                print(f"✅ Proxy OK: {proxy} (testato {i}/{min(len(proxies), PROXY_MAX_ATTEMPTS)})")
                return proxy
            print(f"   ❌ KO ({i}/{min(len(proxies), PROXY_MAX_ATTEMPTS)}): {proxy}", end="\r")
        print("⚠️ Nessun proxy valido, uso IP diretto.")
        return None


# ==========================================
# 📂 CONFIGURAZIONE CSV MULTIPLI
# ==========================================

CSV_FILES = {
    "europei": "Cartellini_Coppe_Europee.csv",
    "italia": "Cartellini_Italia.csv",
    "inghilterra": "Cartellini_Inghilterra.csv",
    "spagna": "Cartellini_Spagna.csv",
    "germania": "Cartellini_Germania.csv",
    "francia": "Cartellini_Francia.csv",
    "altri": "Cartellini_Altri.csv"
}

LEAGUE_CATEGORIES = {
    "europei": [
        "https://www.betmonitor.com/odds-comparison/football/uefa-champions-league/10000315",
        "https://www.betmonitor.com/odds-comparison/football/uefa-europa-league/10000314",
        "https://www.betmonitor.com/odds-comparison/football/uefa-conference-league/10018549"
    ],
    "italia": [
        "https://www.betmonitor.com/odds-comparison/football/italy-serie-a/10000124",
        "https://www.betmonitor.com/odds-comparison/football/italy-cup/10000230"
    ],
    "inghilterra": [
        "https://www.betmonitor.com/odds-comparison/football/england-premier-league/10000070",
        "https://www.betmonitor.com/odds-comparison/football/england-efl-cup/10001679",
        "https://www.betmonitor.com/odds-comparison/football/england-efl-championship/10000066",
        "https://www.betmonitor.com/odds-comparison/football/england-fa-cup/10000225"
    ],
    "spagna": [
        "https://www.betmonitor.com/odds-comparison/football/spain-la-liga/10000184",
        "https://www.betmonitor.com/odds-comparison/football/spain-copa-del-rey/10016610"
    ],
    "germania": [
        "https://www.betmonitor.com/odds-comparison/football/germany-bundesliga/10000090",
        "https://www.betmonitor.com/odds-comparison/football/germany-dfb-pokal/10000375"
    ],
    "francia": [
        "https://www.betmonitor.com/odds-comparison/football/france-ligue-1/10000080"
    ],
    "altri": [
        #"https://www.betmonitor.com/odds-comparison/football/brazil-serie-a/10001637",
        #"https://www.betmonitor.com/odds-comparison/football/portugal-primeira-liga/10000161",
        "https://www.betmonitor.com/odds-comparison/football/fifa-world-cup-qual---europe/10001133",
        "https://www.betmonitor.com/odds-comparison/football/friendly-internationals/10000404"
    ]
}

BOOKMAKERS_FOR_CSV = [
    '10bet', 'bet365', 'betsensation', 'betfair', 'betway', 'boylesports', 
    'coral', 'dafabet', 'draftkings', 'eurobet', 'fanduel', 'iddaa', 
    'ladbrokes', 'ladbrokes_be', 'novibet', 'norsktipping', 'paddypower', 
    'sazka', 'sportybet', 'stake', 'tonybet', 'unibet', 'wwin'
]

# --- CARICAMENTO LISTA DUPLICATI ESTERNA ---
try:
    from duplicates_list import KNOWN_DUPLICATES
    print("✅ Lista duplicati caricata da file esterno.")
except ImportError:
    print("⚠️ ERRORE: File 'duplicates_list.py' non trovato nella stessa cartella!")
    KNOWN_DUPLICATES = []

class SmartBot:
    def __init__(self):
        self.driver = None
        self.empty_leagues_today = set()
        self._sofa_events_cache = None   # cache eventi Sofascore (si ricarica ogni giorno)
        self._sofa_cache_date = None
        self._last_lineup_source = "unknown"
        self.use_proxy = USE_PROXY_ROTATION  # può essere disabilitato a runtime se il proxy fallisce
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    def _clear_chromedriver_cache(self):
        """Rimuove la cache del chromedriver per forzare il redownload."""
        import shutil
        cache_dirs = [
            os.path.expanduser("~/.wdm/chromedriver"),
            os.path.expanduser("~/.undetected_chromedriver"),
            os.path.join(os.getcwd(), ".undetected_chromedriver"),
        ]
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                try:
                    shutil.rmtree(cache_dir)
                    print(f"   🗑️ Cache ripulita: {cache_dir}")
                except Exception as e:
                    print(f"   ⚠️ Impossibile ripulire cache {cache_dir}: {e}")

    def _create_chrome_options(self):
        """Crea una nuova istanza di ChromeOptions con tutte le configurazioni."""
        chrome_options = uc.ChromeOptions()
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--headless") # Forza il cloud a non cercare una finestra
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--silent")
        chrome_options.add_argument("--disable-usb-keyboard-detect")
        chrome_options.add_argument("--disable-features=ServiceWorkerPaymentApps")
        chrome_options.set_capability("pageLoadStrategy", "eager")
        

        # 🔀 User-Agent casuale
        user_agent = random.choice(USER_AGENTS)
        chrome_options.add_argument(f"user-agent={user_agent}")
        
        # 🖥️ Viewport casuale (anti-fingerprint)
        chrome_options.add_argument(f"--window-size={random.randint(1280,1920)},{random.randint(768,1080)}")

        # 🌐 Proxy rotation
        if self.use_proxy:
            proxy = ProxyRotator.get_working_proxy()
            if proxy:
                chrome_options.add_argument(f"--proxy-server=http://{proxy}")
                print(f"🌐 Proxy impostato: {proxy}")
            else:
                print("🔓 Connessione diretta (nessun proxy).")
        else:
            print("🔓 Proxy disabilitato, connessione diretta.")
        
        return chrome_options, user_agent

    def start_driver(self):
        if self.driver: return

        def _extract_current_browser_major(err_text):
            m = re.search(r"Current browser version is\s+(\d+)\.", err_text)
            return int(m.group(1)) if m else None

        def _build_driver_with_options(version_main=None, clear_cache=False):
            """Crea una nuova ChromeOptions e tenta di avviare il driver."""
            if clear_cache:
                self._clear_chromedriver_cache()
            
            chrome_options, user_agent = self._create_chrome_options()
            print(f"🕵️ User-Agent: ...{user_agent[40:80]}...")
            kwargs = {
                "options": chrome_options,
                "headless": True,
                "use_subprocess": True,
            }
            if version_main is not None:
                kwargs["version_main"] = int(version_main)
            return uc.Chrome(**kwargs)

        try:
            # 1) tenta con versione configurata (se presente)
            self.driver = _build_driver_with_options(CHROME_VERSION)
        except Exception as e:
            err_text = str(e)
            print(f"❌ CRITICAL: Errore driver: {e}")

            # 2) retry automatico con major letto dall'errore (Chrome auto-aggiornato) + cache clear
            detected_major = _extract_current_browser_major(err_text)
            if detected_major is not None:
                try:
                    print(f"🔄 Retry driver con Chrome major rilevata: {detected_major} (cache clearing)")
                    self.driver = _build_driver_with_options(detected_major, clear_cache=True)
                except Exception as e2:
                    print(f"❌ Retry fallito: {e2}")

            # 3) fallback finale senza version_main (auto-detect) + cache clear
            if not self.driver:
                try:
                    print("🔄 Retry driver con auto-detect (cache clearing)...")
                    self.driver = _build_driver_with_options(None, clear_cache=True)
                except Exception as e3:
                    print(f"❌ CRITICAL: impossibile avviare il driver: {e3}")
                    self.driver = None

        if self.driver:
            self.driver.set_page_load_timeout(60)   # max 60s per caricare una pagina
            self.driver.set_script_timeout(30)      # max 30s per script JS
            print("✅ Driver avviato (stealth mode)")

    def close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except OSError as e:
                pass  # Ignore OSError on destructor
            except Exception as e:
                pass  # Ignore other exceptions on quit
            try:
                del self.driver
            except Exception:
                pass
            self.driver = None

    def safe_get(self, url, retries=1, restart_on_renderer=True, pause=0.5):
        """Robust wrapper around driver.get that handles TimeoutException and renderer timeouts."""
        if not self.driver:
            return False

        attempt = 0
        while attempt <= retries:
            attempt += 1
            try:
                try:
                    self.driver.get(url)
                except TimeoutException:
                    try:
                        self.driver.execute_script("window.stop();")
                    except Exception:
                        pass
                time.sleep(pause)
                return True
            except WebDriverException as e:
                msg = str(e)
                if restart_on_renderer and "Timed out receiving message from renderer" in msg:
                    try:
                        self.close_driver()
                        time.sleep(0.5)
                        self.start_driver()
                        if not self.driver:
                            return False
                        continue
                    except Exception as e2:
                        return False
                if attempt > retries:
                    return False
                time.sleep(0.5)
            except Exception as e:
                return False

        return False

    def clean_filename(self, filename):
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        return filename.strip()

    def clean_player_name_sofa(self, text):
        """Pulisce nomi da Sofascore (c), numeri, ecc."""
        text = text.replace("(c)", "").replace("(C)", "")
        # Rimuove numero maglia iniziale anche se attaccato al nome (es. 39M. Safonov, 17Vitinha)
        text = re.sub(r'^\s*\d{1,3}\s*', '', text)
        # Se l'iniziale e il cognome sono attaccati, inserisce spazio (es. K.De -> K. De)
        text = re.sub(r'([A-Za-z])\.([A-Za-z])', r'\1. \2', text)
        text = re.sub(r'\b\d+\b', '', text)
        text = re.sub(r'\d+\.\d+', '', text)
        text = text.replace("-", " ").strip()
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    # =========================================================================
    # 🕵️ MODULO SOFASCORE V11.0 - PARSING STRUTTURATO (Confirmed + Probable)
    # =========================================================================

    _SOFA_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9,it;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.sofascore.com/',
        'Origin': 'https://www.sofascore.com',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'Sec-Ch-Ua': '"Chromium";v="145", "Google Chrome";v="145", "Not-A.Brand";v="24"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Connection': 'keep-alive',
    }

    _TEAM_ALIASES = {
        'como calcio': 'como',
        'inter milan': 'inter',
        'ac milan': 'milan',
        'fc barcelona': 'barcelona',
        'paris saint germain': 'psg',
        'psg': 'paris saint germain',
        'manchester united': 'man united',
        'manchester city': 'man city',
        'sparta praha': 'sparta prague',
        'st pauli': 'st. pauli',
        'cf elche': 'elche',
        'rayo vallecano de madrid': 'rayo vallecano',
    }

    _TEAM_STOPWORDS = {
        'calcio', 'fc', 'cf', 'afc', 'ssc', 'ac', 'club', 'football', 'women', 'u19', 'u20', 'u21',
        'de', 'la', 'el', 'the'
    }

    def _normalize_team_for_match(self, text):
        text = unicodedata.normalize('NFKD', str(text))
        text = ''.join(c for c in text if not unicodedata.combining(c))
        text = text.lower()
        text = text.replace('&', ' and ')
        text = re.sub(r'[^a-z0-9\s]+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _team_match_score(self, target, candidate):
        """Ritorna uno score robusto per confrontare nomi squadra con varianti e alias."""
        target_norm = self._normalize_team_for_match(target)
        cand_norm = self._normalize_team_for_match(candidate)

        def core_form(value):
            tokens = [t for t in value.split() if t not in self._TEAM_STOPWORDS]
            return ' '.join(tokens).strip()

        target_core = core_form(target_norm)
        cand_core = core_form(cand_norm)

        alias_target = self._TEAM_ALIASES.get(target_norm, target_norm)
        alias_cand = self._TEAM_ALIASES.get(cand_norm, cand_norm)
        alias_target_core = core_form(alias_target)
        alias_cand_core = core_form(alias_cand)

        scores = [
            difflib.SequenceMatcher(None, target_norm, cand_norm).ratio(),
            difflib.SequenceMatcher(None, target_core, cand_core).ratio() if target_core and cand_core else 0,
            difflib.SequenceMatcher(None, alias_target, alias_cand).ratio(),
            difflib.SequenceMatcher(None, alias_target_core, alias_cand_core).ratio() if alias_target_core and alias_cand_core else 0,
        ]

        # Premia le corrispondenze token-based quando c'è inclusione chiara
        target_tokens = set(target_core.split()) if target_core else set()
        cand_tokens = set(cand_core.split()) if cand_core else set()
        if target_tokens and cand_tokens:
            overlap = len(target_tokens.intersection(cand_tokens)) / max(len(target_tokens), len(cand_tokens))
            scores.append(overlap)

        return max(scores)

    def _find_sofascore_id_via_api(self, team_h, team_a):
        """
        Cerca il match ID navigando con il browser stealth all'URL dell'API Sofascore.
        Il browser bypassa Cloudflare automaticamente — niente 403.
        Cache giornaliera: l'API viene chiamata una volta sola per tutti i match del giorno.
        Ritorna (match_id, referee_info) o (None, 'NON TROVATO').
        """
        today = datetime.now().strftime("%Y-%m-%d")
        api_url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{today}"
        try:
            # Usa cache se disponibile per oggi
            if self._sofa_events_cache is None or self._sofa_cache_date != today:
                print("   📡 Carico eventi Sofascore del giorno...")
                self.safe_get(api_url)
                time.sleep(1)
                raw = self.driver.find_element(By.TAG_NAME, "body").text
                data = json.loads(raw)
                self._sofa_events_cache = data.get("events", [])
                self._sofa_cache_date = today
                print(f"   📅 Cache: {len(self._sofa_events_cache)} eventi caricati")

            events = self._sofa_events_cache
            best_score = 0
            best_event = None
            best_scores = (0, 0)  # (score_h, score_a)
            
            for ev in events:
                # Esclude match femminili: controlla tournament/category per keyword "women"
                tournament = ev.get("tournament", {})
                category = tournament.get("category", {})
                category_name = (category.get("name", "") or "").lower()
                tournament_name = (tournament.get("name", "") or "").lower()
                
                if "women" in category_name or "women" in tournament_name or "femminile" in tournament_name.lower():
                    continue  # Salta match femminili
                
                home = ev.get("homeTeam", {}).get("name", "")
                away = ev.get("awayTeam", {}).get("name", "")
                score_h = self._team_match_score(team_h, home)
                score_a = self._team_match_score(team_a, away)
                combined = score_h + score_a
                if combined > best_score:
                    best_score = combined
                    best_event = ev
                    best_scores = (score_h, score_a)

            # Richiede score combinato decente e NON femminile
            if best_event and best_score >= 1.0:
                match_id = str(best_event["id"])
                referee_info = "NON TROVATO"
                ref = best_event.get("referee")
                if ref:
                    referee_info = ref.get("name", "NON TROVATO")
                home = best_event.get("homeTeam", {}).get("name", "")
                away = best_event.get("awayTeam", {}).get("name", "")
                print(f"   ✅ API Sofascore: trovato '{home} - {away}' (ID: {match_id})")
                return match_id, referee_info
            else:
                print(f"   ⚠️ API Sofascore: nessun match simile (best: {best_score:.2f})")
                return None, "NON TROVATO"

        except Exception as e:
            print(f"   ⚠️ API Sofascore fallita: {e}")
            self._sofa_events_cache = None  # reset cache in caso di errore
            return None, "NON TROVATO"

    def _find_sofascore_id_via_browser(self, team_h, team_a):
        """
        Fallback: cerca il match ID via DuckDuckGo HTML (requests, no browser)
        poi carica la pagina Sofascore col browser solo per estrarre l'ID.
        Ritorna (match_id, referee_info) o (None, 'NON TROVATO').
        """
        import urllib.parse
        referee_info = "NON TROVATO"
        try:
            # Ricerca DDG via requests sull'endpoint HTML (non JS, non bloccato)
            query = f"site:sofascore.com {team_h} {team_a} football"
            ddg_headers = {
                'User-Agent': self._SOFA_HEADERS['User-Agent'],
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://duckduckgo.com/',
            }
            ddg_resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={'q': query, 'b': '', 'kl': ''},
                headers=ddg_headers,
                timeout=10
            )
            ddg_soup = BeautifulSoup(ddg_resp.text, 'html.parser')
            found_link = None
            for a in ddg_soup.select('a.result__a'):
                href = a.get('href', '')
                if 'sofascore.com' in href and 'football' in href:
                    # DDG usa redirect, estrai URL reale dal parametro uddg=
                    real_url = re.search(r'uddg=([^&]+)', href)
                    found_link = urllib.parse.unquote(real_url.group(1)) if real_url else href
                    break

            if not found_link:
                print("   ❌ Nessun link Sofascore trovato (DDG requests).")
                return None, referee_info

            print(f"   🔗 Link trovato: {found_link[:80]}...")

            # Carica pagina con browser per estrarre ID e arbitro
            self.safe_get(found_link)
            time.sleep(3)
            try:
                self.driver.find_element(By.XPATH, "//button[contains(@class, 'fc-cta-consent')]").click()
            except:
                pass
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            full_text = soup.get_text(" | ")
            m = re.search(r"(Referee|Arbitro):\s*([^|]+)", full_text)
            if m:
                referee_info = m.group(2).strip()
            id_matches = re.findall(r'"id":(\d{7,9})', self.driver.page_source)
            match_id = None
            if id_matches:
                match_id = [i for i in id_matches if len(i) >= 7][0]
            if not match_id:
                print("   ❌ Match ID non trovato nella pagina.")
                return None, referee_info
            return match_id, referee_info
        except Exception as e:
            print(f"   ⚠️ Errore browser fallback: {e}")
            return None, referee_info

    def fetch_sofascore_lineups(self, team_h, team_a):
        print(f"   🦆 Avvio Modulo Sofascore V11: {team_h} - {team_a}")
        self._last_lineup_source = "none"
        final_roster = []
        referee_info = "NON TROVATO"

        try:
            # 1. Cerca match ID: prima via API (veloce), poi via browser (fallback)
            match_id, referee_info = self._find_sofascore_id_via_api(team_h, team_a)
            if not match_id:
                print("   🔄 Fallback: ricerca via browser (DuckDuckGo)...")
                match_id, referee_info = self._find_sofascore_id_via_browser(team_h, team_a)
            if not match_id:
                print("   ❌ Match ID non trovato con nessun metodo.")
                return [], referee_info

            # 2. Prima prova lineups strutturati da endpoint evento (piu affidabile del widget HTML)
            api_roster = self._fetch_structured_lineups_by_event_id(match_id)
            if len(api_roster) >= 22:
                self._last_lineup_source = "api"
                print(f"   ✅ Formazioni API estratte: {len(api_roster)} giocatori")
                return api_roster, referee_info

            # 3. Widget Lineups - PARSING STRUTTURATO (fallback)
            widget_url = f"https://widgets.sofascore.com/embed/lineups?id={match_id}&widgetTheme=light"
            self.safe_get(widget_url)
            time.sleep(2)
            
            # DEBUG: Salva HTML per analisi
            if DEBUG_SAVE_HTML:
                try:
                    os.makedirs(DEBUG_FOLDER, exist_ok=True)
                    debug_filename = f"widget_{match_id}_{datetime.now().strftime('%H%M%S')}.html"
                    debug_path = os.path.join(DEBUG_FOLDER, debug_filename)
                    with open(debug_path, 'w', encoding='utf-8') as f:
                        f.write(self.driver.page_source)
                    print(f"   🔍 DEBUG: HTML salvato in {debug_filename}")
                except: pass
            
            # STRATEGIA 1: Parsing CSS Strutturato (Confirmed Lineups)
            final_roster = self._parse_confirmed_lineups()
            if final_roster:
                self._last_lineup_source = "widget_confirmed"
                print(f"   ✅ Formazioni UFFICIALI estratte: {len(final_roster)} giocatori")
                return final_roster, referee_info
            
            # STRATEGIA 2: Parsing CSS Strutturato (Probable Lineups)
            final_roster = self._parse_probable_lineups()
            if final_roster:
                self._last_lineup_source = "widget_probable"
                print(f"   ⚠️ Formazioni PROBABILI estratte: {len(final_roster)} giocatori")
                return final_roster, referee_info
            
            # STRATEGIA 3: Fallback Text-Based (legacy)
            final_roster = self._parse_lineups_fallback(team_h, team_a)
            if final_roster:
                self._last_lineup_source = "widget_fallback"
                print(f"   ⚙️ Formazioni estratte (fallback): {len(final_roster)} giocatori")
                return final_roster, referee_info
            
            print("   ❌ Nessuna formazione trovata con tutti i metodi")
                        
        except Exception as e:
            print(f"   ⚠️ Errore Sofascore: {e}")
            traceback.print_exc()
            
        return final_roster, referee_info

    def _extract_player_names_from_lineups_payload(self, payload):
        """Estrae nomi giocatori da payload JSON lineups con struttura variabile.
        Priorita: titolari home/away separati, poi fallback generico.
        """

        def dedupe_keep_order(items):
            out = []
            seen = set()
            for n in items:
                k = n.upper()
                if k not in seen:
                    seen.add(k)
                    out.append(n)
            return out

        def clean_name(raw_name):
            if not raw_name:
                return None
            clean = self.clean_player_name_sofa(str(raw_name))
            if clean and len(clean) >= 3:
                return clean
            return None

        def extract_from_items(items, starters_only=False):
            names = []
            if not isinstance(items, list):
                return names

            for item in items:
                if not isinstance(item, dict):
                    n = clean_name(item)
                    if n:
                        names.append(n)
                    continue

                # In modalita titolari, scarta panchinari se il flag e presente
                if starters_only and (item.get('substitute') is True or item.get('isSubstitute') is True):
                    continue

                player_obj = item.get('player') if isinstance(item.get('player'), dict) else None
                raw_name = None
                if player_obj:
                    raw_name = player_obj.get('name') or player_obj.get('shortName')
                else:
                    raw_name = item.get('name') or item.get('shortName')

                n = clean_name(raw_name)
                if n:
                    names.append(n)

            return names

        containers = []
        if isinstance(payload, dict):
            containers.extend([
                payload,
                payload.get('lineups') if isinstance(payload.get('lineups'), dict) else None,
                payload.get('data') if isinstance(payload.get('data'), dict) else None,
            ])

        # 1) Prova estrazione per lato (home/away) privilegiando i titolari
        for c in [x for x in containers if x]:
            home_names = []
            away_names = []

            for side_key, side_bucket in (('home', home_names), ('away', away_names)):
                side = c.get(side_key)
                if not isinstance(side, dict):
                    continue

                # Chiavi tipiche per titolari
                for key in ('starters', 'startingLineup', 'starting11'):
                    side_bucket.extend(extract_from_items(side.get(key), starters_only=True))

                # Fallback se non abbiamo trovato abbastanza per lato
                if len(side_bucket) < 9:
                    for key in ('players', 'lineup'):
                        side_bucket.extend(extract_from_items(side.get(key), starters_only=True))

            home_names = dedupe_keep_order(home_names)
            away_names = dedupe_keep_order(away_names)

            # Se abbiamo entrambi i lati, ritorna 11+11 senza tagli ciechi globali
            if len(home_names) >= 10 and len(away_names) >= 10:
                return home_names[:11] + away_names[:11]

        # 2) Fallback generico (flat)
        flat_names = []
        for c in [x for x in containers if x]:
            for side_key in ('home', 'away'):
                side = c.get(side_key)
                if not isinstance(side, dict):
                    continue
                for key in ('starters', 'startingLineup', 'starting11', 'players', 'lineup'):
                    flat_names.extend(extract_from_items(side.get(key), starters_only=True))

        return dedupe_keep_order(flat_names)

    def _fetch_structured_lineups_by_event_id(self, match_id):
        """Prova a leggere lineups da endpoint Sofascore evento (JSON strutturato)."""
        endpoints = [
            f"https://api.sofascore.com/api/v1/event/{match_id}/lineups",
            f"https://api.sofascore.com/api/v1/event/{match_id}/lineups/",
        ]

        for ep in endpoints:
            try:
                self.safe_get(ep)
                time.sleep(0.8)
                raw = self.driver.find_element(By.TAG_NAME, 'body').text
                payload = json.loads(raw)
                names = self._extract_player_names_from_lineups_payload(payload)
                if len(names) >= 22:
                    return names
            except Exception:
                continue

        return []

    def _parse_confirmed_lineups(self):
        """Parsing per FORMAZIONI UFFICIALI (confirmed lineups)"""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            players = []
            
            # SELETTORE PRECISISSIMO (testato su partite giocate)
            # Sofascore usa div[class*="Player-styles__Name"] per i nomi giocatori
            precise_selector = 'div[class*="Player-styles__Name"]'
            
            elements = soup.select(precise_selector)
            if elements:
                
                for elem in elements:
                    # Prendi sia text che title attribute (fallback)
                    name = elem.get_text(strip=True) or elem.get('title', '')
                    if not name or len(name) < 3: continue
                    if name.isdigit(): continue
                    
                    # FILTRO ABBR: solo iniziali pure, NON "R.Y. Konigsdorffer"
                    if re.match(r'^([A-Z]\.)+\s*$', name, re.IGNORECASE): continue
                    
                    # FILTRO KEYWORDS
                    bad_keywords = ['goalkeeper', 'defender', 'midfielder', 'forward', 'manager', 
                                   'coach', 'rating', 'mgr', 'formation', 'lineup', 'bench',
                                   'substitutes', 'average', 'stats', 'window', 'function', 
                                   'every game']
                    if any(x in name.lower() for x in bad_keywords): continue
                    
                    clean_name = self.clean_player_name_sofa(name)
                    if clean_name and len(clean_name) > 2:
                        players.append(clean_name)
            
            # Fallback: altri selettori se il principale fallisce
            if not players:
                print("   🔄 Fallback: tentativo selettori alternativi...")
                fallback_selectors = [
                    'div[title][class*="Name"]',
                    '[class*="PlayerWrapper"] div[class*="Name"]',
                    'div[class*="lineup"] span[class*="name"]'
                ]
                
                for selector in fallback_selectors:
                    elements = soup.select(selector)
                    if len(elements) > 10:  # Solo se trova almeno 10+ elementi
                        print(f"       ✅ Selettore fallback funzionante: {selector[:40]}...")
                        for elem in elements:
                            name = elem.get_text(strip=True) or elem.get('title', '')
                            if not name or len(name) < 3: continue
                            if name.isdigit(): continue
                            if re.match(r'^([A-Z]\.)+\s*$', name, re.IGNORECASE): continue
                            
                            bad_keywords = ['goalkeeper', 'defender', 'midfielder', 'forward', 'manager', 
                                           'coach', 'rating', 'mgr', 'formation', 'lineup', 'bench',
                                           'substitutes', 'average', 'stats', 'window', 'function', 'every game']
                            if any(x in name.lower() for x in bad_keywords): continue
                            
                            clean_name = self.clean_player_name_sofa(name)
                            if clean_name and len(clean_name) > 2:
                                players.append(clean_name)
                        break
            
            # Validazione finale
            players = self._validate_and_clean_roster(players)
            if len(players) >= 11:
                return players
                
        except Exception as e:
            print(f"   ⚠️ _parse_confirmed_lineups fallito: {e}")
        return []

    def _parse_probable_lineups(self):
        """Parsing per FORMAZIONI PROBABILI (probable lineups)"""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            players = []
            
            # STRATEGIA 1: Selettori specifici per probable lineups
            selectors = [
                'div[class*="box"] span',                     # Box layout
                'div[class*="cell"] span',                    # Cell layout
                'div[class*="probable"] span',                # Probable lineup
                'div[class*="possible"] span',                # Possible lineup
                '[class*="player-row"] span',                 # Player rows
                'ul[class*="lineup"] li span',                # Lista lineup
                '[class*="team-lineup"] span'                 # Team lineup
            ]
            
            for selector in selectors:
                if players:  # Se già trovati giocatori, esci
                    break
                    
                try:
                    elements = soup.select(selector)
                    if len(elements) > 10:  # Solo se trova almeno 10+ elementi
                        print(f"   🎯 Selettore probable funzionante: {selector[:40]}...")
                        
                        for elem in elements:
                            name = elem.get_text(strip=True)
                            if not name or len(name) < 3: continue
                            if name.isdigit(): continue
                            
                            # FILTRO ABBR: solo iniziali pure, NON "R.Y. Konigsdorffer"
                            if re.match(r'^([A-Z]\.)+\s*$', name, re.IGNORECASE): continue
                            
                            # FILTRO KEYWORDS
                            bad_keywords = ['window', 'function', 'rating', 'average', 'stats', 'mgr',
                                           'manager', 'coach', 'goalkeeper', 'defender', 'midfielder', 
                                           'forward', 'formation', 'lineup', 'bench', 'substitutes']
                            if any(x in name.lower() for x in bad_keywords): continue
                            
                            clean_name = self.clean_player_name_sofa(name)
                            if clean_name and len(clean_name) > 2:
                                players.append(clean_name)
                except:
                    continue
            
            # STRATEGIA 2: Fallback generico (metodo originale)
            if not players:
                boxes = soup.find_all('div', class_=lambda x: x and ('box' in x.lower() or 'cell' in x.lower()))
                for box in boxes:
                    spans = box.find_all('span')
                    for span in spans:
                        name = span.get_text(strip=True)
                        if not name or len(name) < 3: continue
                        if name.isdigit(): continue
                        if re.match(r'^([A-Z]\.)+\s*$', name, re.IGNORECASE): continue
                        
                        bad_keywords = ['window', 'function', 'rating', 'average', 'stats', 'mgr',
                                       'manager', 'coach', 'goalkeeper', 'defender', 'midfielder', 
                                       'forward', 'formation', 'lineup', 'bench', 'substitutes']
                        if any(x in name.lower() for x in bad_keywords): continue
                        
                        clean_name = self.clean_player_name_sofa(name)
                        if clean_name and len(clean_name) > 2:
                            players.append(clean_name)
            
            # Validazione e pulizia
            players = self._validate_and_clean_roster(players)
            if len(players) >= 11:
                print(f"   🎯 Parsing Probable: {len(players)} giocatori")
                return players
                
        except Exception as e:
            print(f"   ⚠️ _parse_probable_lineups fallito: {e}")
        return []

    def _parse_lineups_fallback(self, team_h, team_a):
        """Fallback: parsing text-based (metodo legacy migliorato)"""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            for s in soup(["script", "style"]): s.decompose()
            all_texts = [t.strip() for t in soup.find_all(string=True) if t.strip()]
            
            garbage = [
                "Goalkeeper", "Defender", "Midfielder", "Forward", "Rating", "Sofascore", "Average", 
                "Subs", "bench", "Every game counts", "See much more", "Substitutes", "Na.", "N/A", 
                "Possible lineups", "Confirmed lineups", "Missing players", "Coach", "Manager",
                "window.", "gtag", "function", "config", "{", "}", "return", "var ",
                "Formation", "Lineup", "Starting", "XI", "mgr", "mgr."
            ]
            
            team_garbage = []
            for t_name in [team_h, team_a]:
                parts = t_name.split()
                for p in parts:
                    if len(p) > 2: team_garbage.append(p.upper())

            temp_list = []
            start = False
            
            for txt in all_texts:
                if not start:
                    if any(x in txt.lower() for x in ["lineups", "formazioni", "possible", "confirmed"]): 
                        start = True
                    continue
                
                if len(txt) < 3 or txt.isdigit(): continue
                if any(bad.lower() in txt.lower() for bad in garbage): continue
                if "{" in txt or "window." in txt: continue
                
                # FILTRO ABBR: solo iniziali pure, NON "R.Y. Konigsdorffer"
                if re.match(r'^([A-Z]\.)+\s*$', txt, re.IGNORECASE): continue
                
                if txt.strip().upper() in team_garbage: continue
                
                clean = self.clean_player_name_sofa(txt)
                if clean and len(clean) > 2 and clean.upper() not in team_garbage:
                    temp_list.append(clean)
            
            # Validazione finale
            players = self._validate_and_clean_roster(temp_list)
            if len(players) >= 11:
                print(f"   🎯 Parsing Fallback: {len(players)} giocatori")
                return players
                
        except Exception as e:
            print(f"   ⚠️ _parse_lineups_fallback fallito: {e}")
        return []

    def _validate_and_clean_roster(self, raw_list):
        """Validazione e pulizia finale della lista giocatori"""
        def normalize_check(text):
            """Normalizza per controllo (senza accenti)"""
            nfkd = unicodedata.normalize('NFKD', text)
            return ''.join([c for c in nfkd if not unicodedata.combining(c)]).upper()
        

        # Rimuovi duplicati mantenendo ordine
        seen = set()
        clean_list = []
        for name in raw_list:
            name_norm = normalize_check(name)
            if name_norm not in seen and len(name) > 2:
                clean_list.append(name)
                seen.add(name_norm)
            elif name_norm in seen:
                pass  # duplicato silenzioso

        # FILTRO FINALE: rimuovi qualsiasi cosa con pattern sospetti
        final_list = []
        # Pattern che devono essere PAROLE COMPLETE (non sottostringhe)
        blocked_keywords = ['MGR', 'COACH', 'MANAGER', 'HOME', 'AWAY', 'TEAM', 
                           'EVERY', 'GAME', 'COUNTS', 'SEE', 'MUCH', 'MORE']
        # Pattern che possono essere sottostringhe (nomi squadre)
        blocked_substrings = ['UNITED', 'CITY', 'BALOMPIE', 'ALBACETE', 'PAULI', 
                             'LEVERKUSEN', 'BAYER', 'ARSENAL', 'CHELSEA', 'BARCELONA']
        
        for name in clean_list:
            # Skip SOLO iniziali pure (es. "R.Y." o "A.B.") ma NON "R.Y. Konigsdorffer"
            if re.match(r'^([A-Z]\.)+\s*$', name, re.IGNORECASE):
                continue
            
            # Skip se contiene solo 1-2 lettere
            if len(name) <= 2:
                continue
            
            # Normalizza per check
            name_norm = normalize_check(name)
            name_words = name_norm.split()
            
            # Skip se PAROLA COMPLETA match keyword bloccata
            is_blocked = False
            for keyword in blocked_keywords:
                if keyword in name_words:
                    is_blocked = True
                    break
            
            if is_blocked: continue
            
            # Skip se sottostringa match pattern squadra
            for pattern in blocked_substrings:
                if pattern in name_norm:
                    is_blocked = True
                    break
            
            if is_blocked: continue
            
            final_list.append(name)

        # Validazione conteggio: se troppi giocatori (>30) probabilmente c'è rumore
        if len(final_list) > 30:
            print(f"   ⚠️ Troppi giocatori ({len(final_list)}), probabile contaminazione")
            # Taglia a primi 22 (2 squadre)
            final_list = final_list[:22]
        
        return final_list
    
    def _filter_team_names_from_lineup(self, lineup_names, team_home, team_away):
        """Filtra nomi squadre dalla lista finale (sicurezza extra)"""
        filtered = []
        
        def normalize_full(text):
            """Normalizza accenti e rimuove spazi/punti/trattini (per match esatto sull'intero nome)."""
            nfkd = unicodedata.normalize('NFKD', text)
            text_normalized = ''.join([c for c in nfkd if not unicodedata.combining(c)])
            return text_normalized.upper().replace(' ', '').replace('.', '').replace('-', '')

        def normalize_words(text):
            """Normalizza accenti e rimuove punti/trattini ma MANTIENE gli spazi (per match per parola)."""
            nfkd = unicodedata.normalize('NFKD', text)
            text_normalized = ''.join([c for c in nfkd if not unicodedata.combining(c)])
            return text_normalized.upper().replace('.', '').replace('-', '')

        # Crea pattern da escludere (normalizzati senza spazi)
        team_patterns = set()
        for team in [team_home, team_away]:
            team_patterns.add(normalize_full(team))
            # Aggiungi singole parole del nome squadra (es. "BAYER", "LEVERKUSEN", "INTER", "MILAN")
            for part in team.split():
                if len(part) > 1:
                    team_patterns.add(normalize_full(part))

        # Aggiungi varianti comuni
        common_patterns = ['FC', 'CF', 'AC', 'SC', 'UNITED', 'CITY', 'BALOMPIE', 'ALBACETE']
        team_patterns.update(common_patterns)

        for name in lineup_names:
            # Match esatto sull'intero nome concatenato
            name_full = normalize_full(name)
            # Parole individuali del nome giocatore (es. ["K", "DE", "WINTER"])
            name_words = normalize_words(name).split()

            is_team_name = False
            for pattern in team_patterns:
                # Match esatto sull'intero nome (es. "INTER" == "INTER")
                if name_full == pattern:
                    is_team_name = True
                    break
                # Match per parola intera: il pattern deve corrispondere a una parola completa
                # Questo evita che "INTER" blocchi "K. De Winter" (WINTER ≠ INTER)
                if len(pattern) > 2 and pattern in name_words:
                    is_team_name = True
                    break

            if not is_team_name:
                filtered.append(name)

        return filtered

    def find_best_match(self, sofa_name, players_data):
        """Fuzzy match tra nome Sofascore e lista giocatori BetMonitor"""
        def norm(text):
            text = unicodedata.normalize('NFKD', str(text))
            text = ''.join(c for c in text if not unicodedata.combining(c))
            text = re.sub(r'[^A-Za-z0-9 ]+', ' ', text).lower()
            text = re.sub(r'\s+', ' ', text).strip()
            return text

        def tokens(text):
            return [t for t in norm(text).split() if t]

        def surname_score(query_tokens, cand_tokens):
            if not query_tokens or not cand_tokens:
                return 0.0
            q_last = query_tokens[-1]
            c_last = cand_tokens[-1]
            if q_last == c_last:
                return 1.0
            if q_last in c_last or c_last in q_last:
                return 0.85
            return 0.0

        def first_initial_match(query_tokens, cand_tokens):
            if not query_tokens or not cand_tokens:
                return False
            q_first = query_tokens[0]
            c_first = cand_tokens[0]
            if len(q_first) == 1:
                return c_first.startswith(q_first)
            return q_first == c_first or q_first.startswith(c_first) or c_first.startswith(q_first)

        sofa_norm = norm(sofa_name)
        sofa_tokens = tokens(sofa_name)
        if not sofa_tokens:
            return None

        # 1) Match esatto normalizzato
        exact_matches = [p for p in players_data if norm(p['player_name']) == sofa_norm]
        if len(exact_matches) == 1:
            return exact_matches[0]

        # 2) Ranking conservativo: cognome + iniziale/primo nome + overlap token
        candidates = []
        for p in players_data:
            cand_tokens = tokens(p['player_name'])
            if not cand_tokens:
                continue

            s_score = surname_score(sofa_tokens, cand_tokens)
            if s_score == 0:
                continue

            initial_ok = first_initial_match(sofa_tokens, cand_tokens)
            overlap = len(set(sofa_tokens).intersection(cand_tokens)) / max(len(set(sofa_tokens)), len(set(cand_tokens)))
            sim = difflib.SequenceMatcher(None, sofa_norm, norm(p['player_name'])).ratio()

            # Pesa forte cognome e iniziale; i casi con cognomi duplicati vengono risolti qui.
            score = (s_score * 0.55) + (0.20 if initial_ok else 0.0) + (overlap * 0.15) + (sim * 0.10)
            candidates.append((score, initial_ok, p))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best_score, best_initial_ok, best_cand = candidates[0]

        # Se il nome Sofascore ha solo iniziale+cognome, richiede iniziale coerente.
        if len(sofa_tokens) >= 2 and len(sofa_tokens[0]) == 1 and not best_initial_ok:
            return None

        # Evita assegnazioni sbagliate quando ci sono due candidati molto simili (es. Sanchez/Garcia)
        if len(candidates) > 1:
            second_score = candidates[1][0]
            if best_score - second_score < 0.08:
                return None

        if best_score >= 0.62:
            return best_cand

        return None

    def _sanitize_lineup_names_for_output(self, lineup_names, players_data):
        """Riduce il rumore in uscita: mantiene i nomi che matchano BetMonitor e scarta artefatti evidenti."""
        if not lineup_names:
            return []

        def norm_for_score(name):
            n = unicodedata.normalize('NFKD', name)
            n = ''.join(c for c in n if not unicodedata.combining(c))
            n = re.sub(r'[^A-Za-z ]+', ' ', n).upper()
            return re.sub(r'\s+', ' ', n).strip()

        # 1) Pulizia locale + deduplica mantenendo ordine
        cleaned = []
        seen = set()
        for raw in lineup_names:
            name = self.clean_player_name_sofa(raw)
            if not name or len(name) < 3:
                continue
            key = norm_for_score(name)
            if key and key not in seen:
                seen.add(key)
                cleaned.append(name)

        # 2) Separa match affidabili da non-match
        matched = []
        unmatched = []
        for name in cleaned:
            m = self.find_best_match(name, players_data)
            if not m:
                unmatched.append(name)
                continue
            score = difflib.SequenceMatcher(None, norm_for_score(name), norm_for_score(m['player_name'])).ratio()
            if score >= 0.55:
                matched.append(name)
            else:
                unmatched.append(name)

        def surname_key(name):
            parts = norm_for_score(name).split()
            return parts[-1] if parts else ""

        matched_surnames = {surname_key(name) for name in matched if surname_key(name)}

        # Se abbiamo gia almeno 22 nomi coerenti con BetMonitor, scartiamo il rumore residuo
        if len(matched) >= 22:
            return matched

        def is_player_like_unmatched(name):
            # Ammetti mononimi (es. Vitinha)
            if re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-]{4,}$", name):
                return True

            # Ammetti formato iniziale+cognome/i: K. De Winter, O. Dembele, C. Casseres Jr.
            if re.match(r"^[A-Z]\.\s*[A-Za-zÀ-ÖØ-öø-ÿ'\-]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-]+){0,2}$", name):
                return True

            return False

        def is_ambiguous_with_matched(name):
            parts = norm_for_score(name).split()
            if not parts:
                return False
            return parts[-1] in matched_surnames

        # Se il match con BetMonitor e già buono, non inserire unmatched rumorosi
        if len(matched) >= 18:
            ordered = []
            seen = set()
            ambiguous_candidates = []
            for n in matched:
                k = n.upper()
                if k not in seen:
                    seen.add(k)
                    ordered.append(n)

            for n in unmatched:
                if len(ordered) >= 22:
                    break
                if not is_player_like_unmatched(n):
                    continue
                if is_ambiguous_with_matched(n):
                    ambiguous_candidates.append(n)
                    continue
                k = n.upper()
                if k not in seen:
                    seen.add(k)
                    ordered.append(n)

            # Recovery edge-case: se restano 21 nomi, reinserisci 1 ambiguo player-like
            # (tipico caso R.Silva con cognome gia presente in lista)
            if len(ordered) == 21 and ambiguous_candidates:
                for n in ambiguous_candidates:
                    k = n.upper()
                    if k not in seen:
                        ordered.append(n)
                        break

            return ordered

        # Fallback conservativo quando i match sono troppo pochi
        plausible_unmatched = [n for n in unmatched if is_player_like_unmatched(n) and not is_ambiguous_with_matched(n)]
        return matched + plausible_unmatched

    def _basic_clean_lineup_names(self, lineup_names):
        """Pulizia leggera (senza matching BetMonitor) per rescue dei lineup API."""
        cleaned = []
        seen = set()
        for raw in lineup_names:
            name = self.clean_player_name_sofa(raw)
            if not name or len(name) < 3:
                continue
            k = unicodedata.normalize('NFKD', name)
            k = ''.join(c for c in k if not unicodedata.combining(c))
            k = re.sub(r'[^A-Za-z0-9 ]+', ' ', k).upper()
            k = re.sub(r'\s+', ' ', k).strip()
            if k and k not in seen:
                seen.add(k)
                cleaned.append(name)
        return cleaned

    # =========================================================================
    # ⚙️ CORE LOGIC (BetMonitor)
    # =========================================================================

    def scan_league_for_today_matches(self, league_url):
        print(f"🔍 Analisi Campionato: {league_url}")
        if not self.driver:
            print("   ⚠️ Driver non disponibile, salto campionato.")
            return []
        
        self.safe_get(league_url)
        time.sleep(3)
        
        try:
            parts = league_url.split("/")
            if parts[-1].isdigit(): league_slug = parts[-2]
            else: league_slug = parts[-1]
        except: league_slug = "xxxxxx"

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        matches_to_scrape = []
        links = soup.find_all("a", href=True)
        
        today = datetime.now()
        date_check_1 = "Today"
        date_check_2 = today.strftime("%d %b")
        
        seen_urls = set()

        for link in links:
            href = link['href']
            full_url = href if href.startswith("http") else f"https://www.betmonitor.com{href}"
            
            if league_slug not in href: continue 
            if not re.search(r'/\d+$', href): continue
            if full_url.rstrip("/#") == league_url.rstrip("/#"): continue
            if href in seen_urls: continue
            
            container = link.find_parent(lambda tag: tag.name in ['tr', 'div'] and ('event' in str(tag.get('class', '')) or 'row' in str(tag.get('class', ''))))
            if not container: container = link.parent.parent
            
            container_text = container.get_text(" ", strip=True) if container else ""
            
            is_today = False
            if date_check_1 in container_text or "Oggi" in container_text: is_today = True
            elif date_check_2 in container_text: is_today = True
                
            if is_today:
                time_match = re.search(r'(\d{1,2}:\d{2})', container_text)
                start_time_str = time_match.group(1) if time_match else "00:00"
                
                title = "Unknown"
                try:
                    slug = href.split("/")[-2]
                    title = slug.replace("-", " ").title()
                    if title.lower().replace(" ", "-") == league_slug: continue 
                    title = title.replace(" Fc", "").replace("United", "Utd")
                except:
                    title = link.get_text(strip=True)

                # MODIFICA: Aggiungi league_url al dizionario match_info
                matches_to_scrape.append({
                    'url': full_url, 
                    'title': title, 
                    'start_time': start_time_str,
                    'league_url': league_url  # <-- NUOVO CAMPO
                })
                seen_urls.add(href)

        if not matches_to_scrape:
            self.empty_leagues_today.add(league_url)
            
        return matches_to_scrape

    def navigate_to_player_props(self, max_retries=2):
        """
        Naviga al menu Player Props su BetMonitor.
        Ritenta 1 volta in caso di errore di caricamento (non se l'opzione non esiste).
        """
        wait = WebDriverWait(self.driver, 9)
        actions = ActionChains(self.driver)
        
        for attempt in range(1, max_retries + 1):
            try:
                # Cookie banner (solo primo tentativo)
                if attempt == 1:
                    try:
                        cookie_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Agree')]")
                        self.driver.execute_script("arguments[0].click();", cookie_btn)
                        time.sleep(0.3)
                    except Exception:
                        pass

                # Trova il menu trigger
                menu_trigger = None
                potential = self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Bet Type') or contains(text(), '3-Way') or contains(text(), '1X2')]")
                for pt in potential:
                    try:
                        if pt.is_displayed():
                            menu_trigger = pt
                            break
                    except Exception:
                        continue
                if not menu_trigger:
                    try:
                        menu_trigger = self.driver.find_element(By.CSS_SELECTOR, ".bet-types, .market-selector, .dropdown")
                    except Exception:
                        menu_trigger = None

                if menu_trigger:
                    try:
                        actions.move_to_element(menu_trigger).perform()
                        time.sleep(0.2)
                    except Exception:
                        pass

                # Try multiple selectors for Player Props (varianti di testo)
                selectors = [
                    "//*[contains(text(), 'Player Props')]",
                    "//*[contains(text(), 'Player Prop')]",
                    "//*[contains(text(), 'Player Markets')]",
                    "//button[contains(@class,'player-props')]",
                ]

                player_props_btn = None
                for sel in selectors:
                    try:
                        elems = self.driver.find_elements(By.XPATH, sel)
                        if elems:
                            for e in elems:
                                try:
                                    if e.is_displayed():
                                        player_props_btn = e
                                        break
                                except Exception:
                                    continue
                        if player_props_btn:
                            break
                    except Exception:
                        continue

                if not player_props_btn:
                    try:
                        js = "Array.from(document.querySelectorAll('button, a, span')).filter(n=>n.innerText && /player\\s+prop/i.test(n.innerText)).shift()"
                        found = self.driver.execute_script(js)
                        if found:
                            try:
                                player_props_btn = self.driver.find_element(By.XPATH, "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'player prop')]")
                            except Exception:
                                player_props_btn = None
                    except Exception:
                        player_props_btn = None

                if player_props_btn:
                    try:
                        player_props_btn.click()
                    except Exception:
                        try:
                            self.driver.execute_script("arguments[0].click();", player_props_btn)
                        except Exception:
                            pass

                # Verifica caricamento
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "outright-cont")))

                return True

            except TimeoutException:
                # Timeout = probabile problema di caricamento, riprova silenziosamente
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                else:
                    return False
            except NoSuchElementException:
                # Elemento non esiste = partita senza Player Props
                return False
            except Exception:
                # Altri errori: riprova silenziosamente una volta
                if attempt < max_retries:
                    time.sleep(0.8)
                    continue
                else:
                    return False

        return False

    def process_match(self, match_info):
        url = match_info['url']
        temp_title = match_info['title']
        start_time_str = match_info['start_time']
        league_url = match_info.get('league_url', '')  # <-- ESTRAI league_url
        
        now = datetime.now()
        try:
            h, m = map(int, start_time_str.split(":"))
            match_date = now.replace(hour=h, minute=m, second=0)
            if match_date < now:
                diff_seconds = 0
                time_label = "LIVE"
            else:
                diff = match_date - now
                diff_seconds = diff.total_seconds()
                hours, remainder = divmod(int(diff_seconds), 3600)
                minutes = int(remainder / 60)
                time_label = f"{hours}h{minutes}m prima"
            diff_minutes_total = diff_seconds / 60
        except:
            diff_minutes_total = 9999
            time_label = "Orario_Sconosciuto"

        if not self.safe_get(url):
            print(f"   ⚠️ Errore caricamento match: {url}")
            return

        # Fix Titolo
        real_title = temp_title
        try:
            page_title = self.driver.title
            if page_title:
                clean_pt = page_title.strip()
                if "," in clean_pt: clean_pt = clean_pt.split(",")[0]
                if "|" in clean_pt: clean_pt = clean_pt.split("|")[0]
                clean_pt = re.split(r' Betting Odds| Odds', clean_pt, flags=re.IGNORECASE)[0]
                clean_pt = clean_pt.replace(u'\u2014', ' - ').replace(u'\u2013', ' - ').replace('—', ' - ').replace('–', ' - ')
                clean_pt = re.sub(r'\s+vs\s+', ' - ', clean_pt, flags=re.IGNORECASE)
                clean_pt = clean_pt.strip()
                if " - " in clean_pt: real_title = clean_pt
        except: pass

        if " - " not in real_title:
            try:
                h1_elem = WebDriverWait(self.driver, 2).until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
                raw_h1 = h1_elem.text.strip()
                if raw_h1:
                    clean_h1 = raw_h1
                    if "," in clean_h1: clean_h1 = clean_h1.split(",")[0]
                    if "|" in clean_h1: clean_h1 = clean_h1.split("|")[0]
                    clean_h1 = re.split(r' Betting Odds| Odds', clean_h1, flags=re.IGNORECASE)[0]
                    clean_h1 = clean_h1.replace(u'\u2014', ' - ').replace(u'\u2013', ' - ').replace('—', ' - ').replace('–', ' - ')
                    clean_h1 = re.sub(r'\s+vs\s+', ' - ', clean_h1, flags=re.IGNORECASE)
                    real_title = " ".join(clean_h1.split())
            except: pass

        print(f"\n⚽ {real_title}")
        print(f"   🕒 Inizio: {start_time_str} | Countdown: {time_label}")

        match_filename = f"{real_title} - ({time_label})"
        match_filename = self.clean_filename(match_filename)
        match_folder_name = self.clean_filename(real_title)

        if not self.navigate_to_player_props():
            print("   ❌ Skip: Player Props non trovati.")
            return

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        target_div = None
        for div in soup.select(".outright-cont"):
            h2 = div.find("h2")
            if h2:
                txt = h2.get_text(strip=True).lower()
                if 'card' in txt and 'player' in txt and 'red' not in txt:
                    target_div = div
                    break
        
        if not target_div: return

        players_data = []
        columns = target_div.select(".outright-cont-col")
        if not columns: columns = [target_div]

        for col in columns:
            for p_soup in col.select(".outright-league"):
                try:
                    name_tag = p_soup.select_one(".outright-league-info span")
                    if not name_tag: continue
                    p_name = name_tag.get_text(strip=True)
                    bookies = []
                    for odd in p_soup.select(".bookie-odd"):
                        try:
                            bn = odd.select_one(".bookie-logo-name").get_text(strip=True)
                            val = odd.select_one(".odd-decimal").get_text(strip=True)
                            bookies.append({'name': bn, 'odds': val})
                        except: pass
                    if bookies: players_data.append({'player_name': p_name, 'bookmakers': bookies})
                except: pass

        if not players_data: return

        # --- AUTO-MERGE ---
        players_data = self.auto_merge_known_duplicates(players_data)
        # smart_auto_merge ora è disabilitato (non unisce più)
        
        # --- RILEVAZIONE DUPLICATI POTENZIALI ---
        potential_duplicates = self.detect_potential_duplicates(players_data)

        # --- ANALISI ---
        matching = self.find_matching_odds(players_data)
        betfair_list = self.extract_betfair_odds_data(players_data)
        highest_odds = self.find_highest_odds_by_bookmaker(players_data)
        highest_no_bf = self.find_highest_odds_excluding_betfair(players_data)
        
        # --- TELEGRAM ALERT CHECK ---
        telegram_sections = []
        
        flag_emoji = "⚽"
        if "england" in url: flag_emoji = "🇬🇧"
        elif "italy" in url: flag_emoji = "🇮🇹"
        elif "france" in url: flag_emoji = "🇫🇷"
        elif "germany" in url: flag_emoji = "🇩🇪"
        elif "spain" in url: flag_emoji = "🇪🇸"
        
        telegram_msg = f"⚽ **{real_title.upper()}** {flag_emoji}\n"

        if matching['betfair_unibet']:
            lines = []
            for item in matching['betfair_unibet']:
                flag = " 💎" if item.get('is_highest') else ""
                lines.append(f"{item['player_name']}{flag}")
            if lines: telegram_sections.append("🔥 **BETFAIR - UNIBET:**\n" + "\n".join(lines))

        if matching['paddypower_unibet']:
            lines = []
            for item in matching['paddypower_unibet']:
                flag = " 💎" if item.get('is_highest') else ""
                lines.append(f"{item['player_name']}{flag}")
            if lines: telegram_sections.append("🍀 **PADDY - UNIBET:**\n" + "\n".join(lines))

        if "italy-serie-a" in url:
            if 'unibet' in highest_odds:
                unibet_highs = highest_odds['unibet']['players']
                if unibet_highs:
                    lines = []
                    for player_name, _ in unibet_highs: lines.append(player_name)
                    if lines: telegram_sections.append("🟢 **HIGH UNIBET:**\n" + "\n".join(lines))

        coral_ladbrokes_players = []
        for p in players_data:
            if len(p['bookmakers']) < 13: continue 
            try:
                all_odds = [float(b['odds']) for b in p['bookmakers']]
                max_odd = max(all_odds)
            except: continue
            is_target_high = False
            for b in p['bookmakers']:
                b_name = b['name'].lower().strip()
                try:
                    b_val = float(b['odds'])
                    if (b_name == 'coral' or b_name == 'ladbrokes') and b_val >= max_odd:
                        is_target_high = True; break
                except: continue
            if is_target_high: coral_ladbrokes_players.append(p['player_name'])
        
        if coral_ladbrokes_players:
            telegram_sections.append("🇬🇧 **HIGH CORAL/LADBROKES:**\n" + "\n".join(coral_ladbrokes_players))

        # --- TRIGGER NOTIFICA E STUDIO (SEMPRE) ---
        # 1. Manda Alert (anche se vuoto)
        if telegram_sections:
            final_msg = telegram_msg + "\n" + "\n\n".join(telegram_sections)
        else:
            final_msg = telegram_msg + "\n⚪ Nessun segnale particolare rilevato"
        self.send_telegram_alert(final_msg)
        
        # 2. AVVIA STUDIO FORMAZIONI (Modulo Sofascore)
        # Estrae nomi squadre per la ricerca
        try:
            parts = real_title.split(" - ")
            t_home = parts[0].strip()
            t_away = parts[1].strip()
        except:
            t_home = "Home"; t_away = "Away"
        
        # SCARICA FORMAZIONI (Usa lo stesso driver navigando via)
        lineup_names, referee = self.fetch_sofascore_lineups(t_home, t_away)
        
        if lineup_names:
            original_lineup_names = list(lineup_names)
            # FILTRA NOMI SQUADRE DALLA LISTA FINALE (Sicurezza extra)
            lineup_names = self._filter_team_names_from_lineup(lineup_names, t_home, t_away)
            # Pulisce rumore residuo (coach/squadre/artefatti) prima del TXT
            lineup_names = self._sanitize_lineup_names_for_output(lineup_names, players_data)

            # Edge-case API: se dopo la sanitizzazione scendiamo sotto 22, prova recovery leggero
            if self._last_lineup_source == "api" and len(lineup_names) < 22 and len(original_lineup_names) >= 22:
                rescued = self._basic_clean_lineup_names(original_lineup_names)
                rescued = self._filter_team_names_from_lineup(rescued, t_home, t_away)
                if len(rescued) >= 22:
                    lineup_names = rescued[:22]
                    print("   ♻️ Recovery API lineup applicato: ripristinati 22 titolari")
            
            if not lineup_names:
                print("   ⚠️ Tutte le formazioni filtrate (solo nomi squadre)")
                return
            
            # CREA FILE STUDIO
            match_folder_path = os.path.join(OUTPUT_FOLDER, match_folder_name)
            os.makedirs(match_folder_path, exist_ok=True)
            studio_filename = f"STUDIO_{self.clean_filename(real_title)}.txt"
            studio_path = os.path.join(match_folder_path, studio_filename)
            
            with open(studio_path, "w", encoding="utf-8") as f:
                f.write(f"STUDIO FINALE: {real_title}\n")
                f.write(f"ARBITRO: {referee}\n")
                f.write("="*60 + "\n\n")
                
                def write_sec(title, players):
                    f.write(f"{title}\n")
                    for p_name in players:
                        match_data = self.find_best_match(p_name, players_data)
                        if match_data:
                            # Raccoglie tutte le quote valide
                            all_odds = []
                            for b in match_data['bookmakers']:
                                try: all_odds.append((b['name'], float(b['odds'])))
                                except: pass
                            if all_odds:
                                all_odds.sort(key=lambda x: x[1], reverse=True)
                                # Bookmaker HIGH (quota massima)
                                max_val = all_odds[0][1]
                                top_books = [x[0] for x in all_odds if x[1] == max_val]
                                high_str = ", ".join(top_books)
                                # Bookmaker LOW (quota minima)
                                min_val = all_odds[-1][1]
                                low_books = [x[0] for x in all_odds if x[1] == min_val]
                                low_str = ", ".join(low_books)
                                line = f"{p_name} - {high_str}   ///   {low_str}"
                            else: line = f"{p_name} - (No quote valide)"
                        else:
                            line = f"{p_name} - ⚠️ Quota non trovata"
                        f.write(line + "\n")
                    f.write("\n")

                if len(lineup_names) >= 22:
                    write_sec(f"--- {t_home.upper()} ---", lineup_names[:11])
                    write_sec(f"--- {t_away.upper()} ---", lineup_names[11:22])
                    if len(lineup_names) > 22: write_sec("--- ALTRI ---", lineup_names[22:])
                else:
                    write_sec("--- LISTA UNICA ---", lineup_names)
                
                # RESOCONTO HIGH PER BOOKMAKER (SOLO TITOLARI)
                f.write("\n" + "="*60 + "\n")
                f.write("📊 RESOCONTO HIGH (SOLO TITOLARI)\n")
                f.write("="*60 + "\n\n")
                
                # Ottieni i nomi dei titolari (primi 22 se disponibili)
                titolari = lineup_names[:22] if len(lineup_names) >= 22 else lineup_names

                def canon_name_for_lineup(text):
                    text = unicodedata.normalize('NFKD', str(text))
                    text = ''.join(c for c in text if not unicodedata.combining(c))
                    text = re.sub(r'[^A-Za-z0-9 ]+', ' ', text).upper()
                    return re.sub(r'\s+', ' ', text).strip()

                titolari_canon = {canon_name_for_lineup(n) for n in titolari}
                
                # Per ogni titolare, trova quali bookmaker hanno la quota massima
                bookmaker_high_titolari = {}
                processed_players = set()  # Per tracciare giocatori già processati ed evitare duplicati
                
                for titolare_name in titolari:
                    if canon_name_for_lineup(titolare_name) not in titolari_canon:
                        continue

                    # Trova i dati del giocatore in players_data
                    match_data = self.find_best_match(titolare_name, players_data)
                    if not match_data:
                        continue
                    
                    # Evita di processare lo stesso giocatore due volte
                    player_key = match_data['player_name'].upper()
                    if player_key in processed_players:
                        continue
                    processed_players.add(player_key)
                    
                    # Trova la quota massima per questo giocatore
                    max_val = -1
                    max_bookmakers = []
                    
                    for bm in match_data['bookmakers']:
                        try:
                            val = float(bm['odds'])
                            if val > max_val:
                                max_val = val
                                max_bookmakers = [bm['name'].lower()]
                            elif val == max_val:
                                max_bookmakers.append(bm['name'].lower())
                        except:
                            continue
                    
                    # Aggiungi questo giocatore a tutti i bookmaker che hanno il massimo
                    for bm_name in max_bookmakers:
                        if bm_name not in bookmaker_high_titolari:
                            bookmaker_high_titolari[bm_name] = []
                        # Evita duplicati anche a livello di singolo bookmaker
                        if match_data['player_name'] not in bookmaker_high_titolari[bm_name]:
                            bookmaker_high_titolari[bm_name].append(match_data['player_name'])
                
                # Ordina i bookmaker alfabeticamente e scrivi
                if bookmaker_high_titolari:
                    for bookmaker in sorted(bookmaker_high_titolari.keys()):
                        players_list = bookmaker_high_titolari[bookmaker]
                        f.write(f"{bookmaker.upper()}:\n")
                        for player in players_list:
                            f.write(f"  • {player}\n")
                        f.write("\n")
                else:
                    f.write("Nessun HIGH trovato tra i titolari.\n")
            
            # INVIA FILE STUDIO A TELEGRAM
            self.send_telegram_file(studio_path)

        # --- SALVATAGGIO CSV & TXT CLASSICO ---
        self.save_to_txt_original(match_folder_name, match_filename, players_data, matching, betfair_list, highest_odds, highest_no_bf, potential_duplicates)
        if -20 <= diff_minutes_total <= 10: 
            self.save_csv(real_title, players_data, league_url)  # <-- PASSA league_url

    def process_match_custom(self, match_info):
        """
        COPIA ESATTA di process_match ma SENZA controllo data.
        Per scrapare manualmente partite future/passate.
        """
        url = match_info['url']
        temp_title = match_info['title']
        start_time_str = match_info['start_time']
        league_url = match_info.get('league_url', '')
        
        # UNICA DIFFERENZA: Salta controllo data
        diff_minutes_total = 0
        time_label = "Manual_Scrape"

        if not self.safe_get(url):
            print(f"   ⚠️ Errore caricamento match (custom): {url}")
            return

        # Fix Titolo (IDENTICO A process_match)
        real_title = temp_title
        try:
            page_title = self.driver.title
            if page_title:
                clean_pt = page_title.strip()
                if "," in clean_pt: clean_pt = clean_pt.split(",")[0]
                if "|" in clean_pt: clean_pt = clean_pt.split("|")[0]
                clean_pt = re.split(r' Betting Odds| Odds', clean_pt, flags=re.IGNORECASE)[0]
                clean_pt = clean_pt.replace(u'\u2014', ' - ').replace(u'\u2013', ' - ').replace('—', ' - ').replace('–', ' - ')
                clean_pt = re.sub(r'\s+vs\s+', ' - ', clean_pt, flags=re.IGNORECASE)
                clean_pt = clean_pt.strip()
                if " - " in clean_pt: real_title = clean_pt
        except: pass

        if " - " not in real_title:
            try:
                h1_elem = WebDriverWait(self.driver, 2).until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
                raw_h1 = h1_elem.text.strip()
                if raw_h1:
                    clean_h1 = raw_h1
                    if "," in clean_h1: clean_h1 = clean_h1.split(",")[0]
                    if "|" in clean_h1: clean_h1 = clean_h1.split("|")[0]
                    clean_h1 = re.split(r' Betting Odds| Odds', clean_h1, flags=re.IGNORECASE)[0]
                    clean_h1 = clean_h1.replace(u'\u2014', ' - ').replace(u'\u2013', ' - ').replace('—', ' - ').replace('–', ' - ')
                    clean_h1 = re.sub(r'\s+vs\s+', ' - ', clean_h1, flags=re.IGNORECASE)
                    real_title = " ".join(clean_h1.split())
            except: pass

        print(f"\n    📌 {real_title}")

        match_filename = f"{real_title} - ({time_label})"
        match_filename = self.clean_filename(match_filename)
        match_folder_name = self.clean_filename(real_title)

        if not self.navigate_to_player_props():
            print("       ❌ Skip: Player Props non trovati.")
            return

        # PARSING HTML (IDENTICO A process_match)
        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        target_div = None
        for div in soup.select(".outright-cont"):
            h2 = div.find("h2")
            if h2:
                txt = h2.get_text(strip=True).lower()
                if 'card' in txt and 'player' in txt and 'red' not in txt:
                    target_div = div
                    break
        
        if not target_div: 
            print("       ❌ Skip: Sezione cartellini non trovata")
            return

        players_data = []
        columns = target_div.select(".outright-cont-col")
        if not columns: columns = [target_div]

        for col in columns:
            for p_soup in col.select(".outright-league"):
                try:
                    name_tag = p_soup.select_one(".outright-league-info span")
                    if not name_tag: continue
                    p_name = name_tag.get_text(strip=True)
                    bookies = []
                    for odd in p_soup.select(".bookie-odd"):
                        try:
                            bn = odd.select_one(".bookie-logo-name").get_text(strip=True)
                            val = odd.select_one(".odd-decimal").get_text(strip=True)
                            bookies.append({'name': bn, 'odds': val})
                        except: pass
                    if bookies: players_data.append({'player_name': p_name, 'bookmakers': bookies})
                except: pass

        if not players_data: 
            print("       ❌ Nessun dato trovato")
            return

        # MERGE & ANALISI (IDENTICO)
        players_data = self.auto_merge_known_duplicates(players_data)
        potential_duplicates = self.detect_potential_duplicates(players_data)
        matching = self.find_matching_odds(players_data)
        betfair_list = self.extract_betfair_odds_data(players_data)
        highest_odds = self.find_highest_odds_by_bookmaker(players_data)
        highest_no_bf = self.find_highest_odds_excluding_betfair(players_data)
        
        # SALVA (sempre, ignora date check)
        self.save_to_txt_original(match_folder_name, match_filename, players_data, matching, betfair_list, highest_odds, highest_no_bf, potential_duplicates)
        
        print(f"       ✅ Salvato: {match_filename}.txt")

    # =========================================================================
    # 🧩 LOGICA MERGE & ANALISI
    # =========================================================================

    def send_telegram_alert(self, message):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
            requests.post(url, data=data)
            print("   📲 Notifica Telegram inviata!")
        except Exception as e: print(f"   ⚠️ Errore Telegram: {e}")

    def send_telegram_file(self, filepath):
        """Invia file .txt su Telegram"""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(filepath, 'rb') as f:
                files = {'document': f}
                data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': "📊 STUDIO FORMAZIONI COMPLETO"}
                requests.post(url, data=data, files=files)
            print("   📲 File Studio inviato su Telegram!")
        except Exception as e: print(f"   ⚠️ Errore invio file Telegram: {e}")

    def auto_merge_known_duplicates(self, players_data):
        merged_players = list(players_data)
        while True:
            merge_count = 0
            for short_name, long_name in KNOWN_DUPLICATES:
                player_short = None; player_long = None; idx_short = None; idx_long = None
                for i, player in enumerate(merged_players):
                    name_upper = player['player_name'].upper()
                    if name_upper == short_name.upper(): player_short = player; idx_short = i
                    elif name_upper == long_name.upper(): player_long = player; idx_long = i
                
                if player_short and player_long:
                    all_bookmakers = []
                    seen = set()
                    for p in [player_short, player_long]:
                        for bm in p['bookmakers']:
                            key = f"{bm['name'].lower()}_{bm['odds']}"
                            if key not in seen: seen.add(key); all_bookmakers.append(bm)
                    
                    unified = {'player_name': short_name, 'bookmakers': sorted(all_bookmakers, key=lambda x: x['name'].lower())}
                    if idx_short > idx_long: merged_players.pop(idx_short); merged_players.pop(idx_long)
                    else: merged_players.pop(idx_long); merged_players.pop(idx_short)
                    merged_players.append(unified)
                    merge_count += 1
                    break 
            if merge_count == 0: break
        return merged_players

    def smart_auto_merge(self, players_data):
        """DEPRECATO: Non unisce più automaticamente, usa detect_potential_duplicates invece"""
        # Ritorna i dati invariati - il merge automatico è disabilitato
        return players_data
    
    def detect_potential_duplicates(self, players_data):
        """
        Rileva coppie di giocatori che potrebbero essere duplicati (alta similarità)
        ma NON sono ancora nella lista KNOWN_DUPLICATES.
        Ritorna lista di tuple: (nome1, nome2, similarità%, tipo_match)
        """
        potential_duplicates = []
        known_pairs = set()
        
        # Crea set delle coppie già note (normalizzate)
        for short_name, long_name in KNOWN_DUPLICATES:
            pair = tuple(sorted([short_name.upper(), long_name.upper()]))
            known_pairs.add(pair)
        
        # Confronta tutti i giocatori tra loro
        for i in range(len(players_data)):
            name1 = players_data[i]['player_name']
            bookies1 = {b['name'] for b in players_data[i]['bookmakers']}
            
            for j in range(i + 1, len(players_data)):
                name2 = players_data[j]['player_name']
                bookies2 = {b['name'] for b in players_data[j]['bookmakers']}
                
                # Skip se condividono bookmaker (non possono essere duplicati)
                if not bookies1.isdisjoint(bookies2):
                    continue
                
                # Skip se già nella lista noti
                pair = tuple(sorted([name1.upper(), name2.upper()]))
                if pair in known_pairs:
                    continue
                
                # Calcola similarità
                name1_lower = name1.lower()
                name2_lower = name2.lower()
                similarity = difflib.SequenceMatcher(None, name1_lower, name2_lower).ratio()
                
                tokens1 = sorted(name1_lower.split())
                tokens2 = sorted(name2_lower.split())
                token_similarity = difflib.SequenceMatcher(None, " ".join(tokens1), " ".join(tokens2)).ratio()
                
                # NUOVO: Check se un nome è substring dell'altro
                is_substring = name1_lower in name2_lower or name2_lower in name1_lower
                
                # NUOVO: Check se condividono almeno 1 token (cognome)
                tokens1_set = set(tokens1)
                tokens2_set = set(tokens2)
                shared_tokens = tokens1_set.intersection(tokens2_set)
                has_shared_token = len(shared_tokens) > 0
                
                # Check iniziale + cognome
                is_initial_match = False
                if len(tokens1) > 1 and len(tokens2) > 1:
                    # Stesso cognome + stessa iniziale nome
                    if tokens1[-1] == tokens2[-1]:
                        if tokens1[0][0] == tokens2[0][0]:
                            if len(tokens1[0]) <= 2 or len(tokens2[0]) <= 2:
                                is_initial_match = True
                
                # Criteri di segnalazione (soglie abbassate per rilevare più casi)
                match_type = None
                confidence = 0
                
                # PRIORITÀ ALTA: Substring match (es. "RIKI" in "RIKI RODRIGUEZ")
                if is_substring and len(name1) >= 3 and len(name2) >= 3:
                    match_type = "Nome contenuto nell'altro"
                    confidence = 95
                # Token condiviso + lunghezza simile
                elif has_shared_token and abs(len(name1) - len(name2)) <= 10:
                    # Calcola % di token condivisi
                    total_tokens = len(tokens1_set.union(tokens2_set))
                    shared_ratio = len(shared_tokens) / total_tokens if total_tokens > 0 else 0
                    if shared_ratio >= 0.4:  # Almeno 40% token condivisi
                        match_type = f"Cognome/Token condiviso ({len(shared_tokens)} token)"
                        confidence = int(shared_ratio * 100)
                # Similarità alta
                elif similarity > 0.85:  # Era 0.92
                    match_type = "Alta similarità generale"
                    confidence = int(similarity * 100)
                elif token_similarity > 0.88:  # Era 0.95
                    match_type = "Token quasi identici"
                    confidence = int(token_similarity * 100)
                elif is_initial_match and similarity > 0.70:  # Era 0.82
                    match_type = "Iniziale + Cognome match"
                    confidence = int(similarity * 100)
                
                if match_type:
                    # Ordina: nome più corto prima (convenzione duplicates_list.py)
                    if len(name1) <= len(name2):
                        potential_duplicates.append((name1, name2, confidence, match_type))
                    else:
                        potential_duplicates.append((name2, name1, confidence, match_type))
        
        # Ordina per confidenza (più alta prima)
        potential_duplicates.sort(key=lambda x: x[2], reverse=True)
        
        # Debug logging
        if potential_duplicates:
            print(f"   🔍 Rilevati {len(potential_duplicates)} possibili duplicati")
        
        return potential_duplicates

    def find_matching_odds(self, players_data):
        matches = {'bet365_betfair': [], 'betfair_unibet': [], 'paddypower_unibet': [], 'betway_unibet': [], 'paddypower_fanduel': [], 'iddaa_stake': [], 'stake_sazka': []}
        for p in players_data:
            name = p['player_name']
            all_odds_values = []
            for bm in p['bookmakers']:
                try: all_odds_values.append(float(bm['odds']))
                except: pass
            max_odd_for_player = max(all_odds_values) if all_odds_values else 0
            odds = {bm['name'].lower(): bm['odds'] for bm in p['bookmakers']}
            
            if 'bet365' in odds and 'betfair' in odds and odds['bet365'] == odds['betfair']:
                matches['bet365_betfair'].append({'player_name': name, 'odds': odds['bet365'], 'bet365_odds': odds['bet365'], 'betfair_odds': odds['betfair']})
            if 'betfair' in odds and 'unibet' in odds and odds['betfair'] == odds['unibet']:
                val = float(odds['betfair']); is_highest = (val >= max_odd_for_player)
                matches['betfair_unibet'].append({'player_name': name, 'odds': odds['betfair'], 'betfair_odds': odds['betfair'], 'unibet_odds': odds['unibet'], 'is_highest': is_highest})
            if 'paddypower' in odds and 'unibet' in odds and odds['paddypower'] == odds['unibet']:
                val = float(odds['paddypower']); is_highest = (val >= max_odd_for_player)
                matches['paddypower_unibet'].append({'player_name': name, 'odds': odds['paddypower'], 'paddypower_odds': odds['paddypower'], 'unibet_odds': odds['unibet'], 'is_highest': is_highest})
            if 'betway' in odds and 'unibet' in odds and odds['betway'] == odds['unibet']:
                matches['betway_unibet'].append({'player_name': name, 'odds': odds['betway'], 'betway_odds': odds['betway'], 'unibet_odds': odds['unibet']})
            if 'paddypower' in odds and 'fanduel' in odds and odds['paddypower'] == odds['fanduel']:
                matches['paddypower_fanduel'].append({'player_name': name, 'odds': odds['paddypower'], 'paddypower_odds': odds['paddypower'], 'fanduel_odds': odds['fanduel']})
            if 'iddaa' in odds and 'stake' in odds:
                try:
                    diff = abs(float(odds['iddaa']) - float(odds['stake']))
                    if diff <= 0.05: matches['iddaa_stake'].append({'player_name': name, 'odds': f"{odds['iddaa']}/{odds['stake']}", 'iddaa_odds': odds['iddaa'], 'stake_odds': odds['stake'], 'diff': diff})
                except: pass
            if 'stake' in odds and 'sazka' in odds and odds['stake'] == odds['sazka']:
                matches['stake_sazka'].append({'player_name': name, 'odds': odds['stake'], 'stake_odds': odds['stake'], 'sazka_odds': odds['sazka']})
        return matches

    def extract_betfair_odds_data(self, players_data):
        bf_list = []
        for p in players_data:
            for bm in p['bookmakers']:
                if bm['name'].lower() == 'betfair': bf_list.append({'player_name': p['player_name'], 'betfair_odds': bm['odds']})
        try: bf_list.sort(key=lambda x: float(x['betfair_odds']))
        except: pass
        return bf_list

    def find_highest_odds_by_bookmaker(self, players_data):
        bookmaker_highest = {}
        for p in players_data:
            max_val = -1; max_str = ""; max_bms = []
            for bm in p['bookmakers']:
                try:
                    val = float(bm['odds'])
                    if val > max_val: max_val = val; max_str = bm['odds']; max_bms = [bm['name'].lower()]
                    elif val == max_val: max_bms.append(bm['name'].lower())
                except: continue
            if max_val > 0:
                for bn in max_bms:
                    if bn not in bookmaker_highest: bookmaker_highest[bn] = []
                    bookmaker_highest[bn].append((p['player_name'], max_str))
        res = {}
        for bk, plist in bookmaker_highest.items(): res[bk] = {'players': plist}
        return res

    def find_highest_odds_excluding_betfair(self, players_data):
        bookmaker_highest = {}
        for p in players_data:
            max_val = -1; max_str = ""; max_bms = []
            for bm in p['bookmakers']:
                if bm['name'].lower() == 'betfair': continue
                try:
                    val = float(bm['odds'])
                    if val > max_val: max_val = val; max_str = bm['odds']; max_bms = [bm['name'].lower()]
                    elif val == max_val: max_bms.append(bm['name'].lower())
                except: continue
            if max_val > 0:
                for bn in max_bms:
                    if bn not in bookmaker_highest: bookmaker_highest[bn] = []
                    bookmaker_highest[bn].append((p['player_name'], max_str))
        res = {}
        for bk, plist in bookmaker_highest.items(): res[bk] = {'players': plist}
        return res

    def save_to_txt_original(self, folder_name, file_title, players_data, matching_odds_results, betfair_odds, highest_odds, highest_odds_no_betfair, potential_duplicates=None):
        # ⚠️ QUI È L'UNICA MODIFICA: ORDINAMENTO ALFABETICO
        players_data.sort(key=lambda x: x['player_name'])
        
        match_folder_path = os.path.join(OUTPUT_FOLDER, folder_name)
        os.makedirs(match_folder_path, exist_ok=True)
        filename = f"{file_title}.txt"
        filepath = os.path.join(match_folder_path, filename)
        
        bet365_betfair_matches = matching_odds_results.get('bet365_betfair', [])
        betfair_unibet_matches = matching_odds_results.get('betfair_unibet', [])
        paddypower_unibet_matches = matching_odds_results.get('paddypower_unibet', [])
        betway_unibet_matches = matching_odds_results.get('betway_unibet', [])
        stake_sazka_matches = matching_odds_results.get('stake_sazka', [])

        content = []
        content.append("="*60); content.append(f"DATI SCRAPING GIOCATORI"); content.append(f"Nome file: {file_title}"); content.append(f"Generato il: {datetime.now().strftime('%d/%m/%Y alle %H:%M:%S')}"); content.append("="*60); content.append("")
        
        if not players_data: content.append("Nessun dato trovato!")
        else:
            content.append("SEZIONE 1: TUTTI I GIOCATORI E BOOKMAKER"); content.append("="*50); content.append("")
            for player in players_data:
                content.append(f"GIOCATORE: \"{player['player_name']}\"")
                if not player['bookmakers']: content.append("   Nessuna quota disponibile")
                else:
                    for bookmaker in player['bookmakers']: content.append(f"   \"{bookmaker['name']}\" - \"{bookmaker['odds']}\"")
                content.append("-" * 40); content.append("")
            content.append(f"Totale giocatori trovati: {len(players_data)}"); content.append("")
            
            if highest_odds:
                content.append("="*60); content.append("SEZIONE 2: QUOTE PIÙ ALTE PER BOOKMAKER"); content.append("="*60); content.append("")
                for bookie_name in sorted(highest_odds.keys()):
                    data = highest_odds[bookie_name]; players_list = data['players']
                    content.append(f"🔸 HIGH {bookie_name.upper()} ({len(players_list)} giocatori):")
                    if players_list:
                        for i, (player_name, odds_string) in enumerate(players_list, 1): content.append(f"   {i}. {player_name} (Quota: {odds_string})")
                    else: content.append("   Nessun giocatore trovato")
                    content.append("")
                content.append("="*60); content.append(f"BOOKMAKER CON QUOTE MASSIME: {len(highest_odds)}")

            if (bet365_betfair_matches or betfair_unibet_matches or paddypower_unibet_matches or betway_unibet_matches or stake_sazka_matches):
                content.append(""); content.append("="*60); content.append("SEZIONE 3: QUOTE IDENTICHE / SIMILI"); content.append("="*60); content.append(""); content.append("📊 NUOVO CHECK:"); content.append("")
                content.append(f"🔷 BET365-BETFAIR ({len(bet365_betfair_matches)} giocatori):"); content.append("")
                if bet365_betfair_matches:
                    for match in bet365_betfair_matches: content.append(f"GIOCATORE: \"{match['player_name']}\""); content.append(f"   BET365: {match['bet365_odds']}"); content.append(f"   BETFAIR: {match['betfair_odds']}"); content.append(f"   QUOTA IDENTICA: {match['odds']}"); content.append("-" * 30)
                else: content.append("Nessun giocatore con quote identiche BET365-BETFAIR")
                content.append(""); content.append("="*60); content.append(""); content.append("📊 NUOVE COMBINAZIONI:"); content.append("")
                content.append(f"🔵 BETWAY-UNIBET ({len(betway_unibet_matches)} giocatori):"); content.append("")
                if betway_unibet_matches:
                    for match in betway_unibet_matches: content.append(f"GIOCATORE: \"{match['player_name']}\""); content.append(f"   BETWAY: {match['betway_odds']}"); content.append(f"   UNIBET: {match['unibet_odds']}"); content.append(f"   QUOTA IDENTICA: {match['odds']}"); content.append("-" * 30)
                else: content.append("Nessun giocatore con quote identiche BETWAY-UNIBET")
                content.append(""); content.append("="*40); content.append("")
                content.append(f"🟣 STAKE-SAZKA ({len(stake_sazka_matches)} giocatori):"); content.append("")
                if stake_sazka_matches:
                    for match in stake_sazka_matches: content.append(f"GIOCATORE: \"{match['player_name']}\""); content.append(f"   STAKE: {match['stake_odds']}"); content.append(f"   SAZKA: {match['sazka_odds']}"); content.append(f"   QUOTA IDENTICA: {match['odds']}"); content.append("-" * 30)
                else: content.append("Nessun giocatore con quote identiche STAKE-SAZKA")
                content.append(""); content.append("="*60); content.append(""); content.append("📊 STUDIO STORICO:"); content.append("")
                content.append(f"🔸 BETFAIR-UNIBET ({len(betfair_unibet_matches)} giocatori):"); content.append("")
                if betfair_unibet_matches:
                    for match in betfair_unibet_matches: flag = " [💎 HIGH]" if match.get('is_highest') else ""; content.append(f"GIOCATORE: \"{match['player_name']}\"{flag}"); content.append(f"   BETFAIR: {match['betfair_odds']}"); content.append(f"   UNIBET: {match['unibet_odds']}"); content.append(f"   QUOTA IDENTICA: {match['odds']}{flag}"); content.append("-" * 30)
                else: content.append("Nessun giocatore con quote identiche BETFAIR-UNIBET")
                content.append(""); content.append("="*40); content.append("")
                content.append(f"🔸 PADDYPOWER-UNIBET ({len(paddypower_unibet_matches)} giocatori):"); content.append("")
                if paddypower_unibet_matches:
                    for match in paddypower_unibet_matches: flag = " [💎 HIGH]" if match.get('is_highest') else ""; content.append(f"GIOCATORE: \"{match['player_name']}\"{flag}"); content.append(f"   PADDYPOWER: {match['paddypower_odds']}"); content.append(f"   UNIBET: {match['unibet_odds']}"); content.append(f"   QUOTA IDENTICA: {match['odds']}{flag}"); content.append("-" * 30)
                else: content.append("Nessun giocatore con quote identiche PADDYPOWER-UNIBET")
                content.append(""); content.append("="*60); content.append(f"TOTALE GIOCATORI BET365-BETFAIR: {len(bet365_betfair_matches)}"); content.append(f"TOTALE GIOCATORI BETWAY-UNIBET: {len(betway_unibet_matches)}"); content.append(f"TOTALE GIOCATORI STAKE-SAZKA: {len(stake_sazka_matches)}"); content.append(f"TOTALE GIOCATORI BETFAIR-UNIBET: {len(betfair_unibet_matches)}"); content.append(f"TOTALE GIOCATORI PADDYPOWER-UNIBET: {len(paddypower_unibet_matches)}")
        
        # --- SEZIONE DUPLICATI POTENZIALI ---
        if potential_duplicates and len(potential_duplicates) > 0:
            content.append(""); content.append("="*60); content.append("⚠️  POSSIBILI DUPLICATI DA VERIFICARE"); content.append("="*60)
            content.append("")
            content.append("❗ Questi giocatori potrebbero essere la stessa persona.")
            content.append("❗ Verifica manualmente e aggiungi a duplicates_list.py se corretti.")
            content.append("")
            content.append(f"Trovati {len(potential_duplicates)} possibili duplicati:")
            content.append("-" * 60)
            content.append("")
            
            for i, (name1, name2, confidence, match_type) in enumerate(potential_duplicates, 1):
                content.append(f"[{i}] CONFIDENZA: {confidence}% ({match_type})")
                content.append(f'    ("{name1.upper()}", "{name2.upper()}"),')
                content.append("")
            
            content.append("-" * 60)
            content.append("📋 COPIA E INCOLLA IN duplicates_list.py:")
            content.append("")
            for name1, name2, confidence, match_type in potential_duplicates:
                content.append(f'    ("{name1.upper()}", "{name2.upper()}"),  # {confidence}% - {match_type}')
            content.append("")
            content.append("="*60)

        with open(filepath, 'w', encoding='utf-8') as f: f.write('\n'.join(content))
        print(f"   📄 TXT Salvato: {filename}")

    def get_csv_category(self, league_url):
        """Determina la categoria CSV in base all'URL della lega"""
        for category, urls in LEAGUE_CATEGORIES.items():
            if league_url in urls:
                return category
        return "altri"  # Fallback per leghe non categorizzate
    
    def save_csv(self, match_name, players_data, league_url):
        try:
            # 1. Determina quale CSV usare
            category = self.get_csv_category(league_url)
            csv_filename = CSV_FILES[category]
            csv_full_path = os.path.join(OUTPUT_FOLDER, csv_filename)
            
            # 2. Controlla duplicati (se il CSV esiste già)
            if os.path.exists(csv_full_path):
                existing_df = pd.read_csv(csv_full_path)
                # Verifica se questa partita è già stata salvata oggi
                today_str = datetime.now().strftime("%Y-%m-%d")
                existing_today = existing_df[
                    (existing_df['Match'] == match_name) & 
                    (existing_df['Timestamp'].str.startswith(today_str))
                ]
                
                if not existing_today.empty:
                    print(f"   ⏭️ SKIP CSV: Partita '{match_name}' già salvata oggi in {csv_filename}")
                    return
            
            # 3. Prepara righe da salvare
            rows = []
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for p in players_data:
                row = {'Timestamp': ts, 'Match': match_name, 'Player_Name': p['player_name'], 'Cartellino': 0}
                for bm in p['bookmakers']:
                    b_clean = bm['name'].lower().strip()
                    try: row[b_clean] = float(bm['odds'])
                    except: row[b_clean] = np.nan
                rows.append(row)
            
            # 4. Crea DataFrame e salva
            df = pd.DataFrame(rows)
            for c in BOOKMAKERS_FOR_CSV:
                if c not in df.columns: df[c] = np.nan
            cols = ['Timestamp', 'Match', 'Player_Name'] + sorted(BOOKMAKERS_FOR_CSV) + ['Cartellino']
            df = df[[c for c in cols if c in df.columns]]
            
            header = not os.path.exists(csv_full_path)
            df.to_csv(csv_full_path, mode='a', header=header, index=False)
            
            print(f"   ✅ Dati salvati in: {csv_filename} (Categoria: {category.upper()})")
            
        except Exception as e: 
            print(f"   ❌ Errore CSV: {e}")
            traceback.print_exc()

    def run_cycle(self):
        self.start_driver()
        if not self.driver:
            print("❌ Driver non avviato: ciclo annullato.")
            return
        print("\n" + "="*50); print(f"🚀 MONITORAGGIO V11.0 (PARSING STRUTTURATO): {datetime.now().strftime('%H:%M')}"); print("="*50)
        for league_url in LEAGUES_TO_MONITOR:
            if league_url in self.empty_leagues_today: print(f"⏭️ SKIP: {league_url}"); continue
            # Delay casuale tra una lega e l'altra (anti rate-limit)
            delay = random.uniform(2.0, 5.0)
            time.sleep(delay)
            try:
                today_matches = self.scan_league_for_today_matches(league_url)
            except Exception as e:
                err_str = str(e)
                if any(k in err_str for k in ("ERR_TUNNEL", "ERR_PROXY", "ERR_CONNECTION", "net::")):
                    print(f"⚠️ Proxy fallito ({err_str[:60]}...) — riavvio senza proxy.")
                    self.close_driver()
                    self.use_proxy = False
                    self.start_driver()
                    try:
                        today_matches = self.scan_league_for_today_matches(league_url)
                    except Exception as e2:
                        print(f"❌ Errore anche senza proxy: {e2}"); continue
                else:
                    print(f"❌ Errore scan campionato: {e}"); continue
            for match_info in today_matches:
                try: self.process_match(match_info)
                except Exception as e: print(f"⚠️ Errore match: {e}"); self.close_driver(); self.start_driver()
        print("\n💤 Ciclo completato.")

if __name__ == "__main__":
    bot = SmartBot()
    try:
        bot.run_cycle()
    except Exception as e:
        print(f"❌ Errore loop: {e}")
    finally:
        bot.close_driver()
