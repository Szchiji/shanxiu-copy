"""初始化 Postgres 表结构。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.db import close_db, connect_db


async def main() -> None:
    await connect_db()
    print("Tables ensured.")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
