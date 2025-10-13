#!/usr/bin/env python3
"""
watchdog.py

Surveillance et réparation automatique pour qbittorrent + gluetun.
- Vérifie l'API Web de qBittorrent pour détecter les torrents stalled et le nombre de peers
- Vérifie l'état du control server Gluetun (si disponible)
- Actions graduées : resume torrents, restart qbittorrent container, restart gluetun container
- Notifications via NTFY (optionnel)

Configuration via variables d'environnement :
- QBT_HOST (ex: http://qbittorrent:8080)
- QBT_USER
- QBT_PASS
- QBT_CONTAINER_NAME (nom Docker du container qbittorrent) default: qbittorrent
- GLUETUN_CONTAINER_NAME default: gluetun
- CHECK_INTERVAL (s) default: 60
- MAX_STALLED default: 5
- MIN_PEERS default: 1
- NTFY_TOPIC optional, ex: mytopic -> envoie notif: "https://ntfy.sh/{topic}"

Le script suppose qu'il peut accéder au socket Docker (exposé dans le service watchdog via
/var/run/docker.sock) s'il doit redémarrer des conteneurs.
"""

import os
import time
import logging
import requests
import docker
from requests.auth import HTTPBasicAuth
from typing import List

# Config
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
QBT_HOST = os.getenv("QBT_HOST", "http://127.0.0.1:8080")
QBT_USER = os.getenv("QBT_USER", "admin")
QBT_PASS = os.getenv("QBT_PASS", "adminadmin")
QBT_CONTAINER_NAME = os.getenv("QBT_CONTAINER_NAME", "qbittorrent")
GLUETUN_CONTAINER_NAME = os.getenv("GLUETUN_CONTAINER_NAME", "gluetun")
GLUETUN_CONTROL_USER = os.getenv("GLUETUN_CONTROL_USER", "api")
GLUETUN_CONTROL_PASS = os.getenv("GLUETUN_CONTROL_PASSWORD", "password")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
MAX_STALLED = int(os.getenv("MAX_STALLED", "5"))
MIN_PEERS = int(os.getenv("MIN_PEERS", "1"))
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "uptime")
GLUETUN_CONTROL_URL = os.getenv("GLUETUN_CONTROL_URL", "http://127.0.0.1:8000/v1/vpn/status")
STALLED_AGE = int(os.getenv("STALLED_AGE", "600"))   
PROGRESS_DONE_THRESHOLD = float(os.getenv("PROGRESS_DONE_THRESHOLD", "0.99")) 
NON_STALLED_STATES = {
    "queued", "queuedup", "queueddl", "queued", "paused", "pauseddl", "pausedup",
    "checking", "checkingdl", "checkingup", "allocating", "moving",
    "meta", "metadl", "uploading", "downloading", "forceddl", "forcedup"
}

# Timeouts
REQUEST_TIMEOUT = 6

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seedbox-watchdog")

# Docker client (may raise if socket not available)
try:
    docker_client = docker.from_env()
except Exception as e:
    docker_client = None
    logger.warning("Docker client not available: %s", e)

def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def get_last_activity(t):
    # qBittorrent peut renvoyer 'last_activity' (timestamp epoch sec).
    # si absent, on renvoie None.
    la = t.get('last_activity') or t.get('lastActivity') or t.get('last_seen_complete') or None
    if la is None:
        return None
    try:
        return int(la)
    except Exception:
        # parfois last_activity est en ms ; essayer de détecter
        try:
            laf = float(la)
            # si > 1e12 -> ms
            if laf > 1e12:
                return int(laf / 1000)
            return int(laf)
        except Exception:
            return None

def is_true_stalled(t):
    """
    Retourne True uniquement pour les *vrais* stalled :
    - ignore les torrents en queue/paused/checking/etc.
    - si torrent complet (progress >= PROGRESS_DONE_THRESHOLD) :
        -> ne pas considérer stalled si peers == 0 (comportement normal)
        -> considérer stalled seulement si peers > 0 mais pas d'activité depuis STALLED_AGE
          ou si qBittorrent indique explicitement stalledUP
    - si torrent incomplet :
        -> si qBittorrent indique stalledDL -> True
        -> si peers == 0 *et* last_activity > STALLED_AGE -> True
    """
    state = (t.get('state') or "").lower()
    progress = safe_float(t.get('progress'), 0.0)  # 0..1
    num_peers = safe_int(t.get('num_peers') or t.get('peers') or 0)
    now = int(time.time())
    last_activity = get_last_activity(t)

    # 1) ignore explicit non-stalled states (queue/paused/checking/etc.)
    for s in NON_STALLED_STATES:
        if s in state:
            # logger.debug(f"ignore by state ({s} in {state}) for {t.get('name')}")
            return False

    # 2) if completed/seed
    if progress >= PROGRESS_DONE_THRESHOLD:
        # normal: no peers -> NOT stalled
        if num_peers == 0:
            return False
        # if peers exist but qBittorrent explicitly says stalledUP -> consider stalled
        if "stalledup" in state or "stalledup" in state or "stalled" in state and "up" in state:
            return True
        # if peers exist but no activity for a long time -> suspicious => stalled
        if last_activity is not None and (now - last_activity) > STALLED_AGE:
            return True
        # otherwise assume fine (seeding but no activity or recent activity)
        return False

    # 3) if not completed (downloading)
    # explicit stalledDL reported
    if "stalldl" in state or "stalleddl" in state or "stalled" in state and "dl" in state:
        return True

    # if no peers and no recent activity => likely stalled
    if num_peers == 0:
        if last_activity is not None:
            if (now - last_activity) > STALLED_AGE:
                return True
            else:
                return False
        # no last_activity available -> be conservative: don't mark stalled immediately
        return False

    # default: not stalled
    return False

# Helper: send ntfy notification (if configured)
def notify(subject: str, body: str = ""):
    if not NTFY_TOPIC:
        return
    try:
        url = f"https://ntfy.mornati.ovh/{NTFY_TOPIC}"
        requests.post(url, data=f"{subject}\n{body}")
    except Exception as e:
        logger.warning("Notification failed: %s", e)


# qBittorrent API helpers
session = requests.Session()


def qbt_login() -> bool:
    try:
        r = session.post(QBT_HOST.rstrip("/") + "/api/v2/auth/login", data={"username": QBT_USER, "password": QBT_PASS}, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200 and 'Ok.' in r.text
    except Exception as e:
        logger.debug("qbt login error: %s", e)
        return False


def qbt_get_torrents() -> List[dict]:
    try:
        r = session.get(QBT_HOST.rstrip("/") + "/api/v2/torrents/info", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug("qbt get torrents error: %s", e)
        return []


def qbt_resume_hash(hash_str: str) -> bool:
    try:
        r = session.post(QBT_HOST.rstrip("/") + "/api/v2/torrents/resume", data={"hashes": hash_str}, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200
    except Exception as e:
        logger.debug("qbt resume error: %s", e)
        return False


# Docker helpers

def restart_container(name: str) -> bool:
    if not docker_client:
        logger.warning("Docker client unavailable, cannot restart %s", name)
        return False
    try:
        c = docker_client.containers.get(name)
        if DRY_RUN:
            logger.warning(f"[DRY_RUN] Would restart container '{name}' but skipping actual restart.")
            return True
        logger.info("Restarting container %s", name)
        c.restart()
        return True
    except Exception as e:
        logger.exception("Failed to restart container %s: %s", name, e)
        return False


# Gluetun control check (optional)
def gluetun_status_ok() -> bool:
    try:
        auth = None
        if GLUETUN_CONTROL_USER and GLUETUN_CONTROL_PASS:
            auth = (GLUETUN_CONTROL_USER, GLUETUN_CONTROL_PASS)
        r = requests.get(GLUETUN_CONTROL_URL, timeout=REQUEST_TIMEOUT, auth=auth, allow_redirects=False)
        # 200 attendu ; 401 indique auth requise/incorrecte ; 3xx/4xx => pas ok
        if r.status_code == 200:
            # minimal check: has a 'vpn' or 'status' key depending on gluetun version
            try:
                j = r.json()
                return True
            except Exception:
                return True
        return False
    except Exception as e:
        logger.debug("gluetun status error: %s", e)
        return False


# Main monitoring loop

def main_loop():
    logged_in = qbt_login()
    if not logged_in:
        logger.warning("Failed to login to qBittorrent at %s", QBT_HOST)

    consecutive_fails = 0

    while True:
        try:
            # ensure login
            if not qbt_login():
                logger.warning("qBittorrent login failed; will retry next tick")

            torrents = qbt_get_torrents()
            stalled = []
            for t in torrents:
               if is_true_stalled(t):
                   stalled.append({
                       "hash": t.get('hash') or t.get('hashString') or t.get('id'),
                       "name": t.get('name') or t.get('file_name') or "<no-name>",
                       "state": t.get('state'),
                       "progress": t.get('progress'),
                       "peers": t.get('num_peers') or t.get('peers') or 0,
                       "last_activity": get_last_activity(t)
                   })

            total_peers = sum(safe_int(t.get('num_peers') or t.get('peers') or 0) for t in torrents)

            logger.info("torrents=%d true_stalled=%d peers=%d", len(torrents), len(stalled), total_peers)
            if stalled:
                # debug print each stalled entry for diagnosis
                for s in stalled:
                    logger.info("stalled candidate: name=%s hash=%s state=%s progress=%s peers=%s last_activity=%s", s["name"], s["hash"], s["state"], s["progress"], s["peers"], s["last_activity"])


            if len(stalled) >= MAX_STALLED and total_peers < MIN_PEERS:
                logger.warning("Detected %d stalled torrents and only %d peers -> trying soft-recovery", len(stalled), total_peers)
                notify("Seedbox watchdog: soft-recovery", f"{len(stalled)} stalled, {total_peers} peers")

                # Attempt soft fix: resume first N stalled torrents
                resumed = 0
                for s in stalled[:20]:
                    h = s.get("hash")
                    if not h:
                        logger.debug("skip resume: missing hash for %s", s["name"])
                        continue
                    try:
                        if qbt_resume_hash(h):
                            resumed += 1
                            logger.info("requested resume for %s (%s)", s["name"], h)
                        else:
                            logger.warning("resume API returned non-200 for %s (%s)", s["name"], h)
                    except Exception as e:
                        logger.exception("error resuming %s (%s): %s", s["name"], h, e)
                
                logger.info("Attempted to resume %d torrents", resumed)

                time.sleep(10)

                # re-evaluate
                torrents2 = qbt_get_torrents()
                total_peers2 = sum(int(t.get('num_peers') or 0) for t in torrents2)
                if total_peers2 < MIN_PEERS:
                    logger.warning("Soft-recovery didn't increase peers -> restarting qbittorrent container")
                    notify("Seedbox watchdog: restarting qbittorrent", f"resumed={resumed} peers={total_peers2}")
                    if restart_container(QBT_CONTAINER_NAME):
                        # give service time
                        time.sleep(30)
                        # optionally check gluetun
                        if not gluetun_status_ok():
                            logger.warning("Gluetun control reports problem after qbittorrent restart -> restarting gluetun")
                            notify("Seedbox watchdog: restarting gluetun", "Gluetun control check failed after qbittorrent restart")
                            restart_container(GLUETUN_CONTAINER_NAME)
                    else:
                        logger.error("Failed to restart qbittorrent container %s", QBT_CONTAINER_NAME)

            # opportunistic check: if gluetun control is down, try restarting it
            if not gluetun_status_ok():
                consecutive_fails += 1
                logger.warning("Gluetun status check failed (%d)", consecutive_fails)
                if consecutive_fails >= 3:
                    logger.warning("Restarting gluetun after repeated control failures")
                    notify("Seedbox watchdog: restarting gluetun (control down)", "control server unreachable")
                    restart_container(GLUETUN_CONTAINER_NAME)
                    consecutive_fails = 0
            else:
                consecutive_fails = 0

        except Exception as e:
            logger.exception("Unexpected error in watchdog main loop: %s", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("Watchdog stopped by user")
