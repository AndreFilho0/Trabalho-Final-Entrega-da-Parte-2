"""A base de dados replicada: o conjunto de mesas de truco (código -> mesa).

Cada peer guarda uma cópia. As alterações chegam como comandos entregues pelo
multicast e `apply` é determinística: mesmo estado + mesmo comando dá o mesmo
resultado em qualquer réplica, inclusive os erros.

Comandos (listas JSON, que é o que viaja nos datagramas):

    ["create",  código, {"id", "name"}, modo, semente]   cria a mesa ("1v1" ou "2v2")
    ["join",    código, {"id", "name"}]                  entra no lobby
    ["seat",    código, jogador, cadeira]                senta (0..1 ou 0..3)
    ["leave",   código, jogador]                         sai do lobby ou desiste da partida
    ["start",   código, jogador]                         anfitrião começa a partida
    ["act",     código, jogador, ação]                   jogada (ver truco.game)
    ["rematch", código, jogador]                         anfitrião começa outra partida

Por que a ordem importa: se dois jogadores pedem a cadeira 0 ao mesmo tempo, a
réplica que aplicar o pedido de A primeiro dá a cadeira para A e rejeita B; a que
aplicar B primeiro faz o contrário. Agora A está no time A numa réplica e sem
cadeira na outra.
"""
import copy

from truco import game
from truco.rules import RuleError

MAX_EVENTS = 40
CAPACITY = {"1v1": 2, "2v2": 4}


def apply(rooms, cmd):
    """Aplica `cmd` em `rooms` (alterando o dicionário).

    Devolve (código, resultado, eventos), com resultado = ["ok", valor] ou
    ["error", motivo]. Em caso de erro a base fica como estava.
    """
    try:
        name, code = cmd[0], cmd[1]
        handler = _COMMANDS[name]
    except (TypeError, IndexError, KeyError):
        return None, ["error", "unknown_command"], []
    room = rooms.get(code)
    if name == "create":
        if room is not None:
            return code, ["error", "code_taken"], []
    elif room is None:
        return code, ["error", "room_not_found"], []

    work = copy.deepcopy(room)
    try:
        new_room, value, events = handler(work, *cmd[2:])
    except RuleError as e:
        return code, ["error", str(e)], []
    except (TypeError, ValueError, KeyError):
        return code, ["error", "bad_command"], []

    if new_room is None:
        del rooms[code]
        return code, ["ok", value], []
    new_room["version"] += 1
    for ev in events:
        ev["version"] = new_room["version"]
    new_room["events"] = (new_room["events"] + events)[-MAX_EVENTS:]
    rooms[code] = new_room
    return code, ["ok", value], events


# --- comandos ------------------------------------------------------------------

def _create(_, player, mode, seed):
    if mode not in CAPACITY:
        raise RuleError("invalid_mode")
    player = _player(player)
    room = {
        "mode": mode,
        "host_id": player["id"],
        "seed": int(seed),
        "status": "lobby",
        "members": [player],
        "seats": {},
        "game": None,
        "games_played": 0,
        "version": 0,
        "events": [],
    }
    return room, "created", [{"type": "room_created", "player_id": player["id"], "mode": mode}]


def _join(room, player):
    player = _player(player)
    if _member(room, player["id"]):
        return room, "already_member", []
    if room["status"] != "lobby":
        raise RuleError("game_in_progress")
    if len(room["members"]) >= CAPACITY[room["mode"]]:
        raise RuleError("room_full")
    room["members"].append(player)
    return room, "joined", [{"type": "player_joined", "player_id": player["id"], "name": player["name"]}]


def _seat(room, player_id, seat):
    if room["status"] != "lobby":
        raise RuleError("game_in_progress")
    if not _member(room, player_id):
        raise RuleError("not_in_room")
    if not isinstance(seat, int) or not 0 <= seat < CAPACITY[room["mode"]]:
        raise RuleError("invalid_seat")
    if room["seats"].get(seat) == player_id:
        return room, seat, []
    if seat in room["seats"]:
        raise RuleError("seat_taken")
    room["seats"] = {s: p for s, p in room["seats"].items() if p != player_id}
    room["seats"][seat] = player_id
    return room, seat, [{"type": "seat_taken", "player_id": player_id, "seat": seat, "team": team_of(seat)}]


def _leave(room, player_id):
    if not _member(room, player_id):
        raise RuleError("not_in_room")
    if room["status"] == "playing":
        # Sair no meio da partida é desistir: o outro time vence.
        events = game.apply(room["game"], player_id, "forfeit")
        room["status"] = "finished"
        return room, "forfeited", events
    room["members"] = [m for m in room["members"] if m["id"] != player_id]
    room["seats"] = {s: p for s, p in room["seats"].items() if p != player_id}
    if not room["members"]:
        return None, "room_closed", []
    if room["host_id"] == player_id:
        room["host_id"] = room["members"][0]["id"]
    return room, "left", [{"type": "player_left", "player_id": player_id, "host_id": room["host_id"]}]


def _start(room, player_id):
    if room["host_id"] != player_id:
        raise RuleError("not_host")
    if room["status"] == "playing":
        raise RuleError("game_in_progress")
    if len(room["seats"]) < CAPACITY[room["mode"]]:
        raise RuleError("seats_not_filled")
    return _start_game(room)


def _rematch(room, player_id):
    if room["host_id"] != player_id:
        raise RuleError("not_host")
    if room["status"] != "finished":
        raise RuleError("game_not_finished")
    return _start_game(room)


def _act(room, player_id, action):
    if room["status"] != "playing":
        raise RuleError("game_not_running")
    events = game.apply(room["game"], player_id, action)
    if room["game"]["status"] == "finished":
        room["status"] = "finished"
    return room, "ok", events


def _start_game(room):
    names = {m["id"]: m["name"] for m in room["members"]}
    players = [{"id": pid, "name": names[pid], "team": team_of(seat), "seat": seat}
               for seat, pid in room["seats"].items()]
    # A semente veio no "create" (sorteada pelo peer de origem): é igual em todas as réplicas.
    room["game"], events = game.new_game(players, room["mode"], room["seed"] + room["games_played"])
    room["status"] = "playing"
    room["games_played"] += 1
    return room, "started", events


_COMMANDS = {"create": _create, "join": _join, "seat": _seat, "leave": _leave,
             "start": _start, "rematch": _rematch, "act": _act}


# --- visões --------------------------------------------------------------------

def view(room, code, player_id):
    """O que o jogador vê da mesa: lobby, placar e só a própria mão."""
    seat_of = {p: s for s, p in room["seats"].items()}
    names = {m["id"]: m["name"] for m in room["members"]}
    seat = seat_of.get(player_id)
    return {
        "code": code,
        "mode": room["mode"],
        "status": room["status"],
        "host_id": room["host_id"],
        "capacity": CAPACITY[room["mode"]],
        "members": room["members"],
        "seats": [{"seat": s, "team": team_of(s),
                   "player": {"id": room["seats"][s], "name": names[room["seats"][s]]} if s in room["seats"] else None}
                  for s in range(CAPACITY[room["mode"]])],
        "version": room["version"],
        "me": {"id": player_id, "seat": seat, "team": team_of(seat) if seat is not None else None},
        "is_host": room["host_id"] == player_id,
        "game": game.view(room["game"], player_id) if room["game"] else None,
        "events": room["events"][-15:],
    }


def summary(room):
    """Resumo usado para comparar réplicas: quem está em cada cadeira e o placar."""
    g = room["game"]
    return {
        "status": room["status"],
        "seats": {str(s): p for s, p in sorted(room["seats"].items())},
        "scores": dict(g["scores"]) if g else None,
        "round": g["round"]["number"] if g and g["round"] else None,
        "version": room["version"],
    }


# --- auxiliares ----------------------------------------------------------------

def team_of(seat):
    """Cadeiras pares são do time A e ímpares do time B (no 2v2, parceiros de frente)."""
    return "a" if seat % 2 == 0 else "b"


def _member(room, player_id):
    return any(m["id"] == player_id for m in room["members"])


def _player(p):
    if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not isinstance(p.get("name"), str):
        raise RuleError("invalid_player")
    return {"id": p["id"], "name": p["name"][:24]}
