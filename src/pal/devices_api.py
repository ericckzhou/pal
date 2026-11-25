"""Devices API: pair a device with a code + passkey, sign in with a passkey, list and revoke devices (S3)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import auth, devices
from .api import require_owner, rp_origin, set_session_cookie

public = APIRouter(prefix="/api/auth")
owner = APIRouter(prefix="/api/devices", dependencies=[Depends(require_owner)])


class PairStart(BaseModel):
    code: str = Field(max_length=32)


class Finish(BaseModel):
    flow: str = Field(max_length=64)
    credential: dict[str, Any]


class NewDevice(BaseModel):
    name: str = Field(max_length=60)
    kind: str = Field(default="phone", max_length=20)


def _bad(exc: devices.DeviceError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@public.post("/pair/start")
def pair_start(body: PairStart, request: Request) -> dict:
    state = request.app.state
    if state.pair_limiter.locked():
        raise HTTPException(status_code=429, detail="Too many wrong codes. Try again in a few minutes.")
    with state.pool.connection() as conn:
        found = devices.check_pairing_code(conn, body.code)
    if found is None:
        state.pair_limiter.record_failure()
        raise HTTPException(status_code=400, detail="That code is wrong, expired or already used.")
    rp_id, origin = rp_origin(request)
    return devices.start_registration(state.ceremonies, rp_id, origin, found[0], found[1])


@public.post("/pair/finish")
def pair_finish(body: Finish, request: Request, response: Response) -> dict:
    state = request.app.state
    try:
        ceremony = state.ceremonies.take(body.flow, "register")
        with state.pool.connection() as conn:
            device_id = devices.finish_registration(conn, ceremony, body.credential)
            token = auth.create_session(conn, state.settings.session_days, "passkey", device_id)
            name = devices.device_name(conn, device_id)
    except devices.DeviceError as exc:
        raise _bad(exc) from exc
    state.pair_limiter.record_success()
    set_session_cookie(request, response, token, state.settings.session_days)
    return {"ok": True, "device": name}


@public.post("/passkey/start")
def passkey_start(request: Request) -> dict:
    rp_id, origin = rp_origin(request)
    return devices.start_login(request.app.state.ceremonies, rp_id, origin)


@public.post("/passkey/finish")
def passkey_finish(body: Finish, request: Request, response: Response) -> dict:
    state = request.app.state
    if state.pair_limiter.locked():
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in a few minutes.")
    try:
        ceremony = state.ceremonies.take(body.flow, "login")
        with state.pool.connection() as conn:
            device_id = devices.finish_login(conn, ceremony, body.credential)
            token = auth.create_session(conn, state.settings.session_days, "passkey", device_id)
            name = devices.device_name(conn, device_id)
    except devices.DeviceError as exc:
        state.pair_limiter.record_failure()
        raise _bad(exc) from exc
    set_session_cookie(request, response, token, state.settings.session_days)
    return {"ok": True, "device": name}


@owner.get("")
def list_devices(request: Request) -> list[dict]:
    with request.app.state.pool.connection() as conn:
        return devices.list_devices(conn)


@owner.post("/pairing", status_code=201)
def new_pairing_code(body: NewDevice, request: Request) -> dict:
    public_host = request.app.state.settings.public_host
    try:
        with request.app.state.pool.connection() as conn:
            made = devices.create_pairing_code(conn, body.name, body.kind)
    except devices.DeviceError as exc:
        raise _bad(exc) from exc
    raw = made.pop("raw")
    made["url"] = f"https://{public_host}/?pair={raw}" if public_host else None
    return made


@owner.post("/{device_id}/revoke")
def revoke(device_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        if not devices.revoke_device(conn, device_id):
            raise HTTPException(status_code=404, detail="No such active device")
    return {"ok": True}


@owner.delete("/{device_id}", status_code=204)
def forget(device_id: UUID, request: Request) -> Response:
    with request.app.state.pool.connection() as conn:
        if not devices.forget_device(conn, device_id):
            raise HTTPException(status_code=409, detail="Revoke the device before removing it")
    return Response(status_code=204)
