#!/usr/bin/env bash
# Uso: scripts/run_local.sh [peers=4] [cenario=disputa|partidas|jogar] [mesas=8]
# Sobe GroupManager, N peers e comparisonServer em localhost.
#   disputa / partidas: roda o cenário com bots, compara as réplicas e encerra tudo;
#   jogar: deixa o menu aberto e as páginas do jogo em http://localhost:8000, 8001, ...
set -euo pipefail
cd "$(dirname "$0")/.."

N=${1:-4}
CENARIO=${2:-disputa}
MESAS=${3:-8}
export ORDERING=${ORDERING:-fifo} LOSS=${LOSS:-0.1} DELAY_MIN=${DELAY_MIN:-0} DELAY_MAX=${DELAY_MAX:-60}
PY=${PYTHON:-.venv/bin/python}
[ -x "$PY" ] || PY=python3

mkdir -p logs
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT

"$PY" -u groupManager.py > logs/groupManager.log 2>&1 &
pids+=($!)
for i in $(seq 0 $((N - 1))); do
  "$PY" -u peerCommunicator.py --index "$i" > "logs/peer$i.log" 2>&1 &
  pids+=($!)
  sleep 0.3   # registra em ordem: o peer i recebe o id i e a página fica em 8000+i
done

if [ "$CENARIO" = "jogar" ]; then
  "$PY" -u comparisonServer.py --wait-peers "$N" --mesas "$MESAS" --duracao "${DURACAO:-15}"
else
  "$PY" -u comparisonServer.py --wait-peers "$N" --cenario "$CENARIO" --mesas "$MESAS" --duracao "${DURACAO:-15}"
fi
wait "${pids[@]}" 2>/dev/null || true
echo "Logs de cada processo em logs/"
