# uptime-watch — проверка прод-сайтов вне Mac

Каждые 15 минут GitHub Actions открывает адреса из `sites.txt`: годен ответ 2xx/3xx и
TLS-сертификат дольше 14 дней. Упал сайт — одно сообщение в Telegram; пока лежит —
напоминание раз в сутки; поднялся — «снова работает». Работает, даже если Mac или
домашний интернет выключены (Uptime Kuma на Mac в этот момент молчит).

- Исходник: `~/LyssovOS/_os/infra/uptime-watch/` (single-write-point), репозиторий публичный —
  у публичных минуты Actions бесплатны. Секретов в коде нет: `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID` — GitHub Secrets.
- `tg_post.py` — побайтовая копия `_os/bin/tg_post.py` (политика повторов Telegram),
  сверяет `_os/bin/tg-policy-check.sh`.
- Тест: Actions → uptime → Run workflow → в `extra_urls` битый адрес → ровно одно сообщение.
- Красный прогон = сообщение не доставлено или нет секретов (не «сайт лежит»).
