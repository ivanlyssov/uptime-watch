#!/usr/bin/env python3
"""Внешняя проверка прод-сайтов — живёт на GitHub Actions, не на Mac.

Uptime Kuma стоит на Mac: умрёт Mac или домашний интернет — о падении сайтов никто
не узнает. Эта проверка идёт каждые 15 минут с серверов GitHub.

Сайт годен, если отвечает 2xx/3xx (редиректы не следуем — /login тоже ответ) и его
TLS-сертификат живёт дольше CERT_DAYS дней. Неудачу перепроверяем через паузу, чтобы
разовый сбой сети не будил Ивана.

Тихий режим: одно сообщение на инцидент. Состояние (что уже лежит и когда о нём
сказали) едет между прогонами через кэш Actions в STATE. Пока сайт лежит — напоминаем
раз в REMIND_H часов; поднялся — одно «снова работает». Адреса из EXTRA_URLS (ручной
тестовый запуск) в состояние не пишутся: каждый тест даёт ровно одно сообщение.

Код выхода: 0 — проверка прошла (даже если сайты лежат, о них сказано в Telegram);
1 — сообщение НЕ доставлено или нет секретов: о таком прогоне должен знать GitHub.
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tg_post import send_message

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("STATE_FILE", HERE / "state" / "state.json"))
CERT_DAYS = 14
REMIND_H = 24
RETRY_PAUSE = 20
UA = "LyssovOS-uptime-watch/1 (+github actions)"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def http_problem(url):
    try:
        with OPENER.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # сеть, DNS, битый сертификат
        return f"нет ответа: {type(e).__name__}: {e}"[:200]
    return None if 200 <= code < 400 else f"HTTP {code}"


def cert_problem(host):
    try:
        with socket.create_connection((host, 443), timeout=20) as s:
            with ssl.create_default_context().wrap_socket(s, server_hostname=host) as t:
                left = (ssl.cert_time_to_seconds(t.getpeercert()["notAfter"]) - time.time()) / 86400
    except Exception as e:
        return f"TLS: {type(e).__name__}: {e}"[:200]
    return f"сертификат истекает через {left:.0f} дн." if left < CERT_DAYS else None


def problem(url):
    host = urllib.parse.urlsplit(url).hostname
    return http_problem(url) or cert_problem(host)


def check(urls):
    bad = {u: p for u in urls if (p := problem(u))}
    if bad:  # перепроверка: разовый сбой не инцидент
        time.sleep(RETRY_PAUSE)
        bad = {u: p for u in bad if (p := problem(u))}
    return bad


def read_list(path):
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


def main():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID в секретах", file=sys.stderr)
        return 1
    sites = read_list(HERE / "sites.txt")
    extra = os.environ.get("EXTRA_URLS", "").split()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    now = time.time()

    bad = check(sites + extra)
    for u in sites + extra:
        print(f"{'FAIL' if u in bad else 'ok  '} {u}  {bad.get(u, '')}")

    down, remind = [], []
    new_state = {}
    for u, p in bad.items():
        if u in extra:
            down.append((u, p + " (тестовый запуск)"))
            continue
        prev = state.get(u)
        if not prev:
            down.append((u, p))
            new_state[u] = {"since": now, "alerted": now}
        elif now - prev["alerted"] >= REMIND_H * 3600:
            hours = (now - prev["since"]) / 3600
            remind.append((u, f"{p} — лежит уже {hours:.0f} ч"))
            new_state[u] = {**prev, "alerted": now}
        else:
            new_state[u] = prev
    up = [u for u in state if u not in bad and u in sites]

    STATE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if down:
        lines += ["🔴 Сайт не работает (проверка с GitHub, не с Mac):"] + [f"• {u} — {p}" for u, p in down]
    if remind:
        lines += ["🔴 Всё ещё не работает:"] + [f"• {u} — {p}" for u, p in remind]
    if up:
        lines += ["✅ Снова работает:"] + [f"• {u}" for u in up]
    if lines and not send_message(token, chat, "\n".join(lines), tag="uptime-watch"):
        print("Telegram не принял сообщение — состояние не сохраняю, повторим в следующий прогон",
              file=sys.stderr)
        return 1  # состояние «уже сказал» пишем только после доставки
    STATE.write_text(json.dumps(new_state, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
