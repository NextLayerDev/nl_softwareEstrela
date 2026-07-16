"""Integrações com serviços externos.

Fora das 4 camadas de propósito, como `app/realtime/` e `app/importer/`: não são regra de
negócio, são adaptadores. Nada aqui pode ser chamado de dentro de um request — o sistema
é offline-first e um request NUNCA espera a internet (ver app/jobs.py).
"""
