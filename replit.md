# Bot de Telegram — Blackjack & UNO

Bot de Telegram grupal con Blackjack y UNO, desarrollado con python-telegram-bot v20+.

## Run & Operate

- Workflow `Telegram Bot` — ejecuta `python bot/main.py`
- Required env: `TOKEN` — token del bot (obtenido de @BotFather)

## Stack

- Python 3 + python-telegram-bot v21 (async)
- Sin base de datos — estado en memoria por chat

## Where things live

- `bot/main.py` — todo el código del bot (único archivo)
- `bot/requirements.txt` — dependencias Python

## Architecture decisions

- Estado de juego en diccionario global `games[chat_id]` — simple y suficiente para un bot de grupos
- Filtro estricto de chat IDs: solo responde a `-1003290179217` y `-1003162772831`
- Timeouts con `job_queue` de PTB — cancela y reprograma por turno
- Privacidad en UNO con `answer_callback_query(show_alert=True)` — solo el usuario ve sus cartas
- Anti-spam con try/except en todos los `edit_message` y `answer` — ignora errores de mensaje no modificado

## Product

Bot grupal de juegos de cartas para Telegram:
- **Blackjack** — 52 cartas reales, turnos gestionados, banca automática con regla de 17
- **UNO** — 108 cartas, cartas privadas, efectos especiales (+2, Reversa, Salta, Comodín, +4)
- **Lobby** — sala de espera con botones de unirse/ver lista/iniciar/cancelar
- **Timeout** — 60s por turno; si el jugador no actúa, se le planta (BJ) o se salta su turno (UNO)

## User preferences

- Código en un solo archivo `main.py`, modular, comentado en español
- Máximo 5 jugadores por partida

## Gotchas

- El bot ignora silenciosamente mensajes fuera de los chat IDs permitidos
- Solo acepta los comandos: `/casino`, `/game`, `/reset_juego`, `/salir`, `/comandos`
- Al cambiar código, reiniciar el workflow `Telegram Bot`
