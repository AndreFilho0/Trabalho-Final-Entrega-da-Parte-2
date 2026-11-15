"""A base de mesas é uma máquina de estados: o resultado depende da ordem dos comandos."""
from replica import digest_of
from truco import rooms as Rooms

ANA, BIA, CAIO, DUDA = ({"id": i, "name": i.title()} for i in ("ana", "bia", "caio", "duda"))


def lobby(mode="2v2", players=(ANA, BIA, CAIO, DUDA)):
    db = {}
    Rooms.apply(db, ["create", "MESA0001", players[0], mode, 123])
    for p in players[1:]:
        Rooms.apply(db, ["join", "MESA0001", p])
    return db


def run(db, cmds):
    return [Rooms.apply(db, c)[1] for c in cmds]


def test_fluxo_da_mesa_ate_comecar():
    db = lobby()
    assert run(db, [["seat", "MESA0001", p["id"], s] for s, p in enumerate((ANA, BIA, CAIO, DUDA))]) == \
        [["ok", 0], ["ok", 1], ["ok", 2], ["ok", 3]]
    assert Rooms.apply(db, ["start", "MESA0001", "bia"])[1] == ["error", "not_host"]
    assert Rooms.apply(db, ["start", "MESA0001", "ana"])[1] == ["ok", "started"]
    view = Rooms.view(db["MESA0001"], "MESA0001", "caio")
    assert view["me"] == {"id": "caio", "seat": 2, "team": "a"}
    assert len(view["game"]["round"]["my_cards"]) == 3


def test_erro_nao_altera_a_base():
    db = lobby()
    before = digest_of(db)
    assert Rooms.apply(db, ["seat", "MESA0001", "ana", 9])[1] == ["error", "invalid_seat"]
    assert Rooms.apply(db, ["start", "MESA0001", "ana"])[1] == ["error", "seats_not_filled"]
    assert Rooms.apply(db, ["create", "MESA0001", BIA, "1v1", 1])[1] == ["error", "code_taken"]
    assert Rooms.apply(db, ["voar", "MESA0001"])[1] == ["error", "unknown_command"]
    assert digest_of(db) == before


def test_disputa_de_cadeira_depende_da_ordem():
    """Bia e Caio pedem a cadeira 0 ao mesmo tempo: cada ordem dá um dono diferente."""
    bia_first, caio_first = lobby(), lobby()
    seat_bia, seat_caio = ["seat", "MESA0001", "bia", 0], ["seat", "MESA0001", "caio", 0]
    assert run(bia_first, [seat_bia, seat_caio]) == [["ok", 0], ["error", "seat_taken"]]
    assert run(caio_first, [seat_caio, seat_bia]) == [["ok", 0], ["error", "seat_taken"]]
    assert bia_first["MESA0001"]["seats"] == {0: "bia"}
    assert caio_first["MESA0001"]["seats"] == {0: "caio"}
    assert digest_of(bia_first) != digest_of(caio_first)


def started_1v1():
    db = lobby("1v1", (ANA, BIA))
    run(db, [["seat", "MESA0001", "ana", 0], ["seat", "MESA0001", "bia", 1], ["start", "MESA0001", "ana"]])
    return db


def test_truco_contra_carta_muda_o_placar_conforme_a_ordem():
    """Ana joga a carta enquanto Bia pede truco: uma ordem aceita a carta, a outra rejeita."""
    a, b = started_1v1(), started_1v1()
    rnd = a["MESA0001"]["game"]["round"]
    turn = rnd["current_turn"]
    other = "bia" if turn == "ana" else "ana"
    play = ["act", "MESA0001", turn, ["play_card", rnd["hands"][turn][0]]]
    truco = ["act", "MESA0001", other, "truco"]
    assert run(a, [play, truco]) == [["ok", "ok"], ["ok", "ok"]]
    assert run(b, [truco, play]) == [["ok", "ok"], ["error", "truco_pending"]]
    assert a["MESA0001"]["game"]["round"]["plays_in_trick"] != b["MESA0001"]["game"]["round"]["plays_in_trick"]


def test_comandos_que_comutam_nao_divergem():
    """Mesas diferentes não conflitam: qualquer ordem chega ao mesmo estado."""
    cmds = [["create", "MESA0001", ANA, "1v1", 1], ["create", "MESA0002", BIA, "2v2", 2]]
    x, y = {}, {}
    run(x, cmds)
    run(y, cmds[::-1])
    assert digest_of(x) == digest_of(y)


def test_sair_no_meio_da_partida_e_desistir():
    db = started_1v1()
    assert Rooms.apply(db, ["leave", "MESA0001", "ana"])[1] == ["ok", "forfeited"]
    assert db["MESA0001"]["game"]["winner_team"] == "b"
    assert Rooms.apply(db, ["rematch", "MESA0001", "ana"])[1] == ["ok", "started"]
