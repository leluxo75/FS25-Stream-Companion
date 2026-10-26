"""FS25 Stream & Companion (ex FS25 Stream Dashboard) - serveur local.

- Sert la page web (index.html)
- Relaie data.json ecrit par le mod
- Retrouve automatiquement l'image de boutique du tracteur et de chaque outil
  (jeu de base, mods en .zip ou dossier, DLC), la convertit en PNG et la met en cache.
- Si le jeu ne fournit aucune image lisible (ex. DLC en archive protegee), cherche une image
  sur le web (config.json) et la met en cache.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import hashlib, html, io, ipaddress, json, os, re, socket, sys, threading, time, urllib.request, zipfile
from urllib.parse import urlencode

from collections import Counter, deque

try:
    from PIL import Image, ImageFilter
except ImportError:  # le .bat installe Pillow, mais on reste lisible si ce n'est pas fait
    Image = None
    ImageFilter = None

ROOT = Path(__file__).resolve().parent
# Application : la page (index.html) est dans l'appli (WEB_ROOT), les fichiers de l'utilisateur
# (disposition, reglages, images, cache) dans un dossier a lui (DATA_ROOT). Par defaut : le dossier du script.
WEB_ROOT = Path(os.environ.get("FS25_DASH_WEB_DIR") or ROOT)
DATA_ROOT = Path(os.environ.get("FS25_DASH_DATA_DIR") or ROOT)
DATA_ROOT.mkdir(parents=True, exist_ok=True)
CACHE = DATA_ROOT / "cache"
LAYOUT = DATA_ROOT / "layout.json"      # disposition des blocs (mode edition : /?edit)
DISPOS = DATA_ROOT / "dispositions.json"  # 1.4.4 : 3 dispositions enregistrees + copie avant le dernier changement
OVERRIDES = DATA_ROOT / "images_perso"   # optionnel : <nom_du_xml>.png pour forcer une image
CACHE.mkdir(exist_ok=True)
OVERRIDES.mkdir(exist_ok=True)
PORT = 8765
SERVER_VERSION = os.environ.get("FS25_DASH_APP_VERSION") or "1.4.4"
MAX_WIDTH = 900

home = Path(os.environ.get("USERPROFILE", str(Path.home())))


def documents_dir():
    """Vrai dossier Documents de Windows (il peut etre deplace, ex. dans OneDrive)."""
    if sys.platform == "win32":
        try:
            import ctypes, uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]
            u = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")          # FOLDERID_Documents
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except Exception:
            pass
    return home / "Documents"


GAME_USER_DIRS = []
for _d in ([Path(os.environ["FS25_DASH_GAME_DIR"])] if os.environ.get("FS25_DASH_GAME_DIR") else []) + [
           documents_dir() / "My Games" / "FarmingSimulator2025",
           home / "Documents" / "My Games" / "FarmingSimulator2025",
           home / "OneDrive" / "Documents" / "My Games" / "FarmingSimulator2025"]:
    if _d not in GAME_USER_DIRS:
        GAME_USER_DIRS.append(_d)
DATA_CANDIDATES = [d / "modSettings" / "FS25_StreamDashboard" / "data.json" for d in GAME_USER_DIRS]
DATA = next((p for p in DATA_CANDIDATES if p.exists()), DATA_CANDIDATES[0])

ARCHIVE_EXT = (".zip", ".dlc", ".gar")
_reported = set()
_quiet = threading.local()          # actif pendant la lecture des icones de produits (voir ci-dessous)


def report(key, message):
    """N'affiche chaque probleme d'image qu'une seule fois."""
    if getattr(_quiet, "on", False):
        return                       # icones de produits du jeu de base : dans dataS.gar, illisibles -> emoji de secours cote page
    if key not in _reported:
        _reported.add(key)
        print(message)


# Au-dela de ce delai sans mise a jour du fichier, on considere que le jeu n'est plus en partie
# (jeu ferme ou plante). Le mod reecrit le fichier chaque seconde ; le delai est large pour ne pas se
# declencher pendant un menu en jeu. En fin de partie normale, le mod ecrit lui-meme "session": false.
STALE_SECONDS = float(os.environ.get("FS25_DASH_STALE_SECONDS", "90"))


GAME_PROCESSES = ("farmingsimulator2025game.exe", "farmingsimulator2025.exe")
_proc_cache = {"t": 0.0, "v": None}


def game_process_running():
    """Le jeu tourne-t-il ? (liste des processus Windows, sans fenetre console). None = impossible a savoir.
    Resultat garde 3 s en memoire (la fenetre d'accueil demande toutes les 2 s)."""
    now = time.time()
    if now - _proc_cache["t"] < 3:
        return _proc_cache["v"]
    v = None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
            k32 = ctypes.windll.kernel32
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)          # TH32CS_SNAPPROCESS
            if snap and snap != wintypes.HANDLE(-1).value:
                try:
                    e = PROCESSENTRY32W()
                    e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                    v = False
                    ok = k32.Process32FirstW(snap, ctypes.byref(e))
                    while ok:
                        if e.szExeFile.lower() in GAME_PROCESSES:
                            v = True
                            break
                        ok = k32.Process32NextW(snap, ctypes.byref(e))
                finally:
                    k32.CloseHandle(snap)
        except Exception as exc:
            print("[Jeu] liste des processus impossible :", exc)
            v = None
    _proc_cache.update(t=now, v=v)
    return v


LAST_READ_PATH = None


def read_data():
    """Lit le fichier de donnees le plus recent (si le jeu a plusieurs dossiers de profil, ex. avec OneDrive)."""
    global LAST_READ_PATH
    found = []
    for p in DATA_CANDIDATES:
        try:
            found.append((p.stat().st_mtime, p))
        except OSError:
            pass
    if not found:
        LAST_READ_PATH = None
        return None
    p = max(found, key=lambda t: t[0])[1]
    LAST_READ_PATH = p
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def data_file_age():
    try:
        return max(0.0, time.time() - LAST_READ_PATH.stat().st_mtime) if LAST_READ_PATH else None
    except OSError:
        return None


def search_roots(data):
    """Dossiers de base ou chercher un chemin relatif."""
    roots = []
    for key in ("gameDir", "userDir"):
        value = (data or {}).get(key)
        if value:
            roots.append(Path(value))
    return roots


def expand_variables(path, data):
    """Variables de chemin FS : $data, $pdlcdir$, $userProfile$..."""
    game = (data or {}).get("gameDir") or ""
    user = (data or {}).get("userDir") or ""
    out = []
    if path.startswith("$data/"):
        out.append(Path(game) / "data" / path[len("$data/"):])
    for prefix in ("$pdlcdir$/", "$pdlcdir/"):
        if path.startswith(prefix):
            rest = path[len(prefix):]
            out.append(Path(user) / "pdlc" / rest)
            out.append(Path(game) / "pdlc" / rest)
    return out


def find_in_archive(parts_path):
    """Si un morceau du chemin est une archive (mod .zip, DLC...), renvoie (archive, chemin_interne)."""
    parts = parts_path.as_posix().split("/")
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i])
        for ext in ARCHIVE_EXT:
            candidate = Path(prefix + ext)
            if candidate.is_file():
                return candidate, "/".join(parts[i:])
    return None


def extension_variants(path):
    """Le jeu annonce souvent .png alors que le fichier reel est .dds (et inversement)."""
    path = path.replace("\\", "/")
    base, dot, ext = path.rpartition(".")
    if not dot:
        return [path]
    out = [path]
    for alt in ("dds", "png"):
        if alt != ext.lower():
            out.append(f"{base}.{alt}")
    return out


def load_bytes(raw_path, data):
    """Renvoie les octets de l'image, ou None (essaie .png puis .dds)."""
    for variant in extension_variants(raw_path):
        blob = load_bytes_exact(variant, data)
        if blob is not None:
            return blob
    return None


def load_bytes_exact(raw_path, data):
    p = raw_path.replace("\\", "/")
    direct = expand_variables(p, data)
    pp = Path(p)
    if pp.is_absolute():
        direct.append(pp)
    for root in search_roots(data):
        direct.append(root / p)
        direct.append(root / "data" / p)

    for candidate in direct:
        if candidate.is_file():
            return candidate.read_bytes()

    # Les mods zippes apparaissent dans le jeu comme un dossier sans ".zip".
    for candidate in direct:
        found = find_in_archive(candidate)
        if not found:
            continue
        archive, inner = found
        if archive.suffix.lower() == ".gar":
            report("gar:" + str(archive),
                   f"[Images] {archive.name} est une archive GIANTS (.gar) : lecture impossible, "
                   "utilise images_perso/ pour cet element.")
            continue
        try:
            with zipfile.ZipFile(archive) as z:
                names = {n.replace("\\", "/").lower(): n for n in z.namelist()}
                real = names.get(inner.lower())
                if real:
                    return z.read(real)
        except zipfile.BadZipFile:
            report("badzip:" + str(archive),
                   f"[Images] {archive.name} est une archive GIANTS protegee : Python ne peut pas la lire. "
                   f"Pour cet element, depose un PNG dans images_perso\\ (nom attendu : "
                   f"{Path(raw_path).stem}.png).")
    return None


def trim_transparent_margins(img):
    """Les images de boutique du jeu ont souvent beaucoup de vide transparent autour du vehicule : on le rogne
    pour que le vehicule remplisse sa case (surtout dans les cartes plates)."""
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] > 200:
        return img                                              # image opaque : rien a rogner
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    if not box:
        return img
    w, h = img.size
    pad = max(2, int(0.03 * max(box[2] - box[0], box[3] - box[1])))
    box = (max(box[0] - pad, 0), max(box[1] - pad, 0), min(box[2] + pad, w), min(box[3] + pad, h))
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.9 * w * h:
        return img                                              # marges negligeables
    return img.crop(box)


def to_png(blob, source_name):
    if Image is None:
        report("nopil", "[Images] Pillow n'est pas installe : lance  py -m pip install pillow")
        return None
    try:
        img = Image.open(io.BytesIO(blob))
        img.load()
    except Exception as exc:
        report("decode:" + source_name, f"[Images] Impossible de decoder {source_name} : {exc}")
        return None
    img = trim_transparent_margins(img.convert("RGBA"))
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def override_path(info):
    """Fichier de images_perso/ qui s'applique a cet element (ou None)."""
    xml = (info or {}).get("xml") or ""
    stem = Path(xml).stem if xml else ""
    image_stem = Path(info.get("image")).stem if (info or {}).get("image") else ""
    if image_stem.lower() in PERSO_GENERIC:
        image_stem = ""
    for candidate in (image_stem, stem, (info or {}).get("name", "")):
        if candidate:
            f = OVERRIDES / f"{candidate}.png"
            if f.is_file():
                return f
    return None


def override_for(info):
    f = override_path(info)
    return f.read_bytes() if f else None


def image_version(info):
    """Change des que le fichier d'image utilise change : sert a forcer le rechargement dans la page."""
    if not info:
        return 0
    try:
        f = override_path(info)
        if f:
            return int(f.stat().st_mtime)
        if info.get("image"):
            c = local_cache_file(info["image"])
            if c.is_file():
                return int(c.stat().st_mtime)
        w = WEB_DIR / (web_key(info) + ".png")
        if w.is_file():
            return int(w.stat().st_mtime)
    except OSError:
        pass
    return 0


def local_cache_file(path):
    return CACHE / (hashlib.sha1((path + "|trim1").encode("utf-8")).hexdigest() + ".png")


def local_png(info, data):
    """Image trouvee dans les fichiers du jeu / des mods (ou None)."""
    path = info.get("image")
    if not path:
        report("noimg:" + str(info.get("xml")),
               f"[Images] Le jeu n'a fourni aucune image pour {info.get('name')}.")
        return None
    cached = local_cache_file(path)
    if cached.is_file():
        return cached.read_bytes()
    blob = load_bytes(path, data)
    if blob is None:
        report("miss:" + path, f"[Images] Fichier introuvable : {path}")
        return None
    png = to_png(blob, Path(path).name)
    if png:
        CACHE.mkdir(exist_ok=True)
        cached.write_bytes(png)
        print(f"[Images] OK : {info.get('name')} <- {path}")
    return png


def image_png(info, data, allow_web=False):
    """PNG pour un vehicule/outil decrit par le mod ({name, image, xml, ...}).
    Ordre : images_perso/ > fichiers du jeu > recherche web (si autorisee et activee)."""
    if not info:
        return None
    forced = override_for(info)
    if forced:
        return forced
    png = local_png(info, data)
    if png is None and allow_web:
        try:
            png = web_png(info)
        except Exception as exc:          # la recherche web ne doit jamais faire tomber le dashboard
            print(f"[Web] Erreur inattendue : {exc}")
            png = None
    return png


# ---------------------------------------------------------------- mini-carte
MAP_MAX = 2048
_map_info_cache = {}


def map_xml_info(minfo, data):
    """Lit le XML de la map (dans le mod .zip ou le jeu) : (imageFilename, largeur) ou (None, None)."""
    xml_path = (minfo or {}).get("mapXml")
    if not xml_path:
        return None, None
    key = xml_path
    if key in _map_info_cache:
        return _map_info_cache[key]
    res = (None, None)
    try:
        blob = load_bytes_exact(xml_path.replace("\\", "/"), data)
        if blob:
            head = blob[:20000].decode("utf-8", "replace")
            m = re.search(r"<map\b[^>]*>", head, re.S)
            if m:
                tag = m.group(0)
                img = re.search(r'imageFilename\s*=\s*"([^"]+)"', tag)
                w = re.search(r'\bwidth\s*=\s*"([\d.]+)"', tag)
                res = (img.group(1) if img else None, float(w.group(1)) if w else None)
    except Exception as exc:
        report("mapxml:" + key, f"[Carte] XML de la map illisible ({exc})")
    _map_info_cache[key] = res
    return res


def map_image_candidates(minfo, data):
    """Chemins possibles de l'image de la carte (vue du dessus du jeu)."""
    out = []
    if (minfo or {}).get("image"):
        out.append(minfo["image"])
    img, _w = map_xml_info(minfo, data)
    if img:
        base = ((minfo or {}).get("baseDir") or "").replace("\\", "/").rstrip("/")
        if img.startswith("$"):
            out.append(img)
        else:
            if base:
                out.append(base + "/" + img)
            xml = ((minfo or {}).get("mapXml") or "").replace("\\", "/")
            if "/" in xml:
                out.append(xml.rsplit("/", 1)[0] + "/" + img)
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c); res.append(c)
    return res


def map_override(minfo):
    """images_perso/carte_<map>.png (ou carte.png) : image de carte deposee a la main."""
    name = re.sub(r'[<>:"/\\|?*\s]+', "_", str((minfo or {}).get("title") or "").strip()) or "map"
    for f in (OVERRIDES / f"carte_{name}.png", OVERRIDES / f"carte_{name}.jpg", OVERRIDES / "carte.png", OVERRIDES / "carte.jpg"):
        if f.is_file():
            return f
    return None


def map_jpg(data):
    """Image de la carte en JPEG (<= 2048 px), mise en cache. None si introuvable."""
    minfo = (data or {}).get("mapInfo") or {}
    forced = map_override(minfo)
    sources = [("perso", str(forced))] if forced else [("jeu", c) for c in map_image_candidates(minfo, data)]
    # L'image de carte du jeu couvre une zone plus grande que la map : la partie jouable est au centre
    # (par defaut la moitie centrale, comme la carte en jeu). On ne garde que cette partie.
    ext = minfo.get("extScale") if isinstance(minfo.get("extScale"), (int, float)) and 0 < minfo.get("extScale") <= 1 else 0.5
    offx = minfo.get("extOffX") if isinstance(minfo.get("extOffX"), (int, float)) else (1 - ext) / 2
    offz = minfo.get("extOffZ") if isinstance(minfo.get("extOffZ"), (int, float)) else (1 - ext) / 2
    for kind, path in sources:
        crop = (offx, offz, ext) if kind == "jeu" else (0, 0, 1)
        cached = CACHE / ("map_" + hashlib.sha1((path + "|m2|" + repr(crop)).encode("utf-8")).hexdigest() + ".jpg")
        if kind == "jeu" and cached.is_file():
            return cached.read_bytes()
        try:
            blob = Path(path).read_bytes() if kind == "perso" else load_bytes(path, data)
        except Exception:
            blob = None
        if not blob or Image is None:
            continue
        try:
            img = Image.open(io.BytesIO(blob)); img.load()
            img = img.convert("RGB")
            if crop != (0, 0, 1):
                W, H = img.size
                img = img.crop((round(crop[0] * W), round(crop[1] * H), round((crop[0] + crop[2]) * W), round((crop[1] + crop[2]) * H)))
            if max(img.size) > MAP_MAX:
                k = MAP_MAX / max(img.size)
                img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
            out = io.BytesIO(); img.save(out, "JPEG", quality=88)
            jpg = out.getvalue()
        except Exception as exc:
            report("mapdecode:" + path, f"[Carte] Image de carte illisible : {path} ({exc})")
            continue
        if kind == "jeu":
            CACHE.mkdir(exist_ok=True); cached.write_bytes(jpg)
            print(f"[Carte] OK : {path}")
        return jpg
    report("mapmiss:" + str(minfo.get("mapXml")), "[Carte] Image de la carte introuvable : depose une capture de la carte du jeu "
           "dans images_perso\\carte.png")
    return None


# ---------------------------------------------------------------- images web
CONFIG_FILE = DATA_ROOT / "config.json"
WEB_DIR = CACHE / "web"
WEB_INDEX = CACHE / "web_index.json"
WEB_DIR.mkdir(exist_ok=True)
DEFAULT_WEB_CONFIG = {
    "enabled": False,      # desactivee : DuckDuckGo et Bing bloquent les requetes automatiques
    # "auto" essaie dans l'ordre : google (si une cle existe), duckduckgo, bing.
    # Ou un seul moteur : "duckduckgo" / "bing" (sans cle, non officiels) / "google" (anciennes cles seulement).
    "provider": "auto",
    "google_api_key": "",
    "google_cx": "",
    "max_searches_per_day": 40,
    "timeout_seconds": 10,
}
CONFIG_VERSION = 2
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ALLOW_LOCAL = os.environ.get("FS25_DASH_ALLOW_LOCAL") == "1"      # tests uniquement
MAX_DOWNLOAD = 6 * 1024 * 1024
MIN_IMAGE_WIDTH = 250
RETRY_AFTER_FAILURE = 6 * 3600
GOOD_DOMAINS = ("farming-simulator.com", "giants-software.com")
BAD_DOMAINS = ("pinterest", "alamy", "shutterstock", "istockphoto", "gettyimages",
               "dreamstime", "youtube", "ytimg", "facebook", "instagram", "tiktok")
_web_lock = threading.RLock()


def load_config():
    cfg = dict(DEFAULT_WEB_CONFIG)
    if CONFIG_FILE.is_file():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            user = raw.get("web_images", {})
            cfg.update(user)
            if raw.get("config_version") != CONFIG_VERSION:
                # ancien reglage par defaut "duckduckgo" devient "auto" (duckduckgo puis bing)
                if user.get("provider") in (None, "duckduckgo"):
                    cfg["provider"] = "auto"
                raw["web_images"] = {**user, "provider": cfg["provider"]}
                raw["config_version"] = CONFIG_VERSION
                CONFIG_FILE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except Exception as exc:
            report("cfg", f"[Config] config.json illisible ({exc}) : valeurs par defaut utilisees.")
    else:
        try:
            CONFIG_FILE.write_text(json.dumps({"config_version": CONFIG_VERSION, "web_images": DEFAULT_WEB_CONFIG}, indent=2),
                                   encoding="utf-8")
        except OSError:
            pass
    return cfg


def host_is_public(host):
    """Refuse localhost / reseau local / adresses privees (le serveur ne doit pas servir de relais)."""
    if ALLOW_LOCAL:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            return False
    return True


def check_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("URL non autorisee")
    if not host_is_public(p.hostname):
        raise ValueError("adresse non publique refusee")


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)           # une redirection ne doit pas mener vers le reseau local
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_GuardedRedirect)


def http_get(url, timeout, headers=None, limit=MAX_DOWNLOAD):
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with _opener.open(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("fichier trop gros")
    return data


def search_duckduckgo(query, timeout):
    """Moteur sans cle (non officiel : peut cesser de fonctionner sans prevenir)."""
    base = os.environ.get("FS25_DASH_DDG_BASE", "https://duckduckgo.com")
    page = http_get(f"{base}/?" + urlencode({"q": query, "iax": "images", "ia": "images"}),
                    timeout, limit=2_000_000).decode("utf-8", "replace")
    m = re.search(r"vqd=[\"']?([\d-]+)", page)
    if not m:
        raise RuntimeError("jeton DuckDuckGo introuvable (le service a peut-etre change)")
    raw = http_get(f"{base}/i.js?" + urlencode({"l": "wt-wt", "o": "json", "q": query,
                                                "vqd": m.group(1), "f": ",,,,,", "p": "1"}),
                   timeout, headers={"Referer": base + "/"}, limit=2_000_000)
    out = []
    for r in json.loads(raw).get("results", [])[:20]:
        out.append({"url": r.get("image"), "title": r.get("title", ""),
                    "domain": urlparse(r.get("url") or r.get("image") or "").hostname or "",
                    "w": r.get("width"), "h": r.get("height")})
    if not out:
        raise RuntimeError("reponse vide du moteur (probablement bloque ou format change) : "
                           + repr(raw[:160].decode("utf-8", "replace")))
    return out


def search_bing(query, timeout):
    """Page de resultats d'images Bing (sans cle, non officiel : le format peut changer)."""
    base = os.environ.get("FS25_DASH_BING_BASE", "https://www.bing.com")
    page = http_get(f"{base}/images/search?" + urlencode({"q": query, "form": "HDRSC2", "first": "1"}),
                    timeout, headers={"Accept-Language": "en-US,en;q=0.9", "Accept": "text/html"},
                    limit=4_000_000).decode("utf-8", "replace")
    out = []
    for tag in re.finditer(r'<a\b[^>]*\bclass="[^"]*\biusc\b[^"]*"[^>]*>', page):
        m = re.search(r'\bm="([^"]*)"', tag.group(0))
        if not m:
            continue
        try:
            meta = json.loads(html.unescape(m.group(1)))
        except ValueError:
            continue
        title = re.sub(r"<[^>]+>", "", str(meta.get("t") or meta.get("desc") or ""))
        out.append({"url": meta.get("murl"), "title": html.unescape(title),
                    "domain": urlparse(meta.get("purl") or meta.get("murl") or "").hostname or "",
                    "w": None, "h": None})
    if not out:
        raise RuntimeError(f"aucun resultat reconnu dans la page Bing ({len(page)} octets, debut : "
                           + repr(page[:120]) + ")")
    return out[:20]


def search_google(query, cfg, timeout):
    """API officielle Google Programmable Search (cle gratuite : ~100 requetes/jour)."""
    if not cfg.get("google_api_key") or not cfg.get("google_cx"):
        raise RuntimeError("google_api_key / google_cx manquants dans config.json")
    base = os.environ.get("FS25_DASH_GOOGLE_URL", "https://www.googleapis.com/customsearch/v1")
    raw = http_get(base + "?" + urlencode({"key": cfg["google_api_key"], "cx": cfg["google_cx"], "q": query,
                                           "searchType": "image", "num": 8, "safe": "active"}),
                   timeout, limit=2_000_000)
    return [{"url": it.get("link"), "title": it.get("title", ""), "domain": it.get("displayLink", ""),
             "w": (it.get("image") or {}).get("width"), "h": (it.get("image") or {}).get("height")}
            for it in json.loads(raw).get("items", [])]


def search_web(cfg, query, timeout):
    """Essaie les moteurs dans l'ordre ; renvoie (nom_du_moteur, resultats) ou leve une erreur detaillee."""
    choice = str(cfg.get("provider", "auto")).lower()
    has_google = bool(cfg.get("google_api_key") and cfg.get("google_cx"))
    order = {"auto": (["google"] if has_google else []) + ["duckduckgo", "bing"]}.get(choice, [choice])
    errors = []
    for name in order:
        try:
            if name == "google":
                found = search_google(query, cfg, timeout)
            elif name == "bing":
                found = search_bing(query, timeout)
            else:
                found = search_duckduckgo(query, timeout)
            if found:
                return name, found
            errors.append(f"{name} : aucun resultat")
        except Exception as exc:
            errors.append(f"{name} : {exc}")
            print(f"[Web] {name} a echoue : {exc}")
    raise RuntimeError(" | ".join(errors) or "aucun moteur configure")


def score_candidate(c, name):
    s = 0
    dom, title = (c.get("domain") or "").lower(), (c.get("title") or "").lower()
    if any(d in dom for d in GOOD_DOMAINS):
        s += 3
    if any(d in dom for d in BAD_DOMAINS):
        s -= 3                       # sites a filigrane / reseaux sociaux
    words = [w for w in re.split(r"\W+", name.lower()) if len(w) > 2]
    if words and all(w in title for w in words):
        s += 2
    w, h = c.get("w"), c.get("h")
    if isinstance(w, (int, float)) and isinstance(h, (int, float)) and h > 0:
        if w >= 500:
            s += 1
        if 1.2 <= w / h <= 2.6:
            s += 1
        if w < MIN_IMAGE_WIDTH:
            s -= 2
    return s


def rank_candidates(found, name):
    seen, cands = set(), []
    for c in found:
        url = c.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        cands.append(c)
    cands.sort(key=lambda c: -score_candidate(c, name))      # tri stable : l'ordre du moteur departage
    return [{k: c.get(k) for k in ("url", "title", "domain", "w", "h")} for c in cands[:8]]


def load_index():
    try:
        return json.loads(WEB_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}, "quota": {"date": "", "count": 0}}


def save_index(ix):
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    WEB_INDEX.write_text(json.dumps(ix, indent=1), encoding="utf-8")


def web_key(info):
    return hashlib.sha1((info.get("xml") or info.get("image") or info.get("name") or "").encode("utf-8")).hexdigest()


def web_query(info):
    name, brand = info.get("name") or "", info.get("brand") or ""
    base = name if (not brand or name.lower().startswith(brand.lower())) else f"{brand} {name}"
    return f"{base} Farming Simulator 25"


def download_candidate(c, timeout):
    blob = http_get(c["url"], timeout)
    if Image is None:
        return None
    img = Image.open(io.BytesIO(blob))
    if img.width < MIN_IMAGE_WIDTH:
        raise ValueError(f"image trop petite ({img.width} px)")
    return to_png(blob, c.get("domain") or "web")


def web_png(info):
    """Cherche (une seule fois par vehicule/outil) une image sur le web, puis la garde en cache."""
    cfg = load_config()
    name = info.get("name") or ""
    if not cfg.get("enabled") or Image is None or not name:
        return None
    key = web_key(info)
    png_file = WEB_DIR / f"{key}.png"
    timeout = float(cfg.get("timeout_seconds", 10))
    with _web_lock:
        ix = load_index()
        item = ix["items"].get(key)
        if png_file.is_file() and item:
            return png_file.read_bytes()
        if item and item.get("failed_at") and time.time() - item["failed_at"] < RETRY_AFTER_FAILURE:
            return None

        if item is None or not item.get("candidates"):
            quota = ix.setdefault("quota", {"date": "", "count": 0})
            today = time.strftime("%Y-%m-%d")
            if quota.get("date") != today:
                quota.update({"date": today, "count": 0})
            if quota["count"] >= int(cfg.get("max_searches_per_day", 40)):
                report("quota:" + today, "[Web] Limite quotidienne de recherches atteinte (config.json).")
                return None
            quota["count"] += 1
            query = web_query(info)
            print(f"[Web] Recherche : {query}")
            item = {"name": name, "query": query, "candidates": [], "pos": 0}
            ix["items"][key] = item
            try:
                engine, found = search_web(cfg, query, timeout)
                item["candidates"] = rank_candidates(found, name)
                print(f"[Web] {len(item['candidates'])} resultat(s) via {engine}")
            except Exception as exc:
                print(f"[Web] Recherche impossible : {exc}")
            if not item["candidates"]:
                item["failed_at"] = time.time()
                save_index(ix)
                print(f"[Web] Aucun resultat utilisable pour {name}.")
                return None
            save_index(ix)

        n = len(item["candidates"])
        start = item.get("pos", 0) % n
        for step in range(n):
            idx = (start + step) % n
            c = item["candidates"][idx]
            try:
                png = download_candidate(c, timeout)
            except Exception as exc:
                print(f"[Web] Candidat {idx + 1}/{n} ignore ({c.get('domain')}) : {exc}")
                continue
            if png:
                WEB_DIR.mkdir(parents=True, exist_ok=True)
                png_file.write_bytes(png)
                item["pos"] = idx
                item.pop("failed_at", None)
                save_index(ix)
                print(f"[Web] OK : {name} <- {c.get('domain')} (image {idx + 1}/{n}). "
                      "Mauvaise image ? Mode edition > bouton 'Autre image'.")
                return png
        item["failed_at"] = time.time()
        save_index(ix)
        return None


_KEEP_TABLE = bytes([255, 0] + [0] * 254)     # 0 (non inonde) -> 255 (garde) ; 1 (fond) -> 0


def remove_uniform_background(png, tol=22):
    """Rend transparent un fond UNI relie aux bords de l'image, puis rogne autour du vehicule.
    Renvoie (png, statut) : 'removed' | 'kept' (fond non uni) | 'transparent' (deja detouree) | 'off'."""
    if Image is None or ImageFilter is None:
        return png, "off"
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = img.size
    alpha = img.getchannel("A")
    if sum(alpha.histogram()[:250]) > 0.02 * w * h:
        return png, "transparent"
    px = list(img.getdata())
    border = ([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
              + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
    key = lambda p: (p[0] >> 3, p[1] >> 3, p[2] >> 3)
    top, _ = Counter(key(px[y * w + x]) for x, y in border).most_common(1)[0]
    sel = [px[y * w + x] for x, y in border if key(px[y * w + x]) == top]
    bg = tuple(sum(c[i] for c in sel) // len(sel) for i in range(3))

    def close(p):
        return abs(p[0] - bg[0]) <= tol and abs(p[1] - bg[1]) <= tol and abs(p[2] - bg[2]) <= tol

    if sum(1 for x, y in border if close(px[y * w + x])) < 0.7 * len(border):
        return png, "kept"                          # fond non uni (decor, degrade...) : on ne touche a rien
    seen = bytearray(w * h)
    dq = deque()
    for x, y in border:
        i = y * w + x
        if not seen[i] and close(px[i]):
            seen[i] = 1
            dq.append(i)
    while dq:                                       # inondation depuis les bords (4 voisins)
        i = dq.popleft()
        y, x = divmod(i, w)
        for j in (i - 1 if x > 0 else -1, i + 1 if x < w - 1 else -1, i - w if y > 0 else -1, i + w if y < h - 1 else -1):
            if j >= 0 and not seen[j] and close(px[j]):
                seen[j] = 1
                dq.append(j)
    removed = sum(seen)
    if removed < 0.05 * w * h or removed > 0.97 * w * h:
        return png, "kept"                          # rien d'utile retire, ou image quasi vide
    mask = Image.frombytes("L", (w, h), bytes(seen).translate(_KEEP_TABLE))
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))   # bords propres, sans liseré
    img.putalpha(mask)
    box = mask.point(lambda v: 255 if v > 10 else 0).getbbox()
    if box:
        pad = 8
        img = img.crop((max(box[0] - pad, 0), max(box[1] - pad, 0), min(box[2] + pad, w), min(box[3] + pad, h)))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue(), "removed"


PERSO_GENERIC = {"store", "icon", "image", "preview", "thumbnail", "thumb"}   # trop courants : risque de collision entre mods


def perso_stem(info):
    image_stem = Path(info["image"]).stem if info.get("image") else ""
    xml_stem = Path(info.get("xml") or "").stem
    stem = (image_stem if image_stem.lower() not in PERSO_GENERIC else "") or xml_stem or image_stem or info.get("name") or "element"
    return re.sub(r'[<>:"/\\|?*]', "_", stem)


def perso_save(info, png, original=None):
    """Enregistre l'image deposee dans images_perso/ sous le nom que override_for() cherche en premier.
    Si un detourage a eu lieu, l'original est garde dans images_perso/_originaux/."""
    stem = perso_stem(info)
    OVERRIDES.mkdir(exist_ok=True)
    (OVERRIDES / f"{stem}.png").write_bytes(png)
    if original is not None:
        (OVERRIDES / "_originaux").mkdir(exist_ok=True)
        (OVERRIDES / "_originaux" / f"{stem}.png").write_bytes(original)
    return stem


def perso_remove(info):
    """Retire l'image perso d'un element : elle est deplacee (pas effacee) dans images_perso/_retirees/.
    L'element reprend alors l'image du jeu. Renvoie le nom du fichier retire, ou None s'il n'y en avait pas."""
    f = override_path(info)
    if not f:
        return None
    dest_dir = OVERRIDES / "_retirees"
    dest_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    f.replace(dest_dir / f"{f.stem}_{stamp}.png")
    orig = OVERRIDES / "_originaux" / f.name
    if orig.is_file():
        orig.replace(dest_dir / f"{f.stem}_{stamp}_original.png")
    return f.name


def web_next(info):
    """Passe a l'image suivante parmi les resultats trouves (bouton 'Autre image')."""
    with _web_lock:
        ix = load_index()
        item = ix["items"].get(web_key(info or {}))
        if not item or not item.get("candidates"):
            return {"ok": False, "reason": "Cette image ne vient pas d'une recherche web."}
        n = len(item["candidates"])
        item["pos"] = (item.get("pos", 0) + 1) % n
        item.pop("failed_at", None)
        f = WEB_DIR / f"{web_key(info)}.png"
        if f.is_file():
            f.unlink()
        save_index(ix)
        return {"ok": True, "position": item["pos"] + 1, "total": n}


# ---------------------------------------------------------------- dispositions enregistrees (1.4.4, choix A de Luxo)
DISPO_SLOTS = 3


def dispos_load():
    try:
        d = json.loads(DISPOS.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    slots = d.get("slots") if isinstance(d.get("slots"), list) else []
    slots = (slots + [None] * DISPO_SLOTS)[:DISPO_SLOTS]
    slots = [s if isinstance(s, dict) and isinstance(s.get("layout"), dict) else None for s in slots]
    names = d.get("names") if isinstance(d.get("names"), list) else []
    names = [(str(n)[:24] if n else "") for n in (names + [""] * DISPO_SLOTS)[:DISPO_SLOTS]]
    backup = d.get("backup") if isinstance(d.get("backup"), dict) and isinstance(d["backup"].get("layout"), dict) else None
    return {"slots": slots, "names": names, "backup": backup}


def dispos_save(d):
    tmp = DISPOS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DISPOS)


def dispos_public(d):
    """Ce que la page affiche (sans le contenu des dispositions)."""
    return {"slots": [{"name": d["names"][i] or f"Disposition {i + 1}", "at": (s or {}).get("at"), "empty": s is None}
                      for i, s in enumerate(d["slots"])],
            "backup": {"at": d["backup"]["at"], "why": d["backup"].get("why", "")} if d["backup"] else None}


def dispos_action(req):
    d = dispos_load()
    act = req.get("action")
    now = time.strftime("%d/%m %H:%M")
    cur = lambda: clean_layout(json.loads(LAYOUT.read_text(encoding="utf-8"))) if LAYOUT.is_file() else {}
    if act in ("save", "backup"):
        lay = clean_layout(req.get("layout")) if isinstance(req.get("layout"), dict) else cur()
        if act == "save":
            i = int(req.get("slot", -1))
            if not 0 <= i < DISPO_SLOTS:
                raise ValueError("emplacement inconnu")
            d["slots"][i] = {"at": now, "layout": lay}
        else:
            d["backup"] = {"at": now, "why": str(req.get("why") or "")[:40], "layout": lay}
    elif act == "load":
        which = req.get("slot")
        src = d["backup"] if which == "backup" else (d["slots"][int(which)] if str(which).isdigit() and int(which) < DISPO_SLOTS else None)
        if not src:
            raise ValueError("emplacement vide")
        lay = clean_layout(src["layout"])
        old = cur()
        if which != "backup":                                    # avant de charger : copie de la disposition actuelle
            d["backup"] = {"at": now, "why": "avant chargement", "layout": old}
        else:                                                    # revenir : on echange (on peut re-annuler)
            d["backup"] = {"at": now, "why": "avant retour", "layout": old}
        tmp = LAYOUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(lay, indent=1), encoding="utf-8")
        tmp.replace(LAYOUT)
    elif act == "rename":
        i = int(req.get("slot", -1))
        if not 0 <= i < DISPO_SLOTS:
            raise ValueError("emplacement inconnu")
        d["names"][i] = re.sub(r"[\x00-\x1f<>]", "", str(req.get("name") or ""))[:24].strip()
    elif act != "list":
        raise ValueError("action inconnue")
    if act != "list":
        dispos_save(d)
    return dispos_public(d)


def clean_layout(obj):
    """Ne garde que {id: {x,y,w,h,visible}} avec des valeurs valides."""
    out = {}
    if not isinstance(obj, dict):
        return out
    for key, g in obj.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,32}", key) or not isinstance(g, dict):
            continue
        item = {}
        for k in ("x", "y", "w", "h"):
            v = g.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                item[k] = round(max(0.0, min(100.0, float(v))), 2)
        if isinstance(g.get("visible"), bool):
            item["visible"] = g["visible"]
        if item:
            out[key] = item
    return out


def _exc(e):
    return f"{type(e).__name__}: {e}"


def diag_report():
    """Page /diag : teste chaque maillon (donnees du jeu, fichiers, internet, moteur, telechargement)."""
    L = []
    add = L.append
    try:
        from PIL import __version__ as pil_v
    except Exception:
        pil_v = "MANQUANT"
    add(f"=== DIAGNOSTIC FS25 STREAM DASHBOARD ===")
    add(f"Serveur v{SERVER_VERSION} | Python {sys.version.split()[0]} | Pillow {pil_v} | {sys.platform}")
    add("")
    add("[1] Donnees du jeu (data.json)")
    found = False
    for p in DATA_CANDIDATES:
        if p.exists():
            found = True
            add(f"  trouve : {p}  (modifie il y a {int(time.time() - p.stat().st_mtime)} s)")
    if not found:
        add("  AUCUN data.json trouve. Chemins testes :")
        for p in DATA_CANDIDATES:
            add(f"   - {p}")
    data = read_data() or {}
    age = data_file_age()
    add(f"  session={data.get('session')} (False = hors partie / menu principal) | age du fichier : "
        + (f"{age:.0f} s (considere perime apres {STALE_SECONDS:.0f} s)" if age is not None else "inconnu"))
    add(f"  active={data.get('active')} | vehicule={data.get('vehicle')} | gameDir={data.get('gameDir')}")
    impls = data.get("implements") or []
    add(f"  outils detectes : {len(impls)}")

    cfg = load_config()
    shown = {k: ("***" if k == "google_api_key" and v else v) for k, v in cfg.items()}
    add("")
    add(f"[2] Reglages web : {shown}")

    add("")
    add("[3] Image de chaque element")
    targets = [("vehicule", data.get("vehicleInfo"))] + [(f"outil {i}", x) for i, x in enumerate(impls)]
    for label, info in targets:
        if not info:
            continue
        add(f"  - {label} : {info.get('name')} ({info.get('brand')})")
        add(f"      chemin du jeu : {info.get('image')}")
        add(f"      images_perso  : {'OUI' if override_for(info) else 'non'}")
        blob = None
        try:
            blob = load_bytes(info["image"], data) if info.get("image") else None
        except Exception as e:
            add(f"      lecture locale : ERREUR {_exc(e)}")
        add(f"      fichier local : {'TROUVE (' + str(len(blob)) + ' octets)' if blob else 'introuvable / illisible'}")
        item = load_index().get("items", {}).get(web_key(info))
        if item:
            add(f"      web (cache)   : {len(item.get('candidates', []))} candidat(s), position {item.get('pos', 0) + 1}"
                + (f", ECHEC il y a {int(time.time() - item['failed_at'])} s" if item.get("failed_at") else ""))
        else:
            add("      web (cache)   : jamais cherche")

    add("")
    add(f"[3b] Dossier images_perso : {OVERRIDES}")
    try:
        files = sorted(x.name for x in OVERRIDES.iterdir())
    except OSError as e:
        files = []
        add(f"  illisible : {_exc(e)}")
    add("  contenu : " + (", ".join(files) if files else "(vide)"))
    for label, info in targets:
        if info and info.get("name"):
            ip = Path(info["image"]).stem if info.get("image") else ""
            wanted = [x for x in (ip, Path(info.get("xml") or "").stem, info.get("name")) if x]
            add(f"  - {label} : noms acceptes -> " + " | ".join(f"{w}.png" for w in wanted))

    add("")
    add("[3c] Etats du vehicule et des outils (voyants) - ce que le jeu expose reellement")
    for label, info in targets:
        if not info:
            continue
        st = info.get("status")
        if st is None:
            add(f"  - {label} : AUCUN etat recu -> mets le mod a jour puis relance le jeu")
        else:
            add(f"  - {label} : " + (", ".join(f"{k}={v}" for k, v in sorted(st.items())) or "(le jeu n'expose aucun etat pour cet element)"))

    add("")
    add("[3d] Partie : mois, vitesse du temps, previsions meteo")
    g = data.get("game") or {}
    if not g:
        add("  aucune donnee de partie" + (" (pas de partie en cours)" if data.get("session") is False else ""))
    else:
        add(f"  periode (mois) = {g.get('period')} | jour dans la periode = {g.get('dayInPeriod')} | jour = {g.get('day')}")
        add(f"  vitesse du temps = {g.get('timeScale')} (source : {g.get('timeScaleSrc')})")
        fc = g.get("forecast")
        if fc is None:
            add("  previsions : AUCUNE (le jeu n'expose pas getWeatherTypeAtTime, ou le mod est trop ancien)")
        else:
            add(f"  previsions : {len(fc)} entree(s) | temperature future fiable : {g.get('forecastTempOk')}")
            for x in fc:
                add(f"     +{x.get('hours')} h (jour +{x.get('dayOffset')}) : {x.get('type')} {x.get('temperature', '')}")

    add("")
    add("[3e] Champ sous le vehicule (ou le joueur a pied)")
    fi = data.get("field")
    if fi is None and data.get("session") is False:
        add("  pas de partie en cours (lance une partie pour voir le champ)")
    elif fi is None:
        add("  AUCUN champ recu -> mets le mod a jour puis relance le jeu")
    else:
        add("  " + ", ".join(f"{k}={v}" for k, v in sorted(fi.items())))
        raw = data.get("fieldRaw")
        if raw:
            add("  donnees brutes du panneau d'info de champ du jeu (" + str(len(raw)) + " valeurs) :")
            for k in sorted(raw):
                add(f"     {k} = {raw[k]}")
        else:
            add("  pas de donnees du panneau du jeu captees (le calcul direct FieldState est utilise si disponible)")
        add("  (dans le log.txt du jeu : ligne '[StreamDashboard] ... field hooks: N' = nombre de fonctions du panneau branchees)")

    add("")
    add("")
    add("[3f] Mini-carte : joueurs et image de la carte")
    pl = data.get("players")
    if pl is None:
        add("  AUCUNE liste de joueurs recue" + (" (pas de partie en cours)" if data.get("session") is False else " -> mets le mod a jour puis relance le jeu"))
    else:
        add(f"  {len(pl)} joueur(s) (source : {data.get('playersSrc')})")
        for p in pl[:16]:
            add(f"   - {p.get('name')} | moi={p.get('self')} | x={p.get('x')} z={p.get('z')} | vehicule={p.get('vehicle')}")
    mi = data.get("mapInfo") or {}
    add(f"  infos carte : {mi}")
    _img, _w = map_xml_info(mi, data)
    add(f"  XML de la map : imageFilename={_img} | largeur={_w}")
    for c in map_image_candidates(mi, data):
        try:
            b = load_bytes(c, data)
        except Exception as e:
            b = None
        add(f"   image candidate : {c} -> {'TROUVEE (' + str(len(b)) + ' octets)' if b else 'introuvable'}")
    fs = data.get("fieldStates")
    ff = LAST_READ_PATH.with_name("fields.json") if LAST_READ_PATH else None
    try:
        shapes = json.loads(ff.read_text(encoding="utf-8")) if ff and ff.is_file() else {}
    except Exception:
        shapes = {}
    nshape = len(shapes.get("fields") or [])
    npoly = sum(1 for x in (shapes.get("fields") or []) if x.get("polys"))
    add(f"  couches de champs : {nshape} champ(s) dont {npoly} avec contour (source : {shapes.get('source') or data.get('fieldShapesSrc')})")
    add(f"  etats des champs : {len(fs) if isinstance(fs, list) else 'AUCUN'} (source : {data.get('fieldStatesSrc')})")
    if isinstance(fs, list) and fs:
        add(f"   exemple : {fs[0]}")
    mo = map_override(mi)
    add("  image perso de carte : " + (str(mo) if mo else "aucune (nom accepte : images_perso\\carte.png)"))

    if not cfg.get("enabled"):
        add("[4] Recherche web automatique : desactivee (images du jeu, images_perso et bouton Google uniquement)")
        add("")
        add("=== FIN : copie tout ce texte et envoie-le moi ===")
        return "\n".join(L)
    add("[4] Internet et moteur de recherche (peut prendre jusqu'a 30 s)")
    t = time.time()
    try:
        page = http_get("https://www.wikipedia.org/", 10, limit=500_000)
        add(f"  internet general : OK ({len(page)} octets, {time.time() - t:.1f} s)")
    except Exception as e:
        add(f"  internet general : ECHEC -> {_exc(e)}")
    query = web_query(impls[0]) if impls else "Dammann Profi-Class Tridem Farming Simulator 25"
    name0 = (impls[0].get("name") if impls else "") or ""
    add(f"  requete test : {query}")
    add(f"  reglage 'provider' : {cfg.get('provider')}")
    engines = [("duckduckgo", lambda: search_duckduckgo(query, 10)), ("bing", lambda: search_bing(query, 10))]
    if cfg.get("google_api_key") and cfg.get("google_cx"):
        engines.insert(0, ("google", lambda: search_google(query, cfg, 10)))
    best = []
    for engine, fn in engines:
        t = time.time()
        try:
            res = fn()
            add(f"  - {engine} : OK, {len(res)} resultat(s) en {time.time() - t:.1f} s")
            for r in rank_candidates(res, name0)[:3]:
                add(f"       * {r.get('domain')} | {str(r.get('title'))[:50]} | {r.get('w')}x{r.get('h')}")
            best = best or rank_candidates(res, name0)
        except Exception as e:
            add(f"  - {engine} : ECHEC -> {_exc(e)}")
    if best:
        try:
            png = download_candidate(best[0], 10)
            im = Image.open(io.BytesIO(png)) if png else None
            add(f"  telechargement du 1er resultat : OK ({im.width}x{im.height})" if im else "  telechargement : image illisible")
        except Exception as e:
            add(f"  telechargement du 1er resultat : ECHEC -> {_exc(e)}")
    add("")
    add("=== FIN : copie tout ce texte et envoie-le moi ===")
    return "\n".join(L)


# ----------------------------------------------------------------- QR CODE (mode compagnon) ----
# Petit generateur de QR code (mode octets, correction M, versions 1 a 9) : aucune librairie a installer.
_QR_M = {1: (10, [16]), 2: (16, [28]), 3: (26, [44]), 4: (18, [32, 32]), 5: (24, [43, 43]),
         6: (16, [27] * 4), 7: (18, [31] * 4), 8: (22, [38, 38, 39, 39]), 9: (22, [36, 36, 36, 37, 37])}
_QR_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
             7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46]}
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_ec(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _gf_mul(gen[j + 1], f)
    return rem


def _bch(value, poly, bits):
    v = value << bits
    plen = poly.bit_length()
    while v.bit_length() >= plen:
        v ^= poly << (v.bit_length() - plen)
    return (value << bits) | v


def qr_matrix(text):
    """Renvoie la matrice (liste de listes de bool) du QR code de 'text'."""
    data = text.encode("utf-8")
    for ver in range(1, 10):
        ecn, blocks = _QR_M[ver]
        if len(data) + 2 <= sum(blocks):
            break
    else:
        raise ValueError("texte trop long pour le QR code")
    cap = sum(blocks)
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in range(7, -1, -1)]
    for b in data:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(cw) < cap:
        cw.append(0xEC if pad % 2 == 0 else 0x11)
        pad += 1
    dblocks, eblocks, pos = [], [], 0
    for n in blocks:
        d = cw[pos:pos + n]
        pos += n
        dblocks.append(d)
        eblocks.append(_rs_ec(d, ecn))
    final = []
    for i in range(max(blocks)):
        for d in dblocks:
            if i < len(d):
                final.append(d[i])
    for i in range(ecn):
        for e in eblocks:
            final.append(e[i])

    size = ver * 4 + 17
    M = [[None] * size for _ in range(size)]

    def finder(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    on = (0 <= dr <= 6 and dc in (0, 6)) or (0 <= dc <= 6 and dr in (0, 6)) or (2 <= dr <= 4 and 2 <= dc <= 4)
                    M[rr][cc] = on
    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)
    for i in range(8, size - 8):
        M[6][i] = M[i][6] = (i % 2 == 0)
    al = _QR_ALIGN[ver]
    for r in al:
        for c in al:
            if (r, c) in ((al[0], al[0]), (al[0], al[-1]), (al[-1], al[0])):
                continue                                    # sous les reperes des coins
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    M[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1
    M[size - 8][8] = True                                   # module sombre fixe
    reserved = set()
    for i in range(9):
        reserved.add((8, i)); reserved.add((i, 8))
    for i in range(8):
        reserved.add((8, size - 1 - i)); reserved.add((size - 1 - i, 8))
    if ver >= 7:
        for i in range(6):
            for j in range(3):
                reserved.add((i, size - 11 + j)); reserved.add((size - 11 + j, i))
    for (r, c) in reserved:
        if M[r][c] is None:
            M[r][c] = False
    func = [[M[r][c] is not None for c in range(size)] for r in range(size)]

    allbits = []
    for b in final:
        allbits += [(b >> i) & 1 for i in range(7, -1, -1)]
    idx, up, c = 0, True, size - 1
    while c > 0:
        if c == 6:
            c -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for cc in (c, c - 1):
                if not func[r][cc]:
                    M[r][cc] = bool(allbits[idx]) if idx < len(allbits) else False
                    idx += 1
        up = not up
        c -= 2

    masks = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
             lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
             lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
             lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]

    def place_info(G, mask):
        fmt = _bch((0b00 << 3) | mask, 0x537, 10) ^ 0x5412    # correction M = 00
        fb = [(fmt >> i) & 1 for i in range(15)]                # bit 0 = poids faible
        for i in range(15):
            v = bool(fb[i])
            if i < 6:
                G[i][8] = v                                     # 1re copie (pres du coin haut gauche)
            elif i < 8:
                G[i + 1][8] = v
            elif i == 8:
                G[8][7] = v
            else:
                G[8][14 - i] = v
            if i < 8:
                G[8][size - 1 - i] = v                          # 2e copie
            else:
                G[size - 15 + i][8] = v
        if ver >= 7:
            vb = _bch(ver, 0x1F25, 12)
            for i in range(18):
                v = bool((vb >> i) & 1)
                G[i // 3][size - 11 + i % 3] = v
                G[size - 11 + i % 3][i // 3] = v

    def penalty(G):
        p = 0
        for line in list(G) + [list(x) for x in zip(*G)]:
            run = 1
            for i in range(1, size):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        p += run - 2
                    run = 1
            if run >= 5:
                p += run - 2
            s = "".join("1" if v else "0" for v in line)
            p += 40 * (s.count("10111010000") + s.count("00001011101"))
        for r in range(size - 1):
            for c in range(size - 1):
                if G[r][c] == G[r + 1][c] == G[r][c + 1] == G[r + 1][c + 1]:
                    p += 3
        dark = sum(v for row in G for v in row)
        p += abs(dark * 20 // (size * size) - 10) * 10
        return p

    best = None
    for m in range(8):
        G = [row[:] for row in M]
        f = masks[m]
        for r in range(size):
            for c in range(size):
                if not func[r][c] and f(r, c):
                    G[r][c] = not G[r][c]
        place_info(G, m)
        sc = penalty(G)
        if best is None or sc < best[0]:
            best = (sc, G)
    return best[1]


def qr_svg(text, px=6):
    G = qr_matrix(text)
    n, q = len(G), 4
    w = (n + 2 * q) * px
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{w}" viewBox="0 0 {n + 2 * q} {n + 2 * q}" shape-rendering="crispEdges">',
             f'<rect width="{n + 2 * q}" height="{n + 2 * q}" fill="#fff"/><path fill="#000" d="']
    for r in range(n):
        for c in range(n):
            if G[r][c]:
                parts.append(f"M{c + q} {r + q}h1v1h-1z")
    parts.append('"/></svg>')
    return "".join(parts)


# ------------------------------------------------------------- MODE COMPAGNON (Wi-Fi) ----
# La page /compagnon peut etre ouverte depuis un telephone / une tablette du reseau de la maison.
# Le serveur principal reste sur 127.0.0.1:8765 (OBS, mode edition). Quand le Wi-Fi est active, un
# 2e serveur ecoute sur le reseau local (port 8766) et ne laisse passer QUE la page compagnon et ses donnees.
COMP_PORT = 8766
COMP_FILE = DATA_ROOT / "compagnon.json"
REMOTE_GET = {"/", "/compagnon", "/compagnon/", "/data.json", "/fields.json", "/img/map", "/version", "/compagnon/icon.svg", "/stream/icon.svg", "/compagnon/icon.png", "/compagnon/icon-512.png", "/compagnon/manifest.json", "/compagnon/notes", "/compagnon/ingame"}
REMOTE_POST = {"/compagnon/login", "/compagnon/notes", "/compagnon/ingame", "/compagnon/goto"}
NOTES_FILE = DATA_ROOT / "notes_champs.json"
_notes_lock = threading.Lock()
_lan = {"httpd": None, "error": None}
_lan_lock = threading.Lock()


INGAME_DEFAULT = {"notif": True, "todo": True, "field": True, "who": True,
                  "weather": False, "fieldnow": False, "alerts": False, "tp": True}
INGAME_PANELS = ("todo", "who", "field", "weather", "fieldnow", "alerts")   # blocs reglables


def ingame_layout(src, dst):
    """Copie (en verifiant) les reglages des blocs dans le jeu : position (x, y = coin haut-gauche, fraction de l'ecran),
    taille (s), transparence du fond (a), mode (m : "key" = avec la touche des blocs (F9), "always" = toujours), duree de l'encart champ."""
    def num(v, lo, hi):
        try:
            v = float(v)
            return v if lo <= v <= hi else None
        except (TypeError, ValueError):
            return None
    sc = num(src.get("scale"), 0.6, 1.8)
    if sc is not None:
        dst["scale"] = round(sc, 2)
    fs = num(src.get("fieldSec"), 2, 60)
    if fs is not None:
        dst["fieldSec"] = int(round(fs))
    for key, lo, hi in (("alertSec", 10, 600), ("notifSec", 2, 60)):     # duree des alertes (bloc) et des notifications
        v = num(src.get(key), lo, hi)
        if v is not None:
            dst[key] = int(round(v))
    if src.get("alertFarm") in ("mine", "all"):          # 1.4.4 : alertes remorque pleine : ma ferme / tout le serveur
        dst["alertFarm"] = src["alertFarm"]
    pos = src.get("pos")
    if isinstance(pos, dict):
        out = {k: dict(v) for k, v in (dst.get("pos") or {}).items() if isinstance(v, dict)}
        for k in INGAME_PANELS:
            if k not in pos:
                continue
            v = pos.get(k)
            if v is None:
                out.pop(k, None)
                continue
            if not isinstance(v, dict):
                continue
            e = out.get(k, {})
            x, y = num(v.get("x"), 0, 1), num(v.get("y"), 0, 1)
            if x is not None and y is not None:
                e["x"], e["y"] = round(x, 4), round(y, 4)
            if "s" in v:
                sv = num(v.get("s"), 0.5, 2.0)
                if sv is not None:
                    e["s"] = round(sv, 2)
            if "a" in v:
                av = num(v.get("a"), 0, 1)
                if av is not None:
                    e["a"] = round(av, 2)
            for a, lo, hi in (("w", 0.06, 0.9), ("h", 0.04, 0.95)):        # largeur / hauteur libres (0 = auto)
                if a in v:
                    if not v.get(a):
                        e.pop(a, None)
                    else:
                        av = num(v.get(a), lo, hi)
                        if av is not None:
                            e[a] = round(av, 4)
            if v.get("m") in ("key", "always"):
                e["m"] = v["m"]
            out[k] = e
        dst["pos"] = out


def comp_load():
    cfg = {"lan": False, "code": "", "ingame": dict(INGAME_DEFAULT)}
    try:
        got = json.loads(COMP_FILE.read_text(encoding="utf-8"))
        if isinstance(got, dict):
            cfg["lan"] = bool(got.get("lan"))
            code = str(got.get("code") or "")
            cfg["code"] = code if re.fullmatch(r"\d{4,8}", code) else ""
            ig = got.get("ingame")
            if isinstance(ig, dict):
                for k in INGAME_DEFAULT:
                    if k in ig:
                        cfg["ingame"][k] = bool(ig[k])
                ingame_layout(ig, cfg["ingame"])
    except Exception:
        pass
    return cfg


def comp_save(cfg):
    tmp = COMP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"lan": bool(cfg.get("lan")), "code": cfg.get("code") or "",
                               "ingame": cfg.get("ingame") or dict(INGAME_DEFAULT)}, indent=1), encoding="utf-8")
    tmp.replace(COMP_FILE)


def comp_token(code):
    return hashlib.sha256(("fs25-compagnon:" + code).encode("utf-8")).hexdigest()[:32]


def lan_ips():
    """Adresses IPv4 de ce PC sur le reseau local (la plus probable en premier)."""
    ips = []
    try:                                   # adresse utilisee pour sortir vers le reseau (aucun paquet envoye)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 9))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    out = []
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip)
            if a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local:
                out.append(ip)
        except ValueError:
            pass
    return out


def lan_start():
    with _lan_lock:
        if _lan["httpd"] is not None:
            return True
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", COMP_PORT), Handler)
        except OSError as exc:
            _lan["error"] = f"port {COMP_PORT} indisponible ({exc})"
            print("[Compagnon] Wi-Fi impossible :", exc)
            return False
        _lan["httpd"], _lan["error"] = httpd, None
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"[Compagnon] ouvert au reseau local : port {COMP_PORT} ({', '.join(lan_ips()) or 'adresse inconnue'})")
        return True


def lan_stop():
    with _lan_lock:
        httpd, _lan["httpd"] = _lan["httpd"], None
    if httpd is not None:
        threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()
        print("[Compagnon] ferme au reseau local")


def lan_apply():
    """Demarre / arrete l'acces Wi-Fi selon compagnon.json (appele au lancement de l'app)."""
    mod_files_sync(force=True)                            # reglages + notes pour l'affichage dans le jeu
    if comp_load()["lan"]:
        lan_start()
    else:
        lan_stop()


def notes_load():
    try:
        d = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def notes_set(map_key, field_id, text):
    """Note perso d'un champ (partagee entre tous les appareils). Texte vide = note supprimee."""
    map_key = str(map_key or "")[:300]
    field_id = str(int(field_id))
    text = str(text or "").strip()[:2000]
    with _notes_lock:
        d = notes_load()
        m = d.setdefault(map_key, {})
        if text:
            m[field_id] = {"t": text, "at": int(time.time())}
        else:
            m.pop(field_id, None)
        if not m:
            d.pop(map_key, None)
        tmp = NOTES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(NOTES_FILE)
        return {k: v.get("t", "") for k, v in m.items()}


def mod_dirs():
    """Dossiers modSettings du mod (ceux qui existent : le mod les cree au lancement d'une partie)."""
    return [p.parent for p in DATA_CANDIDATES if p.parent.is_dir()]


def ingame_xml(ig):
    """ingame.xml pour le mod : le jeu sait lire le XML (loadXMLFile), pas les fichiers texte (pas de file:read)."""
    from xml.sax.saxutils import quoteattr
    b = lambda v: "true" if v is not False else "false"
    flags = " ".join('%s="%s"' % (k, "true" if ig.get(k, d) else "false") for k, d in INGAME_DEFAULT.items())
    lines = ['<?xml version="1.0" encoding="utf-8" standalone="no"?>',
             '<ingame %s scale="%s" fieldSec="%s" alertSec="%s" notifSec="%s" alertFarm="%s">' % (
                 flags, ig.get("scale", 1.0), ig.get("fieldSec", 7), ig.get("alertSec", 60), ig.get("notifSec", 7),
                 ig.get("alertFarm") if ig.get("alertFarm") in ("mine", "all") else "mine")]
    for k, v in (ig.get("pos") or {}).items():
        if k in INGAME_PANELS and isinstance(v, dict):
            attrs = " ".join('%s="%s"' % (a, v[a]) for a in ("x", "y", "s", "a", "m", "w", "h") if a in v)
            if attrs:
                lines.append('    <%s %s/>' % (k, attrs))
    lines.append("</ingame>")
    return "\n".join(lines) + "\n"


def notes_xml(notes):
    from xml.sax.saxutils import escape, quoteattr
    lines = ['<?xml version="1.0" encoding="utf-8" standalone="no"?>', "<notes>"]
    for key, m in notes.items():
        if not isinstance(m, dict) or not m:
            continue
        lines.append("    <map key=%s>" % quoteattr(str(key)))
        for fid, text in m.items():
            text = str(text or "").replace("\r", " ").replace("\n", " ")
            if text:
                lines.append("        <field id=%s>%s</field>" % (quoteattr(str(fid)), escape(text)))
        lines.append("    </map>")
    lines.append("</notes>")
    return "\n".join(lines) + "\n"


def mod_files_sync(force=False):
    """Ecrit pour le mod (affichage dans le jeu) : ingame.xml (reglages, positions) et notes.xml (notes des champs).
    Les .json sont gardes pour les anciennes versions du mod (1.4.1.3 et avant)."""
    try:
        ig = comp_load()["ingame"]
        notes = {k: {i: v.get("t", "") for i, v in m.items() if isinstance(v, dict)}
                 for k, m in notes_load().items() if isinstance(m, dict)}
        files = {"ingame.xml": ingame_xml(ig), "notes.xml": notes_xml(notes),
                 "ingame.json": json.dumps(ig, ensure_ascii=False), "notes.json": json.dumps(notes, ensure_ascii=False)}
        for d in mod_dirs():
            for name, body in files.items():
                f = d / name
                try:
                    if not force and f.is_file() and f.read_text(encoding="utf-8") == body:
                        continue
                except OSError:
                    pass
                tmp = f.with_suffix(".tmp")
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(f)
    except Exception as exc:
        print("[Compagnon] fichiers pour le mod impossibles :", exc)


def goto_write(kind, target):
    """Ecrit l'ordre "y aller" pour le mod (cmd.xml, relu chaque seconde). Renvoie l'identifiant, ou None sans partie."""
    from xml.sax.saxutils import quoteattr
    cid = str(int(time.time() * 1000))
    body = ('<?xml version="1.0" encoding="utf-8" standalone="no"?>\n<cmd id=%s kind=%s target=%s/>\n'
            % (quoteattr(cid), quoteattr(kind), quoteattr(target)))
    done = False
    for d in mod_dirs():
        try:
            tmp = d / "cmd.tmp"
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(d / "cmd.xml")
            done = True
        except OSError as exc:
            print("[Compagnon] ordre impossible :", exc)
    return cid if done else None


_mod_sync_at = [0.0]


def mod_files_tick():
    """Appele avec data.json : recree les fichiers du mod s'ils manquent (dossier cree apres le lancement de l'app)."""
    if time.time() - _mod_sync_at[0] < 10:
        return
    _mod_sync_at[0] = time.time()
    mod_files_sync()


def comp_info():
    cfg = comp_load()
    ips = lan_ips()
    urls = [f"http://{ip}:{COMP_PORT}/compagnon" for ip in ips]
    qr = ""
    if urls:
        try:
            qr = qr_svg(urls[0])
        except Exception as exc:
            print("[Compagnon] QR code impossible :", exc)
    return {"lan": cfg["lan"], "running": _lan["httpd"] is not None, "error": _lan["error"],
            "port": COMP_PORT, "ips": ips, "urls": urls, "qr": qr, "code": cfg["code"],
            "windows": sys.platform == "win32", "version": SERVER_VERSION}


def firewall_allow():
    """Ajoute une regle au pare-feu Windows pour le port du mode compagnon (Windows demande l'accord admin)."""
    if sys.platform != "win32":
        return False, "Seulement sous Windows."
    try:
        import ctypes
        name = "FS25 Stream Dashboard - compagnon"
        args = (f'/c netsh advfirewall firewall delete rule name="{name}" & '
                f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow '
                f'protocol=TCP localport={COMP_PORT} profile=private,public')
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", args, None, 0)
        return (r > 32), ("Regle ajoutee." if r > 32 else "Refuse ou annule.")
    except Exception as exc:
        return False, str(exc)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def _send(self, body, ctype, status=200, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- mode compagnon : ce qui vient du reseau (telephone, tablette) est filtre ---
    def _is_remote(self):
        try:
            return not ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return True

    def _cookie(self, name):
        from http.cookies import SimpleCookie
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c[name].value if name in c else ""
        except Exception:
            return ""

    def _gate(self, path, post=False):
        """True si la requete peut continuer. Depuis le reseau : seulement la page compagnon et ses donnees."""
        if not self._is_remote():
            return True
        cfg = comp_load()
        fv = (not post) and re.fullmatch(r"/img/fv/[A-Za-z0-9_.-]+(/\d+)?", path) is not None   # 1.4.4 : photos des engins
        if not cfg["lan"] or (path not in (REMOTE_POST if post else REMOTE_GET) and not fv):
            self._send("Acces reserve a ce PC.".encode("utf-8"), "text/plain; charset=utf-8", status=403)
            return False
        if cfg["code"] and (fv or path in ("/data.json", "/fields.json", "/img/map", "/compagnon/notes", "/compagnon/ingame", "/compagnon/goto")) and self._cookie("fs25cp") != comp_token(cfg["code"]):
            self._send(b'{"needCode":true}', "application/json", status=401)
            return False
        return True

    def _json_body(self, limit=4000):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > limit:
            raise ValueError("trop gros")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _web_file(self, name):
        try:
            body = (WEB_ROOT / name).read_bytes()
        except OSError:
            self._send(b"Page introuvable.", "text/plain; charset=utf-8", status=404)
            return
        self._send(body, "text/html; charset=utf-8")

    def _comp_get(self, path):
        """Routes GET du mode compagnon. Renvoie True si la requete est traitee."""
        if path == "/" and self._is_remote():
            self.send_response(302)
            self.send_header("Location", "/compagnon")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if path in ("/compagnon", "/compagnon/"):
            self._web_file("compagnon.html")
            return True
        if path == "/compagnon/connexion":
            self._web_file("connexion.html")
            return True
        if path == "/compagnon/info":
            self._send(json.dumps(comp_info()).encode("utf-8"), "application/json; charset=utf-8")
            return True
        if path == "/compagnon/ingame":
            self._send(json.dumps(comp_load()["ingame"]).encode("utf-8"), "application/json")
            return True
        if path == "/compagnon/notes":
            from urllib.parse import parse_qs
            key = (parse_qs(urlparse(self.path).query).get("map") or [""])[0]
            m = notes_load().get(key, {})
            body = json.dumps({k: v.get("t", "") for k, v in m.items() if isinstance(v, dict)}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
            return True
        if path in ("/compagnon/icon.svg", "/stream/icon.svg", "/compagnon/icon.png", "/compagnon/icon-512.png"):
            name, ctype = {"/compagnon/icon.svg": ("compagnon-icon.svg", "image/svg+xml"),
                           "/stream/icon.svg": ("stream-icon.svg", "image/svg+xml"),      # 1.4.4 : icone du mode edition
                           "/compagnon/icon.png": ("compagnon-icon-180.png", "image/png"),
                           "/compagnon/icon-512.png": ("compagnon-icon-512.png", "image/png")}[path]
            try:
                self._send((WEB_ROOT / name).read_bytes(), ctype, cache="public, max-age=86400")
            except OSError:
                self._send(b"", ctype, status=404)
            return True
        if path == "/compagnon/manifest.json":                # ajout a l'ecran d'accueil du telephone
            man = {"name": "Compagnon - FS25 Stream & Companion", "short_name": "Compagnon FS25", "start_url": "/compagnon",
                   "display": "fullscreen", "display_override": ["fullscreen", "standalone"], "background_color": "#0c1210", "theme_color": "#0c1210",
                   "icons": [{"src": "/compagnon/icon.png", "sizes": "180x180", "type": "image/png"},
                             {"src": "/compagnon/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
            self._send(json.dumps(man).encode("utf-8"), "application/manifest+json")
            return True
        return False

    def _comp_post(self, path):
        """Routes POST du mode compagnon. Renvoie True si la requete est traitee."""
        if path == "/compagnon/login":
            try:
                code = str(self._json_body().get("code") or "").strip()
            except Exception:
                code = ""
            cfg = comp_load()
            if not cfg["code"] or code == cfg["code"]:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "11")
                if cfg["code"]:
                    self.send_header("Set-Cookie", f"fs25cp={comp_token(cfg['code'])}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                time.sleep(1.0)                           # freine les essais au hasard
                self._send(b'{"ok":false}', "application/json", status=403)
            return True
        if path == "/compagnon/notes":
            try:
                req = self._json_body(8000)
                notes = notes_set(req.get("map"), req.get("id"), req.get("text"))
                mod_files_sync()                                 # notes aussi dans le jeu
                self._send(json.dumps(notes, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(json.dumps({"error": str(exc)}).encode("utf-8"), "application/json; charset=utf-8", status=400)
            return True
        if path == "/compagnon/ingame":                       # affichage dans le jeu : interrupteurs A B C D
            try:
                req = self._json_body()
                cfg = comp_load()
                for k in INGAME_DEFAULT:
                    if k in req:
                        cfg["ingame"][k] = bool(req[k])
                if req.get("resetLayout"):
                    cfg["ingame"].pop("pos", None)
                    cfg["ingame"].pop("scale", None)
                    cfg["ingame"].pop("fieldSec", None)
                    cfg["ingame"].pop("alertSec", None)
                    cfg["ingame"].pop("notifSec", None)
                ingame_layout(req, cfg["ingame"])
                comp_save(cfg)
                mod_files_sync()
                self._send(json.dumps(cfg["ingame"]).encode("utf-8"), "application/json")
            except Exception as exc:
                self._send(json.dumps({"error": str(exc)}).encode("utf-8"), "application/json", status=400)
            return True
        if path == "/compagnon/goto":                         # "y aller" : champ, engin ou lieu, execute par le mod (cmd.xml)
            try:
                req = self._json_body()
                kind, target = req.get("kind"), str(req.get("target") or "")[:80]
                if kind not in ("field", "vehicle", "poi", "player") or not target:
                    raise ValueError("ordre inconnu")
                if comp_load()["ingame"].get("tp", True) is False:
                    self._send(b'{"ok":false,"error":"tp-off"}', "application/json", status=403)
                    return True
                cid = goto_write(kind, target)
                if cid is None:
                    self._send(b'{"ok":false,"error":"no-game"}', "application/json", status=409)
                else:
                    self._send(json.dumps({"ok": True, "id": cid}).encode("utf-8"), "application/json")
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"), "application/json", status=400)
            return True
        if path == "/compagnon/settings":
            try:
                req = self._json_body()
                cfg = comp_load()
                if "lan" in req:
                    cfg["lan"] = bool(req["lan"])
                if "code" in req:
                    code = str(req.get("code") or "").strip()
                    if code and not re.fullmatch(r"\d{4,8}", code):
                        raise ValueError("le code doit faire 4 a 8 chiffres")
                    cfg["code"] = code
                comp_save(cfg)
                lan_apply()
                time.sleep(0.2)
                self._send(json.dumps(comp_info()).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(json.dumps({"error": str(exc)}).encode("utf-8"), "application/json; charset=utf-8", status=400)
            return True
        if path == "/compagnon/firewall":
            ok, msg = firewall_allow()
            self._send(json.dumps({"ok": ok, "message": msg}).encode("utf-8"), "application/json; charset=utf-8")
            return True
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._gate(path):
            return
        if self._comp_get(path):
            return

        if path == "/data.json":
            data = read_data()
            mod_files_tick()
            if data is None:
                data = {"updatedAt": "—", "vehicle": "En attente…", "speedKmh": 0,
                        "implements": [], "active": False, "session": False}
            else:
                age = data_file_age()
                if age is not None:
                    data["fileAge"] = round(age, 1)
                    # fichier perime : jeu ferme / plante / plus de partie. Le mod ne reecrit data.json que si
                    # son contenu change : a l'arret, il peut rester identique longtemps -> on demande aussi a
                    # Windows si le jeu tourne encore (1.4.4).
                    if age > STALE_SECONDS and data.get("session") is not False and game_process_running() is not True:
                        data["session"] = False
                        data["stale"] = True
            if isinstance(data.get("vehicleInfo"), dict):
                data["vehicleInfo"]["imgv"] = image_version(data["vehicleInfo"])
            for it in data.get("implements") or []:
                if isinstance(it, dict):
                    it["imgv"] = image_version(it)
            if self._is_remote():                       # pas de chemins du PC vers le telephone
                for k in ("gameDir", "modsDir", "userDir", "fieldRaw"):
                    data.pop(k, None)
            self._send(json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")
            return

        if path == "/diag":
            try:
                body = diag_report().encode("utf-8")
            except Exception as exc:
                body = f"Erreur dans le diagnostic : {_exc(exc)}".encode("utf-8")
            self._send(body, "text/plain; charset=utf-8")
            return

        if path == "/version":
            self._send(json.dumps({"server": SERVER_VERSION}).encode("utf-8"), "application/json")
            return

        if path == "/dispositions":
            self._send(json.dumps(dispos_public(dispos_load())).encode("utf-8"), "application/json; charset=utf-8")
            return

        if path == "/layout.json":
            try:
                body = LAYOUT.read_bytes() if LAYOUT.is_file() else b"{}"
                json.loads(body)
            except Exception:
                body = b"{}"
            self._send(body, "application/json; charset=utf-8")
            return

        mv = re.fullmatch(r"/img/fv/([A-Za-z0-9_.-]+)(?:/(\d+))?", path)   # 1.4.4 : photo d'un engin de la ferme (ou de son outil n)
        if mv:
            data = read_data() or {}
            v = next((x for x in (data.get("farmVehicles") or []) if isinstance(x, dict) and str(x.get("id")) == mv.group(1)), None)
            o = v
            if v is not None and mv.group(2) is not None:
                tl = v.get("t") or []
                i = int(mv.group(2))
                o = tl[i] if i < len(tl) and isinstance(tl[i], dict) else None
            info = {"name": o.get("n"), "brand": o.get("b") or (v or {}).get("b"), "image": o.get("img"),
                    "xml": o.get("xml"), "mod": o.get("mod")} if o and (o.get("img") or o.get("xml")) else None
            png = image_png(info, data, allow_web=True) if info else None
            if png:
                self._send(png, "image/png", cache="public, max-age=3600")
            else:
                self._send(b"", "image/png", status=404)
            return

        mf = re.fullmatch(r"/img/fill/(vehicle|\d+)/(\d+)", path)
        if mf:
            data = read_data() or {}
            if mf.group(1) == "vehicle":
                owner = data.get("vehicleInfo")
            else:
                impls = data.get("implements") or []
                idx = int(mf.group(1))
                owner = impls[idx] if idx < len(impls) else None
            fills = (owner or {}).get("fills") or []
            fidx = int(mf.group(2))
            f = fills[fidx] if fidx < len(fills) else None
            _quiet.on = True
            try:
                png = image_png({"name": f.get("title"), "image": f.get("icon")}, data) if f and f.get("icon") else None
            finally:
                _quiet.on = False
            if png:
                self._send(png, "image/png", cache="public, max-age=3600")
            else:
                self._send(b"", "image/png", status=404)
            return

        if path == "/fields.json":                 # contours des champs (ecrits par le mod a cote de data.json)
            read_data()
            f = LAST_READ_PATH.with_name("fields.json") if LAST_READ_PATH else None
            body = f.read_bytes() if f and f.is_file() else b'{"fields":[]}'
            self._send(body, "application/json; charset=utf-8")
            return

        if path == "/img/map":
            jpg = map_jpg(read_data() or {})
            if jpg:
                self._send(jpg, "image/jpeg", cache="public, max-age=600")
            else:
                self._send(b"", "image/jpeg", status=404)
            return

        m = re.fullmatch(r"/img/(vehicle|implement/(\d+))", path)
        if m:
            data = read_data() or {}
            if m.group(1) == "vehicle":
                info = data.get("vehicleInfo")
            else:
                idx = int(m.group(2))
                impls = data.get("implements") or []
                info = impls[idx] if idx < len(impls) else None
            png = image_png(info, data, allow_web=True)
            if png:
                self._send(png, "image/png", cache="no-cache")
            else:
                self._send(b"", "image/png", status=404)
            return

        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _web_next(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2000:
                raise ValueError("trop gros")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            data = read_data() or {}
            if req.get("kind") == "vehicle":
                info = data.get("vehicleInfo")
            else:
                impls = data.get("implements") or []
                idx = int(req.get("index", 0))
                info = impls[idx] if 0 <= idx < len(impls) else None
            result = web_next(info) if info else {"ok": False, "reason": "Aucun element."}
            self._send(json.dumps(result).encode("utf-8"), "application/json")
        except Exception as exc:
            print("[Web] requete refusee :", exc)
            self._send(b'{"ok":false}', "application/json", status=400)

    def _perso_remove(self):
        """POST /perso/remove {kind, index} : revient a l'image d'origine (jeu / mod)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2000:
                raise ValueError("trop gros")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            data = read_data() or {}
            if req.get("kind") == "vehicle":
                info = data.get("vehicleInfo")
            else:
                impls = data.get("implements") or []
                idx = int(req.get("index", 0))
                info = impls[idx] if 0 <= idx < len(impls) else None
            if not info:
                raise ValueError("aucun element a cet emplacement")
            name = perso_remove(info)
            if name:
                print(f"[Perso] Image retiree : images_perso\\{name} ({info.get('name')}) -> images_perso\\_retirees")
                self._send(json.dumps({"ok": True}).encode(), "application/json")
            else:
                self._send(json.dumps({"ok": False, "reason": "Pas d'image perso pour cet element : c'est deja l'image d'origine."}).encode("utf-8"), "application/json")
        except Exception as exc:
            print("[Perso] retrait refuse :", exc)
            self._send(json.dumps({"ok": False, "reason": f"Echec : {exc}"}).encode("utf-8"), "application/json")

    def _perso(self):
        """POST /perso : image deposee sur une carte (fichier brut, ou JSON {url} d'une image du web)."""
        try:
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            length = int(self.headers.get("Content-Length", "0"))
            if length > 16 * 1024 * 1024:
                raise ValueError("fichier trop gros (16 Mo max)")
            body = self.rfile.read(length)
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "application/json":
                req = json.loads(body.decode("utf-8"))
                kind, index = req.get("kind"), int(req.get("index", 0))
                bg_on = bool(req.get("bg", True))
                blob = http_get(str(req.get("url", "")), 12)
            else:
                kind, index = (q.get("kind") or ["implement"])[0], int((q.get("index") or ["0"])[0])
                bg_on = (q.get("bg") or ["1"])[0] != "0"
                blob = body
            data = read_data() or {}
            if kind == "vehicle":
                info = data.get("vehicleInfo")
            else:
                impls = data.get("implements") or []
                info = impls[index] if 0 <= index < len(impls) else None
            if not info:
                raise ValueError("aucun element a cet emplacement")
            png = to_png(blob, "image deposee")
            if not png:
                self._send(json.dumps({"ok": False, "reason": "Image illisible (essaie un fichier PNG ou JPG)."}).encode(), "application/json")
                return
            status, original = "off", None
            if bg_on:
                try:
                    new_png, status = remove_uniform_background(png)
                except Exception as exc:                 # le detourage ne doit jamais empecher l'enregistrement
                    print(f"[Perso] detourage impossible : {exc}")
                    new_png, status = png, "kept"
                if status == "removed":
                    original, png = png, new_png
            stem = perso_save(info, png, original)
            print(f"[Perso] Image enregistree : images_perso\\{stem}.png ({info.get('name')}) - fond : {status}")
            self._send(json.dumps({"ok": True, "bg": status}).encode(), "application/json")
        except Exception as exc:
            print("[Perso] refuse :", exc)
            self._send(json.dumps({"ok": False, "reason": f"Echec : {exc}"}).encode("utf-8"), "application/json")

    def do_HEAD(self):
        if self._is_remote():
            self._send(b"", "text/plain", status=405)
            return
        return super().do_HEAD()

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._gate(path, post=True):
            return
        if self._comp_post(path):
            return
        if path == "/app/quit":                   # l'installateur demande a l'appli de se fermer
            self._send(b"{}", "application/json")
            if ON_QUIT:
                ON_QUIT()
            return
        if path == "/app/accueil":                # appli relancee alors qu'elle tourne deja : rouvrir l'accueil
            ok = ON_ACCUEIL is not None
            self._send(json.dumps({"ok": ok}).encode(), "application/json")
            if ok:
                ON_ACCUEIL()
            return
        if path == "/web/next":
            self._web_next()
            return
        if path == "/perso":
            self._perso()
            return
        if path == "/perso/remove":
            self._perso_remove()
            return
        if path == "/dispositions":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 40000:
                    raise ValueError("trop gros")
                out = dispos_action(json.loads(self.rfile.read(length).decode("utf-8") or "{}"))
                self._send(json.dumps(out).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(json.dumps({"error": str(exc)}).encode("utf-8"), "application/json; charset=utf-8", status=400)
            return
        if path != "/layout.json":
            self._send(b"", "text/plain", status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20000:
                raise ValueError("trop gros")
            layout = clean_layout(json.loads(self.rfile.read(length).decode("utf-8")))
            tmp = LAYOUT.with_suffix(".tmp")
            tmp.write_text(json.dumps(layout, indent=1), encoding="utf-8")
            tmp.replace(LAYOUT)
            self._send(b"{}", "application/json")
        except Exception as exc:
            print("[Layout] refuse :", exc)
            self._send(b"{}", "application/json", status=400)

    def log_message(self, fmt, *args):
        pass  # evite de noyer la console avec les requetes toutes les 500 ms


def another_instance_running():
    """Sous Windows, deux serveurs peuvent se lancer sur le meme port sans erreur :
    l'ancien continue alors de repondre et le nouveau ne recoit rien."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.7):
            return True
    except OSError:
        return False


ON_QUIT = None          # defini par l'application : fermeture demandee via POST /app/quit
ON_ACCUEIL = None       # defini par l'application : rouvrir la fenetre d'accueil (POST /app/accueil)


def make_server():
    return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)


if __name__ == "__main__":
    if another_instance_running():
        print(f"ERREUR : un dashboard tourne deja sur http://127.0.0.1:{PORT}.")
        print("Ferme l'autre fenetre noire (ou termine le processus python) puis relance.")
        print("Astuce : dans cmd, tape  netstat -ano | findstr :%d  pour voir quel processus ecoute." % PORT)
        raise SystemExit(1)
    print(f"Dashboard v{SERVER_VERSION} : http://127.0.0.1:{PORT}")
    print("Fichier de donnees attendu :", DATA)
    print("Pillow :", "OK" if Image else "MANQUANT (py -m pip install pillow)")
    _wc = load_config()
    print("Images web :", f"activees ({_wc.get('provider')})" if _wc.get("enabled") else "desactivees (config.json)")
    print("Garde cette fenetre ouverte pendant le stream.")
    lan_apply()
    print(f"Mode compagnon : http://127.0.0.1:{PORT}/compagnon/connexion")
    make_server().serve_forever()
