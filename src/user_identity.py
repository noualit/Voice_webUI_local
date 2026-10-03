# user_identity.py
"""Identidad de Windows/AD que gobierna el namespace de memoria.

Por que existe
--------------
El login de la app es "introduce tu nombre" (texto libre) y ese texto era la
clave del namespace de memoria, via la cabecera `X-User`. Medido el 2026-10-01
en la BD real: 9 memorias bajo el nombre corto tecleado (p. ej. `ana`) y 1 bajo
la misma cuenta con dominio (`ana@EJEMPLO`, que es `config.memory_user`). Dos
historiales para la misma persona.

El problema mas grave no era la duplicidad sino que el aislamiento era nominal:
cualquiera que teclease el nombre de otra persona leia y escribia sus memorias,
porque no habia nada que comprobase ese nombre.

Que decide aqui
---------------
La identidad la fija `config.memory_user`, que es la cuenta real de Windows/AD de
la maquina, no lo que escriba el usuario. Se aceptan las formas de escribir esa
misma cuenta (corto, con dominio, en mayusculas, con espacios) porque el usuario
no tiene por que saber la forma canonica, pero **se guarda siempre la forma
canonica**: asi el namespace depende de la configuracion y no de como se tecleo
en cada sesion.

Lo que NO hace
--------------
No borra el `@DOMAIN`. La solucion facil al namespace duplicado habria sido
fundir las dos claves tirando el dominio, pero eso fusionaria cuentas homonimas
de dominios distintos en cualquier despliegue con mas de un dominio. Lo unico
que se acepta sin dominio es la *forma corta* de la misma cuenta, que es
justamente el caso real.
"""

DOMAIN_SEPARATOR = "@"


def canonical_user(name: str | None) -> str:
    """Forma canonica de una identidad: minusculas y sin espacios.

    Conserva el `@DOMAIN` si viene. `None` y vacios devuelven "" para que el
    llamante decida si eso es un error o el caso "sin sesion".
    """
    if not name:
        return ""
    return str(name).strip().lower()


def _parts(canonical: str) -> tuple[str, str]:
    """Separa (nombre corto, dominio) de una identidad ya canonizada."""
    if DOMAIN_SEPARATOR in canonical:
        short, _, domain = canonical.partition(DOMAIN_SEPARATOR)
        return short.strip(), domain.strip()
    return canonical, ""


def same_identity(typed: str | None, configured: str | None) -> bool:
    """True si `typed` es otra forma de escribir la cuenta `configured`.

    Se acepta la forma corta contra la forma con dominio porque en Active
    Directory son la misma cuenta. Dos identidades con dominio distinto NO son la
    misma cuenta, aunque coincida el nombre corto.
    """
    typed_c = canonical_user(typed)
    config_c = canonical_user(configured)
    if not typed_c or not config_c:
        return False
    if typed_c == config_c:
        return True
    typed_short, typed_domain = _parts(typed_c)
    config_short, config_domain = _parts(config_c)
    if typed_domain and config_domain and typed_domain != config_domain:
        return False
    return typed_short == config_short