import threading
import time
from collections import deque


class Trace:
    """Registro em memória do que passa por um peer, para o painel de mensagens.

    Cada evento é um dict com um id crescente (o painel pede "o que veio depois do
    id N"), o horário em ms (relógio deste peer) e o id do peer. Tipos de evento:

      send    datagrama enviado: to, x, type (d=comando, a=ACK, r=UDP cru), seq/cum,
              retx (é retransmissão?), dropped (descartado pela falha simulada?)
      recv    datagrama recebido: from, x, type, seq/cum, status (entregue, espera, duplicata)
      apply   comando aplicado na réplica: origin, k, label, ok, pos (posição na sequência)
      session começo de uma sessão nova (o painel limpa a tela)

    `x` é um número que o remetente coloca em cada datagrama: (remetente, x) liga o
    envio à recepção, e o painel desenha a seta entre os dois.
    """

    def __init__(self, maxlen=20000):
        self.lock = threading.Lock()
        self.events = deque(maxlen=maxlen)
        self.next_id = 0
        self.peer = None

    def record(self, kind, **fields):
        with self.lock:
            self.next_id += 1
            self.events.append({"id": self.next_id, "ts": round(time.time() * 1000, 1), "peer": self.peer,
                                "kind": kind, **fields})

    def since(self, last_id, limit=4000):
        with self.lock:
            out = [e for e in self.events if e["id"] > last_id]
        return out[-limit:]

    def clear(self, **fields):
        with self.lock:
            self.events.clear()
        self.record("session", **fields)
