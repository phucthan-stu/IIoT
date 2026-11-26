"""Independent motor polling, normalization and verified AAS updates."""
import asyncio
import base64
import json
import math
import os
import time
from datetime import datetime, timezone
from aas_http import request, configuration
from pymodbus.client import ModbusTcpClient
from asyncua import Client

API_BASE = os.getenv("AAS_BASE_URL", "http://localhost:8082").rstrip("/")
BASE_URN = "urn:university:aas-demo"
PERIOD = float(os.getenv("POLL_PERIOD_S", "1.0"))
if not math.isfinite(PERIOD) or PERIOD <= 0:
    raise ValueError("POLL_PERIOD_S must be finite and positive")
OPCUA_ENDPOINT = "opc.tcp://127.0.0.1:4840/motor-b/"
NS_URI = "urn:aas-demo:motors"


def encoded_submodel_id(motor):
    identifier = f"{BASE_URN}:submodel:motor-{motor.lower()}:operational"
    return base64.urlsafe_b64encode(identifier.encode()).decode().rstrip("=")


def normalize(raw, protocol):
    t, rpm, state, current = raw
    if protocol == "modbus":
        if state not in (0, 1):
            raise ValueError("Modbus operating state must be 0 or 1")
        t, current = t / 10.0, current / 100.0
    else:
        if not isinstance(state, bool) or float(t) < 0:
            raise ValueError("Invalid OPC UA state or Kelvin temperature")
        t = float(t) - 273.15
    if not all(math.isfinite(float(v)) for v in (t, rpm, current)):
        raise ValueError("Non-finite telemetry")
    return {"MotorTemperature": round(float(t), 2),
            "MotorSpeed": round(float(rpm), 2),
            "OperatingState": bool(state),
            "MotorCurrent": round(float(current), 2)}


def read_motor_a():
    client = ModbusTcpClient("127.0.0.1", port=15020, timeout=2, retries=0)
    try:
        if not client.connect():
            raise ConnectionError("Motor A unavailable")
        result = client.read_holding_registers(address=0, count=4, device_id=1)
        if result.isError() or len(result.registers) != 4:
            raise ValueError("Invalid Modbus response")
        return list(result.registers)
    finally:
        client.close()


async def read_motor_b():
    # New bounded session on each poll also permits recovery after a restart.
    # Session overhead is included in acquisition time; optimize before scale tests.
    async with Client(OPCUA_ENDPOINT, timeout=2) as client:
        idx = await client.get_namespace_index(NS_URI)
        motor = await client.nodes.objects.get_child([f"{idx}:MotorB"])
        nodes = [await motor.get_child([f"{idx}:{name}"]) for name in
                 ("TemperatureK", "SpeedRPM", "Running", "CurrentA")]
        return await client.read_values(nodes)


def update_and_verify(motor, sample):
    codes = []
    for name, value in sample.items():
        path = f"/submodels/{encoded_submodel_id(motor)}/submodel-elements/{name}"
        response = request("PATCH", path + "/$value", json=value, timeout=3)
        response.raise_for_status()
        codes.append(response.status_code)
        response = request("GET", path, timeout=3)
        response.raise_for_status()
        observed = response.json()["value"]
        if isinstance(value, bool):
            matches = str(observed).lower() == str(value).lower()
        else:
            matches = math.isclose(float(observed), value, rel_tol=0, abs_tol=0.005)
        if not matches:
            raise ValueError(f"Readback mismatch: {motor}/{name}")
    return codes


def log(**record):
    print(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **record}), flush=True)


async def worker(motor):
    while True:
        started = time.perf_counter()
        try:
            raw = (await asyncio.to_thread(read_motor_a) if motor == "A"
                   else await asyncio.wait_for(read_motor_b(), timeout=10))
            acquired = time.perf_counter()
            sample = normalize(raw, "modbus" if motor == "A" else "opcua")
            codes = await asyncio.to_thread(update_and_verify, motor, sample)
            log(motor=motor, status="verified", raw=raw, normalized=sample,
                write_codes=codes,
                acquisition_ms=round((acquired-started)*1000, 3),
                update_readback_ms=round((time.perf_counter()-acquired)*1000, 3))
        except Exception as exc:
            # No replacement/fake value is written on source read failure.
            log(motor=motor, status="error", error=f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started
        await asyncio.sleep(max(0.05, PERIOD-elapsed))


async def main():
    configuration()  # Fail early on unsafe HTTP configuration.
    await asyncio.gather(worker("A"), worker("B"))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
