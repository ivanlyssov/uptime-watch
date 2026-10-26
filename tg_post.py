#!/usr/bin/env python3
"""Единая отправка в Telegram Bot API, пережившая обрыв сети.

Зачем модуль. 28.08.2026 утренний голосовой брифинг собрался, но не ушёл: одиночный
`urlopen` поймал `Connection reset by peer`, и сообщение пропало молча. Правильная
политика повторов была написана в `loops/loop-status/notify.py`, а ещё в полутора
десятках мест по `_os/` рядом жил свой одиночный `urlopen` без единого повтора —
включая алерт о нехватке места на прод-VPS. Здесь эта политика лежит один раз, и
все скрипты `_os/` её импортируют, а не копируют.

Политика повторов — что повторяем, а что нет:
  • обрыв/таймаут сети и 5xx — повторяем: сервис или канал временно недоступен;
  • 429 — повторяем, выждав `retry_after`, который присылает сам Telegram;
  • прочие 4xx (400 «bad request», 403 «bot was blocked») — НЕ повторяем: это наша
    ошибка в запросе, повтор даст ровно тот же отказ. Такой отказ печатаем в stderr,
    чтобы он не выглядел как успех — у watchdog LVL ровно этим и кончилось:
    «alert sent» в логе при HTTP 400 и ни одного сообщения в чате.

Как импортировать (путь считаем от своего файла, не от $HOME — абсолютные пути
у нас уже четырежды тихо ломались при переездах дерева):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
    from tg_post import send_message

Импортом обходятся не все, и это осознанно: у политики один экземпляр на ЕДИНИЦУ
ВЫКАТКИ, а не один на весь мир. Боты `lyssov-base` уезжают на VPS своим репозиторием,
`resource-alert.sh` — одиночным файлом в cron, продажный бот Forevent живёт в чужом
репозитории. Импорт из `_os/bin/` сделал бы их зависимыми от дерева, которого на той
машине нет, — а `resource-alert.sh` существует ровно для случая, когда Mac недоступен.

Поэтому копии есть, но они не расходятся молча:
  • `knowledge/lyssov-base/05_SYSTEM/bots/tg_post.py` — ПОБАЙТОВАЯ копия этого файла;
  • `infra/vps-monitoring/resource-alert.sh` и продажный бот Forevent — та же политика
    на своём языке (bash / встроенная функция).
Сторож `_os/bin/tg-policy-check.sh` падает, если копия разъехалась с оригиналом или
если где-то снова завёлся одиночный `urlopen`/`curl` без повторов. Меняешь политику —
меняй здесь и прогоняй сторожа.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ATTEMPTS = 4
DEFAULT_PAUSE = 3.0
MAX_TEXT = 3800          # лимит Telegram 4096, оставляем запас на экранирование


def request(url, data=None, headers=None, timeout=30,
            attempts=DEFAULT_ATTEMPTS, pause=DEFAULT_PAUSE, tag="tg",
            quiet=False, raise_http=False):
    """Выполнить запрос к Bot API по политике повторов выше.

    Возвращает разобранный JSON-ответ (dict) при успехе и None при отказе.
    None — единственный признак «не доставлено»: вызывающий обязан его проверять,
    иначе получится та же тихая потеря, из-за которой модуль и появился.

    quiet=True — не печатать промежуточные попытки. Для долгоживущих поллеров,
    которые ведут свой счётчик подряд идущих сбоев: иначе `getUpdates` при упавшей
    сети засыпает лог одинаковой строкой каждые несколько секунд. Неповторяемый
    4xx печатается всё равно — именно он не должен теряться молча.

    raise_http=True — неповторяемый HTTPError пробросить наружу вместо None, если
    вызывающему нужно различать коды (queue-bot по-разному ждёт на 409 и на прочем).
    """
    req = urllib.request.Request(url, data=data, headers=headers or {})
    last = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            try:
                return json.loads(raw) if raw else {"ok": True}
            except ValueError:
                return {"ok": True}          # ответ не JSON, но запрос прошёл
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code == 429:
                wait = pause
                try:
                    wait = float(json.loads(body).get("parameters", {}).get("retry_after", pause))
                except Exception:
                    pass
                if attempt < attempts:
                    if not quiet:
                        print(f"{tag}: 429, жду {wait:.0f}с ({attempt}/{attempts})", file=sys.stderr)
                    time.sleep(min(wait, 60)); last = e; continue
            elif 500 <= e.code < 600 and attempt < attempts:
                if not quiet:
                    print(f"{tag}: HTTP {e.code}, повтор {attempt}/{attempts}", file=sys.stderr)
                time.sleep(pause); last = e; continue
            print(f"{tag}: HTTP {e.code} — не повторяю: {body}", file=sys.stderr)
            if raise_http:
                raise
            return None
        except Exception as e:
            last = e
            if attempt < attempts:
                if not quiet:
                    print(f"{tag}: {e} — попытка {attempt}/{attempts}, жду {pause:.0f}с", file=sys.stderr)
                time.sleep(pause)
    if not quiet:
        print(f"{tag}: не отправлено после {attempts} попыток: {last}", file=sys.stderr)
    if raise_http and last is not None:
        raise last
    return None


def post(url, data, headers, timeout=30,
         attempts=DEFAULT_ATTEMPTS, pause=DEFAULT_PAUSE, tag="tg"):
    """Тот же запрос, но ответ не нужен — только «дошло / не дошло»."""
    return request(url, data, headers, timeout, attempts, pause, tag) is not None


def call(token, method, params=None, timeout=30, attempts=DEFAULT_ATTEMPTS,
         pause=DEFAULT_PAUSE, tag=None, api_base="https://api.telegram.org",
         json_body=False, quiet=False, raise_http=False):
    """Вызов метода Bot API. `token` — токен бота либо готовый базовый URL с ним.

    json_body=True — слать `application/json` (нужно для reply_markup и вложенных
    структур), иначе обычный urlencode.
    """
    base = token if token.startswith("http") else f"{api_base.rstrip('/')}/bot{token}"
    if json_body:
        data = json.dumps(params or {}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    else:
        data = urllib.parse.urlencode(params or {}).encode()
        headers = {}
    return request(f"{base.rstrip('/')}/{method}", data, headers,
                   timeout, attempts, pause, tag or f"tg {method}",
                   quiet=quiet, raise_http=raise_http)


def send_message(token, chat, text, thread_id=None, parse_mode=None, reply_to=None,
                 reply_markup=None, preview=False, timeout=30,
                 attempts=DEFAULT_ATTEMPTS, pause=DEFAULT_PAUSE, tag="tg",
                 api_base="https://api.telegram.org", quiet=False):
    """Отправить текст, разбив по лимиту Telegram. True — дошли ВСЕ куски.

    Кусок, который не дошёл, не отменяет попытку отправить следующие: половина
    длинного отчёта лучше, чем ничего, — но результат честно будет False.
    """
    ok = True
    chunks = [text[i:i + MAX_TEXT] for i in range(0, max(len(text), 1), MAX_TEXT)] or [text]
    for chunk in chunks:
        p = {"chat_id": chat, "text": chunk, "disable_web_page_preview": not preview}
        if thread_id: p["message_thread_id"] = thread_id
        if parse_mode: p["parse_mode"] = parse_mode
        if reply_to: p["reply_to_message_id"] = reply_to
        if reply_markup: p["reply_markup"] = json.dumps(reply_markup)
        r = call(token, "sendMessage", p, timeout=timeout, attempts=attempts,
                 pause=pause, tag=tag, api_base=api_base, quiet=quiet)
        if r is None:
            ok = False
    return ok
