import threading

import rpyc

import rpc
from constMP import GROUPMNGR_PORT


class GroupManager(rpyc.Service):
    def __init__(self):
        self.lock = threading.Lock()
        self.members = {}  # id -> (host, udp_port, ctrl_port, web_port)
        self.next_id = 0
        self.server = None

    def exposed_register(self, host, udp_port, ctrl_port, web_port=None):
        addr = (host, udp_port, ctrl_port, web_port)
        with self.lock:
            # Idempotente: um peer que reinicia no mesmo endereço recebe o mesmo id
            # em vez de aparecer duas vezes na lista.
            peer_id = next((i for i, a in self.members.items() if a[:2] == addr[:2]), None)
            if peer_id is None:
                peer_id = self.next_id
                self.next_id += 1
            self.members[peer_id] = addr
        print(f"Peer {peer_id} registrado: {host} udp={udp_port} ctrl={ctrl_port} web={web_port}", flush=True)
        return peer_id

    def exposed_unregister(self, peer_id):
        with self.lock:
            removed = self.members.pop(peer_id, None) is not None
        if removed:
            print(f"Peer {peer_id} removido", flush=True)
        return removed

    def exposed_list(self):
        # Tupla é enviada por valor; uma lista viraria referência remota (netref).
        with self.lock:
            return tuple((peer_id, *addr) for peer_id, addr in sorted(self.members.items()))

    def exposed_stop(self):
        print("Encerrando.", flush=True)
        rpc.stop_later(self.server)


if __name__ == "__main__":
    server = rpc.serve(GroupManager(), GROUPMNGR_PORT)
    print(f"GroupManager ouvindo RPC na porta {GROUPMNGR_PORT}", flush=True)
    server.start()
