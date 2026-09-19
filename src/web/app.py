"""
Web 服务入口（阶段 2）。

启动：
    python -m src.web.app
    或
    uvicorn src.web.app:app --reload --port 8000

技术选型说明：

**为什么用 FastAPI？**
1. 原生支持 async，适合处理「调用外部 API 等待数十秒」这类 IO 密集任务
2. 自动生成 API 文档（/docs），面试演示时是个加分项
3. 类型提示驱动，配合 Pydantic 做请求校验

**为什么用 SSE 而不是 WebSocket？**
本场景是「客户端提交任务，服务器单向推送进度」——只有服务器往客户端推，
客户端不需要在分析过程中发消息。SSE 正好匹配这个模式：
- 基于 HTTP，无需额外协议握手
- 浏览器原生 EventSource API 支持
- 自动重连

WebSocket 是双向的，用在这里属于过度设计。

**为什么要用后台线程 + 队列？**
分析流水线是同步阻塞的（httpx 同步请求），如果直接在 SSE 生成器里跑，
会把整个事件循环卡住，其他请求全部排队。
所以用 asyncio.to_thread 把阻塞代码丢到线程池，通过队列把进度传回事件循环。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime
from functools import partial
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import get_config
from src.llm_client import LLMError
from src.pipeline import STAGES, analyze_image

# 项目根目录
WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"

# 允许的图片格式与大小上限
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

app = FastAPI(
    title="English-Windish API",
    description="拍照 → 语法解析 → 单词精讲",
    version="0.1.0",
)


# ======================================================================
# 页面
# ======================================================================


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """返回前端页面。"""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="前端页面缺失")
    return FileResponse(index_file)


# ======================================================================
# 健康检查
# ======================================================================


@app.get("/api/health")
async def health() -> dict:
    """健康检查。同时返回配置状态，便于排查「Key 没配上」这类问题。"""
    config = get_config()
    problems = config.validate()

    return {
        "status": "ok" if not problems else "config_error",
        "vision_provider": config.vision_provider,
        "vision_model": config.vision_model,
        "text_provider": config.text_provider,
        "text_model": config.text_model,
        "stages": STAGES,
        "problems": [p.replace("\n", " ") for p in problems],
    }


# ======================================================================
# 核心接口：分析图片（SSE 流式返回进度与结果）
# ======================================================================


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(..., description="英语文本图片"),
    word_count: int = Form(8, description="生成词卡数量"),
):
    """上传图片并分析。

    以 Server-Sent Events 流式返回，事件类型：
    - `{"type": "start", ...}`    开始，告知总步骤数
    - `{"type": "progress", ...}` 进度更新
    - `{"type": "done", ...}`     完成，带完整结果
    - `{"type": "error", ...}`    出错，带错误信息
    """
    # ---- 校验 ----
    config = get_config()
    problems = config.validate()
    if problems:
        raise HTTPException(
            status_code=503,
            detail="服务器配置不完整：" + "；".join(p.replace("\n", " ") for p in problems),
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片格式：{suffix or '未知'}。"
            f"支持的格式：{'、'.join(sorted(ALLOWED_SUFFIXES))}",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"图片过大（{len(content) / 1024 / 1024:.1f} MB），上限 10 MB",
        )
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件是空的")

    # ---- 保存到本地 ----
    # 用 uuid 重命名，避免：
    # 1. 用户的原始文件名含路径分隔符导致目录穿越
    # 2. 中文文件名在不同系统上的编码问题
    # 3. 同名文件互相覆盖
    upload_dir = config.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
    saved_path = upload_dir / saved_name
    saved_path.write_bytes(content)

    return StreamingResponse(
        _analysis_event_stream(saved_path, word_count),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 部署在 nginx 后面时，这个头能禁用响应缓冲，
            # 否则 SSE 会被缓冲成一坨，进度就失去意义了
            "X-Accel-Buffering": "no",
        },
    )


async def _analysis_event_stream(saved_path: Path, word_count: int):
    """把分析过程转成 SSE 事件流。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_progress(step: int, total: int, message: str) -> None:
        """进度回调。

        这个回调在**工作线程**里被调用，而 asyncio.Queue 不是线程安全的，
        所以必须用 call_soon_threadsafe 把写队列的操作丢回事件循环线程。
        这是 asyncio 与线程池协作时的关键细节。
        """
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {
                "type": "progress",
                "step": step,
                "total": total,
                "message": message,
            },
        )

    def worker() -> None:
        """在工作线程里执行的阻塞流水线。"""
        try:
            result = analyze_image(
                saved_path,
                word_count=word_count,
                on_progress=on_progress,
            )
            payload = {
                "type": "done",
                "result": result.to_dict(),
                "summary": result.summary(),
            }
        except (LLMError, FileNotFoundError, ValueError) as exc:
            payload = {"type": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 — 兜底，避免线程静默死亡
            payload = {
                "type": "error",
                "message": f"未预期的错误：{type(exc).__name__}: {exc}",
            }

        loop.call_soon_threadsafe(queue.put_nowait, payload)

    # 先发一个 start 事件，让前端知道总步骤数，好渲染进度条
    yield _sse(
        {
            "type": "start",
            "total": len(STAGES),
            "stages": STAGES,
            "filename": saved_path.name,
        }
    )

    task = asyncio.create_task(asyncio.to_thread(worker))

    try:
        while True:
            event = await queue.get()
            yield _sse(event)
            if event["type"] in ("done", "error"):
                break
    finally:
        # 客户端中途断开时，确保后台线程不会一直挂着
        if not task.done():
            task.cancel()


def _sse(payload: dict) -> str:
    """把字典格式化成 SSE 事件。

    ensure_ascii=False 很重要——否则中文会被转义成 \\uXXXX，
    白白撑大传输体积。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ======================================================================
# 报告下载（可选）
# ======================================================================


@app.get("/api/samples")
async def list_samples() -> dict:
    """列出 data/samples 下的示例图片，方便快速试跑。"""
    config = get_config()
    sample_dir = config.sample_dir

    if not sample_dir.exists():
        return {"samples": []}

    files = [
        p.name
        for p in sorted(sample_dir.iterdir())
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES and not p.name.startswith(".")
    ]
    return {"samples": files}


@app.exception_handler(HTTPException)
async def http_exception_handler(_request, exc: HTTPException):
    """统一错误返回格式，前端只需处理一种结构。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"type": "error", "message": exc.detail},
    )


# 静态资源挂在最后，避免覆盖上面定义的路由
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> int:
    """直接运行本模块时启动开发服务器。"""
    import uvicorn

    config = get_config()
    if config.debug:
        print("=" * 60)
        print("  English-Windish Web")
        print("=" * 60)
        print(f"  视觉：{config.vision_provider} / {config.vision_model}")
        print(f"  文本：{config.text_provider} / {config.text_model}")
        problems = config.validate()
        if problems:
            print("\n  ⚠ 配置问题：")
            for p in problems:
                print(f"    - {p.replace(chr(10), ' ')}")
        print("\n  访问：http://127.0.0.1:8000")
        print("=" * 60)

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
