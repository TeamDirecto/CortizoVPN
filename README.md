# CortizoVPN

Herramienta para administrar usuarios, grupos y extensiones de VICIdial en el clúster CortizoVPN.

## Objetivo inicial

La primera fase del proyecto se enfoca en:

- descubrir y consultar `vicidial_user_groups`;
- validar conectividad SSH hacia los dialers;
- validar el salto SSH hacia BD MASTER y BD SLAVE;
- consultar extensiones y usuarios existentes;
- preparar operaciones de alta/modificación mediante un flujo `plan -> apply`.

En esta primera etapa **no se realizan cambios destructivos ni altas automáticas**.

## Topología

| Nodo | LAN | WAN | Acceso |
|---|---|---|---|
| DIAL1 | 10.10.15.10 | 201.132.81.26 | SSH WAN :19600 |
| DIAL2 | 10.10.15.11 | 201.132.81.27 | SSH WAN :19600 |
| DIAL3 | 10.10.15.12 | 201.132.81.28 | SSH WAN :19600 |
| DIAL4 | 10.10.15.13 | 201.132.81.29 | SSH WAN :19600 |
| BD SLAVE | 10.10.15.14 | N/A | segundo salto por LAN |
| BD MASTER | 10.10.15.15 | N/A | segundo salto por LAN |

## Seguridad

No guardar contraseñas, llaves privadas ni secretos dentro del repositorio.

La configuración sensible deberá mantenerse fuera de Git y cargarse por variables de entorno o archivos locales ignorados por `.gitignore`.

## Estructura prevista

```text
app/
  main.py
  config.py
  db.py
  ssh.py
  services/
    user_groups.py
    users.py
    extensions.py
    nodes.py
config/
  infrastructure.yml.example
scripts/
  test_connections.py
```

## Flujo previsto

```text
infraestructura
    -> conectividad SSH
    -> conectividad BD
    -> lectura de user_groups
    -> validación de usuario/extensión
    -> plan
    -> apply
    -> verify
```


## Arranque local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config/infrastructure.yml.example config/infrastructure.yml
cp .env.example .env
```

Las credenciales SSH reales deben cargarse únicamente en el entorno local. Por ejemplo:

```bash
export CORTIZOVPN_SSH_PASSWORD='...'
```

Prueba inicial de conectividad:

```bash
python3 scripts/test_connections.py
```

API de desarrollo:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Endpoints iniciales:

- `GET /health`
- `GET /nodes`

La consulta de `vicidial_user_groups` ya está definida en `app/services/user_groups.py`; la conexión a MariaDB se habilitará después de validar primero la ruta SSH completa hasta MASTER/SLAVE.


## Frontend web

La aplicación sirve una interfaz inicial en `/`. En desarrollo:

```bash
python3 app/main.py
```

Abrir localmente:

```text
http://127.0.0.1:8000/
```

Para publicación interna detrás de Apache, el ejemplo está en:

```text
deployment/apache/CortizoVPN.conf.example
```

La ruta prevista es:

```text
/CortizoVPN/
```

El servicio de producción puede ejecutarse con Gunicorn usando:

```text
deployment/systemd/cortizovpn.service.example
```

La interfaz actual está intencionalmente en modo lectura: muestra los `user_groups` del MASTER, pero no crea ni modifica usuarios todavía.


## GitHub Pages frontend

El frontend estático vive en `docs/` para publicarse con GitHub Pages.

URL esperada:

```text
https://teamdirecto.github.io/CortizoVPN/
```

En GitHub configurar **Settings -> Pages -> Deploy from a branch -> main -> /docs**.

El frontend consume la API publicada por Apache en:

```text
https://vicidial97.directo.com/cortizovpn-api/
```

Apache debe mapear esa ruta a `http://127.0.0.1:8000/api/`.


## Extensiones Cortizo

Rango global reservado:

```text
161001 - 162800
```

La extension base pertenece al agente. La homologacion por dialer se prepara asi:

```text
dial1 / Marcador1 -> 161001
dial2 / marcador2 -> 161001b
dial3 / marcador3 -> 161001c
dial4 / marcador4 -> 161001d
```

DIAL1 queda preparado pero deshabilitado temporalmente en configuracion. El flujo de
despliegue previsto por nodo es:

```text
CREATE_PHONE
-> REQUEST_REBUILD_CONF
-> WAIT_REBUILD
-> SIP_RELOAD
-> VERIFY_SIP_PEER
```

Antes de habilitar CREATE_PHONE se debe validar una plantilla real de `phones`
del nuevo Cortizo. Los endpoints de plan/verificacion ya estan disponibles:

```text
GET /api/extensions/161001/plan
GET /api/extensions/161001/verify
```
