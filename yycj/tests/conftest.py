import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Fake token, test-only. Never a real credential.
os.environ.setdefault("BOT_TOKEN", "123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx")
os.environ.setdefault("ADMIN_IDS", "1")


# A test must use a FakeBot/AsyncMock, never an actual Telegram request.
import pytest

@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    from aiogram import Bot
    async def forbidden(*args,**kwargs):
        raise AssertionError("Real Telegram API disabled in tests; use FakeBot")
    monkeypatch.setattr(Bot,"__call__",forbidden)


@pytest.fixture(autouse=True)
def isolated_local_pg(request):
    """Opt-in reset ONLY for this box's disposable local cluster; never arbitrary DBs."""
    if os.environ.get('YYCJ_TEST_RESET_PG')!='1' or not request.node.path.name.startswith('test_pg'):
        return
    import asyncio
    import asyncpg
    from urllib.parse import urlsplit
    uri=os.environ['YYCJ_TEST_PG'].replace('postgresql+asyncpg://','postgresql://')
    u=urlsplit(uri)
    assert (u.hostname,u.port,u.username,u.path)==('127.0.0.1',55441,'yycj_test','/yycj_edit_test')
    async def reset():
        con=await asyncpg.connect(uri)
        try:
            names=await con.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            if names:
                quoted=','.join('public."'+r['tablename'].replace('"','""')+'"' for r in names)
                await con.execute('TRUNCATE '+quoted+' RESTART IDENTITY CASCADE')
        finally:await con.close()
    asyncio.run(reset())
