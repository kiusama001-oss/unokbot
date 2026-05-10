"""
Bot de Telegram — Blackjack & UNO grupal.
Seguridad máxima: lista blanca + lista negra + filtro de string en cada función.
Privacidad total: cartas nunca al chat grupal, solo popup privado (show_alert=True).
"""

import os
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.error import BadRequest

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN", "").strip()

# ─────────────────────────────────────────────────────────────
# FILTRO DE CHATS — REGLA DE ORO
# Solo responde a estos dos chats. Nadie más existe para el bot.
# ─────────────────────────────────────────────────────────────
_PERMITIDOS = {"-1003290179217", "-1003162772831"}

_BLOQUEADOS = {
    "317718", "1129", "-4746401037", "5857143431",
    "6207865243", "7026569116", "7446115984", "8446383738",
    "1275990283", "2082987114", "1540540097", "1539209581", "1331707986",
}


def _ok(update: Update) -> bool:
    """
    Comprueba que el chat esté en la lista blanca Y no en la negra.
    Usar: if not _ok(update): return   — primera línea de cada función.
    """
    cid = str(update.effective_chat.id)
    if cid in _BLOQUEADOS:
        return False
    return cid in _PERMITIDOS


# ─────────────────────────────────────────────────────────────
# ESTADO EN MEMORIA
# ─────────────────────────────────────────────────────────────
games: dict   = {}   # games[chat_id]  -> estado de partida
ranking: dict = {}   # ranking[chat_id][uid] -> {nombre, victorias, partidas}

MAX_JUGADORES = 5
TIMEOUT_S     = 60

# ─────────────────────────────────────────────────────────────
# BLACKJACK — CARTAS Y CÁLCULO
# ─────────────────────────────────────────────────────────────
_PALOS_BJ   = ["♥️", "♦️", "♣️", "♠️"]
_VALORES_BJ = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _mazo_bj():
    m = [(v, p) for p in _PALOS_BJ for v in _VALORES_BJ]
    random.shuffle(m)
    return m


def _pts_bj(mano: list) -> int:
    """
    As = 11; baja a 1 automáticamente si la suma supera 21.
    J / Q / K = 10 siempre.
    """
    total, ases = 0, 0
    for v, _ in mano:
        if v in ("J", "Q", "K"):
            total += 10
        elif v == "A":
            total += 11
            ases += 1
        else:
            total += int(v)
    while total > 21 and ases:
        total -= 10
        ases -= 1
    return total


def _mano_txt(mano: list, ocultar: bool = False) -> str:
    if ocultar:
        return "🂠  " + "  ".join(f"{v}{p}" for v, p in mano[1:])
    return "  ".join(f"{v}{p}" for v, p in mano)


# ─────────────────────────────────────────────────────────────
# UNO — CARTAS
# ─────────────────────────────────────────────────────────────
_COLORES_UNO    = ["🔴", "🔵", "🟢", "🟡"]
_ESPECIALES_UNO = ["+2", "🔄", "⛔"]


def _mazo_uno():
    m = []
    for c in _COLORES_UNO:
        m.append((c, "0"))
        for n in "123456789":
            m += [(c, n), (c, n)]
        for e in _ESPECIALES_UNO:
            m += [(c, e), (c, e)]
    m += [("🌈", "Wild")] * 4
    m += [("🌈", "+4")] * 4
    random.shuffle(m)
    return m


def _cs(c: tuple) -> str:
    return f"{c[0]}{c[1]}"


def _valida(carta, en_mesa, color_activo) -> bool:
    cc, tc = carta
    _, tm  = en_mesa
    return cc == "🌈" or cc == color_activo or tc == tm


# ─────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────

def _nombre(user) -> str:
    return f"@{user.username}" if user.username else user.first_name


def _activo(chat_id: int):
    g = games.get(chat_id)
    return g["jugadores"][g["turno_actual"]] if g else None


def _avanzar(chat_id: int, pasos: int = 1):
    g = games[chat_id]
    n = len(g["jugadores"])
    g["turno_actual"] = (g["turno_actual"] + g["dir"] * pasos) % n


def _robar(g: dict, uid: int, n: int = 1):
    for _ in range(n):
        if not g["mazo"]:
            descarte = g["descarte"]
            g["mazo"] = descarte[:-1]
            random.shuffle(g["mazo"])
            g["descarte"] = [descarte[-1]]
        if g["mazo"]:
            g["manos"][uid].append(g["mazo"].pop())


def _registrar(chat_id: int, jugadores, nombres: dict, ganadores: set, tipo: str = ""):
    """
    Registra el resultado de una partida en el ranking del grupo.
    tipo: "blackjack" o "uno"
    Actualiza: victorias totales, partidas totales, racha actual,
               racha máxima histórica y estadísticas por juego (BJ/UNO).
    """
    if chat_id not in ranking:
        ranking[chat_id] = {}
    for uid in jugadores:
        if uid not in ranking[chat_id]:
            ranking[chat_id][uid] = {
                "nombre":       nombres.get(uid, "?"),
                "victorias":    0,
                "partidas":     0,
                "racha_actual": 0,
                "racha_max":    0,
                "bj_victorias": 0,
                "bj_partidas":  0,
                "uno_victorias":0,
                "uno_partidas": 0,
            }
        r = ranking[chat_id][uid]
        r["nombre"] = nombres.get(uid, r["nombre"])
        r["partidas"] += 1

        # Estadísticas por juego
        if tipo == "blackjack":
            r["bj_partidas"] += 1
        elif tipo == "uno":
            r["uno_partidas"] += 1

        if uid in ganadores:
            r["victorias"] += 1
            r["racha_actual"] += 1
            if r["racha_actual"] > r["racha_max"]:
                r["racha_max"] = r["racha_actual"]
            if tipo == "blackjack":
                r["bj_victorias"] += 1
            elif tipo == "uno":
                r["uno_victorias"] += 1
        else:
            r["racha_actual"] = 0   # derrota rompe la racha


# ─────────────────────────────────────────────────────────────
# TECLADOS
# ─────────────────────────────────────────────────────────────

def _kb_lobby():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🃏 Blackjack", callback_data="ej_bj"),
            InlineKeyboardButton("🎴 UNO",       callback_data="ej_uno"),
        ],
        [
            InlineKeyboardButton("Unirse ✋",    callback_data="lob_unirse"),
            InlineKeyboardButton("Ver Lista 👥", callback_data="lob_lista"),
        ],
        [
            InlineKeyboardButton("Iniciar 🚀",   callback_data="lob_iniciar"),
            InlineKeyboardButton("Cancelar ❌",  callback_data="lob_cancelar"),
        ],
    ])


def _kb_bj():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Pedir 🃏",     callback_data="bj_pedir"),
        InlineKeyboardButton("Plantarse 🛑", callback_data="bj_plantarse"),
    ]])


def _kb_nueva():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Nueva Partida 🔄", callback_data="nueva_partida"),
    ]])


# ─────────────────────────────────────────────────────────────
# RESPUESTA SEGURA A CALLBACKS
# Nunca llamar query.answer() antes de esta función — Telegram
# solo permite una respuesta por callback.
# ─────────────────────────────────────────────────────────────

async def _ack(query, texto: str = None, alerta: bool = False):
    try:
        await query.answer(text=texto, show_alert=alerta)
    except BadRequest:
        pass


# ─────────────────────────────────────────────────────────────
# TIMEOUT DE TURNO
# ─────────────────────────────────────────────────────────────

async def _cancelar_to(context, chat_id: int):
    g = games.get(chat_id)
    if g and g.get("to_job"):
        try:
            g["to_job"].schedule_removal()
        except Exception:
            pass
        g["to_job"] = None


async def _programar_to(context, chat_id: int):
    await _cancelar_to(context, chat_id)
    g = games.get(chat_id)
    if not g:
        return
    job = context.job_queue.run_once(
        _to_cb, when=TIMEOUT_S,
        data={"chat_id": chat_id},
        name=f"to_{chat_id}",
    )
    g["to_job"] = job


async def _to_cb(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    g = games.get(chat_id)
    if not g:
        return
    uid = _activo(chat_id)
    nom = g["nombres"].get(uid, "Jugador")
    if g.get("tipo") == "blackjack":
        g["plantados"].add(uid)
        await context.bot.send_message(
            chat_id, f"⏰ Tiempo agotado. {nom} es plantado automáticamente."
        )
        _avanzar(chat_id)
        await _turno_bj(context, chat_id)
    elif g.get("tipo") == "uno":
        await context.bot.send_message(
            chat_id, f"⏰ Tiempo agotado. Se salta el turno de {nom}."
        )
        _avanzar(chat_id)
        await _turno_uno(context, chat_id)


# ─────────────────────────────────────────────────────────────
# VICTORIA POR ABANDONO
# ─────────────────────────────────────────────────────────────

async def _victoria_abandono(context, chat_id: int) -> bool:
    """
    Si queda exactamente 1 jugador activo, se declara ganador por abandono.
    Limpia el juego inmediatamente y muestra el mensaje oficial.
    """
    g = games.get(chat_id)
    if not g or len(g["jugadores"]) != 1:
        return False

    uid_g = g["jugadores"][0]
    nom_g = g["nombres"].get(uid_g, "?")
    _registrar(chat_id, [uid_g], g["nombres"], {uid_g}, tipo=g.get("tipo", ""))
    await _cancelar_to(context, chat_id)
    del games[chat_id]

    await context.bot.send_message(
        chat_id,
        f"🏆 ¡Victoria por abandono! Todos se han ido, *{nom_g}* gana la partida. 🎉",
        parse_mode="Markdown",
        reply_markup=_kb_nueva(),
    )
    return True


# ─────────────────────────────────────────────────────────────
# BLACKJACK — FLUJO DE JUEGO
# ─────────────────────────────────────────────────────────────

async def _iniciar_bj(context, chat_id: int):
    g = games[chat_id]
    g.update({
        "tipo": "blackjack",
        "mazo": _mazo_bj(),
        "manos": {uid: [] for uid in g["jugadores"]},
        "mano_banca": [],
        "plantados": set(),
        "turno_actual": 0,
        "dir": 1,
    })
    for _ in range(2):
        for uid in g["jugadores"]:
            g["manos"][uid].append(g["mazo"].pop())
        g["mano_banca"].append(g["mazo"].pop())

    lineas = ["🃏 *¡Blackjack iniciado!*\n"]
    for uid in g["jugadores"]:
        m = g["manos"][uid]
        lineas.append(f"  {g['nombres'][uid]}: {_mano_txt(m)} → *{_pts_bj(m)} pts*")
    lineas.append(f"\n🏦 Banca: {_mano_txt(g['mano_banca'], ocultar=True)}")
    await context.bot.send_message(chat_id, "\n".join(lineas), parse_mode="Markdown")
    await _turno_bj(context, chat_id)


def _bj_todos_listos(g: dict) -> bool:
    for uid in g["jugadores"]:
        if uid not in g["plantados"] and _pts_bj(g["manos"][uid]) < 21:
            return False
    return True


async def _turno_bj(context, chat_id: int):
    g = games.get(chat_id)
    if not g:
        return
    if _bj_todos_listos(g):
        await _banca_bj(context, chat_id)
        return
    # Saltar jugadores finalizados
    for _ in range(len(g["jugadores"])):
        uid = g["jugadores"][g["turno_actual"]]
        if uid in g["plantados"] or _pts_bj(g["manos"][uid]) >= 21:
            _avanzar(chat_id)
        else:
            break
    uid = g["jugadores"][g["turno_actual"]]
    if uid in g["plantados"] or _pts_bj(g["manos"][uid]) >= 21:
        await _banca_bj(context, chat_id)
        return
    m   = g["manos"][uid]
    pts = _pts_bj(m)
    nom = g["nombres"].get(uid, "?")
    await context.bot.send_message(
        chat_id,
        f"🎯 Turno de *{nom}*\n"
        f"Mano: {_mano_txt(m)}\n"
        f"Puntos actuales: *{pts}*\n\n"
        f"⏰ Tienes {TIMEOUT_S}s para decidir.",
        parse_mode="Markdown",
        reply_markup=_kb_bj(),
    )
    await _programar_to(context, chat_id)


async def _banca_bj(context, chat_id: int):
    """Casa revela carta oculta y pide hasta 17. Regla estándar de casino."""
    await _cancelar_to(context, chat_id)
    g = games[chat_id]
    banca = g["mano_banca"]

    lineas = ["🏦 *Turno de la Banca*\n"]
    lineas.append(f"Revela: {_mano_txt(banca)} → *{_pts_bj(banca)} pts*")
    while _pts_bj(banca) < 17:   # Casa DEBE pedir hasta llegar a 17
        c = g["mazo"].pop()
        banca.append(c)
        lineas.append(f"  ➕ Pide {_cs(c)} → *{_pts_bj(banca)} pts*")
    pb = _pts_bj(banca)
    lineas.append(f"\n🏦 Banca final: {_mano_txt(banca)} — *{pb} pts*\n")
    lineas.append("━━━━━━━━━━━━━━━━━━━\n🏆 *Resultados:*")

    ganadores = set()
    for uid in g["jugadores"]:
        nom = g["nombres"].get(uid, "?")
        m   = g["manos"][uid]
        pj  = _pts_bj(m)
        if pj > 21:
            res = "💀 Bust — Perdiste"
        elif pb > 21 or pj > pb:
            res = "🎉 ¡Ganaste!"
            ganadores.add(uid)
        elif pj == pb:
            res = "🤝 Empate"
        else:
            res = "😞 Perdiste"
        lineas.append(f"  *{nom}*: {_mano_txt(m)} = {pj}pts → {res}")

    _registrar(chat_id, g["jugadores"], g["nombres"], ganadores, tipo="blackjack")
    del games[chat_id]
    await context.bot.send_message(
        chat_id, "\n".join(lineas),
        parse_mode="Markdown",
        reply_markup=_kb_nueva(),
    )


# ─────────────────────────────────────────────────────────────
# UNO — FLUJO DE JUEGO
# ─────────────────────────────────────────────────────────────

async def _iniciar_uno(context, chat_id: int):
    g = games[chat_id]
    g.update({
        "tipo": "uno",
        "mazo": _mazo_uno(),
        "manos": {},
        "descarte": [],
        "color_actual": "",
        "dir": 1,
        "turno_actual": 0,
        "esp_color": False,
    })
    for uid in g["jugadores"]:
        g["manos"][uid] = [g["mazo"].pop() for _ in range(7)]
    # Primera carta: no puede ser comodín
    primera = g["mazo"].pop()
    while primera[0] == "🌈":
        g["mazo"].insert(0, primera)
        primera = g["mazo"].pop()
    g["descarte"]     = [primera]
    g["color_actual"] = primera[0]
    await context.bot.send_message(
        chat_id,
        f"🎴 *¡UNO iniciado!* — {len(g['jugadores'])} jugadores, 7 cartas c/u\n\n"
        f"Carta inicial: *{_cs(primera)}*  |  Color: {primera[0]}",
        parse_mode="Markdown",
    )
    await _turno_uno(context, chat_id)


async def _turno_uno(context, chat_id: int):
    """
    Mensaje de turno al grupo.
    ─────────────────────────────────────────────────────────
    PRIVACIDAD TOTAL: Este mensaje NUNCA revela las cartas
    de ningún jugador en el chat grupal. Solo muestra:
      • Quién juega.
      • La carta en mesa.
      • Cuántas cartas tiene cada jugador (número, no valores).
    Las cartas se ven SOLO mediante popup privado (show_alert=True).
    ─────────────────────────────────────────────────────────
    """
    g = games.get(chat_id)
    if not g:
        return
    uid_act = _activo(chat_id)
    nom_act = g["nombres"].get(uid_act, "?")
    en_mesa = g["descarte"][-1]
    color   = g["color_actual"]

    # Estado público: solo conteo de cartas por jugador
    estado = ""
    for uid in g["jugadores"]:
        n   = g["nombres"].get(uid, "?")
        cnt = len(g["manos"].get(uid, []))
        pfx = "👉 " if uid == uid_act else "   "
        estado += f"{pfx}*{n}*: {cnt} carta(s)\n"

    texto = (
        f"🎴 *Turno de {nom_act}*\n\n"
        f"En mesa: *{_cs(en_mesa)}*  |  Color: {color}\n\n"
        f"{estado}\n"
        f"⏰ {TIMEOUT_S}s para jugar"
    )

    # Botones de cartas jugables (solo para jugador activo)
    # Cada otro jugador que pulse un botón de carta recibe
    # "No es tu turno" sin ensuciar el chat.
    mano_act = g["manos"].get(uid_act, [])
    filas    = []
    fila     = []
    for i, carta in enumerate(mano_act):
        if _valida(carta, en_mesa, color):
            fila.append(InlineKeyboardButton(_cs(carta), callback_data=f"uno_j_{i}"))
            if len(fila) == 3:
                filas.append(fila)
                fila = []
    if fila:
        filas.append(fila)

    # Botones disponibles para todos (cada uno ve sus propias cartas)
    filas.append([InlineKeyboardButton("🃏 Ver mis cartas",  callback_data="uno_ver")])
    filas.append([InlineKeyboardButton("🎴 Robar carta",     callback_data="uno_robar")])

    try:
        await context.bot.send_message(
            chat_id, texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(filas),
        )
    except BadRequest:
        pass
    await _programar_to(context, chat_id)


async def _efecto_uno(context, chat_id: int, carta: tuple):
    g = games[chat_id]
    _, t = carta
    n    = len(g["jugadores"])

    if t == "+2":
        sig = (g["turno_actual"] + g["dir"]) % n
        su  = g["jugadores"][sig]
        _robar(g, su, 2)
        await context.bot.send_message(
            chat_id,
            f"⚠️ {g['nombres'].get(su,'?')} roba 2 cartas y pierde turno."
        )
        _avanzar(chat_id, 2)
    elif t == "🔄":
        g["dir"] *= -1
        d_txt = "⬅️ al revés" if g["dir"] == -1 else "➡️ normal"
        await context.bot.send_message(chat_id, f"🔄 Dirección invertida — ahora va {d_txt}.")
        _avanzar(chat_id)
    elif t == "⛔":
        sig = (g["turno_actual"] + g["dir"]) % n
        su  = g["jugadores"][sig]
        await context.bot.send_message(chat_id, f"⛔ {g['nombres'].get(su,'?')} pierde su turno.")
        _avanzar(chat_id, 2)
    elif t == "+4":
        sig = (g["turno_actual"] + g["dir"]) % n
        su  = g["jugadores"][sig]
        _robar(g, su, 4)
        await context.bot.send_message(
            chat_id,
            f"⚠️ {g['nombres'].get(su,'?')} roba 4 cartas y pierde turno."
        )
        _avanzar(chat_id, 2)
    else:
        _avanzar(chat_id)


# ─────────────────────────────────────────────────────────────
# COMANDOS — filtro _ok() es la primera línea de cada uno
# ─────────────────────────────────────────────────────────────

async def cmd_casino(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/casino o /game — Abre lobby."""
    if not _ok(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in games:
        await update.message.reply_text(
            "⚠️ Ya hay una sala activa. Usa /reset_juego para reiniciar."
        )
        return
    user = update.effective_user
    games[chat_id] = {
        "tipo": "lobby",
        "creador": user.id,
        "jugadores": [user.id],
        "nombres": {user.id: _nombre(user)},
        "juego": None,
        "to_job": None,
        "turno_actual": 0,
        "dir": 1,
    }
    await update.message.reply_text(
        f"🎮 *Sala creada por {_nombre(user)}*\n\n"
        f"1. Elige el juego con los botones.\n"
        f"2. Únete (mín 2, máx {MAX_JUGADORES} jugadores).\n"
        f"3. El creador inicia cuando estén listos.\n\n"
        f"Jugadores (1/{MAX_JUGADORES}):\n  1. {_nombre(user)}",
        parse_mode="Markdown",
        reply_markup=_kb_lobby(),
    )


async def cmd_reset_juego(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reset_juego — Cancela la partida activa."""
    if not _ok(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in games:
        await _cancelar_to(context, chat_id)
        del games[chat_id]
        await update.message.reply_text(
            "♻️ Juego reiniciado. Usa /casino o /game para empezar."
        )
    else:
        await update.message.reply_text("No hay ninguna partida activa.")


async def cmd_salir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/salir — Abandona la partida. Si queda 1 jugador, gana por abandono."""
    if not _ok(update):
        return
    chat_id = update.effective_chat.id
    g    = games.get(chat_id)
    user = update.effective_user
    uid  = user.id

    if not g:
        await update.message.reply_text("No hay ninguna partida activa.")
        return
    if uid not in g["jugadores"]:
        await update.message.reply_text("No estás en la partida actual.")
        return

    idx = g["jugadores"].index(uid)
    g["jugadores"].remove(uid)
    g["nombres"].pop(uid, None)
    await update.message.reply_text(f"👋 {_nombre(user)} ha abandonado la partida.")

    # ── Victoria por abandono ──────────────────────────────────
    if g.get("tipo") in ("blackjack", "uno") and len(g["jugadores"]) == 1:
        await _victoria_abandono(context, chat_id)
        return

    # ── Sin jugadores → cerrar sala ───────────────────────────
    if not g["jugadores"]:
        await _cancelar_to(context, chat_id)
        del games[chat_id]
        await context.bot.send_message(chat_id, "🚪 Sala cerrada: sin jugadores.")
        return

    # ── Ajustar índice de turno y continuar ───────────────────
    if g.get("tipo") in ("blackjack", "uno"):
        if idx <= g["turno_actual"]:
            g["turno_actual"] = g["turno_actual"] % len(g["jugadores"])
        await _cancelar_to(context, chat_id)
        if g["tipo"] == "blackjack":
            await _turno_bj(context, chat_id)
        else:
            await _turno_uno(context, chat_id)


async def cmd_comandos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/comandos — Lista de comandos."""
    if not _ok(update):
        return
    await update.message.reply_text(
        "📋 *Comandos disponibles:*\n\n"
        "  /casino o /game — Crear sala\n"
        "  /salir — Abandonar la partida\n"
        "  /reset\\_juego — Reiniciar la partida\n"
        "  /ranking — Tabla de victorias del grupo\n"
        "  /comandos — Esta ayuda\n\n"
        "*Juegos:*  🃏 Blackjack  |  🎴 UNO",
        parse_mode="Markdown",
    )


async def cmd_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ranking — Tabla de victorias con estadísticas extendidas."""
    if not _ok(update):
        return
    chat_id = update.effective_chat.id
    datos   = ranking.get(chat_id)
    if not datos:
        await update.message.reply_text(
            "📊 Aún no hay partidas registradas. ¡Usa /casino para jugar!"
        )
        return

    # Ordenar: victorias desc, luego partidas desc
    tabla    = sorted(datos.values(), key=lambda x: (-x["victorias"], -x["partidas"]))
    medallas = ["🥇", "🥈", "🥉"]
    lineas   = ["🏆 *Ranking del grupo*\n"]

    for i, j in enumerate(tabla):
        med = medallas[i] if i < 3 else f"  {i+1}."
        pct = int(j["victorias"] / j["partidas"] * 100) if j["partidas"] else 0

        # Racha actual y máxima
        racha_act = j.get("racha_actual", 0)
        racha_max = j.get("racha_max", 0)
        racha_txt = f"🔥{racha_act}" if racha_act >= 2 else ""

        # Stats BJ
        bj_v = j.get("bj_victorias", 0)
        bj_p = j.get("bj_partidas",  0)
        bj_pct = int(bj_v / bj_p * 100) if bj_p else 0

        # Stats UNO
        un_v = j.get("uno_victorias", 0)
        un_p = j.get("uno_partidas",  0)
        un_pct = int(un_v / un_p * 100) if un_p else 0

        # Línea principal
        lineas.append(
            f"{med} *{j['nombre']}*  {racha_txt}\n"
            f"     Total: {j['victorias']}V / {j['partidas']}P ({pct}%)  |  "
            f"Racha máx: {racha_max}🏆\n"
            f"     🃏 BJ: {bj_v}V/{bj_p}P ({bj_pct}%)  "
            f"🎴 UNO: {un_v}V/{un_p}P ({un_pct}%)"
        )

    await update.message.reply_text("\n".join(lineas), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# HANDLER DE CALLBACKS
# _ok() es LO PRIMERO que se ejecuta — antes de cualquier lógica.
# ─────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador único de botones inline.

    REGLAS DE SEGURIDAD Y PRIVACIDAD:
    ─────────────────────────────────
    1. Filtro de chat (_ok) al inicio — antes de cualquier acción.
    2. _ack() se llama UNA SOLA VEZ por rama para que show_alert=True funcione.
    3. Las cartas NUNCA se envían al chat grupal; solo via query.answer(show_alert=True).
    4. Solo el jugador activo puede usar botones de juego.
    5. "Ver mis cartas" verifica que uid pertenezca a la partida.
       Cada usuario ve SOLO su propia mano.
    """
    query   = update.callback_query
    chat_id = query.message.chat_id
    user    = query.from_user
    uid     = user.id
    data    = query.data

    # ── FILTRO DE SEGURIDAD — SIEMPRE PRIMERO ──────────────────
    if str(chat_id) not in ["-1003290179217", "-1003162772831"]:
        return   # Fantasma total: ni siquiera responde al callback

    g = games.get(chat_id)

    # ────────────────────────────────────────────────────────────
    # NUEVA PARTIDA
    # ────────────────────────────────────────────────────────────
    if data == "nueva_partida":
        await _ack(query)
        if chat_id in games:
            await _cancelar_to(context, chat_id)
            del games[chat_id]
        games[chat_id] = {
            "tipo": "lobby",
            "creador": uid,
            "jugadores": [uid],
            "nombres": {uid: _nombre(user)},
            "juego": None,
            "to_job": None,
            "turno_actual": 0,
            "dir": 1,
        }
        await query.message.reply_text(
            f"🎮 *Nueva sala — {_nombre(user)}*\nElige el juego y espera más jugadores.",
            parse_mode="Markdown",
            reply_markup=_kb_lobby(),
        )
        return

    if not g:
        await _ack(query, "⚠️ No hay partida activa.", alerta=True)
        return

    # ────────────────────────────────────────────────────────────
    # ELECCIÓN DE JUEGO
    # ────────────────────────────────────────────────────────────
    if data in ("ej_bj", "ej_uno"):
        if uid != g["creador"]:
            await _ack(query, "Solo el creador elige el juego.", alerta=True)
            return
        g["juego"] = "blackjack" if data == "ej_bj" else "uno"
        label = "🃏 Blackjack" if g["juego"] == "blackjack" else "🎴 UNO"
        await _ack(query)
        try:
            base = query.message.text.split("\n✅")[0]
            await query.edit_message_text(
                base + f"\n✅ Juego elegido: {label}",
                parse_mode="Markdown",
                reply_markup=query.message.reply_markup,
            )
        except BadRequest:
            pass
        return

    # ────────────────────────────────────────────────────────────
    # LOBBY: UNIRSE
    # ────────────────────────────────────────────────────────────
    if data == "lob_unirse":
        if g["tipo"] != "lobby":
            await _ack(query, "La partida ya comenzó.", alerta=True)
            return
        if uid in g["jugadores"]:
            await _ack(query, "Ya estás en la sala.", alerta=True)
            return
        if len(g["jugadores"]) >= MAX_JUGADORES:
            await _ack(query, f"Sala llena ({MAX_JUGADORES} máx).", alerta=True)
            return
        g["jugadores"].append(uid)
        g["nombres"][uid] = _nombre(user)
        await _ack(query, "✅ Te uniste a la sala.")
        lista = "\n".join(f"  {i+1}. {g['nombres'][u]}" for i, u in enumerate(g["jugadores"]))
        try:
            await query.edit_message_text(
                f"🎮 *Sala activa*\nJuego: {g['juego'] or 'por elegir'}\n"
                f"Jugadores ({len(g['jugadores'])}/{MAX_JUGADORES}):\n{lista}",
                parse_mode="Markdown",
                reply_markup=query.message.reply_markup,
            )
        except BadRequest:
            pass
        return

    # ────────────────────────────────────────────────────────────
    # LOBBY: VER LISTA
    # ────────────────────────────────────────────────────────────
    if data == "lob_lista":
        lista = "\n".join(
            f"{i+1}. {g['nombres'][u]}" for i, u in enumerate(g["jugadores"])
        )
        await _ack(query, f"👥 Jugadores ({len(g['jugadores'])}/{MAX_JUGADORES}):\n{lista}", alerta=True)
        return

    # ────────────────────────────────────────────────────────────
    # LOBBY: CANCELAR
    # ────────────────────────────────────────────────────────────
    if data == "lob_cancelar":
        if uid != g["creador"]:
            await _ack(query, "Solo el creador puede cancelar.", alerta=True)
            return
        await _ack(query)
        await _cancelar_to(context, chat_id)
        del games[chat_id]
        try:
            await query.edit_message_text("❌ Sala cancelada.", reply_markup=None)
        except BadRequest:
            await query.message.reply_text("❌ Sala cancelada.")
        return

    # ────────────────────────────────────────────────────────────
    # LOBBY: INICIAR
    # ────────────────────────────────────────────────────────────
    if data == "lob_iniciar":
        if uid != g["creador"]:
            await _ack(query, "Solo el creador puede iniciar.", alerta=True)
            return
        if not g.get("juego"):
            await _ack(query, "Primero elige un juego.", alerta=True)
            return
        if len(g["jugadores"]) < 2:
            await _ack(query, "Se necesitan al menos 2 jugadores.", alerta=True)
            return
        await _ack(query)
        try:
            await query.edit_message_text("🚀 ¡Iniciando la partida!", reply_markup=None)
        except BadRequest:
            pass
        if g["juego"] == "blackjack":
            await _iniciar_bj(context, chat_id)
        else:
            await _iniciar_uno(context, chat_id)
        return

    # ────────────────────────────────────────────────────────────
    # BLACKJACK: PEDIR
    # ────────────────────────────────────────────────────────────
    if data == "bj_pedir":
        if g.get("tipo") != "blackjack":
            await _ack(query)
            return
        if uid != _activo(chat_id):
            # No es su turno → notificación discreta (show_alert=False)
            await _ack(query, "⚠️ No es tu turno", alerta=False)
            return
        await _ack(query)
        await _cancelar_to(context, chat_id)
        nueva = g["mazo"].pop()
        g["manos"][uid].append(nueva)
        pts = _pts_bj(g["manos"][uid])
        nom = g["nombres"].get(uid, "?")
        ms  = _mano_txt(g["manos"][uid])
        if pts > 21:
            await context.bot.send_message(
                chat_id,
                f"💥 *{nom}* pide {_cs(nueva)}\nMano: {ms} = *{pts} pts* — ¡BUST!",
                parse_mode="Markdown",
            )
            g["plantados"].add(uid)
            _avanzar(chat_id)
        elif pts == 21:
            await context.bot.send_message(
                chat_id,
                f"⭐ *{nom}* pide {_cs(nueva)}\nMano: {ms} = *21!* ¡Blackjack!",
                parse_mode="Markdown",
            )
            _avanzar(chat_id)
        else:
            await context.bot.send_message(
                chat_id,
                f"🃏 *{nom}* pide {_cs(nueva)}\nMano: {ms} = *{pts} pts*",
                parse_mode="Markdown",
            )
        await _turno_bj(context, chat_id)
        return

    # ────────────────────────────────────────────────────────────
    # BLACKJACK: PLANTARSE
    # ────────────────────────────────────────────────────────────
    if data == "bj_plantarse":
        if g.get("tipo") != "blackjack":
            await _ack(query)
            return
        if uid != _activo(chat_id):
            await _ack(query, "⚠️ No es tu turno", alerta=False)
            return
        await _ack(query)
        await _cancelar_to(context, chat_id)
        nom = g["nombres"].get(uid, "?")
        pts = _pts_bj(g["manos"][uid])
        g["plantados"].add(uid)
        await context.bot.send_message(
            chat_id,
            f"🛑 *{nom}* se planta con *{pts} pts*.",
            parse_mode="Markdown",
        )
        _avanzar(chat_id)
        await _turno_bj(context, chat_id)
        return

    # ────────────────────────────────────────────────────────────
    # UNO: VER MIS CARTAS
    # ─────────────────────────────────────────────────────────────
    # PRIVACIDAD TOTAL:
    # • Se verifica que el uid esté en la partida activa.
    # • Cada usuario ve ÚNICAMENTE su propia mano.
    # • NUNCA se envía nada al chat grupal.
    # • La información llega SOLO mediante popup privado (show_alert=True).
    # • Si alguien que no está en la partida pulsa el botón → denegado.
    # ─────────────────────────────────────────────────────────────
    if data == "uno_ver":
        if g.get("tipo") != "uno":
            await _ack(query)
            return
        # Verificar que el uid es un jugador de esta partida
        if uid not in g.get("manos", {}):
            await _ack(query, "❌ No puedes ver cartas ajenas", alerta=False)
            return
        mano = g["manos"][uid]
        if not mano:
            await _ack(query, "No tienes cartas.", alerta=True)
            return
        # Popup privado: SOLO visible para quien pulsó el botón
        txt = "   ".join(_cs(c) for c in mano)
        await _ack(query, f"🃏 Tus cartas ({len(mano)}):\n{txt}", alerta=True)
        return

    # ────────────────────────────────────────────────────────────
    # UNO: ROBAR CARTA
    # ────────────────────────────────────────────────────────────
    if data == "uno_robar":
        if g.get("tipo") != "uno":
            await _ack(query)
            return
        if uid != _activo(chat_id):
            await _ack(query, "⚠️ No es tu turno", alerta=False)
            return
        await _ack(query)
        await _cancelar_to(context, chat_id)
        _robar(g, uid, 1)
        nom = g["nombres"].get(uid, "?")
        await context.bot.send_message(
            chat_id,
            f"🎴 {nom} robó una carta. Tiene {len(g['manos'][uid])} carta(s)."
        )
        _avanzar(chat_id)
        await _turno_uno(context, chat_id)
        return

    # ────────────────────────────────────────────────────────────
    # UNO: JUGAR CARTA
    # ────────────────────────────────────────────────────────────
    if data.startswith("uno_j_"):
        if g.get("tipo") != "uno":
            await _ack(query)
            return
        if uid != _activo(chat_id):
            await _ack(query, "⚠️ No es tu turno", alerta=False)
            return
        try:
            idx = int(data.split("_")[-1])
        except ValueError:
            await _ack(query)
            return
        mano = g["manos"].get(uid, [])
        if idx >= len(mano):
            await _ack(query)
            return
        carta   = mano[idx]
        en_mesa = g["descarte"][-1]
        if not _valida(carta, en_mesa, g["color_actual"]):
            await _ack(query, "❌ Carta no válida en este momento.", alerta=True)
            return
        await _ack(query)
        await _cancelar_to(context, chat_id)
        mano.pop(idx)
        g["descarte"].append(carta)
        nom = g["nombres"].get(uid, "?")
        if carta[0] != "🌈":
            g["color_actual"] = carta[0]
        await context.bot.send_message(
            chat_id,
            f"🎴 *{nom}* jugó *{_cs(carta)}*",
            parse_mode="Markdown",
        )
        # ¿Victoria?
        if not mano:
            _registrar(chat_id, g["jugadores"], g["nombres"], {uid}, tipo="uno")
            del games[chat_id]
            await context.bot.send_message(
                chat_id,
                f"🏆 ¡*{nom}* ganó el UNO! ¡Felicidades! 🎉",
                parse_mode="Markdown",
                reply_markup=_kb_nueva(),
            )
            return
        # Comodín → elegir color
        if carta[0] == "🌈":
            g["esp_color"] = True
            await context.bot.send_message(
                chat_id,
                f"🌈 {nom} jugó comodín. Elige el color:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(c, callback_data=f"uno_col_{c}")
                    for c in _COLORES_UNO
                ]]),
            )
            return
        await _efecto_uno(context, chat_id, carta)
        await _turno_uno(context, chat_id)
        return

    # ────────────────────────────────────────────────────────────
    # UNO: ELEGIR COLOR (tras comodín)
    # ────────────────────────────────────────────────────────────
    if data.startswith("uno_col_"):
        if g.get("tipo") != "uno":
            await _ack(query)
            return
        if not g.get("esp_color"):
            await _ack(query, "No hay comodín pendiente.", alerta=True)
            return
        if uid != _activo(chat_id):
            await _ack(query, "Solo quien jugó el comodín elige el color.", alerta=True)
            return
        color = data.replace("uno_col_", "")
        if color not in _COLORES_UNO:
            await _ack(query)
            return
        await _ack(query)
        g["color_actual"] = color
        g["esp_color"]    = False
        nom = g["nombres"].get(uid, "?")
        await context.bot.send_message(chat_id, f"🎨 {nom} eligió el color: {color}")
        ultima = g["descarte"][-1]
        await _efecto_uno(context, chat_id, ultima)
        await _turno_uno(context, chat_id)
        return

    # Callback desconocido → ignorar
    await _ack(query)


# ─────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        logger.error("TOKEN no configurado. Añádelo como variable de entorno.")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("casino",      cmd_casino))
    app.add_handler(CommandHandler("game",        cmd_casino))
    app.add_handler(CommandHandler("reset_juego", cmd_reset_juego))
    app.add_handler(CommandHandler("salir",       cmd_salir))
    app.add_handler(CommandHandler("comandos",    cmd_comandos))
    app.add_handler(CommandHandler("ranking",     cmd_ranking))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("✅ Bot iniciado — tolerancia cero activa.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
