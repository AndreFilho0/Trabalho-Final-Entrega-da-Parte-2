"""Réplicas reais sobre UDP com perda: quando a ordem FIFO basta e quando não basta."""
import threading
import time

from multicast import Multicast
from replica import Replica
from udpTransport import UdpTransport

N = 3


def make_group(base_port, session):
    members = {i: ("127.0.0.1", base_port + i) for i in range(N)}
    replicas, mcs = {}, {}
    for i in members:
        replicas[i] = Replica(i)
        transport = UdpTransport(base_port + i, lambda d, i=i: mcs[i].on_datagram(d))
        transport.configure(loss=0.2, delay_min=0, delay_max=20)
        mcs[i] = Multicast(i, session, members, transport, replicas[i].deliver, "fifo")
        replicas[i].multicast = mcs[i]
    return replicas, mcs


def wait_all_applied(replicas, total, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(r.digest()["applied"] == total for r in replicas.values()):
            return True
        time.sleep(0.05)
    return False


def test_comandos_de_uma_so_origem_deixam_as_replicas_iguais():
    """FIFO garante a mesma ordem para os comandos de um mesmo peer."""
    replicas, mcs = make_group(17789, 1)
    host = replicas[0]
    assert host.submit(["create", "MESA0001", {"id": "ana", "name": "Ana"}, "1v1", 9]) == ["ok", "created"]
    host.submit(["join", "MESA0001", {"id": "bia", "name": "Bia"}])
    host.submit(["seat", "MESA0001", "ana", 0])
    host.submit(["seat", "MESA0001", "bia", 0])   # rejeitado em todas as réplicas, igual
    host.submit(["seat", "MESA0001", "bia", 1])
    assert host.submit(["start", "MESA0001", "ana"]) == ["ok", "started"]
    assert wait_all_applied(replicas, 6)
    digests = [r.digest() for r in replicas.values()]
    assert len({d["chain"] for d in digests}) == 1
    assert len({d["state"] for d in digests}) == 1
    for mc in mcs.values():
        mc.close()


def test_disputa_entre_peers_e_aplicada_por_todas_as_replicas():
    """Comandos concorrentes de peers diferentes chegam a todas as réplicas (sem perdas),
    mas FIFO não garante que cheguem na mesma ordem: a divergência é medida, não proibida."""
    replicas, mcs = make_group(17889, 2)
    replicas[0].submit(["create", "MESA0001", {"id": "p0", "name": "P0"}, "2v2", 9])
    for i in (1, 2):
        replicas[i].submit(["join", "MESA0001", {"id": f"p{i}", "name": f"P{i}"}])
    assert wait_all_applied(replicas, 3)

    threads = [threading.Thread(target=replicas[i].submit, args=(["seat", "MESA0001", f"p{i}", 0],))
               for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wait_all_applied(replicas, 6)
    for r in replicas.values():
        # Em cada réplica exatamente um dos três ganhou a cadeira 0 (qual deles depende da ordem).
        seats = r.rooms["MESA0001"]["seats"]
        assert list(seats) == [0] and seats[0] in {"p0", "p1", "p2"}
        assert sorted(r.applied_log()) == sorted({(0, 0), (0, 1), (1, 0), (2, 0), (1, 1), (2, 1)})
    for mc in mcs.values():
        mc.close()
