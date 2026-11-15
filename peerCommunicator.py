import argparse
import json
import threading
import time

import rpyc

import rpc
from constMP import GROUPMNGR_ADDR, GROUPMNGR_PORT, HOST, PEER_CTRL_BASE, PEER_UDP_BASE, PEER_WEB_BASE
from multicast import Multicast
from replica import Replica
from msgtrace import Trace
from udpTransport import UdpTransport
from web import WebServer


class Peer(rpyc.Service):
    """Um peer do TrucoON: guarda uma réplica das mesas e atende os jogadores ligados a ele.

    - UDP (multicast confiável FIFO da Parte 1b): comandos replicados entre os peers;
    - RPC (Parte 1a): controle pelo comparisonServer e consultas entre peers;
    - HTTP: página do jogo para os jogadores deste peer (a "região" deles).
    """

    def __init__(self, index):
        self.udp_port = PEER_UDP_BASE + index
        self.ctrl_port = PEER_CTRL_BASE + index
        self.web_port = PEER_WEB_BASE + index
        self.id = None
        self.server = None
        self.lock = threading.Lock()
        self.session = None   # Multicast da sessão atual
        self.replica = None
        self.ordering = None
        self.members = ()     # (id, host, udp, ctrl, web)
        self.early = []
        self.params = {}      # parâmetros da sessão atual (ordenação, perda, atraso)
        self.trace = Trace()  # o que passou por este peer, para o painel
        self.scenario = {"name": None, "running": False}
        self.transport = UdpTransport(self.udp_port, self.on_datagram)

    # --- grupo -------------------------------------------------------------------

    def register(self):
        with rpc.connect(GROUPMNGR_ADDR, GROUPMNGR_PORT) as conn:
            self.id = conn.root.register(HOST, self.udp_port, self.ctrl_port, self.web_port)
        self.trace.peer = self.id
        print(f"Registrado no GroupManager com id {self.id}", flush=True)

    def unregister(self):
        try:
            with rpc.connect(GROUPMNGR_ADDR, GROUPMNGR_PORT, retry_for=1) as conn:
                conn.root.unregister(self.id)
        except OSError:
            pass  # o GroupManager já pode ter saído

    # --- controle (RPC, chamado pelo comparisonServer) -------------------------

    def exposed_start(self, session_id, ordering="fifo", loss=0.0, delay_min=0.0, delay_max=0.0):
        """Abre uma sessão: réplica vazia e multicast com os membros atuais do grupo."""
        with rpc.connect(GROUPMNGR_ADDR, GROUPMNGR_PORT) as conn:
            members = tuple(tuple(m) for m in conn.root.list())
        self.transport.configure(loss, delay_min, delay_max)
        params = {"session": session_id, "ordering": ordering, "loss": loss,
                  "delay_min": delay_min, "delay_max": delay_max}
        self.trace.clear(**params)
        replica = Replica(self.id, self.trace)
        mc = Multicast(self.id, session_id, {m[0]: (m[1], m[2]) for m in members},
                       self.transport, replica.deliver, ordering, trace=self.trace, label=replica.describe)
        replica.multicast = mc
        with self.lock:
            if self.session:
                self.session.close()
            self.session, self.replica, self.ordering, self.members = mc, replica, ordering, members
            self.params = params
            replay = [d for d in self.early if d["round"] == session_id]
            self.early = [d for d in self.early if d["round"] > session_id]
        for d in replay:
            mc.on_datagram(d)
        print(f"Sessão {session_id} aberta: {len(members)} peers, ORDERING={ordering}, "
              f"LOSS={loss} DELAY={delay_min:g}..{delay_max:g} ms", flush=True)
        return f"Peer {self.id} pronto (http://{HOST}:{self.web_port})"

    def exposed_stop(self):
        print("Terminando.", flush=True)
        self.unregister()
        rpc.stop_later(self.server)
        return f"Peer {self.id} terminando."

    # --- aplicação (RPC, usado pelos bots e por outros peers) -----------------
    # Tudo vai e volta como texto JSON: tipos imutáveis são copiados pelo RPyC,
    # enquanto listas e dicts virariam referências remotas (netrefs).

    def exposed_submit(self, cmd_json):
        return json.dumps(self.submit(json.loads(cmd_json)))

    def exposed_view(self, code, player_id):
        return json.dumps(self.view(code, player_id))

    def exposed_digest(self):
        return json.dumps(self.digest())

    def exposed_log(self):
        replica = self.replica
        return replica.applied_log() if replica else ()

    def exposed_trace(self, since):
        return json.dumps(self.trace.since(since))

    def exposed_panel(self, n):
        return json.dumps(self.panel_local(n))

    # --- usados pela página web e pelo RPC --------------------------------------

    def submit(self, cmd):
        replica = self.replica
        if replica is None:
            return ["error", "no_session"]
        return replica.submit(cmd)

    def view(self, code, player_id):
        replica = self.replica
        return replica.view(code, player_id) if replica else None

    def digest(self):
        replica, mc = self.replica, self.session
        if replica is None:
            return {"peer": self.id, "error": "sem sessão"}
        return {**replica.digest(), **mc.counters(), "peer": self.id, "ordering": self.ordering,
                "sent": self.transport.sent, "dropped": self.transport.dropped}

    def all_digests(self):
        """Resumo da réplica de cada peer do grupo, para o painel de réplicas."""
        out = []
        for peer_id, host, _, ctrl, _ in self.members:
            if peer_id == self.id:
                out.append(self.digest())
                continue
            try:
                with rpc.connect(host, ctrl, retry_for=0.5) as conn:
                    out.append(json.loads(conn.root.digest()))
            except (OSError, EOFError, TimeoutError):
                out.append({"peer": peer_id, "error": "inacessível"})
        return out

    # --- painel de mensagens (web.py chama estes métodos) ----------------------

    def panel_local(self, n):
        replica = self.replica
        if replica is None:
            return {"peer": self.id, "error": "sem sessão"}
        return {"digest": self.digest(), "history": replica.history_tail(n), "log": replica.applied_log()}

    def _each_peer(self, local, remote):
        """Chama `local()` neste peer e `remote(conn, id)` nos outros, por RPC."""
        out = {}
        for peer_id, host, _, ctrl, _ in self.members:
            try:
                if peer_id == self.id:
                    out[peer_id] = local()
                else:
                    with rpc.connect(host, ctrl, retry_for=0.5) as conn:
                        out[peer_id] = remote(conn, peer_id)
            except (OSError, EOFError, TimeoutError):
                out[peer_id] = None
        return out

    def panel_events(self, cursors):
        """Eventos novos de todos os peers. cursors: id do peer -> último id de evento já visto."""
        got = self._each_peer(
            lambda: self.trace.since(cursors.get(self.id, 0)),
            lambda conn, peer_id: json.loads(conn.root.trace(cursors.get(peer_id, 0))))
        events = [e for evs in got.values() if evs for e in evs]
        return {"events": events, "now": time.time() * 1000}

    def panel_state(self, n=30):
        """Contadores, últimos comandos aplicados e comparação da ordem entre as réplicas."""
        got = self._each_peer(lambda: self.panel_local(n), lambda conn, _: json.loads(conn.root.panel(n)))
        peers = [v for v in got.values() if v and "digest" in v]
        logs = [p.pop("log") for p in peers]
        shortest = min((len(log) for log in logs), default=0)
        diverging = [i for i in range(shortest) if len({tuple(log[i]) for log in logs}) > 1]
        return {
            "now": time.time() * 1000,
            "params": self.params,
            "scenario": self.scenario,
            "peers": peers,
            "unreachable": [pid for pid, v in got.items() if v is None],
            "compared": shortest,
            "diverging_positions": len(diverging),
            "first_divergence": diverging[0] + 1 if diverging else None,
            "same_state": len({p["digest"]["state"] for p in peers}) <= 1,
        }

    def new_session(self, ordering, loss, delay_min, delay_max):
        """Abre uma sessão nova (réplicas vazias) em todos os peers, como o comparisonServer faz."""
        session_id = int(time.time() * 1000)
        for _, host, _, ctrl, _ in self.members:
            with rpc.connect(host, ctrl, retry_for=1) as conn:
                conn.root.start(session_id, ordering, loss, delay_min, delay_max)

    def run_scenario(self, name, n_rooms, duration):
        """Roda um cenário do comparisonServer a partir deste peer, numa thread."""
        import comparisonServer as cs
        if self.scenario["running"]:
            return False
        self.scenario = {"name": name, "running": True}

        def run():
            try:
                if name == "disputa":
                    cs.seat_race(self.members, n_rooms)
                else:
                    cs.games(self.members, n_rooms, duration)
            finally:
                self.scenario = {"name": name, "running": False}
        threading.Thread(target=run, daemon=True).start()
        return True

    def on_datagram(self, d):
        # Um peer pode receber datagramas de uma sessão que ele ainda não abriu.
        with self.lock:
            mc = self.session
            if mc is None or d["round"] > mc.round_id:
                self.early.append(d)
                return
        mc.on_datagram(d)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, default=0, help="desloca as portas para rodar vários peers na mesma máquina")
    args = parser.parse_args()

    peer = Peer(args.index)
    server = rpc.serve(peer, peer.ctrl_port)
    peer.register()
    WebServer(peer, peer.web_port).start()
    print(f"Página do jogo em http://{HOST}:{peer.web_port}", flush=True)
    print("Aguardando o início da sessão...", flush=True)
    server.start()


if __name__ == "__main__":
    main()
