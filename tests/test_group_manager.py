from groupManager import GroupManager


def test_unregister_tira_o_peer_da_lista():
    gm = GroupManager()
    a = gm.exposed_register("10.0.0.1", 6789, 5700, 8000)
    b = gm.exposed_register("10.0.0.2", 6789, 5700, 8000)
    assert gm.exposed_unregister(a) is True
    assert gm.exposed_list() == ((b, "10.0.0.2", 6789, 5700, 8000),)
    assert gm.exposed_unregister(a) is False


def test_peer_que_reinicia_no_mesmo_endereco_mantem_o_id():
    gm = GroupManager()
    a = gm.exposed_register("10.0.0.1", 6789, 5700, 8000)
    assert gm.exposed_register("10.0.0.1", 6789, 5701, 8001) == a
    assert len(gm.exposed_list()) == 1
