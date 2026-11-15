import itertools
import threading
import time

from constMP import RTO, TICK
from reliable import ReliableChannels


class Multicast:
    """Multicast de uma sessão sobre UDP.

    Datagramas:
      {"t": "d", round, src, x, seq, m}  dado do canal confiável
      {"t": "a", round, src, x, cum}     ACK cumulativo
      {"t": "r", round, src, x, m}       UDP cru, sem garantia (ORDERING=none)

    `x` numera os datagramas que este peer envia; serve só para o painel ligar
    cada envio à sua recepção (ver msgtrace.py). Se `trace` for None, nada é registrado.
    """

    def __init__(self, my_id, round_id, members, transport, deliver, ordering="fifo", trace=None, label=None):
        self.my_id = my_id
        self.round_id = round_id
        self.members = members  # id -> (host, udp_port)
        self.transport = transport
        self.deliver = deliver
        self.ordering = ordering
        self.trace = trace
        self.label = label or (lambda m: str(m)[:40])
        self.channels = ReliableChannels()
        self.cond = threading.Condition()
        self.retransmissions = 0
        self.duplicates = 0
        self.closed = False
        self._x = itertools.count(1)
        threading.Thread(target=self._retransmit_loop, daemon=True).start()

    def multicast(self, msg, reliable=None):
        """No modo none os dados vão crus, como no MPComm original."""
        if reliable is None:
            reliable = self.ordering != "none"
        for dest in self.members:
            if reliable:
                with self.cond:
                    seq = self.channels.send(dest, msg, time.monotonic())
                self._send(dest, {"t": "d", "seq": seq, "m": msg})
            else:
                self._send(dest, {"t": "r", "m": msg})

    def on_datagram(self, d):
        if d["round"] != self.round_id:
            return
        kind, src = d["t"], d["src"]
        if kind == "r":
            self._trace_recv(d, "entregue")
            self.deliver(src, d["m"])
        elif kind == "a":
            self._trace_recv(d, "ack")
            with self.cond:
                self.channels.on_ack(src, d["cum"])
                self.cond.notify_all()
        elif kind == "d":
            with self.cond:
                delivered, cum, duplicate = self.channels.on_data(src, d["seq"], d["m"])
                self.duplicates += duplicate
            if duplicate:
                status = "duplicata"
            elif not delivered:
                status = "espera"  # chegou adiantada: fica na hold-back queue
            else:
                status = "entregue" if len(delivered) == 1 else f"entregue + {len(delivered) - 1} da fila"
            self._trace_recv(d, status)
            # Duplicata também recebe ACK: o ACK anterior pode ter se perdido.
            self._send(src, {"t": "a", "cum": cum})
            for msg in delivered:
                self.deliver(src, msg)

    def wait_acked(self, timeout):
        """Espera todos os destinatários confirmarem tudo que este peer enviou."""
        with self.cond:
            return self.cond.wait_for(lambda: self.channels.pending() == 0, timeout)

    def counters(self):
        with self.cond:
            return {"held_back": self.channels.held_back(), "unacked": self.channels.pending(),
                    "retransmissions": self.retransmissions, "duplicates": self.duplicates}

    def close(self):
        self.closed = True

    def _send(self, dest, payload, retx=False):
        x = next(self._x)
        ok = self.transport.send(self.members[dest], {**payload, "round": self.round_id, "src": self.my_id, "x": x})
        if self.trace:
            self.trace.record("send", to=dest, x=x, type=payload["t"], seq=payload.get("seq"),
                              cum=payload.get("cum"), label=self.label(payload["m"]) if "m" in payload else None,
                              retx=retx, dropped=not ok)

    def _trace_recv(self, d, status):
        if self.trace:
            self.trace.record("recv", **{"from": d["src"]}, x=d.get("x"), type=d["t"], seq=d.get("seq"),
                              cum=d.get("cum"), label=self.label(d["m"]) if "m" in d else None, status=status)

    def _retransmit_loop(self):
        while not self.closed:
            time.sleep(TICK)
            with self.cond:
                late = self.channels.retransmit_due(time.monotonic(), RTO)
                self.retransmissions += len(late)
            for dest, seq, msg in late:
                self._send(dest, {"t": "d", "seq": seq, "m": msg}, retx=True)
