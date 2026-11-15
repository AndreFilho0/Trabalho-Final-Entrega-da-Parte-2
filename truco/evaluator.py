"""Quem ganha uma vaza e quem ganha a mão (funções puras)."""
from truco.deck import card_strength


def evaluate_trick(plays):
    """plays: [{"player_id", "team", "card"}] na ordem em que foram jogadas.

    Devolve (player_id, time) do vencedor ou None se empatou ("cangou").
    Cartas de mesma força do mesmo time não empatam: vence quem jogou primeiro.
    """
    best = max(card_strength(p["card"]) for p in plays)
    top = [p for p in plays if card_strength(p["card"]) == best]
    if len({p["team"] for p in top}) == 1:
        return top[0]["player_id"], top[0]["team"]
    return None


def evaluate_round(results):
    """results: resultado de cada vaza já jogada ("a", "b" ou "draw").

    Devolve "a", "b", "draw" (mão nula: três empates) ou None (ainda indefinida).
    - quem ganha 2 vazas leva;
    - 1 vitória e o resto empate: leva quem ganhou;
    - 1 a 1 e empate na terceira: leva quem ganhou a primeira.
    """
    if len(results) < 2:
        return None
    a, b, d, n = results.count("a"), results.count("b"), results.count("draw"), len(results)
    if a >= 2:
        return "a"
    if b >= 2:
        return "b"
    if a == 1 and b == 0 and d >= 1:
        return "a"
    if b == 1 and a == 0 and d >= 1:
        return "b"
    if a == 1 and b == 1 and n == 3:
        return next(r for r in results if r != "draw")
    if d == n == 3:
        return "draw"
    return None
