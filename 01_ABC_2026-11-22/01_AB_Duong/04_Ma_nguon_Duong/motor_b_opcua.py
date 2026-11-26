"""Simulated industrial motor B: OPC UA nodes with Kelvin temperature."""
import asyncio
import math
import time
from asyncua import Server
 
ENDPOINT = "opc.tcp://127.0.0.1:4840/motor-b/"
NAMESPACE = "urn:aas-demo:motors"
 
async def main():
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    namespace_idx = await server.register_namespace(NAMESPACE)
    motor = await server.nodes.objects.add_object(namespace_idx, "MotorB")
    temp_node = await motor.add_variable(namespace_idx, "TemperatureK", 338.15)
    speed_node = await motor.add_variable(namespace_idx, "SpeedRPM", 1450.0)
    running_node = await motor.add_variable(namespace_idx, "Running", True)
    current_node = await motor.add_variable(namespace_idx, "CurrentA", 3.1)
    async with server:
        while True:
            t = time.monotonic()
            await temp_node.write_value(338.15 + 5.0 * math.sin(t / 11.0))
            await speed_node.write_value(1450.0 + 60.0 * math.sin(t / 7.5))
            await running_node.write_value(True)
            await current_node.write_value(3.1 + 0.2 * math.sin(t / 6.0))
            await asyncio.sleep(1.0)
 
if __name__ == "__main__":
    asyncio.run(main())
