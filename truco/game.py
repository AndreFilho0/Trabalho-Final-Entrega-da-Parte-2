"""Partida de truco mineiro como máquina de estados determinística.

Não há relógio, timer nem sorteio fora da semente: o próximo estado depende só
de (estado, jogador, ação). É isso que permite replicar a partida em vários
peers: se todos aplicarem as mesmas ações na mesma ordem, terminam iguais.

`apply` altera o estado recebido e devolve a lista de eventos (para a interface
mostrar o que aconteceu) ou levanta RuleError. Quem chama (rooms.apply) trabalha
sobre uma cópia, então uma ação rejeitada não deixa nada pela metade.

Ações: ["play_card", carta], "truco", "accept", "reject",
       "accept_mao_de_11", "reject_mao_de_11", "forfeit".
"""
from truco import deck, rules
from truco.evaluator import evaluate_round, evaluate_trick
from truco.rules import RuleError

def new_game(players, mode, seed):
    """players: [{"id", "name", "team", "seat"}]. Dá as cartas da primeira mão."""
    expected = 2 if mode == "1v1" else 4
    if len(players) != expected:
        raise ValueError(f"modo {mode} precisa de {expected} jogadores")
    players = sorted(({k: p[k] for k in ("id", "name", "team", "seat")} for p in players),
                     key=lambda p: p["seat"])
    state = {
        "mode": mode,
        "players": players,
        "seat_order": [p["id"] for p in players],
        "scores": {"a": 0, "b": 0},
        "status": "playing",
        "winner_team": None,
        "finish_reason": None,
        "round": None,
        "starting_seat": 0,
        "seed": seed,
        "last_trick": None,
        "last_round": None,
    }
    events = [{"type": "game_started", "mode": mode}]
    _start_round(state, 1, events)
    return state, events


def apply(state, player_id, action):
    if state["status"] == "finished":
        raise RuleError("game_finished")
    if _player(state, player_id) is None:
        raise RuleError("not_in_game")
    events = []
    if isinstance(action, (list, tuple)) and len(action) == 2 and action[0] == "play_card":
        _play_card(state, player_id, action[1], events)
    elif isinstance(action, str) and action in _HANDLERS:
        _HANDLERS[action](state, player_id, events)
    else:
        raise RuleError("unknown_action")
    return events


# --- jogar carta ---------------------------------------------------------------

def _play_card(state, player_id, card, events):
    rnd = _active_round(state)
    if rnd["bet"]["pending_team"] is not None:
        raise RuleError("truco_pending")
    if rnd["status"] == "mao_de_11":
        raise RuleError("mao_de_11_pending")
    if rnd["status"] not in ("playing", "mao_de_ferro"):
        raise RuleError("round_not_open_for_play")
    if rnd["current_turn"] != player_id:
        raise RuleError("not_your_turn")
    if card not in rnd["hands"][player_id]:
        raise RuleError("card_not_in_hand")

    rnd["hands"][player_id].remove(card)
    rnd["plays_in_trick"].append({"player_id": player_id, "team": _player(state, player_id)["team"],
                                  "card": card})
    events.append({"type": "card_played", "player_id": player_id, "card": card,
                   "trick_number": len(rnd["trick_results"]) + 1})
    if len(rnd["plays_in_trick"]) == len(state["players"]):
        _finish_trick(state, rnd, events)
    else:
        rnd["current_turn"] = _next_in_order(state, player_id)


def _finish_trick(state, rnd, events):
    winner = evaluate_trick(rnd["plays_in_trick"])
    winner_pid, winner_team = winner if winner else (None, None)
    last_trick = {
        "number": len(rnd["trick_results"]) + 1,
        "winner_team": winner_team,
        "winner_player_id": winner_pid,
        "plays": [{"player_id": p["player_id"], "card": p["card"]} for p in rnd["plays_in_trick"]],
    }
    events.append({"type": "trick_finished", **last_trick})
    state["last_trick"] = last_trick
    rnd["trick_results"].append(winner_team or "draw")
    first_player = rnd["plays_in_trick"][0]["player_id"]
    rnd["plays_in_trick"] = []

    result = evaluate_round(rnd["trick_results"])
    if result is None:
        # Quem ganhou a vaza começa a próxima; se cangou, começa quem abriu.
        rnd["current_turn"] = winner_pid or first_player
    elif result == "draw":
        _award(state, None, 0, "played", events)
    else:
        _award(state, result, rnd["bet"]["value"], "played", events)


def _award(state, team, tentos, reason, events):
    rnd = state["round"]
    if team:
        state["scores"][team] += tentos
    state["last_round"] = {"number": rnd["number"], "winner_team": team, "tentos": tentos,
                           "bet_value": rnd["bet"]["value"], "reason": reason}
    events.append({"type": "round_finished", **state["last_round"], "scores": dict(state["scores"])})
    state["round"] = None

    winner = rules.game_winner(state["scores"])
    if winner:
        _finish(state, winner, "completed", events)
    else:
        state["starting_seat"] += 1
        _start_round(state, rnd["number"] + 1, events)


def _finish(state, winner, reason, events):
    state.update(status="finished", winner_team=winner, finish_reason=reason, round=None)
    events.append({"type": "game_over", "winner_team": winner, "scores": dict(state["scores"]),
                   "reason": reason})


# --- início de mão -------------------------------------------------------------

def _start_round(state, number, events):
    n = len(state["players"])
    hands = deck.deal(deck.shuffle(deck.new_deck(), state["seed"] * 1000 + number), n)
    category = rules.round_category(state["scores"]["a"], state["scores"]["b"])
    if category == "normal":
        status, value = "playing", 1
    elif category == "mao_de_ferro":
        status, value = "mao_de_ferro", 3   # às cegas, vale 3, sem truco
    else:
        status, value = "mao_de_11", 3      # o time com 11 decide se joga
    starter = state["seat_order"][state["starting_seat"] % n]
    state["round"] = {
        "number": number,
        "hands": dict(zip(state["seat_order"], hands)),
        "plays_in_trick": [],
        "trick_results": [],
        "bet": rules.initial_bet(value),
        "category": category,
        "status": status,
        "current_turn": starter,
    }
    events.append({"type": "round_started", "number": number, "category": category,
                   "current_bet": value, "current_turn": starter})


# --- truco ---------------------------------------------------------------------

def _truco(state, player_id, events):
    rnd = _active_round(state)
    if rnd["category"] != "normal":
        raise RuleError("truco_disabled_in_special_round")
    if rnd["status"] not in ("playing", "truco_pending"):
        raise RuleError("round_not_open_for_play")
    team = _player(state, player_id)["team"]
    if rnd["bet"]["pending_team"] is None:
        rnd["bet"] = rules.call_truco(rnd["bet"], team)
        kind = "truco_called"
    else:
        rnd["bet"] = rules.raise_bet(rnd["bet"], team)
        kind = "truco_raised"
    rnd["status"] = "truco_pending"
    events.append({"type": kind, "player_id": player_id, "team": team, "current_bet": rnd["bet"]["value"]})


def _accept(state, player_id, events):
    rnd = _active_round(state)
    team = _player(state, player_id)["team"]
    rnd["bet"] = rules.accept(rnd["bet"], team)
    rnd["status"] = "playing"
    events.append({"type": "truco_accepted", "player_id": player_id, "team": team,
                   "current_bet": rnd["bet"]["value"]})


def _reject(state, player_id, events):
    rnd = _active_round(state)
    team = _player(state, player_id)["team"]
    tentos, awarded_to = rules.reject(rnd["bet"], team)
    events.append({"type": "truco_rejected", "player_id": player_id, "team": team,
                   "tentos": tentos, "awarded_to": awarded_to})
    _award(state, awarded_to, tentos, "fled", events)


# --- mão de 11 -----------------------------------------------------------------

def _mao_de_11_team(state, player_id):
    rnd = _active_round(state)
    if rnd["status"] != "mao_de_11":
        raise RuleError("not_mao_de_11")
    team = rnd["category"][-1]
    if _player(state, player_id)["team"] != team:
        raise RuleError("not_mao_de_11_team")
    return rnd, team


def _accept_mao_de_11(state, player_id, events):
    rnd, team = _mao_de_11_team(state, player_id)
    rnd["status"] = "playing"
    events.append({"type": "mao_de_11_accepted", "player_id": player_id, "team": team})


def _reject_mao_de_11(state, player_id, events):
    _, team = _mao_de_11_team(state, player_id)
    events.append({"type": "mao_de_11_rejected", "player_id": player_id, "team": team,
                   "awarded_to": rules.opposing(team)})
    _award(state, rules.opposing(team), 1, "fled", events)


def _forfeit(state, player_id, events):
    winner = rules.opposing(_player(state, player_id)["team"])
    state["scores"][winner] = 12
    _finish(state, winner, "forfeit", events)


# --- visão de um jogador -------------------------------------------------------

def view(state, player_id):
    """Estado que um jogador pode ver: a própria mão, nunca a dos adversários.

    Na mão de 11 do 2v2 o time com 11 vê também a mão do parceiro (regra do truco
    mineiro) para decidir se joga.
    """
    out = {k: state[k] for k in ("mode", "status", "scores", "winner_team", "finish_reason",
                                 "last_trick", "last_round")}
    out["players"] = state["players"]
    rnd = state["round"]
    if rnd is None:
        out["round"] = None
        return out
    me = _player(state, player_id)
    partner_cards = None
    if state["mode"] == "2v2" and me and rnd["status"] == "mao_de_11" and rnd["category"][-1] == me["team"]:
        partner = next(p for p in state["players"] if p["team"] == me["team"] and p["id"] != me["id"])
        partner_cards = rnd["hands"][partner["id"]]
    out["round"] = {
        "number": rnd["number"],
        "status": rnd["status"],
        "category": rnd["category"],
        "current_bet": rnd["bet"]["value"],
        "bet_pending_team": rnd["bet"]["pending_team"],
        "last_caller_team": rnd["bet"]["last_caller_team"],
        "current_turn": rnd["current_turn"],
        "my_cards": list(rnd["hands"].get(player_id, [])),
        "partner_cards": partner_cards,
        "cards_left": {pid: len(c) for pid, c in rnd["hands"].items()},
        "plays_in_trick": [{"player_id": p["player_id"], "card": p["card"]} for p in rnd["plays_in_trick"]],
        "trick_results": list(rnd["trick_results"]),
    }
    return out


# --- auxiliares ----------------------------------------------------------------

def _player(state, player_id):
    return next((p for p in state["players"] if p["id"] == player_id), None)


def _active_round(state):
    if state["round"] is None:
        raise RuleError("no_active_round")
    return state["round"]


def _next_in_order(state, player_id):
    order = state["seat_order"]
    return order[(order.index(player_id) + 1) % len(order)]


_HANDLERS = {
    "truco": _truco,
    "accept": _accept,
    "reject": _reject,
    "accept_mao_de_11": _accept_mao_de_11,
    "reject_mao_de_11": _reject_mao_de_11,
    "forfeit": _forfeit,
}
