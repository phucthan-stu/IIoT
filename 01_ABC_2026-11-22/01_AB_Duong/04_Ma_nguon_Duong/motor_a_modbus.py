"""Simulated industrial motor A: Modbus TCP holding registers."""
import asyncio
import math
import time
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusDeviceContext, ModbusServerContext
from pymodbus.server import StartAsyncTcpServer
 
HOST = "127.0.0.1"
PORT = 15020
DEVICE_ID = 1
 
async def main():
    device = ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [0] * 32))
    context = ModbusServerContext(devices={DEVICE_ID: device}, single=False)
 
    async def update_registers():
        while True:
            elapsed = time.monotonic()
            temperature_c = 65.0 + 5.0 * math.sin(elapsed / 10.0)
            speed_rpm = 1450 + int(60 * math.sin(elapsed / 7.0))
            running = 1
            current_a = 3.20 + 0.20 * math.sin(elapsed / 5.0)
            # Register map (0-based): T * 10, RPM, state, I * 100.
            raw_values = [round(temperature_c * 10), speed_rpm, running, round(current_a * 100)]
            device.setValues(3, 0, raw_values)
            await asyncio.sleep(1.0)
 
    await asyncio.gather(
        StartAsyncTcpServer(context=context, address=(HOST, PORT)),
        update_registers(),
    )
 
if __name__ == "__main__":
    asyncio.run(main())
