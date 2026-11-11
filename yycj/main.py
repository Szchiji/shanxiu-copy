"""Railpack 入口：暴露 FastAPI app，兼容 uvicorn main:app 与 python main.py。"""
from bot.main import app, main

__all__ = ["app", "main"]

if __name__ == "__main__":
    main()
