import asyncio
import pytest
import src.assembly.assembler as _asm


@pytest.fixture(autouse=True)
def reset_assembler_semaphore():
    _asm._SEMAPHORE = asyncio.Semaphore(1)
    yield
    _asm._SEMAPHORE = None
