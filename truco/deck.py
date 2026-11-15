"""Baralho de truco mineiro: 27 cartas, manilhas fixas, sem vira.

Cartas são strings "<valor>_<naipe>" (ex.: "3_paus").

Força das comuns:   Q < J < K < A < 2 < 3            (1..6)
Força das manilhas: 7_ouros < A_espadas < 7_copas < 4_paus   (7..10)
"""
import random

COMMON_VALUES = ["Q", "J", "K", "A", "2", "3"]
SUITS = ["ouros", "espadas", "copas", "paus"]
MANILHAS = ["7_ouros", "A_espadas", "7_copas", "4_paus"]

VALUE_STRENGTH = {v: i for i, v in enumerate(COMMON_VALUES, start=1)}
MANILHA_STRENGTH = {c: i for i, c in enumerate(MANILHAS, start=7)}


def new_deck():
    """As 27 cartas em ordem canônica (o A de espadas só aparece como manilha)."""
    common = [f"{v}_{s}" for v in COMMON_VALUES for s in SUITS if not (v == "A" and s == "espadas")]
    return common + MANILHAS


def shuffle(deck, seed):
    """Embaralhamento determinístico.

    Cada peer guarda uma réplica da mesa e precisa distribuir as mesmas cartas
    que os outros. Por isso o embaralhamento usa um gerador com estado próprio,
    criado só a partir da semente que viaja no comando replicado, e nunca o
    random global do processo.
    """
    cards = list(deck)
    random.Random(seed).shuffle(cards)
    return cards


def deal(shuffled, n_players):
    """3 cartas para cada jogador, em ordem de cadeira."""
    if n_players not in (2, 4):
        raise ValueError("truco é jogado por 2 ou 4 pessoas")
    return [shuffled[3 * i:3 * i + 3] for i in range(n_players)]


def card_strength(card):
    if card in MANILHA_STRENGTH:
        return MANILHA_STRENGTH[card]
    value, _ = parse(card)
    return VALUE_STRENGTH[value]


def parse(card):
    value, sep, suit = card.partition("_")
    valid = card in MANILHA_STRENGTH or (
        sep and value in VALUE_STRENGTH and suit in SUITS and not (value == "A" and suit == "espadas"))
    if not valid:
        raise ValueError(f"carta inválida: {card!r}")
    return value, suit
