import random

import pytest

from truco import deck, game, rules
from truco.evaluator import evaluate_round, evaluate_trick
from truco.rules import RuleError

PLAYERS_2V2 = [{"id": f"p{i}", "name": f"P{i}", "team": "a" if i % 2 == 0 else "b", "seat": i} for i in range(4)]


def test_baralho_tem_27_cartas_e_manilhas_mais_fortes():
    cards = deck.new_deck()
    assert len(cards) == len(set(cards)) == 27
    assert "A_espadas" in cards and cards.count("A_espadas") == 1
    strengths = [deck.card_strength(c) for c in deck.MANILHAS]
    assert strengths == [7, 8, 9, 10]
    assert max(deck.card_strength(c) for c in cards if c not in deck.MANILHAS) == 6


def test_embaralhamento_depende_so_da_semente():
    assert deck.shuffle(deck.new_deck(), 42) == deck.shuffle(deck.new_deck(), 42)
    assert deck.shuffle(deck.new_deck(), 42) != deck.shuffle(deck.new_deck(), 43)


def test_vaza_manilha_vence_e_empate_entre_times_canga():
    plays = [{"player_id": "x", "team": "a", "card": "3_ouros"}, {"player_id": "y", "team": "b", "card": "7_ouros"}]
    assert evaluate_trick(plays) == ("y", "b")
    plays = [{"player_id": "x", "team": "a", "card": "K_ouros"}, {"player_id": "y", "team": "b", "card": "K_copas"}]
    assert evaluate_trick(plays) is None
    plays = [{"player_id": "x", "team": "a", "card": "K_ouros"}, {"player_id": "y", "team": "b", "card": "Q_copas"},
             {"player_id": "z", "team": "a", "card": "K_copas"}]
    assert evaluate_trick(plays) == ("x", "a")


@pytest.mark.parametrize("results, expected", [
    (["a"], None), (["a", "a"], "a"), (["a", "b"], None), (["a", "b", "b"], "b"),
    (["draw", "a"], "a"), (["a", "draw"], "a"), (["b", "a", "draw"], "b"), (["draw", "draw", "draw"], "draw"),
])
def test_resultado_da_mao(results, expected):
    assert evaluate_round(results) == expected


def test_aposta_truco_seis_e_fuga():
    bet = rules.call_truco(rules.initial_bet(), "a")
    assert bet == {"value": 3, "pending_team": "b", "last_caller_team": "a"}
    with pytest.raises(RuleError, match="not_pending_team"):
        rules.accept(bet, "a")
    bet = rules.raise_bet(bet, "b")
    assert bet["value"] == 6 and bet["pending_team"] == "a"
    assert rules.reject(bet, "a") == (3, "b")
    with pytest.raises(RuleError, match="same_team_cannot_raise"):
        rules.call_truco(rules.accept(rules.call_truco(rules.initial_bet(), "a"), "b"), "a")


def test_carta_nao_pode_ser_jogada_com_truco_pendente():
    state, _ = game.new_game(PLAYERS_2V2, "2v2", 7)
    turn = state["round"]["current_turn"]
    game.apply(state, turn, "truco")
    card = state["round"]["hands"][turn][0]
    with pytest.raises(RuleError, match="truco_pending"):
        game.apply(state, turn, ["play_card", card])


def test_fugir_do_truco_da_um_tento_e_comeca_outra_mao():
    state, _ = game.new_game(PLAYERS_2V2, "2v2", 7)
    game.apply(state, "p0", "truco")
    events = game.apply(state, "p1", "reject")
    assert state["scores"] == {"a": 1, "b": 0}
    assert state["round"]["number"] == 2
    assert [e["type"] for e in events][:2] == ["truco_rejected", "round_finished"]


def test_visao_nao_mostra_a_mao_dos_outros():
    state, _ = game.new_game(PLAYERS_2V2, "2v2", 7)
    v = game.view(state, "p0")
    assert v["round"]["my_cards"] == state["round"]["hands"]["p0"]
    assert "hands" not in v["round"] and v["round"]["partner_cards"] is None


def play_random_game(seed, mode="2v2"):
    rng = random.Random(seed)
    players = PLAYERS_2V2 if mode == "2v2" else PLAYERS_2V2[:2]
    state, _ = game.new_game(players, mode, seed)
    seen_categories = set()
    for _ in range(5000):
        if state["status"] == "finished":
            return state, seen_categories
        rnd = state["round"]
        seen_categories.add(rnd["category"])
        if rnd["status"] == "mao_de_11":
            team = rnd["category"][-1]
            pid = next(p["id"] for p in players if p["team"] == team)
            game.apply(state, pid, "accept_mao_de_11")
        elif rnd["bet"]["pending_team"]:
            pid = next(p["id"] for p in players if p["team"] == rnd["bet"]["pending_team"])
            game.apply(state, pid, rng.choice(["accept", "accept", "reject"]))
        else:
            pid = rnd["current_turn"]
            try:
                if rng.random() < 0.1:
                    game.apply(state, pid, "truco")
                    continue
            except RuleError:
                pass
            game.apply(state, pid, ["play_card", rng.choice(rnd["hands"][pid])])
    raise AssertionError("a partida não terminou")


@pytest.mark.parametrize("mode", ["1v1", "2v2"])
def test_partidas_aleatorias_terminam_com_um_vencedor(mode):
    categories = set()
    for seed in range(60):
        state, seen = play_random_game(seed, mode)
        categories |= seen
        assert state["winner_team"] in ("a", "b")
        assert state["scores"][state["winner_team"]] >= 12
    assert "mao_de_11_a" in categories or "mao_de_11_b" in categories


def test_mesma_sequencia_de_acoes_da_o_mesmo_estado():
    a, _ = play_random_game(5)
    b, _ = play_random_game(5)
    assert a == b
