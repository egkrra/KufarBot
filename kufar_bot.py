from __future__ import annotations
import os
import sys
import time
import json
import sqlite3
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv


if getattr(sys, 'frozen', False):
    # Запущено как exe (PyInstaller)
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Запущено как обычный .py скрипт из редактора/консоли
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, '.env'))


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]

KUFAR_SEARCH_URL = os.getenv("KUFAR_SEARCH_URL", "").strip()
KUFAR_BASE_URL = os.getenv("KUFAR_BASE_URL", "https://api.kufar.by/search-api/v2/search").strip()
KUFAR_PARAMS_JSON = os.getenv("KUFAR_PARAMS_JSON", "{}").strip()

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))

SEED_IF_EMPTY = os.getenv("SEED_IF_EMPTY", "1").strip() not in {"0", "false", "False"}

EXTRA_HEADERS_JSON = os.getenv("EXTRA_HEADERS_JSON", "{}").strip()

DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "kufar_seen.sqlite3"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOG_LEVEL_FINAL = logging.DEBUG if os.getenv("DEBUG", "0") == "1" else LOG_LEVEL
logging.basicConfig(level=LOG_LEVEL_FINAL, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("kufar_bot")

TELEGRAM_API_BASE = "https://api.telegram.org"



def tg_send_message(text: str, parse_mode: Optional[str] = None, disable_web_page_preview: bool = False) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        logger.error("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        return
    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code >= 400:
                logger.warning("Telegram send failed for chat_id=%s %s: %s", chat_id, r.status_code, r.text[:300])
        except Exception as e:
            logger.exception("Telegram send error for chat_id=%s: %s", chat_id, e)


class SeenStore:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_ads (
              ad_id TEXT PRIMARY KEY,
              first_seen_ts INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def contains(self, ad_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_ads WHERE ad_id=?", (ad_id,))
        return cur.fetchone() is not None

    def add_many(self, ids: List[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO seen_ads(ad_id, first_seen_ts) VALUES (?, ?)",
                [(i, int(time.time())) for i in ids],
            )

    def purge_older_than(self, days: int = 90) -> int:
        cutoff = int(time.time()) - days * 86400
        with self.conn:
            cur = self.conn.execute("DELETE FROM seen_ads WHERE first_seen_ts < ?", (cutoff,))
        return cur.rowcount

    def close(self):
        self.conn.close()

PREFERRED_LIST_KEYS = [
    "ads", "items", "list", "adverts", "results", "data.items", "data.ads", "data.list"
]

POSSIBLE_ID_KEYS = ["ad_id", "ad_id_str", "id", "adId"]
POSSIBLE_TITLE_KEYS = ["subject", "title", "header", "name"]
POSSIBLE_PRICE_KEYS = ["price", "price_byn", "price_usd", "price_byn_min"]
POSSIBLE_URL_KEYS = ["url", "share_url", "ad_link", "link"]
POSSIBLE_TIME_KEYS = ["list_time", "created_at", "date", "ts"]


def _walk_collect_lists(obj: Any, path: str = "") -> List[Tuple[str, list]]:
    out: List[Tuple[str, list]] = []
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            out.append((path or "<root_list>", obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            out.extend(_walk_collect_lists(v, p))
    return out




MINSK_LON_MIN = float(os.getenv("MINSK_LON_MIN", "27.3"))
MINSK_LON_MAX = float(os.getenv("MINSK_LON_MAX", "27.7"))
MINSK_LAT_MIN = float(os.getenv("MINSK_LAT_MIN", "53.7"))
MINSK_LAT_MAX = float(os.getenv("MINSK_LAT_MAX", "54.0"))


def is_in_minsk(ad: Dict[str, Any]) -> bool:
    """Проверяет, находится ли объявление в пределах Минска по координатам"""
    coords = ad.get("coordinates") or ad.get("c")
    if coords and len(coords) == 2:
        try:
            lon, lat = coords
            return MINSK_LON_MIN <= lon <= MINSK_LON_MAX and MINSK_LAT_MIN <= lat <= MINSK_LAT_MAX
        except (ValueError, TypeError):
            return False
    return True


def _score_path(path: str) -> int:
    score = 0
    lower = path.lower()
    for key in PREFERRED_LIST_KEYS:
        if key in lower:
            score += 2
    # prioritize shallower paths slightly
    score -= lower.count('.')
    return score


def is_map_format_ad(ad: Dict[str, Any]) -> bool:
    """Проверяет, является ли объявление в map-формате"""
    return all(key in ad for key in ["i", "p", "c"])


def convert_map_format_to_standard(map_ads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Конвертирует map-формат в стандартный"""
    standard_ads = []
    for ad in map_ads:
        standard_ad = {
            "ad_id": str(ad.get("i", "")),
            "ad_id_str": str(ad.get("i", "")),
            "price": ad.get("p"),
            "price_byn": ad.get("p"),
            # убрали subject!
            "coordinates": ad.get("c"),
            "is_rent": ad.get("r", False),
            "is_verified": ad.get("v", False)
        }
        standard_ads.append(standard_ad)
    return standard_ads


def extract_ads_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Проверяем, это ли map-формат
    if "ads" in payload and isinstance(payload["ads"], list):
        if payload["ads"] and isinstance(payload["ads"][0], dict):
            # Проверяем наличие полей map-формата
            first_ad = payload["ads"][0]
            if is_map_format_ad(first_ad):
                logger.info("Detected map format, converting %d ads", len(payload["ads"]))
                return convert_map_format_to_standard(payload["ads"])

    # Стандартная обработка
    candidates = _walk_collect_lists(payload)
    if not candidates:
        return []
    candidates.sort(key=lambda kv: _score_path(kv[0]), reverse=True)
    return candidates[0][1]


def get_first(ad: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in ad:
            return ad[k]
    # nested fallbacks
    for k in keys:
        parts = k.split('.')
        cur = ad
        ok = True
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def normalize_price(val: Any) -> Optional[str]:
    try:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            # Для чисел форматируем как рубли
            return f"{val:.2f} р."
        if isinstance(val, str):
            # Для строк пробуем преобразовать в число
            try:
                num_val = float(val)
                return f"{num_val:.2f} р."
            except ValueError:
                return val
        if isinstance(val, dict):
            amount = val.get("amount") or val.get("value") or val.get("price")
            cur = val.get("currency") or val.get("currency_code") or val.get("cur")
            if amount is not None and cur is not None:
                return f"{amount} {cur}"
            if amount is not None:
                return str(amount)
        return None
    except Exception:
        return None


def build_search_url() -> Tuple[str, Dict[str, str]]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "kufar-notifier/1.0 (+https://github.com)"
    }
    try:
        extra = json.loads(EXTRA_HEADERS_JSON or "{}")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
    except Exception:
        logger.warning("Invalid EXTRA_HEADERS_JSON; ignoring")

    if KUFAR_SEARCH_URL:
        return KUFAR_SEARCH_URL, headers
    # else
    try:
        params = json.loads(KUFAR_PARAMS_JSON or "{}")
    except Exception as e:
        logger.error("Invalid KUFAR_PARAMS_JSON: %s", e)
        params = {}
    # Ensure strings
    params = {str(k): str(v) for k, v in params.items()}
    url = KUFAR_BASE_URL
    if params:
        url = f"{url}?{urlencode(params)}"
    return url, headers


def fetch_ads(max_retries: int = 3, retry_delay: float = 5.0) -> List[Dict[str, Any]]:
    url, headers = build_search_url()
    logger.debug("Fetching: %s", url)

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            ads = extract_ads_list(data)
            if not isinstance(ads, list):
                logger.warning("Unexpected payload; couldn't find ads list. Top keys: %s", list(data)[:10])
                return []
            return ads
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            logger.warning("Fetch attempt %d/%d failed: %s", attempt, max_retries, e)
            if attempt < max_retries:
                time.sleep(retry_delay)

    logger.error("All %d fetch attempts failed: %s", max_retries, last_exc)
    raise last_exc


def extract_id(ad: Dict[str, Any]) -> Optional[str]:
    # Сначала пробуем map-формат
    if is_map_format_ad(ad):
        return str(ad.get("i", ""))
    # Затем стандартная обработка
    v = get_first(ad, POSSIBLE_ID_KEYS)
    return str(v) if v is not None else None


def extract_title(ad: Dict[str, Any]) -> Optional[str]:
    if is_map_format_ad(ad):
        coords = ad.get("c")
        if coords and len(coords) == 2:
            lon, lat = coords
            return f"Минск, координаты: {lat:.5f}, {lon:.5f}"
        return f"Объявление {ad.get('i', '')}"

    v = get_first(ad, POSSIBLE_TITLE_KEYS)
    return str(v) if v is not None else "Объявление"



def extract_price(ad: Dict[str, Any]) -> Optional[str]:
    if is_map_format_ad(ad):
        price = ad.get("p")
        if not price:
            return None
        try:
            price_num = int(price)
            value = price_num / 100.0
            return f"{value:.2f} р."
        except (ValueError, TypeError):
            return None

    v = get_first(ad, POSSIBLE_PRICE_KEYS)
    try:
        return f"{float(v) / 100.0:.2f} р." if v else None
    except Exception:
        return str(v) if v else None




def extract_url(ad: Dict[str, Any], fallback_id: Optional[str]) -> Optional[str]:
    # Для map-формата строим URL из ID
    if is_map_format_ad(ad) and "i" in ad:
        return f"https://www.kufar.by/item/{ad['i']}"
    # Стандартная обработка
    v = get_first(ad, POSSIBLE_URL_KEYS)
    if isinstance(v, str) and v.startswith("http"):
        return v
    # try to build from id
    if fallback_id:
        return f"https://www.kufar.by/item/{fallback_id}"
    return None


def pretty_ad_line(ad: Dict[str, Any]) -> Optional[str]:
    ad_id = extract_id(ad)
    title = extract_title(ad) or "Без названия"
    price = extract_price(ad)
    url = extract_url(ad, ad_id)
    pieces = [f"🔔 *{title}*"]
    if price:
        pieces.append(f"— {price}")
    if url:
        pieces.append(f"\n{url}")
    if not ad_id:
        pieces.append("\n(ID не найден)")
    return " ".join(pieces)


# ---------------------------
# Main loop
# ---------------------------

def run_once(store: SeenStore, send_messages: bool = True) -> int:
    ads = fetch_ads()
    if not ads:
        logger.info("No ads found in response")
        return 0

    new_ids: List[str] = []
    messages: List[str] = []
    filtered_count = 0

    minsk_count = 0
    suspicious: List[Dict[str, Any]] = []

    for ad in ads:
        in_minsk = is_in_minsk(ad)

        if not in_minsk:
            filtered_count += 1
            continue

        minsk_count += 1

        if ad.get("is_rent") is False:
            filtered_count += 1
            continue  # пропускаем объявления о продаже

        ad_id = extract_id(ad)
        if not ad_id:
            continue
        if store.contains(ad_id):
            continue

        new_ids.append(ad_id)
        msg = pretty_ad_line(ad)
        if msg:
            messages.append(msg)

    logger.info(
        "Обработано: %d всего, %d в Минске, %d отфильтровано, %d подозрительных (is_rent=False)",
        len(ads), minsk_count, filtered_count, len(suspicious),
    )
    for ad in suspicious[:10]:
        logger.debug("SUSPICIOUS (is_rent=False, но прошло фильтр Минска): %s", ad)

    if not new_ids:
        logger.info("No new ads")
        return 0

    # Save first, then send (to avoid duplicates if interrupted)
    store.add_many(new_ids)

    sent = 0
    if send_messages:
        for chunk in _chunk_messages(messages, max_chars=3800):
            tg_send_message("\n\n".join(chunk), parse_mode="Markdown", disable_web_page_preview=False)
            sent += len(chunk)
    logger.info("New ads: %d (sent: %d)", len(new_ids), sent)
    logger.info(
        "Обработано: %d всего, %d в Минске, %d отфильтровано, %d подозрительных (is_rent=False)",
        len(ads), minsk_count, filtered_count, len(suspicious),
    )
    for ad in suspicious[:10]:  # не более 10, чтобы не заспамить лог
        logger.debug("SUSPICIOUS (is_rent=False, но прошло фильтр Минска): %s", ad)
    return len(new_ids)


def _chunk_messages(lines: List[str], max_chars: int = 3800) -> List[List[str]]:
    out: List[List[str]] = []
    cur: List[str] = []
    cur_len = 0
    for line in lines:
        add = len(line) + 2  # with spacing
        if cur and cur_len + add > max_chars:
            out.append(cur)
            cur = []
            cur_len = 0
        cur.append(line)
        cur_len += add
    if cur:
        out.append(cur)
    return out


def main() -> None:
    # Добавляем логирование в файл (ДОБАВЬ ЭТО)
    file_handler = logging.FileHandler(os.path.join(BASE_DIR, 'bot_log.txt'), encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(file_handler)

    logger.info("=== Kufar Bot Started ===")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        logger.error("Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars")
        sys.exit(2)
    # ... остальной код без изменений

    url, _ = build_search_url()
    logger.info("Using search URL: %s", url)

    store = SeenStore(DB_PATH)
    try:
        # Seed mode for first run
        cur = store.conn.execute("SELECT COUNT(*) FROM seen_ads").fetchone()[0]
        if SEED_IF_EMPTY and cur == 0:
            logger.info("DB empty; seeding without sending…")
            ads = fetch_ads()
            ids = [i for i in (extract_id(a) for a in ads) if i]
            store.add_many(ids)
            logger.info("Seeded %d IDs", len(ids))

        # Run forever as a simple loop (use cron if you prefer)
        last_purge = 0.0
        while True:
            try:
                run_once(store, send_messages=True)
            except requests.HTTPError as e:
                logger.warning("HTTP error: %s", e)
            except Exception:
                logger.exception("Unexpected error in run_once")

            if time.time() - last_purge > 86400:  # раз в сутки
                removed = store.purge_older_than(days=90)
                if removed:
                    logger.info("Purged %d old seen_ads records", removed)
                last_purge = time.time()

            time.sleep(POLL_SECONDS)
    finally:
        store.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("=== Kufar Bot Stopped (KeyboardInterrupt) ===")
        sys.exit(0)