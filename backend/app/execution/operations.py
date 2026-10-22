"""Keep operation reservations until the actual asynchronous worker has finished."""

import asyncio


async def finish_on_cancel(awaitable):
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError()
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def run_sync(call, *args):
    return await finish_on_cancel(asyncio.to_thread(call, *args))
