"""
API HTTP que envuelve uno o varios ZTE MF920V para enviar SMS desde
cualquier equipo de la red local, con historial en SQLite y un dashboard
web para configurar routers y ver los mensajes enviados.

Configuración:
    .env         -> SMS_API_KEY (secreto compartido de la API),
                    DASHBOARD_USER / DASHBOARD_PASSWORD (login del dashboard,
                    opcional: si se dejan vacíos el dashboard no pide login)
    sms_gateway.db -> routers (id, ip, password, numero) y mensajes enviados,
                      administrados desde el dashboard web (GET /)

Ejecutar:
    python -m uvicorn api:app --host 0.0.0.0 --port 8000

Llamar desde otro equipo de la red:
    POST http://<ip_de_esta_pc>:8000/routers/<id_router>/sms/send
    Header: X-API-Key: <API_KEY>
    Body JSON: {"phone": "+51987654321", "message": "Hola"}
"""
import hmac
import os
import time
from pathlib import Path

import anyio
import anyio.to_thread
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from zte_sms import ZteApiError, ZteSms

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
API_KEY = os.environ.get("SMS_API_KEY", "")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
# Limpieza automática (0 = desactivada):
#   MAX_MESSAGES_DB -> mensajes que se conservan en sms_gateway.db por router
#   MAX_SMS_MODEM   -> si la memoria del módem llega a este número, se vacía antes de enviar
MAX_MESSAGES_DB = int(os.environ.get("MAX_MESSAGES_DB", "50"))
MAX_SMS_MODEM = int(os.environ.get("MAX_SMS_MODEM", "50"))
#   MAX_QUEUE_MODEM -> envíos que pueden esperar turno por módem; más allá se responde 503
MAX_QUEUE_MODEM = int(os.environ.get("MAX_QUEUE_MODEM", "20"))

db.init_db()

app = FastAPI(title="SMS Gateway (ZTE MF920V)")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class SendSmsRequest(BaseModel):
    phone: str = Field(..., description="Número destino, con código de país. Ej: +51987654321")
    message: str = Field(..., min_length=1, max_length=600)


class RouterIn(BaseModel):
    ip: str
    password: str | None = None  # None al editar = mantener la password actual
    numero: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


def check_api_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida")


def get_router_or_404(router_id: str) -> dict:
    router = db.get_router(router_id)
    if not router:
        raise HTTPException(status_code=404, detail=f"Router '{router_id}' no existe")
    return router


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "static" / "dashboard.html")


@app.get("/bootstrap")
def bootstrap():
    login_required = bool(DASHBOARD_USER and DASHBOARD_PASSWORD)
    return {
        "login_required": login_required,
        "api_key": None if login_required else API_KEY,
    }


@app.post("/login")
def login(req: LoginRequest):
    ok = (
        bool(DASHBOARD_USER)
        and bool(DASHBOARD_PASSWORD)
        and hmac.compare_digest(req.username, DASHBOARD_USER)
        and hmac.compare_digest(req.password, DASHBOARD_PASSWORD)
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Usuario o password invalidos")
    return {"api_key": API_KEY}


# ---------- routers ----------

@app.get("/routers")
def list_routers(x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    return db.list_routers()


@app.put("/routers/{router_id}")
def upsert_router(router_id: str, req: RouterIn, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    try:
        return db.upsert_router(router_id, req.ip, req.password, req.numero)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/routers/{router_id}")
def remove_router(router_id: str, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    if not db.delete_router(router_id):
        raise HTTPException(status_code=404, detail=f"Router '{router_id}' no existe")
    return {"status": "deleted", "router": router_id}


# ---------- SMS ----------

@app.post("/routers/{router_id}/sms/send")
async def send_sms(router_id: str, req: SendSmsRequest, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    router = get_router_or_404(router_id)

    # Un envío a la vez por módem: el router identifica la sesión por IP, así que
    # el logout de un envío paralelo cortaba la sesión de los demás.
    # La espera del turno es async (no ocupa un thread del pool), así el dashboard
    # y los demás endpoints siguen respondiendo aunque el módem esté lento.
    # pendientes = el que se está enviando + los que esperan turno
    if _pending.get(router_id, 0) > MAX_QUEUE_MODEM:
        raise HTTPException(
            status_code=503,
            detail=f"Modem '{router_id}' ocupado ({MAX_QUEUE_MODEM} envios en cola), reintente mas tarde",
        )
    _pending[router_id] = _pending.get(router_id, 0) + 1
    try:
        return await anyio.to_thread.run_sync(
            _send_locked, router, router_id, req, limiter=_router_limiter(router_id)
        )
    finally:
        _pending[router_id] -= 1


# Una conexión (sesión) por módem, reutilizada entre envíos: iniciar y cerrar
# sesión en cada SMS costaba varios segundos (la web del módem es lenta).
# Solo se usa dentro del limiter del router, o sea de a un thread por módem.
_clients: dict[str, ZteSms] = {}
_sends_since_trim: dict[str, int] = {}
TRIM_EVERY = 10  # revisar la memoria del módem cada N envíos


def _get_client(router: dict, router_id: str) -> ZteSms:
    zte = _clients.get(router_id)
    if zte is None or zte.router_ip != router["ip"] or zte.password != router["password"]:
        zte = ZteSms(router_ip=router["ip"], password=router["password"])
        zte.login()
        _clients[router_id] = zte
        _sends_since_trim[router_id] = TRIM_EVERY  # revisar la memoria al empezar
    return zte


def _send_locked(router: dict, router_id: str, req: SendSmsRequest):
    t0 = time.monotonic()
    try:
        zte = _get_client(router, router_id)

        # vaciar la memoria del módem antes de que se llene (llena = no envía);
        # si la limpieza falla, igual se intenta el envío
        if _sends_since_trim.get(router_id, 0) >= TRIM_EVERY:
            _sends_since_trim[router_id] = 0
            try:
                borrados = zte.trim_sms(MAX_SMS_MODEM)
                if borrados:
                    print(f"[{router_id}] memoria del modem vaciada: {borrados} SMS borrados", flush=True)
            except Exception as e:
                print(f"[{router_id}] no se pudo vaciar la memoria del modem: {e}", flush=True)

        zte.send_sms(req.phone, req.message)
        _sends_since_trim[router_id] = _sends_since_trim.get(router_id, 0) + 1
    except ZteApiError as e:
        _log(router_id, req, "failed", str(e))
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        # sesión/conexión en mal estado: la próxima vez se crea de nuevo
        _clients.pop(router_id, None)
        error = f"Error de conexion con el modem: {e}"
        _log(router_id, req, "failed", error)
        raise HTTPException(status_code=500, detail=error)

    print(f"[{router_id}] SMS enviado en {time.monotonic() - t0:.1f}s", flush=True)
    _log(router_id, req, "sent")
    return {"status": "sent", "router": router_id, "phone": req.phone}


_router_limiters: dict[str, anyio.CapacityLimiter] = {}
_pending: dict[str, int] = {}


def _router_limiter(router_id: str) -> anyio.CapacityLimiter:
    # solo se llama desde el event loop (un único thread), no necesita lock
    if router_id not in _router_limiters:
        _router_limiters[router_id] = anyio.CapacityLimiter(1)
    return _router_limiters[router_id]


def _log(router_id: str, req: SendSmsRequest, status: str, error: str | None = None):
    db.log_message(router_id, req.phone, req.message, status, error)
    db.prune_messages(router_id, MAX_MESSAGES_DB)


# ---------- historial y estadisticas ----------

@app.get("/messages")
def list_messages(
    router_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)
    return {
        "total": db.count_messages(router_id),
        "items": db.list_messages(router_id, limit, offset),
    }


@app.get("/routers/{router_id}/messages")
def list_router_messages(
    router_id: str,
    limit: int = 20,
    offset: int = 0,
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)
    get_router_or_404(router_id)
    return {
        "total": db.count_messages(router_id),
        "items": db.list_messages(router_id, limit, offset),
    }


@app.get("/stats")
def get_stats(x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    return db.stats()
