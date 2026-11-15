import hashlib
import json
import threading

from truco import rooms as Rooms

SUBMIT_TIMEOUT = 15


def digest_of(obj):
    """Hash canônico de um valor JSON (mesmo valor -> mesmo hash em qualquer peer)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Replica:
    """Réplica da base de mesas de truco neste peer.

    Toda alteração faz o mesmo caminho, inclusive as do jogador ligado a este peer:

        jogador -> submit(cmd) -> multicast ["cmd", origem, k, cmd] para todos (inclusive este peer)
                                         ... rede + ordenação ...
        entrega -> deliver -> Rooms.apply -> novo estado -> responde quem fez o submit

    O peer não aplica o comando do próprio jogador na hora: ele espera a entrega
    pelo multicast, como todas as outras réplicas. Assim, se o multicast entregar
    a mesma sequência em todo lugar, todas as réplicas terminam no mesmo estado
    (replicação por máquina de estados). Com FIFO isso só vale para comandos de
    uma mesma origem; comandos concorrentes de peers diferentes podem ser
    aplicados em ordens diferentes, e as réplicas divergem.

    Cada comando é identificado por (origem, k), com k = 0, 1, 2... por origem. É o
    mesmo formato das mensagens do experimento da Parte 1, então metrics.py mede
    perdas, duplicatas, FIFO e posições divergentes nos logs das réplicas.
    """

    def __init__(self, my_id, trace=None):
        self.my_id = my_id
        self.trace = trace
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.rooms = {}
        self.applied = 0
        self.chain = "0" * 64
        self.log = []
        self.history = []  # o que o painel mostra: posição, origem, k, rótulo, resultado
        self.names = {}    # id do jogador -> apelido, só para os rótulos do painel
        self.submitted = 0
        self.waiters = {}  # k -> [threading.Event, resultado]
        self.multicast = None

    def submit(self, cmd, timeout=SUBMIT_TIMEOUT):
        """Envia `cmd` ao grupo e espera a própria réplica aplicá-lo."""
        # Numerar e enviar juntos: senão dois submits concorrentes deste peer
        # poderiam sair na ordem k=5, k=4 e a ordem de envio não seria a de k.
        with self.send_lock:
            with self.lock:
                k = self.submitted
                self.submitted += 1
                waiter = self.waiters[k] = [threading.Event(), None]
            self.multicast.multicast(["cmd", self.my_id, k, cmd])
        delivered = waiter[0].wait(timeout)
        with self.lock:
            self.waiters.pop(k, None)
        # Sem confiabilidade (ORDERING=none) o comando pode se perder no caminho.
        return waiter[1] if delivered else ["error", "timeout"]

    def deliver(self, src, msg):
        if not (isinstance(msg, list) and len(msg) == 4 and msg[0] == "cmd"):
            return
        _, origin, k, cmd = msg
        with self.lock:
            _, result, _ = Rooms.apply(self.rooms, cmd)
            self.applied += 1
            # Hash encadeado da sequência de comandos: muda se a ordem mudar.
            self.chain = hashlib.sha256(f"{self.chain}|{origin}:{k}".encode()).hexdigest()
            self.log.append((origin, k))
            if cmd and cmd[0] in ("create", "join") and isinstance(cmd[2], dict):
                self.names[cmd[2].get("id")] = cmd[2].get("name")
            entry = {"pos": self.applied, "origin": origin, "k": k, "label": self.describe(msg),
                     "ok": result[0] == "ok", "result": result[1] if result[0] == "error" else None}
            self.history.append(entry)
            waiter = self.waiters.get(k) if origin == self.my_id else None
        if self.trace:
            self.trace.record("apply", **entry)
        if waiter:
            waiter[1] = result
            waiter[0].set()

    def view(self, code, player_id):
        with self.lock:
            room = self.rooms.get(code)
            # json faz uma cópia: a visão não fica ligada ao estado que continua mudando.
            return json.loads(json.dumps(Rooms.view(room, code, player_id))) if room else None

    def digest(self):
        with self.lock:
            return {
                "applied": self.applied,
                "submitted": self.submitted,
                "chain": self.chain[:16],
                "state": digest_of(self.rooms)[:16],
                "rooms": {code: {"hash": digest_of(room)[:12], **Rooms.summary(room)}
                          for code, room in sorted(self.rooms.items())},
            }

    def history_tail(self, n):
        with self.lock:
            return self.history[-n:]

    def describe(self, msg):
        """Rótulo curto de um comando para o painel, ex.: "seat Bia→1", "Ana: truco"."""
        try:
            _, _, _, cmd = msg
            name, code = cmd[0], str(cmd[1])[:4]
            if name in ("create", "join"):
                who = cmd[2].get("name")
                return f"{code} {name} {who}" + (f" ({cmd[3]})" if name == "create" else "")
            who = self.names.get(cmd[2]) or str(cmd[2])[:6]
            if name == "seat":
                return f"{code} {who} senta na {cmd[3] + 1}"
            if name == "act":
                action = cmd[3]
                if isinstance(action, list):
                    return f"{code} {who} joga {action[1]}"
                return f"{code} {who}: {action}"
            return f"{code} {who}: {name}"
        except (TypeError, IndexError, KeyError, AttributeError):
            return str(msg)[:40]

    def applied_log(self):
        with self.lock:
            return tuple(self.log)
