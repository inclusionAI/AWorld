import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

@asynccontextmanager
async def aw(r:bool) -> AsyncGenerator[bool, None]:
    if r:
        print(f"True")
        yield True
    else:
        print(f"False")
        
        
async def test_async_generator():
    async with aw(True) as result:
        print(f"Result: {result}")
    async with aw(False) as result:
        print(f"Result: {result}")

asyncio.run(test_async_generator())