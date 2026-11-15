"""Jogador automático, usado nos cenários do comparisonServer.

O bot fala com o peer "dele" por RPC, como um jogador humano fala pela página:
lê a visão da mesa na réplica local e envia comandos. Com bots de uma mesma mesa
em peers diferentes, comandos concorrentes (um pede truco enquanto o outro joga a
carta) disputam a ordem, e é isso que mostra a divergência entre réplicas quando
não há ordem total.
"""
import random


def choose(view, player_id, rng=random, chaos=0.02):
    """Escolhe a próxima ação a partir da visão da mesa, ou None para esperar."""
    game = view.get("game") if view else None
    rnd = game.get("round") if game else None
    if not rnd or view["status"] != "playing":
        return None
    team = view["me"]["team"]

    if rnd["status"] == "mao_de_11" and rnd["category"] == f"mao_de_11_{team}":
        return "accept_mao_de_11" if rng.random() < 0.8 else "reject_mao_de_11"

    if rnd["bet_pending_team"] == team:
        r = rng.random()
        if r < 0.5:
            return "accept"
        if r < 0.7:
            return "reject"
        return "truco" if rnd["current_bet"] < 12 else "accept"

    my_turn = (rnd["current_turn"] == player_id and rnd["bet_pending_team"] is None
               and rnd["status"] in ("playing", "mao_de_ferro") and rnd["my_cards"])
    if my_turn:
        if (rnd["category"] == "normal" and rnd["last_caller_team"] != team
                and rnd["current_bet"] < 12 and rng.random() < 0.15):
            return "truco"
        return ["play_card", rng.choice(rnd["my_cards"])]

    can_call = (rnd["category"] == "normal" and rnd["bet_pending_team"] is None
                and rnd["last_caller_team"] != team and rnd["current_bet"] < 12)
    if can_call and rng.random() < chaos:
        # Truco fora da própria vez (vale no truco): válido na réplica deste bot, mas
        # concorre com a carta que o adversário está jogando em outro peer.
        return "truco"
    return None
