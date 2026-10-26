"""FS25 Stream & Companion by leluxo (ex FS25 Stream Dashboard) - application Windows.

Au lancement :
1. installe / met a jour le mod dans Documents\\My Games\\FarmingSimulator2025\\mods
2. demarre le serveur du dashboard (http://127.0.0.1:8765) et, si active, le mode compagnon (Wi-Fi, port 8766)
3. ouvre la fenetre d'accueil (Stream / Compagnon / Aide) et affiche une icone pres de l'horloge
   (clic : rouvre l'accueil ; clic droit : menu)
Les fichiers de l'utilisateur (disposition, reglages, images perso, cache) sont dans
Documents\\FS25 Stream Dashboard.
"""
import json, os, re, shutil, socket, sys, threading, time, webbrowser, zipfile
from pathlib import Path

APP_NAME = "FS25 Stream Dashboard"          # NE PAS CHANGER : dossier des reglages, cle "Lancer avec Windows"
DISPLAY_NAME = "FS25 Stream & Companion"    # nom affiche
APP_VERSION = "1.4.4"
GITHUB_LATEST = "https://api.github.com/repos/leluxo75/FS25-Stream-Companion/releases/latest"   # mises a jour automatiques
DISCORD_URL = "https://discord.gg/xhsWj2e5z3"   # serveur Discord "La Ferme a Luxo" (lien permanent)
DONATE_URL = "https://ko-fi.com/leluxo"
MOD_FILE = "FS25_StreamDashboard.zip"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"

# Dossier des fichiers embarques (PyInstaller les extrait dans sys._MEIPASS)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = BUNDLE / "web"
MOD_ZIP = BUNDLE / "mod" / MOD_FILE
ICON_FILE = BUNDLE / "app.ico"
LOGO_FILE = WEB_DIR / "accueil-logo.png"   # logo de la fenetre d'accueil (fond transparent)


# ------------------------------------------------------------------ outils Windows
def message(text, error=False):
    """Petite fenetre de message Windows (rien sur les autres systemes)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, DISPLAY_NAME, 0x10 if error else 0x40)
            return
        except Exception:
            pass
    print(("ERREUR : " if error else "") + text)


def ask(text, flags):
    """Question Windows ; renvoie le code du bouton (1 OK, 2 Annuler, 6 Oui, 7 Non)."""
    if sys.platform == "win32":
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, text, DISPLAY_NAME, flags | 0x40000)
        except Exception:
            pass
    return 2


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
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"


def port_in_use():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.7):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------ ou est le jeu ? ou sont les mods ?
GAME = "FarmingSimulator2025"


def profile_candidates(docs):
    """Dossiers possibles du profil du jeu (Documents\\My Games\\FarmingSimulator2025), OneDrive compris."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    roots = [docs, home / "Documents"]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]) / "Documents")
    try:
        roots += [d / "Documents" for d in home.iterdir() if d.is_dir() and d.name.lower().startswith("onedrive")]
    except OSError:
        pass
    out = []
    for r in roots:
        c = r / "My Games" / GAME
        if c not in out:
            out.append(c)
    return out


def active_profile(docs):
    """Le profil reellement utilise par le jeu : celui dont le log.txt est le plus recent."""
    existing = [c for c in profile_candidates(docs) if c.is_dir()]
    with_log = [c for c in existing if (c / "log.txt").is_file()]
    if with_log:
        return max(with_log, key=lambda c: (c / "log.txt").stat().st_mtime)
    return existing[0] if existing else None


def mods_dir_from_log(profile):
    """Le jeu ecrit dans log.txt la ligne 'Mod Directory: ...' : c'est le dossier qu'il utilise vraiment."""
    try:
        with open(profile / "log.txt", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                m = re.search(r"Mod Directory:\s*(.+?)\s*$", line)
                if m:
                    return Path(m.group(1).replace("/", os.sep))
                if i > 3000:
                    break
    except OSError:
        pass
    return None


def mods_dir_from_settings(profile):
    """Dossier des mods choisi dans les options du jeu (gameSettings.xml), s'il est active."""
    try:
        xml = (profile / "gameSettings.xml").read_text(encoding="utf-8", errors="replace")
        tag = re.search(r"<modsDirectoryOverride\b[^>]*>", xml)
        if tag and re.search(r'active\s*=\s*"true"', tag.group(0), re.I):
            d = re.search(r'directory\s*=\s*"([^"]+)"', tag.group(0))
            if d:
                return Path(d.group(1))
    except OSError:
        pass
    return None


def load_app_settings(data_dir):
    try:
        return json.loads((data_dir / "app.json").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_app_settings(data_dir, cfg):
    try:
        (data_dir / "app.json").write_text(json.dumps(cfg, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def find_mods_dir(docs, data_dir):
    """(dossier mods, comment il a ete trouve) ou (None, raison)."""
    chosen = load_app_settings(data_dir).get("modsDir")
    if chosen and Path(chosen).is_dir():
        return Path(chosen), "choisi a la main"
    profile = active_profile(docs)
    if profile is None:
        return None, "profil du jeu introuvable"
    d = mods_dir_from_log(profile)
    if d and d.is_dir():
        return d, "journal du jeu (log.txt)"
    d = mods_dir_from_settings(profile)
    if d and d.is_dir():
        return d, "options du jeu (gameSettings.xml)"
    return profile / "mods", "dossier par defaut"


def ask_mods_dir(data_dir, reason="", parent=None):
    """Demande le dossier des mods a l'utilisateur (fenetre de choix de dossier). Le retient pour la suite.
    parent : fenetre tkinter deja ouverte (fenetre d'accueil) ; sinon une fenetre temporaire est creee."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = parent
        if root is None:
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        message((reason + "\n\n" if reason else "") +
                "Choisis le dossier \"mods\" de Farming Simulator 25 (celui ou tu mets tes mods).")
        path = filedialog.askdirectory(title="Dossier mods de Farming Simulator 25", mustexist=True, parent=root)
        if parent is None:
            root.destroy()
    except Exception as exc:
        print("[Mods] choix du dossier impossible :", exc)
        return None
    if not path:
        return None
    cfg = load_app_settings(data_dir); cfg["modsDir"] = path; save_app_settings(data_dir, cfg)
    print("[Mods] dossier choisi :", path)
    return Path(path)


# ------------------------------------------------------------------ installation du mod
def mod_version(zip_path):
    """Version lue dans modDesc.xml du ZIP, en tuple (1, 0, 0, 0), ou None."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            xml = z.read("modDesc.xml").decode("utf-8", "replace")
        m = re.search(r"<version>\s*([\d.]+)\s*</version>", xml)
        return tuple(int(x) for x in m.group(1).split(".") if x != "") if m else None
    except Exception:
        return None


DATA_DIR = None      # dossier perso de l'appli (defini au demarrage)


def remember_install(mods):
    """Retient chaque dossier mods ou le mod a ete mis (pour la desinstallation)."""
    if DATA_DIR is None:
        return
    cfg = load_app_settings(DATA_DIR)
    lst = cfg.get("installedIn") or []
    if str(mods) not in lst:
        lst.append(str(mods)); cfg["installedIn"] = lst; save_app_settings(DATA_DIR, cfg)


MOD_FAIL = "Impossible"      # debut des textes d'echec renvoyes par install_mod


def same_file(a, b):
    """Vrai si les deux fichiers ont exactement le meme contenu."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        import hashlib
        h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        return h(a) == h(b)
    except OSError:
        return False


def install_mod(mods):
    """Copie le mod embarque dans le dossier mods s'il est absent, plus ancien, ou DIFFERENT avec le meme numero
    (1.4.4 : le 25/10/2026, deux zips 1.4.3.0 differents ont bloque des joueurs a l'entree du serveur).
    Un mod de numero plus haut (version de test) est laisse en place.
    Renvoie un texte pour l'utilisateur (ou None si rien a dire). Les echecs commencent par MOD_FAIL."""
    if not MOD_ZIP.is_file():
        return None
    remember_install(mods)
    try:
        mods.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return (f"{MOD_FAIL} de creer le dossier des mods :\n{mods}\n\nRaison : {e.strerror or e}\n"
                "Le dossier est peut-etre protege (antivirus, \"Acces controle aux dossiers\" de Windows) "
                "ou ce n'est pas le bon dossier.")
    target = mods / MOD_FILE
    new_v = mod_version(MOD_ZIP)
    replaced_same = False
    if target.is_file():
        old_v = mod_version(target)
        legacy = old_v is not None and old_v[0] >= 4      # versions de test 4.x d'avant la v1.0 : a remplacer
        if old_v is not None and new_v is not None and not legacy:
            if old_v > new_v:
                return None                               # version plus recente (test) : on n'y touche pas
            if old_v == new_v:
                if same_file(target, MOD_ZIP):
                    return None                           # deja a jour
                replaced_same = True                      # meme numero, contenu different : on remet l'officiel
    target_existed = target.is_file()
    tmp = target.with_name(MOD_FILE + ".tmp")
    try:
        shutil.copyfile(MOD_ZIP, tmp)
        os.replace(tmp, target)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        why = e.strerror or str(e)
        hint = ("Farming Simulator 25 est peut-etre ouvert (il bloque le fichier) : ferme le jeu puis reessaie."
                if getattr(e, "winerror", None) in (32, 33) else
                "Un antivirus ou l'\"Acces controle aux dossiers\" de Windows bloque peut-etre la copie. "
                "Ferme aussi le jeu s'il est ouvert.")
        return f"{MOD_FAIL} d'installer le mod dans :\n{mods}\n\nRaison : {why}\n{hint}"
    v = ".".join(str(x) for x in new_v[:3]) if new_v else ""
    if replaced_same:
        return (f"Le mod (v{v}) a ete remplace par la version officielle (ton fichier etait different).\n"
                "Relance Farming Simulator 25 s'il est ouvert.\n"
                "MULTI : le serveur doit avoir exactement ce fichier.")
    if target_existed:
        return (f"Mod mis a jour (v{v}). Relance Farming Simulator 25 s'il est ouvert.\n"
                "MULTI : pense a mettre aussi ce mod a jour sur ton serveur, sinon tu ne pourras plus le rejoindre.")
    return (f"Mod installe (v{v}) dans :\n{mods}\n\n"
            "Relance Farming Simulator 25 s'il est ouvert, et active le mod dans ta sauvegarde.")


def install_mod_interactive(mods, data_dir):
    """Installe le mod ; si ca rate, fenetre claire avec la raison et 3 choix (reessayer / autre dossier / plus tard).
    Renvoie (dossier mods final, texte)."""
    note = install_mod(mods) if mods else None
    tries = 0
    while note and note.startswith(MOD_FAIL) and tries < 5:
        tries += 1
        print("[Mod]", note.replace("\n", " "))
        r = ask(note + "\n\nOui = reessayer\nNon = choisir un autre dossier \"mods\"\nAnnuler = plus tard "
                       "(clic droit sur l'icone > \"Choisir le dossier des mods\")", 0x3 | 0x30)   # Oui/Non/Annuler
        if r == 6:                                   # Oui : reessayer
            note = install_mod(mods)
        elif r == 7:                                 # Non : autre dossier
            d = ask_mods_dir(data_dir, "Choisis le dossier \"mods\" de Farming Simulator 25.")
            if d:
                mods = d
                note = install_mod(mods)
        else:
            break
    return mods, note


def watch_game(docs, data_dir, state, notify):
    """Apres chaque lancement du jeu (log.txt mis a jour) : verifie le dossier des mods reellement utilise
    et que le mod y est. Sinon, l'installe au bon endroit (ou previent)."""
    seen = {}
    while True:
        time.sleep(15)
        try:
            profile = active_profile(docs)
            if profile is None:
                continue
            log = profile / "log.txt"
            mt = log.stat().st_mtime if log.is_file() else 0
            if not mt or seen.get(str(log)) == mt or mt < state["start"]:
                continue
            text = log.read_text(encoding="utf-8", errors="replace")
            if "Mod Directory:" not in text or "Available mod:" not in text:
                continue                                  # le jeu n'a pas fini de lister les mods
            seen[str(log)] = mt
            real = mods_dir_from_log(profile)
            if "FS25_StreamDashboard" in text:
                continue                                  # le jeu voit le mod : tout va bien
            if real and real.is_dir() and real != state.get("mods"):
                note = install_mod(real)
                state["mods"] = real
                if note and note.startswith(MOD_FAIL):
                    print(f"[Mods] le jeu utilise {real} : installation ratee")
                    notify("Le jeu utilise un autre dossier de mods.\n\n" + note)
                else:
                    print(f"[Mods] le jeu utilise {real} : mod installe la")
                    notify("Le jeu utilise un autre dossier de mods :\n" + str(real) +
                           "\nLe mod y a ete installe. Relance Farming Simulator 25.")
            else:
                notify("Le jeu n'a pas trouve le mod FS25 Stream & Companion.\n"
                       "Clic droit sur l'icone > \"Choisir le dossier des mods\".")
        except Exception as exc:
            print("[Mods] verification :", exc)


# ------------------------------------------------------------------ lancement avec Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_enabled():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --silent')
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# ------------------------------------------------------------------ desinstallation
def is_our_mod(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as z:
            return b"StreamDashboard" in z.read("modDesc.xml") or "scripts/StreamDashboard.lua" in z.namelist()
    except Exception:
        return False


def installed_uninstaller():
    """Version installee (installateur Windows) : chemin de son desinstalleur, sinon None (version portable)."""
    if not getattr(sys, "frozen", False):
        return None
    u = Path(sys.executable).parent / "unins000.exe"
    return u if u.is_file() else None


def uninstall(docs, data_dir, state, stop_all, from_installer=False):
    """Supprime proprement : lancement auto, mod (tous les dossiers), donnees du mod, reglages (au choix), l'appli.
    from_installer : appele par le desinstalleur Windows (qui supprime ensuite l'appli lui-meme)."""
    if not from_installer:
        unins = installed_uninstaller()
        if unins is not None:                         # appli installee : on passe par la desinstallation Windows
            import subprocess
            subprocess.Popen([str(unins)], close_fds=True)
            stop_all(); os._exit(0)
        if ask("Desinstaller FS25 Stream & Companion ?\n\n"
               "- le mod sera retire de tes dossiers mods\n"
               "- ses donnees seront effacees\n"
               "- l'application se supprimera a la fermeture\n\n"
               "Ferme Farming Simulator 25 avant de continuer.", 0x01 | 0x30) != 1:
            return
    keep = ask("Garder tes reglages et tes images perso ?\n(utile si tu reinstalles plus tard)\n\n"
               f"Dossier : {data_dir}", 0x04 | 0x20) == 6
    done, failed = [], []

    # 1. lancement avec Windows
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_NAME)
            done.append("lancement avec Windows desactive")
        except OSError:
            pass

    # 2. le mod, dans tous les dossiers connus
    dirs = set(load_app_settings(data_dir).get("installedIn") or [])
    if state.get("mods"):
        dirs.add(str(state["mods"]))
    for prof in profile_candidates(docs):
        dirs.add(str(prof / "mods"))
        d = mods_dir_from_log(prof) if prof.is_dir() else None
        if d:
            dirs.add(str(d))
    for d in sorted(dirs):
        z = Path(d) / MOD_FILE
        if z.is_file() and is_our_mod(z):
            try:
                z.unlink(); done.append(f"mod retire : {z}")
            except OSError:
                failed.append(f"mod non retire (jeu ouvert ?) : {z}")

    # 3. donnees ecrites par le mod
    for prof in profile_candidates(docs):
        ms = prof / "modSettings" / "FS25_StreamDashboard"
        if ms.is_dir():
            shutil.rmtree(ms, ignore_errors=True)
            (failed if ms.exists() else done).append(f"donnees du mod {'non ' if ms.exists() else ''}effacees : {ms}")

    # 4. arret du dashboard, puis reglages perso
    stop_all()
    if keep:
        done.append(f"reglages gardes : {data_dir}")
    else:
        try:
            sys.stdout.flush(); sys.stdout.close()
        except Exception:
            pass
        sys.stdout = sys.stderr = open(os.devnull, "w")
        shutil.rmtree(data_dir, ignore_errors=True)
        (failed if data_dir.exists() else done).append(f"reglages {'non ' if data_dir.exists() else ''}effaces : {data_dir}")

    # 5. l'application elle-meme (apres sa fermeture) - version portable seulement
    if not from_installer and getattr(sys, "frozen", False) and sys.platform == "win32":
        import subprocess
        exe = sys.executable
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 4 >nul & del /f /q "{exe}"',
                         creationflags=0x08000000 | 0x00000008, close_fds=True)      # sans fenetre, detache
        done.append(f"application supprimee : {exe}")

    text = "Desinstallation terminee.\n\n" + "\n".join("OK  " + x for x in done)
    if failed:
        text += "\n\nA faire a la main :\n" + "\n".join("!!  " + x for x in failed)
    text += ("\n\nSi une sauvegarde utilisait le mod, le jeu signalera qu'il manque : c'est sans gravite."
             "\nSur un serveur dedie, retire aussi le mod depuis son panneau.")
    message(text)
    if from_installer:
        return
    os._exit(0)


def stop_running_instance():
    """Demande a une appli deja lancee de se fermer (avant desinstallation / mise a jour)."""
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/app/quit", data=b"{}", method="POST"), timeout=2)
    except Exception:
        pass
    for _ in range(20):
        if not port_in_use():
            return True
        time.sleep(0.25)
    return not port_in_use()


# ------------------------------------------------------------------ programme
def main():
    silent = "--silent" in sys.argv          # lancement avec Windows : pas d'ouverture du navigateur

    if "--quit" in sys.argv:                 # installateur : ferme l'appli si elle tourne
        stop_running_instance()
        return
    if "--uninstall" in sys.argv:            # appele par le desinstalleur Windows
        stop_running_instance()
        docs = documents_dir()
        data_dir = docs / APP_NAME
        mods, _how = find_mods_dir(docs, data_dir) if data_dir.is_dir() else (None, "")
        uninstall(docs, data_dir, {"mods": mods}, lambda: None, from_installer=True)
        return

    # deja lance ? on rouvre la fenetre d'accueil de l'appli qui tourne (sinon la page)
    if port_in_use():
        if not silent and not reopen_running_accueil():
            open_page("edit")
        return

    global DATA_DIR
    docs = documents_dir()
    data_dir = docs / APP_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR = data_dir

    # journal (pas de console dans l'appli)
    log = open(data_dir / "journal.txt", "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    print(f"{DISPLAY_NAME} - demarrage {time.strftime('%Y-%m-%d %H:%M:%S')}")

    mods, how = find_mods_dir(docs, data_dir)
    if mods is None or not (mods.is_dir() or mods.parent.is_dir()):
        mods = ask_mods_dir(data_dir, "Je ne trouve pas Farming Simulator 25 (" + (how or "") + ").")
        how = "choisi a la main"
    print(f"[Mods] dossier : {mods} ({how})")
    if mods:
        mods, note = install_mod_interactive(mods, data_dir)
    else:
        note = ("Dossier des mods non choisi : le mod n'est pas installe.\n"
                "Clic droit sur l'icone > \"Choisir le dossier des mods\".")
    state = {"start": time.time(), "mods": mods}
    profile = active_profile(docs)
    if profile is not None:
        os.environ["FS25_DASH_GAME_DIR"] = str(profile)
    if note:
        print("[Mod]", note)
        if not note.startswith(MOD_FAIL):        # installe / mis a jour : fenetre bien visible (relancer le jeu, serveur)
            threading.Thread(target=message, args=(note,), daemon=True).start()

    os.environ["FS25_DASH_WEB_DIR"] = str(WEB_DIR)
    os.environ["FS25_DASH_APP_VERSION"] = APP_VERSION
    os.environ["FS25_DASH_DATA_DIR"] = str(data_dir)
    import server                                    # lit les deux variables ci-dessus
    try:
        httpd = server.make_server()
    except OSError as e:
        message(f"Le dashboard n'a pas pu demarrer (port {PORT} occupe ?)\n{e}", error=True)
        return
    print(f"Serveur v{server.SERVER_VERSION} : {URL}")
    print("Donnees du jeu :", server.DATA)
    print("Dossier perso :", data_dir)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        server.lan_apply()                           # mode compagnon : acces Wi-Fi si active
    except Exception as e:
        print("[Compagnon] demarrage impossible :", e)

    def notify(text):
        print("[Info]", text.replace("\n", " "))
        threading.Thread(target=message, args=(text,), daemon=True).start()
    threading.Thread(target=watch_game, args=(docs, data_dir, state, notify), daemon=True).start()

    def quit_app():
        httpd.shutdown()

    def remote_quit():                        # demande de fermeture (installateur / desinstalleur)
        threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
    server.ON_QUIT = remote_quit

    # fenetre d'accueil (tkinter, fil principal) + icone pres de l'horloge (a cote)
    upd = update_api(lambda: os._exit(0))          # mise a jour : l'installateur relance l'appli ensuite
    ui = make_accueil(data_dir, state, server, update=upd)
    first = "rien" if silent else startup_choice(data_dir, ui)
    if ui is not None:
        server.ON_ACCUEIL = ui.show
        try:
            icon = run_tray(data_dir, note, quit_app, docs, state, ui=ui)
        except Exception as e:
            print("[Icone] indisponible :", e)
            icon = None
        if first in ("stream", "les2"):
            open_page("edit")
        if first in ("compagnon", "les2"):
            open_page("compagnon")
        try:
            ui.run(show_now=(first == "accueil"))       # rend la main quand on quitte
        except Exception as e:
            print("[Accueil] erreur :", e)
        finally:
            try:
                icon and icon.stop()
            except Exception:
                pass
            try:
                quit_app()
            except Exception:
                pass
        return

    if first in ("stream", "accueil", "les2"):        # pas de fenetre possible : comme avant
        open_page("edit")
    if first in ("compagnon", "les2"):
        open_page("compagnon")
    try:
        run_tray(data_dir, note, quit_app, docs, state)
    except Exception as e:                            # pas d'icone possible : on reste lance quand meme
        print("[Icone] indisponible :", e)
        if note:
            message(note)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            quit_app()


# ------------------------------------------------------------------ fenetre d'accueil
def reopen_running_accueil():
    """Appli deja lancee : lui demande de rouvrir sa fenetre d'accueil. False si elle ne sait pas (ancienne version)."""
    import urllib.request
    try:
        r = urllib.request.urlopen(urllib.request.Request(URL + "/app/accueil", data=b"{}", method="POST"), timeout=2)
        return bool(json.loads(r.read().decode("utf-8") or "{}").get("ok"))
    except Exception:
        return False


def accueil_prefs(data_dir):
    p = load_app_settings(data_dir).get("accueil") or {}
    choice = p.get("open") if p.get("open") in ("stream", "compagnon", "les2", "rien") else "stream"
    return {"skip": bool(p.get("skip")), "open": choice}


def startup_choice(data_dir, ui):
    """Ce qu'on ouvre au lancement : "accueil", "stream", "compagnon", "les2" (1.4.4) ou "rien"."""
    p = accueil_prefs(data_dir)
    if not p["skip"]:
        return "accueil"
    return p["open"]


def game_session_active(data_path):
    """Partie en cours = le jeu tourne ET le mod dit qu'une partie est chargee (data.json "session": true).
    Avant (1.4.3) : data.json modifie il y a moins de 6 s. Mais le mod ne reecrit le fichier que si son contenu
    change (et FS25 ne donne pas l'heure aux mods) : a l'arret, l'accueil croyait le jeu ferme."""
    try:
        p = Path(data_path)
        age = time.time() - p.stat().st_mtime
        d = json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return False
    if not d.get("session"):
        return False                          # menu principal / hors partie (le mod ecrit session = false)
    import server
    running = server.game_process_running()
    if running is None:                       # impossible de lister les processus : on se fie au fichier
        return age < 120
    return running


def accueil_status(state, server):
    """Les 3 pastilles d'etat : mod, partie en cours, compagnon Wi-Fi."""
    out = []
    want = mod_version(MOD_ZIP)
    mods = state.get("mods")
    have = mod_version(Path(mods) / MOD_FILE) if mods else None
    if have is None:
        out.append(("warn", "mod absent"))
    elif want is not None and have < want:
        out.append(("warn", "mod à mettre à jour"))
    else:
        out.append(("ok", "mod à jour"))
    out.append(("ok", "partie en cours") if game_session_active(server.DATA) else ("off", "en attente du jeu"))
    out.insert(0, out.pop())
    try:
        lan_on = bool(server.comp_load().get("lan")) and server._lan.get("httpd") is not None
    except Exception:
        lan_on = False
    out.append(("ok", "Wi-Fi") if lan_on else ("off", "Wi-Fi coupé"))
    return out



# ------------------------------------------------------------------ lancer le jeu (fenetre d'accueil)
# Plateforme choisie une fois par le joueur (gardee dans les reglages, modifiable ensuite).
GAME_PLATFORMS = [("steam", "Steam"), ("epic", "Epic Games"), ("giants", "Version Giants (site officiel)"), ("ms", "Microsoft Store / Xbox")]
STEAM_APP_ID = "2300320"                      # Farming Simulator 25 sur Steam
GAME_EXE = "FarmingSimulator2025.exe"


def game_prefs(data_dir):
    g = load_app_settings(data_dir).get("game") or {}
    return g if isinstance(g, dict) else {}


def remember_game_dir(data_dir, server):
    """Garde le dossier du jeu vu pendant une partie (le mod l'ecrit dans data.json) : utile pour la version Giants."""
    try:
        d = (server.read_data() or {}).get("gameDir")
    except Exception:
        d = None
    if d and Path(d, GAME_EXE).is_file():
        cfg = load_app_settings(data_dir)
        g = cfg.get("game") if isinstance(cfg.get("game"), dict) else {}
        if g.get("seenDir") != d:
            g["seenDir"] = d
            cfg["game"] = g
            save_app_settings(data_dir, cfg)


def _epic_uri():
    """Lien de lancement Epic, d'apres les fichiers du lanceur Epic (ProgramData/Epic/EpicGamesLauncher/Data/Manifests/*.item)."""
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    for f in base.glob("*.item") if base.is_dir() else []:
        try:
            m = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        name = str(m.get("DisplayName") or "") + " " + str(m.get("LaunchExecutable") or "")
        if "Farming Simulator 25" in name or GAME_EXE.lower() in name.lower():
            ns, item, app = m.get("CatalogNamespace"), m.get("CatalogItemId"), m.get("AppName")
            if ns and item and app:
                return f"com.epicgames.launcher://apps/{ns}%3A{item}%3A{app}?action=launch&silent=true"
    return None


def _giants_exe(g):
    cands = [g.get("seenDir")]
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env)
        if root:
            cands.append(str(Path(root) / "Farming Simulator 2025"))
    for c in cands:
        if c and Path(c, GAME_EXE).is_file():
            return Path(c, GAME_EXE)
    return None


def _start_exe(path):
    import subprocess
    subprocess.Popen([str(path)], cwd=str(Path(path).parent), close_fds=True)


def launch_game(data_dir):
    """Lance FS25 selon la plateforme choisie. Renvoie (ok, message) ; message "need-exe" = demander le fichier du jeu."""
    g = game_prefs(data_dir)
    kind = g.get("kind")
    if sys.platform != "win32":
        return False, "lancement possible seulement sous Windows"
    if kind == "steam":
        os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
        return True, ""
    if kind == "epic":
        uri = _epic_uri()
        if uri is None:
            return False, "Farming Simulator 25 introuvable dans le lanceur Epic Games."
        os.startfile(uri)
        return True, ""
    if kind == "giants":
        exe = _giants_exe(g)
        if exe is None:
            return False, "need-exe"
        _start_exe(exe)
        return True, ""
    if kind == "ms":
        import subprocess
        ps = ("$p = Get-AppxPackage *Farming*Simulator*25* | Select-Object -First 1; if (-not $p) { exit 3 }; "
              "$id = (Get-AppxPackageManifest $p).Package.Applications.Application.Id | Select-Object -First 1; "
              "Start-Process ('shell:AppsFolder\\' + $p.PackageFamilyName + '!' + $id)")
        r = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                           capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 3:
            return False, "Farming Simulator 25 introuvable dans le Microsoft Store / Xbox."
        return (r.returncode == 0), ("" if r.returncode == 0 else (r.stderr or b"").decode("utf-8", "ignore")[:300])
    if kind == "exe":
        p = g.get("path")
        if p and Path(p).is_file():
            _start_exe(p)
            return True, ""
        return False, "need-exe"
    return False, "Choisis d'abord la plateforme (menu à droite de la barre verte)."


def game_api(data_dir, server):
    labels = dict(GAME_PLATFORMS)

    def get():
        g = game_prefs(data_dir)
        k = g.get("kind")
        if k == "exe" and g.get("path"):
            return ("exe", Path(g["path"]).name)
        short = {"steam": "Steam", "epic": "Epic", "giants": "Giants", "ms": "Xbox"}
        return (k, short[k]) if k in short else None

    def set_(kind, path=None):
        cfg = load_app_settings(data_dir)
        g = cfg.get("game") if isinstance(cfg.get("game"), dict) else {}
        g["kind"] = kind
        if kind == "exe" and path:
            g["path"] = path
        cfg["game"] = g
        save_app_settings(data_dir, cfg)

    def launch():
        remember_game_dir(data_dir, server)
        return launch_game(data_dir)

    return {"get": get, "set": set_, "launch": launch, "choices": GAME_PLATFORMS}


# ------------------------------------------------------------------ mise a jour automatique (GitHub)
def _ver_key(v):
    """'v1.4.3' / '1.4.0c' / '1.4.3-dev2' -> (1, 4, 3, 'c') pour comparer."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?([a-z]?)", str(v or ""))
    if not m:
        return (0, 0, 0, "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4) or "")


def _clean_notes(md):
    """Notes de version (markdown GitHub) -> texte simple pour la fenetre."""
    out = []
    for line in str(md or "").replace("\r", "").split("\n"):
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        out.append(line.rstrip())
    txt = "\n".join(out).strip()
    return re.sub(r"\n{3,}", "\n\n", txt)[:4000]


def _release_mod_change(rel):
    """Version du mod de la release si elle est PLUS RECENTE que le mod de l'appli installee, sinon None (1.4.4).
    Cherchee dans le nom du zip du mod joint a la release, puis dans les notes (\"mod 1.4.4.0\")."""
    found = None
    for a in rel.get("assets") or []:
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", str(a.get("name", ""))) if "streamdashboard" in str(a.get("name", "")).lower() else None
        if m:
            found = m.group(1)
            break
    if not found:
        m = re.search(r"\bmod\s*(?:v)?\s*(\d+\.\d+\.\d+\.\d+)", str(rel.get("body") or ""), re.I)
        found = m.group(1) if m else None
    if not found:
        return None
    mine = mod_version(MOD_ZIP)
    new = tuple(int(x) for x in found.split("."))
    return found if (mine is None or new > tuple(mine)) else None


def update_api(quit_now):
    """Verifie GitHub (au demarrage puis toutes les 6 h). Si une version plus recente existe : la fenetre d'accueil
    affiche un bandeau. Mettre a jour = telecharger l'installateur, le lancer (mode silencieux : il ferme l'appli,
    installe, puis la relance). Les reglages (Documents\\FS25 Stream Dashboard) ne sont pas touches."""
    import urllib.request
    st = {"status": "none", "version": None, "notes": "", "pct": 0, "error": "", "mod": None}
    lock = threading.Lock()
    rel = {}
    test = os.environ.get("FS25SC_UPDATE_TEST") == "1"         # pour essayer le bandeau avec la version actuelle

    def fetch(url, timeout=15):
        req = urllib.request.Request(url, headers={"User-Agent": DISPLAY_NAME + "/" + APP_VERSION,
                                                   "Accept": "application/vnd.github+json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def check():
        try:
            with fetch(GITHUB_LATEST) as r:
                j = json.loads(r.read().decode("utf-8"))
            tag = j.get("tag_name") or j.get("name") or ""
            asset = next((a for a in j.get("assets") or [] if str(a.get("name", "")).lower().endswith(".exe")), None)
            newer = _ver_key(tag) > _ver_key(APP_VERSION) or (test and asset is not None)
            print(f"[MAJ] derniere version sur GitHub : {tag} (installee : {APP_VERSION})" + (" -> mise a jour disponible" if newer and asset else ""))
            if newer and asset and str(asset.get("browser_download_url", "")).startswith("https://github.com/"):
                with lock:
                    rel.update(url=asset["browser_download_url"], name=asset.get("name") or "installation.exe", size=int(asset.get("size") or 0))
                    if st["status"] in ("none", "error"):
                        st.update(status="available", version=tag.lstrip("vV"), notes=_clean_notes(j.get("body")), error="",
                                  mod=_release_mod_change(j))
        except Exception as e:
            print("[MAJ] verification impossible :", e)

    def loop():
        time.sleep(4)
        while True:
            check()
            time.sleep(6 * 3600)
    threading.Thread(target=loop, daemon=True).start()

    def get():
        with lock:
            return dict(st)

    def run_download():
        import subprocess, tempfile
        try:
            folder = Path(tempfile.gettempdir()) / "FS25SC_update"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / re.sub(r'[\\/:*?"<>|]', "_", rel["name"])
            got = 0
            with fetch(rel["url"], timeout=30) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or rel.get("size") or 0)
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        with lock:
                            st["pct"] = min(99, int(got * 100 / total))
            if rel.get("size") and got != rel["size"]:
                raise RuntimeError(f"fichier incomplet ({got} / {rel['size']} octets)")
            with lock:
                st.update(status="launching", pct=100)
            print("[MAJ] installateur telecharge :", dest)
            flags = 0
            if sys.platform == "win32":
                flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
            # /SILENT : seulement la barre de progression ; l'installateur ferme l'appli puis la relance (1.4.3 et +)
            subprocess.Popen([str(dest), "/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/RESTARTAPPLICATIONS"],
                             cwd=str(folder), close_fds=True, creationflags=flags)
            time.sleep(1.5)
            quit_now()
        except Exception as e:
            print("[MAJ] echec :", e)
            with lock:
                st.update(status="error", error=str(e)[:200], pct=0)

    def start():
        with lock:
            if st["status"] not in ("available", "error") or not rel.get("url"):
                return False
            st.update(status="downloading", pct=0, error="")
        threading.Thread(target=run_download, daemon=True).start()
        return True

    return {"get": get, "start": start, "check": check}


# ------------------------------------------------------------------ fenetres Stream / Compagnon (1.4.4)
# Avant : chaque clic ouvrait un nouvel onglet dans le navigateur. Maintenant : si la fenetre est deja ouverte, on la
# ramene devant ; sinon on l'ouvre dans sa propre fenetre "application" (Edge ou Chrome en mode --app : sans onglets
# ni barre d'adresse, profil a part dans le dossier de l'appli). Si aucun des deux n'est trouve : navigateur normal.
PAGES = {"edit": ("/?edit", "Mode édition · FS25", (1600, 960)),
         "compagnon": ("/compagnon", "Compagnon · FS25", (1400, 900))}


def focus_window(title_part):
    """Ramene devant une fenetre dont le titre contient title_part. Vrai si trouvee."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _l):
            if u.IsWindowVisible(hwnd):
                n = u.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u.GetWindowTextW(hwnd, buf, n + 1)
                    if title_part in buf.value:
                        found.append(hwnd)
                        return False
            return True
        u.EnumWindows(cb, 0)
        if not found:
            return False
        h = found[0]
        if u.IsIconic(h):
            u.ShowWindow(h, 9)                       # SW_RESTORE
        u.SetForegroundWindow(h)
        return True
    except Exception as exc:
        print("[Fenetre] impossible de ramener la fenetre :", exc)
        return False


def app_browser():
    """Chemin de Chrome, sinon Edge (pour les fenetres "application"), ou None.
    1.4.4 : Chrome d'abord (demande de Luxo : Edge affiche son message de synchronisation du compte Microsoft)."""
    if sys.platform != "win32":
        return None
    cands = []
    bases = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
    for exe, sub in (("chrome.exe", ("Google", "Chrome")), ("msedge.exe", ("Microsoft", "Edge"))):
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe) as k:
                        cands.append(winreg.QueryValue(k, None))
                except OSError:
                    pass
        except Exception:
            pass
        cands += [Path(b) / sub[0] / sub[1] / "Application" / exe for b in bases if b]
    for c in cands:
        if c and Path(str(c).strip('"')).is_file():
            return str(c).strip('"')
    return None


def open_page(kind):
    """Ouvre (ou ramene devant) la fenetre du mode edition ("edit") ou du compagnon ("compagnon")."""
    path, title, (w, h) = PAGES[kind]
    if focus_window(title):
        return
    exe = app_browser()
    if exe:
        try:
            import subprocess
            # un profil par navigateur (Chrome et Edge ne doivent pas partager le meme dossier)
            prof = (DATA_DIR or Path.home()) / "fenetres" / ("edge" if "msedge" in exe.lower() else "chrome")
            subprocess.Popen([exe, f"--app={URL}{path}", f"--user-data-dir={prof}", f"--window-size={w},{h}",
                              "--no-first-run", "--no-default-browser-check", "--disable-sync"], close_fds=True)
            return
        except Exception as exc:
            print("[Fenetre] mode application impossible :", exc)
    webbrowser.open(URL + path)


def make_accueil(data_dir, state, server, update=None):
    """Prepare la fenetre d'accueil, ou None si tkinter n'est pas disponible."""
    try:
        import accueil
    except Exception as e:
        print("[Accueil] indisponible :", e)
        return None

    def set_prefs(p):
        cfg = load_app_settings(data_dir)
        cfg["accueil"] = {"skip": bool(p.get("skip")), "open": p.get("open") or "stream"}
        save_app_settings(data_dir, cfg)

    return accueil.Accueil(
        title=DISPLAY_NAME, version=APP_VERSION,
        icon_path=ICON_FILE if ICON_FILE.is_file() else None,
        logo_path=LOGO_FILE if LOGO_FILE.is_file() else None,
        font_dir=WEB_DIR / "fonts",
        get_prefs=lambda: accueil_prefs(data_dir), set_prefs=set_prefs,
        status=lambda: (remember_game_dir(data_dir, server), accueil_status(state, server))[1],
        open_stream=lambda: open_page("edit"),
        open_companion=lambda: open_page("compagnon"),
        discord_url=DISCORD_URL, donate_url=DONATE_URL, game=game_api(data_dir, server), update=update,
        phone=phone_api(server))


def phone_api(server):
    """Panneau « Telephone / tablette » du lanceur (1.4.4) : QR code, Wi-Fi, code d'acces, pare-feu,
    sans ouvrir la page /compagnon/connexion dans le navigateur."""
    cache = {"url": None, "matrix": None}

    def info():
        cfg = server.comp_load()
        ips = server.lan_ips()
        url = f"http://{ips[0]}:{server.COMP_PORT}/compagnon" if ips else None
        if url != cache["url"]:
            cache["url"], cache["matrix"] = url, None
            if url:
                try:
                    cache["matrix"] = server.qr_matrix(url)
                except Exception as exc:
                    print("[Lanceur] QR code impossible :", exc)
        return {"lan": bool(cfg.get("lan")), "running": server._lan.get("httpd") is not None,
                "error": server._lan.get("error") or "", "url": url, "matrix": cache["matrix"], "code": cfg.get("code") or ""}

    def _save(**kw):
        cfg = server.comp_load()
        cfg.update(kw)
        server.comp_save(cfg)
        server.lan_apply()
        time.sleep(0.2)

    def set_code(code):
        code = str(code or "").strip()
        if code and not re.fullmatch(r"\d{4,8}", code):
            raise ValueError("Le code doit faire 4 à 8 chiffres.")
        _save(code=code)

    return {"info": info, "set_lan": lambda on: _save(lan=bool(on)), "set_code": set_code,
            "firewall": server.firewall_allow}


def run_tray(data_dir, note, quit_app, docs, state, ui=None):
    import pystray
    from PIL import Image

    image = Image.open(ICON_FILE) if ICON_FILE.is_file() else Image.new("RGB", (64, 64), (143, 228, 95))

    def open_url(path):
        kind = {"/?edit": "edit", "/compagnon": "compagnon"}.get(path)
        return (lambda icon, item: open_page(kind)) if kind else (lambda icon, item: webbrowser.open(URL + path))

    def open_folder(icon, item):
        os.startfile(str(data_dir)) if hasattr(os, "startfile") else None

    def open_mods(icon, item):
        if state.get("mods") and hasattr(os, "startfile"):
            os.startfile(str(state["mods"]))

    def choose_mods(icon, item):
        def run():
            d = ui.call(lambda: ask_mods_dir(data_dir, parent=ui.root)) if ui else ask_mods_dir(data_dir)
            if d:
                state["mods"] = d
                n = install_mod(d) or "Le mod est deja a jour dans ce dossier."
                message("Dossier des mods : " + str(d) + "\n\n" + n, error=n.startswith(MOD_FAIL))
        threading.Thread(target=run, daemon=True).start()

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def stop(icon, item):
        quit_app()
        icon.stop()
        if ui:
            ui.quit()

    def do_uninstall(icon, item):
        def stop_all():
            try:
                quit_app()
            except Exception:
                pass
            try:
                icon.visible = False
            except Exception:
                pass
        threading.Thread(target=uninstall, args=(docs, data_dir, state, stop_all), daemon=True).start()

    def show_accueil(icon, item):
        ui.show()

    menu = pystray.Menu(
        pystray.MenuItem("Fenetre d'accueil", show_accueil, default=True, visible=ui is not None),
        pystray.MenuItem("Mode edition (reglages)", open_url("/?edit"), default=ui is None),
        pystray.MenuItem("Apercu (comme dans OBS)", open_url("/")),
        pystray.MenuItem("Mode compagnon (telephone, tablette)...", open_url("/compagnon/connexion")),
        pystray.MenuItem("Diagnostic", open_url("/diag")),
        pystray.MenuItem("Ouvrir le dossier (images, reglages)", open_folder),
        pystray.MenuItem("Ouvrir le dossier des mods", open_mods),
        pystray.MenuItem("Choisir le dossier des mods...", choose_mods),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Lancer avec Windows", toggle_autostart,
                         checked=lambda item: autostart_enabled(),
                         visible=getattr(sys, "frozen", False)),
        pystray.MenuItem("Desinstaller...", do_uninstall),
        pystray.MenuItem("Quitter", stop),
    )
    icon = pystray.Icon("fs25streamdashboard", image, f"{DISPLAY_NAME} by leluxo", menu)

    def setup(icon):
        icon.visible = True
        if note:
            try:
                icon.notify(note, DISPLAY_NAME)
            except Exception:
                pass

    if ui is not None:                     # la fenetre d'accueil garde le fil principal
        icon.run_detached(setup=setup)
        return icon
    icon.run(setup=setup)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        message(f"Erreur inattendue :\n{exc}", error=True)
