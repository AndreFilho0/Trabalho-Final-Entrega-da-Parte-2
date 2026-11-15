"""Página do jogo e API HTTP de um peer (só biblioteca padrão).

A API não guarda estado: a identidade do jogador vem em cada pedido e os dados
vêm da réplica local. Toda alteração vira um comando replicado (peer.submit).

    GET  /, /m/<código>           página do jogo (static/index.html)
    GET  /api/info                id do peer, sessão e modo de ordenação
    GET  /api/room/<código>?player=<id>   visão da mesa para esse jogador
    POST /api/cmd                 {"op", "player": {"id","name"}, "code", "mode", "seat", "action"}
    GET  /api/replicas            resumo (hashes) da réplica de cada peer

Painel de mensagens (para a demonstração; sem autenticação):

    GET  /painel                  página do painel (static/painel.html)
    GET  /api/painel/eventos?c=<json {peer: último id}>   datagramas e aplicações novos de todos os peers
    GET  /api/painel/estado       contadores, últimos comandos aplicados e comparação da ordem
    POST /api/painel/sessao       {"ordering", "loss", "delay_max"}: abre uma sessão nova em todos os peers
    POST /api/painel/cenario      {"nome": "disputa"|"partidas", "mesas", "duracao"}
"""
import json
import random
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from truco import code as Code

STATIC = Path(__file__).resolve().parent / "static"
PAGE = STATIC / "index.html"
PANEL = STATIC / "painel.html"
MAX_BODY = 16 * 1024


def build_command(body):
    """Traduz o pedido do navegador num comando replicado."""
    op, player = body.get("op"), body.get("player") or {}
    pid = player.get("id")
    code = Code.normalize(body.get("code", ""))
    if op == "create":
        # Código e semente sorteados aqui (peer de origem) e enviados no comando:
        # todas as réplicas usam os mesmos valores.
        return ["create", Code.generate(), player, body.get("mode"), random.randrange(1, 10**9)]
    if op == "join":
        return ["join", code, player]
    if op == "seat":
        return ["seat", code, pid, body.get("seat")]
    if op in ("leave", "start", "rematch"):
        return [op, code, pid]
    if op == "act":
        return ["act", code, pid, body.get("action")]
    return None


def handle_command(peer, body):
    cmd = build_command(body)
    if cmd is None:
        return 400, {"result": ["error", "unknown_op"]}
    for _ in range(3):
        result = peer.submit(cmd)
        if cmd[0] == "create" and result == ["error", "code_taken"]:
            cmd[1] = Code.generate()
            continue
        break
    code = cmd[1]
    return 200, {"result": result, "code": code, "view": peer.view(code, (body.get("player") or {}).get("id"))}


class Handler(BaseHTTPRequestHandler):
    peer = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if not parts or parts[0] == "m":
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if parts == ["painel"]:
            return self._send(200, PANEL.read_bytes(), "text/html; charset=utf-8")
        if parts == ["api", "painel", "eventos"]:
            try:
                cursors = {int(k): int(v) for k, v in json.loads(parse_qs(url.query).get("c", ["{}"])[0]).items()}
            except (ValueError, AttributeError):
                cursors = {}
            return self._json(200, self.peer.panel_events(cursors))
        if parts == ["api", "painel", "estado"]:
            return self._json(200, self.peer.panel_state())
        if parts == ["api", "info"]:
            p = self.peer
            return self._json(200, {"peer": p.id, "ordering": p.ordering,
                                    "session": p.session.round_id if p.session else None,
                                    "members": [{"id": m[0], "web": f"http://{m[1]}:{m[4]}"} for m in p.members]})
        if parts == ["api", "replicas"]:
            return self._json(200, self.peer.all_digests())
        if len(parts) == 3 and parts[:2] == ["api", "room"]:
            player = parse_qs(url.query).get("player", [""])[0]
            view = self.peer.view(Code.normalize(parts[2]), player)
            return self._json(200 if view else 404, view or {"error": "room_not_found"})
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/cmd", "/api/painel/sessao", "/api/painel/cenario"):
            return self._json(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            return self._json(413, {"error": "too_large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad_json"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "bad_json"})
        if path == "/api/cmd":
            return self._json(*handle_command(self.peer, body))
        try:
            if path == "/api/painel/sessao":
                ordering = body.get("ordering") if body.get("ordering") in ("none", "fifo") else "fifo"
                loss = min(max(float(body.get("loss", 0)), 0.0), 0.9)
                delay_max = min(max(float(body.get("delay_max", 0)), 0.0), 2000.0)
                self.peer.new_session(ordering, loss, 0.0, delay_max)
                return self._json(200, {"ok": True})
            name = "disputa" if body.get("nome") == "disputa" else "partidas"
            mesas = min(max(int(body.get("mesas", 4)), 1), 20)
            duracao = min(max(float(body.get("duracao", 15)), 1.0), 120.0)
            started = self.peer.run_scenario(name, mesas, duracao)
            return self._json(200 if started else 409, {"ok": started})
        except (TypeError, ValueError):
            return self._json(400, {"error": "bad_params"})
        except (OSError, EOFError):
            return self._json(502, {"error": "peer_unreachable"})

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _send(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        # HTTPServer.server_bind chama getfqdn(), que no macOS pode travar
        # vários segundos esperando DNS reverso. O nome não é usado aqui.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class WebServer:
    def __init__(self, peer, port):
        handler = type("PeerHandler", (Handler,), {"peer": peer})
        self.httpd = _HTTPServer(("0.0.0.0", port), handler)

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
