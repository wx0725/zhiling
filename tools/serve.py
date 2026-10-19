"""启动网页：python -m tools.serve，然后打开 http://127.0.0.1:8790 。只监听本机。"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("server.app:app", host="127.0.0.1", port=8790, log_level="warning")
