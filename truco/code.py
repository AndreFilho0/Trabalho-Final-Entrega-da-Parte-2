"""Código de convite da mesa (vai no link).

8 caracteres de um alfabeto de 31 símbolos sem os ambíguos (0/O, 1/I/L):
31^8 ≈ 8,5 × 10^11 combinações, inviável de adivinhar. Exibido como K7QX-29FA.
"""
import re
import secrets

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def normalize(code):
    """Aceita "k7qx-29fa", "K7QX29FA" etc. e devolve "K7QX29FA"."""
    return re.sub(r"[^A-Z0-9]", "", str(code).upper())


def fmt(code):
    return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code
