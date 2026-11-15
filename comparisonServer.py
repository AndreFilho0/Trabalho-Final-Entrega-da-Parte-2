"""Coordena o grupo de peers do TrucoON e verifica a consistência das réplicas.

Papel dos servidores do MPComm original, agora com significado: em vez de mandar
cada peer disparar mensagens numeradas, ele abre uma sessão do jogo em todos os
peers, roda cenários de truco com bots (ou deixa pessoas jogarem pela página) e
compara as réplicas da base de mesas.

    python comparisonServer.py --wait-peers 3                         # menu interativo
    python comparisonServer.py --wait-peers 4 --cenario disputa --mesas 8
    python comparisonServer.py --wait-peers 4 --cenario partidas --mesas 3 --duracao 20
"""
import argparse
import json
import random
import threading
import time

import bot
import rpc
from constMP import DELAY_MAX, DELAY_MIN, GROUPMNGR_ADDR, GROUPMNGR_PORT, LOSS, ORDERING
from metrics import compare
from truco import code as Code


# --- grupo ---------------------------------------------------------------------

def group_members():
    with rpc.connect(GROUPMNGR_ADDR, GROUPMNGR_PORT) as conn:
        return tuple(tuple(m) for m in conn.root.list())


def wait_for_peers(n):
    while len(group_members()) < n:
        time.sleep(0.2)


def start_session(peers, session_id):
    print(f"Peers: {[p[0] for p in peers]}  ORDERING={ORDERING} LOSS={LOSS} "
          f"DELAY={DELAY_MIN:g}..{DELAY_MAX:g} ms", flush=True)
    for _, host, _, ctrl, _ in peers:
        with rpc.connect(host, ctrl) as conn:
            print(" ", conn.root.start(session_id, ORDERING, LOSS, DELAY_MIN, DELAY_MAX), flush=True)


def call(peer, method, *args):
    _, host, _, ctrl, _ = peer
    with rpc.connect(host, ctrl) as conn:
        return getattr(conn.root, method)(*args)


def submit(peer, cmd):
    return json.loads(call(peer, "submit", json.dumps(cmd)))


def view(peer, code, player_id):
    return json.loads(call(peer, "view", code, player_id))


def digests(peers):
    return [json.loads(call(p, "digest")) for p in peers]


def wait_quiet(peers, timeout=30):
    """Espera todas as réplicas aplicarem tudo que foi enviado (ou o tempo acabar).

    Sem confiabilidade (ORDERING=none) pode faltar comando para sempre; por isso
    para também quando o número de aplicados deixa de mudar.
    """
    deadline = time.monotonic() + timeout
    last, stable_since = None, time.monotonic()
    while time.monotonic() < deadline:
        ds = digests(peers)
        sent = sum(d["submitted"] for d in ds)
        applied = tuple(d["applied"] for d in ds)
        if all(a == sent for a in applied):
            return True
        if applied != last:
            last, stable_since = applied, time.monotonic()
        elif time.monotonic() - stable_since > 3:
            return False
        time.sleep(0.2)
    return False


def parallel(fns):
    threads = [threading.Thread(target=f) for f in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# --- cenários ------------------------------------------------------------------

def setup_room(peers, r):
    """Mesa 2x2 com os 4 jogadores espalhados por peers diferentes (amigos em regiões diferentes)."""
    players = []
    for i in range(4):
        players.append(({"id": f"bot{r}-{i}", "name": f"Bot {r}.{i + 1}"}, peers[(r + i) % len(peers)]))
    host, host_peer = players[0]
    for _ in range(3):
        code = Code.generate()
        if submit(host_peer, ["create", code, host, "2v2", random.randrange(1, 10**9)])[0] == "ok":
            break
    parallel([lambda p=p, peer=peer: submit(peer, ["join", code, p]) for p, peer in players[1:]])
    return {"code": code, "players": players}


def grab_seat(peer, code, player_id):
    """Tenta as cadeiras 0, 1, 2, 3 em ordem até conseguir uma (pela réplica do próprio peer)."""
    for seat in range(4):
        if submit(peer, ["seat", code, player_id, seat])[0] == "ok":
            return seat
    return None


def seat_race(peers, n_rooms):
    """Disputa de cadeiras: os 4 jogadores de cada mesa pedem as cadeiras ao mesmo tempo."""
    tables = [setup_room(peers, r) for r in range(n_rooms)]
    wait_quiet(peers)
    parallel([lambda t=t, p=p, peer=peer: grab_seat(peer, t["code"], p["id"])
              for t in tables for p, peer in t["players"]])
    wait_quiet(peers)
    return tables


def bot_loop(peer, code, player_id, deadline, seed):
    rng = random.Random(seed)
    _, host, _, ctrl, _ = peer
    with rpc.connect(host, ctrl) as conn:
        while time.monotonic() < deadline:
            time.sleep(rng.uniform(0.02, 0.12))
            v = json.loads(conn.root.view(code, player_id))
            if v is None or v["status"] == "finished":
                return
            action = bot.choose(v, player_id, rng)
            if action is not None:
                conn.root.submit(json.dumps(["act", code, player_id, action]))


def games(peers, n_rooms, duration):
    """Partidas inteiras com bots: cada um joga pela réplica do peer dele."""
    tables = [setup_room(peers, r) for r in range(n_rooms)]
    wait_quiet(peers)
    for t in tables:
        for seat, (p, peer) in enumerate(t["players"]):
            submit(peer, ["seat", t["code"], p["id"], seat])
    # Barreira: só começa quando todas as réplicas viram as 4 cadeiras ocupadas.
    wait_quiet(peers)
    for t in tables:
        host, host_peer = t["players"][0]
        submit(host_peer, ["start", t["code"], host["id"]])
    wait_quiet(peers)
    deadline = time.monotonic() + duration
    parallel([lambda t=t, p=p, peer=peer, s=s: bot_loop(peer, t["code"], p["id"], deadline, s)
              for t in tables for s, (p, peer) in enumerate(t["players"])])
    wait_quiet(peers)
    return tables


# --- comparação das réplicas ---------------------------------------------------

def compare_replicas(peers, show_example=True):
    ds = digests(peers)
    logs = {p[0]: [tuple(e) for e in call(p, "log")] for p in peers}
    expected = {(d["peer"], k) for d in ds for k in range(d["submitted"])}
    result = compare(logs, expected)

    codes = sorted({c for d in ds for c in d["rooms"]})
    divergent = [c for c in codes if len({(d["rooms"].get(c) or {}).get("hash") for d in ds}) > 1]

    columns = ["aplicados", "faltando", "violações FIFO", "retransmissões", "datagramas", "hash da sequência",
               "hash do estado"]
    print(f"\n{len(peers)} peers, {len(expected)} comandos enviados ao todo (ORDERING={ds[0]['ordering']})")
    print("peer" + "".join(f"{c:>19}" for c in columns))
    for d in ds:
        row = result["per_peer"][d["peer"]]
        values = [d["applied"], row["faltando"], row["violações FIFO"], d["retransmissions"], d["sent"],
                  d["chain"], d["state"]]
        print(f"{d['peer']:>4}" + "".join(f"{v:>19}" for v in values))
    print(f"Posições da sequência de comandos que divergem entre réplicas: {result['divergentes']} de "
          f"{result['posições']}")
    print(f"Mesas divergentes: {len(divergent)} de {len(codes)}", flush=True)

    if show_example and divergent:
        c = divergent[0]
        print(f"\nExemplo, mesa {Code.fmt(c)} em cada réplica:")
        for d in ds:
            room = d["rooms"].get(c)
            if room is None:
                print(f"  peer {d['peer']}: (não existe)")
            else:
                seats = ", ".join(f"cadeira {s}={p}" for s, p in room["seats"].items()) or "ninguém sentado"
                print(f"  peer {d['peer']}: {room['status']:<8} {seats}  placar={room['scores']}")
    print(flush=True)
    return {"divergent_rooms": len(divergent), "rooms": len(codes), **result}


# --- programa --------------------------------------------------------------------

def shutdown(peers):
    for p in peers:
        try:
            print(call(p, "stop"), flush=True)
        except (OSError, EOFError):
            pass
    with rpc.connect(GROUPMNGR_ADDR, GROUPMNGR_PORT) as conn:
        conn.root.stop()


def run_scenario(peers, name, n_rooms, duration):
    if name == "disputa":
        print(f"Disputa de cadeiras em {n_rooms} mesas 2x2...", flush=True)
        seat_race(peers, n_rooms)
    else:
        print(f"{n_rooms} partidas 2x2 com bots por {duration:g} s...", flush=True)
        games(peers, n_rooms, duration)
    return compare_replicas(peers)


def menu(peers, args):
    print("\nPáginas do jogo (cada uma é um peer, como se fosse uma região):")
    for p in peers:
        print(f"  peer {p[0]}: http://{p[1]}:{p[4]}")
    while True:
        choice = input("\n[1] disputa de cadeiras  [2] partidas com bots  [3] comparar réplicas  [0] encerrar => ")
        if choice == "1":
            run_scenario(peers, "disputa", args.mesas, args.duracao)
        elif choice == "2":
            run_scenario(peers, "partidas", args.mesas, args.duracao)
        elif choice == "3":
            compare_replicas(peers)
        elif choice == "0":
            return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-peers", type=int, default=1, help="espera esse número de peers registrados")
    parser.add_argument("--cenario", choices=["disputa", "partidas"], help="roda um cenário e encerra")
    parser.add_argument("--mesas", type=int, default=8)
    parser.add_argument("--duracao", type=float, default=15, help="segundos de partida no cenário com bots")
    parser.add_argument("--manter", action="store_true", help="não encerra os peers no fim do cenário")
    args = parser.parse_args()

    wait_for_peers(args.wait_peers)
    peers = group_members()
    start_session(peers, int(time.time()))

    if args.cenario:
        run_scenario(peers, args.cenario, args.mesas, args.duracao)
        if args.manter:
            menu(peers, args)
    else:
        menu(peers, args)
    shutdown(peers)
    print("Encerrado.")


if __name__ == "__main__":
    main()
