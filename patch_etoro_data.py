import re

with open("automation/adapters/etoro_data.py", "r") as f:
    code = f.read()

code = code.replace(
    "except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:",
    """except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None"""
)

code = code.replace(
    "await asyncio.sleep(delay)",
    """await asyncio.sleep(delay)
            except Exception as e:
                self._log.error(
                    f"Unerwarteter Fehler (Programmierfehler) beim Verbindungsaufbau: {e}\\n{traceback.format_exc()}",
                    LogColor.RED
                )
                os._exit(1)"""
)

code = code.replace(
    "size_prec = self._instrument_size_precision.get(instr_id, 2)",
    "size_prec = self._instrument_size_precision.get(instr_id) or get_size_precision(instr_id)"
)


with open("automation/adapters/etoro_data.py", "w") as f:
    f.write(code)
