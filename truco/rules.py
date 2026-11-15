"""Regras de aposta (truco, seis, nove, doze) e categoria da mão."""

BET_LEVELS = [1, 3, 6, 9, 12]
# Quem foge de um pedido dá ao adversário o valor anterior ao pedido.
REJECT_VALUE = {3: 1, 6: 3, 9: 6, 12: 9}


class RuleError(Exception):
    """Jogada inválida. O motivo é determinístico: toda réplica rejeita igual."""


def initial_bet(value=1):
    return {"value": value, "pending_team": None, "last_caller_team": None}


def next_bet(value):
    i = BET_LEVELS.index(value)
    if i + 1 == len(BET_LEVELS):
        raise RuleError("max_bet_reached")
    return BET_LEVELS[i + 1]


def opposing(team):
    return "b" if team == "a" else "a"


def call_truco(bet, team):
    """Pedir truco (ou aumentar) quando não há pedido pendente."""
    if bet["value"] == 12:
        raise RuleError("max_bet_reached")
    if bet["pending_team"] is not None:
        raise RuleError("bet_already_pending")
    if bet["last_caller_team"] == team:
        raise RuleError("same_team_cannot_raise")
    return {"value": next_bet(bet["value"]), "pending_team": opposing(team), "last_caller_team": team}


def raise_bet(bet, team):
    """Responder um pedido pendente aumentando (ex.: "seis!" em resposta ao truco)."""
    if bet["pending_team"] != team:
        raise RuleError("not_pending_team")
    return {"value": next_bet(bet["value"]), "pending_team": opposing(team), "last_caller_team": team}


def accept(bet, team):
    if bet["pending_team"] is None:
        raise RuleError("no_bet_pending")
    if bet["pending_team"] != team:
        raise RuleError("not_pending_team")
    return {**bet, "pending_team": None}


def reject(bet, team):
    """Fugir: devolve (tentos, time que recebe)."""
    if bet["pending_team"] is None:
        raise RuleError("no_bet_pending")
    if bet["pending_team"] != team:
        raise RuleError("not_pending_team")
    return REJECT_VALUE[bet["value"]], opposing(team)


def round_category(score_a, score_b):
    """"normal", "mao_de_11_a", "mao_de_11_b" ou "mao_de_ferro" (os dois com 11)."""
    if score_a == 11 and score_b == 11:
        return "mao_de_ferro"
    if score_a == 11:
        return "mao_de_11_a"
    if score_b == 11:
        return "mao_de_11_b"
    return "normal"


def game_winner(scores):
    for team in ("a", "b"):
        if scores[team] >= 12:
            return team
    return None
