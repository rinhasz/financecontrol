"""Preferência por IPv4 nas conexões de saída.

Esta rede anuncia endereços IPv6 para os serviços que o app consome
(login.microsoftonline.com, graph.microsoft.com, generativelanguage.
googleapis.com) mas não roteia nenhum deles: conectar num IPv6 só falha
depois do timeout de TCP do Windows (~21s), enquanto o IPv4 responde em
~0,02s. Como o Python tenta os endereços na ordem que o sistema devolve —
IPv6 primeiro — cada chamada de rede pagava esse pedágio: medi 21s numa
construção de cliente MSAL e 168s numa requisição simples. Na prática
isso travava a tela de "Procurar em Emails" inteira.

O curl não sofre com isso porque implementa Happy Eyeballs (RFC 8305),
tentando IPv4 e IPv6 em paralelo; nem urllib3 (requests/MSAL) nem httpx
(SDK do Gemini) têm equivalente.

O patch é em `socket.getaddrinfo` de propósito: é o ponto único por onde
todas essas bibliotecas passam, então cobre requests, httpx e qualquer
outra de uma vez — remendar cada uma separadamente deixaria brechas (foi
o que aconteceu quando só o urllib3 estava coberto e o Gemini continuou
travando).

Se algum dia o app rodar numa rede só-IPv6, basta `FORCE_IPV4=0` no .env.
"""
import os
import socket

_getaddrinfo_original = socket.getaddrinfo
_aplicado = False


def preferir_ipv4() -> bool:
    global _aplicado
    if _aplicado or os.environ.get('FORCE_IPV4', '1') == '0':
        return _aplicado

    def getaddrinfo_somente_ipv4(host, port, family=0, *args, **kwargs):
        # só força quando a família não foi pedida explicitamente — quem
        # pede AF_INET6 de propósito continua recebendo o que pediu
        if family == socket.AF_UNSPEC:
            family = socket.AF_INET
        return _getaddrinfo_original(host, port, family, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo_somente_ipv4
    _aplicado = True
    return True
