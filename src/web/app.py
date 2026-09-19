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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import get_config
from src.llm_client import LLMError
from src.pipeline import IMAGE_STAGES, TEXT_STAGES, analyze_image, analyze_text
from src.storage import KIND_IMAGE, KIND_TEXT, get_history_store

# 项目根目录
WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"

# 允许的图片格式与大小上限
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# 文本输入的字符上限。设这个值是为了防止超长文本一次消耗过多 API 额度
MAX_TEXT_CHARS = 20000

app = FastAPI(
    title="English-Windish API",
    description="拍照 / 输入文本 → 语法解析 → 单词精讲",
    version="0.2.0",
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
        "image_stages": IMAGE_STAGES,
        "text_stages": TEXT_STAGES,
        "problems": [p.replace("\n", " ") for p in problems],
    }


# ======================================================================
# SSE 通用框架
#
# 图片分析和文本分析都是「跑一个阻塞任务 + 推进度」，只是任务内容不同。
# 所以把 SSE 的骨架抽出来，两处共用。
# ======================================================================

# 任务函数的签名：接收进度回调，返回要发给前端的最终 payload
JobRunner = Callable[[Callable[[int, int, str], None]], dict[str, Any]]


def _build_event_stream(
    run_job: JobRunner,
    start_payload: dict[str, Any],
    stages: list[str],
) -> StreamingResponse:
    """构造一个 SSE 响应。

    Args:
        run_job: 在工作线程里执行的函数，接收进度回调，返回最终 payload
        start_payload: start 事件的附加字段
        stages: 本次任务实际会经历的阶段。

            必须由调用方显式传入，不能用模块级常量兜底——
            图片 4 步、文本 3 步，阶段文案写错会让进度条显示一个
            根本不会执行的步骤，比没有进度条更让人困惑。
    """

    async def event_stream():
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
            """在工作线程里执行的阻塞任务。"""
            try:
                payload = run_job(on_progress)
            except (LLMError, FileNotFoundError, ValueError) as exc:
                payload = {"type": "error", "message": str(exc)}
            except Exception as exc:  # noqa: BLE001 — 兜底，避免线程静默死亡
                payload = {
                    "type": "error",
                    "message": f"未预期的错误：{type(exc).__name__}: {exc}",
                }

            loop.call_soon_threadsafe(queue.put_nowait, payload)

        yield _sse(
            {
                "type": "start",
                "total": len(stages),
                "stages": stages,
                **start_payload,
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

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 部署在 nginx 后面时，这个头能禁用响应缓冲，
            # 否则 SSE 会被缓冲成一坨，进度就失去意义了
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict) -> str:
    """把字典格式化成 SSE 事件。

    ensure_ascii=False 很重要——否则中文会被转义成 \\uXXXX，
    白白撑大传输体积。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _ensure_configured() -> None:
    """配置不完整时直接拒绝请求，并说明缺什么。"""
    problems = get_config().validate()
    if problems:
        raise HTTPException(
            status_code=503,
            detail="服务器配置不完整：" + "；".join(p.replace("\n", " ") for p in problems),
        )


def _save_history(
    kind: str, source: str, result_dict: dict[str, Any]
) -> int | None:
    """保存到历史记录。失败不应影响主流程，所以吞掉异常只打印警告。"""
    try:
        return get_history_store().save(kind, source, result_dict)
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 保存历史记录失败：{exc}", file=sys.stderr)
        return None


# ======================================================================
# 图片分析
# ======================================================================


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(..., description="英语文本图片"),
    word_count: int = Form(8, description="生成词卡数量"),
):
    """上传图片并分析（SSE 流式返回进度与结果）。"""
    _ensure_configured()

    original_name = file.filename or "未命名图片"
    suffix = Path(original_name).suffix.lower()

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

    # 用 uuid 重命名，避免：
    # 1. 用户的原始文件名含路径分隔符导致目录穿越
    # 2. 中文文件名在不同系统上的编码问题
    # 3. 同名文件互相覆盖
    config = get_config()
    upload_dir = config.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
    saved_path = upload_dir / saved_name
    saved_path.write_bytes(content)

    word_count = max(3, min(15, word_count))

    def run_job(report) -> dict[str, Any]:
        result = analyze_image(
            saved_path, word_count=word_count, on_progress=report
        )
        payload = result.to_dict()
        record_id = _save_history(KIND_IMAGE, original_name, payload)
        return {
            "type": "done",
            "result": payload,
            "summary": result.summary(),
            "record_id": record_id,
        }

    return _build_event_stream(
        run_job, {"kind": "image", "filename": original_name}, IMAGE_STAGES
    )


# ======================================================================
# 文本分析
# ======================================================================


@app.post("/api/analyze-text")
async def analyze_text_endpoint(
    text: str = Form(..., description="英文文本，可以是单词、词组或句子"),
    word_count: int = Form(8, description="生成词卡数量"),
):
    """直接分析文本（SSE 流式返回）。

    支持三种输入粒度：
    - **单词**：如 `accommodate` —— 生成完整词卡，语法部分为空
    - **词组**：如 `take advantage of` —— 词卡 + 短语用法
    - **句子/段落**：完整的语法解析 + 词卡
    """
    _ensure_configured()

    cleaned = (text or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="输入内容不能为空")

    if len(cleaned) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"文本过长（{len(cleaned)} 字符），上限 {MAX_TEXT_CHARS} 字符",
        )

    word_count = max(3, min(15, word_count))

    # 历史记录里的来源标识：短文本直接用原文，长文本截断
    source = cleaned if len(cleaned) <= 100 else cleaned[:100] + "…"

    def run_job(report) -> dict[str, Any]:
        result = analyze_text(
            cleaned, word_count=word_count, on_progress=report
        )
        payload = result.to_dict()
        record_id = _save_history(KIND_TEXT, source, payload)
        return {
            "type": "done",
            "result": payload,
            "summary": result.summary(),
            "record_id": record_id,
        }

    return _build_event_stream(
        run_job, {"kind": "text", "filename": source}, TEXT_STAGES
    )


# ======================================================================
# 历史记录
# ======================================================================


@app.get("/api/history")
async def history_list(limit: int = 50, offset: int = 0) -> dict:
    """列出历史记录（不含完整内容）。"""
    store = get_history_store()
    limit = max(1, min(200, limit))

    items = store.list(limit=limit, offset=offset)

    return {
        "total": store.count(),
        "items": [item.to_dict() for item in items],
    }


@app.get("/api/history/{record_id}")
async def history_detail(record_id: int) -> dict:
    """读取单条历史记录的完整内容。"""
    record = get_history_store().get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"记录不存在：{record_id}")
    return record


@app.delete("/api/history/{record_id}")
async def history_delete(record_id: int) -> dict:
    """删除单条历史记录。"""
    if not get_history_store().delete(record_id):
        raise HTTPException(status_code=404, detail=f"记录不存在：{record_id}")
    return {"deleted": record_id}


@app.delete("/api/history")
async def history_clear() -> dict:
    """清空全部历史记录。"""
    count = get_history_store().clear()
    return {"cleared": count}


# ======================================================================
# 示例图片
# ======================================================================


@app.get("/api/samples")
async def list_samples() -> dict:
    """列出 data/samples 下的示例图片，方便快速试跑。"""
    sample_dir = get_config().sample_dir

    if not sample_dir.exists():
        return {"samples": []}

    files = [
        p.name
        for p in sorted(sample_dir.iterdir())
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES and not p.name.startswith(".")
    ]
    return {"samples": files}


# ======================================================================
# 异常处理
# ======================================================================


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


# ======================================================================
# 启动
# ======================================================================


def main() -> int:
    """启动服务。

    两种运行模式：

    - **本地开发**：监听 127.0.0.1:8000。
      绑定到回环地址而不是 0.0.0.0 是有意的——避免把开发中的服务
      暴露给同一局域网内的其他设备（比如校园网里的陌生人）。

    - **云端部署**：平台会注入 PORT 环境变量，此时监听 0.0.0.0:$PORT。
      容器环境下必须绑定 0.0.0.0，否则容器外无法访问。
    """
    import os

    import uvicorn

    port_env = os.getenv("PORT")
    if port_env:
        host, port = "0.0.0.0", int(port_env)
        is_deploy = True
    else:
        host, port = "127.0.0.1", 8000
        is_deploy = False

    config = get_config()

    if not is_deploy:
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
        print(f"\n  访问：http://{host}:{port}")
        print("=" * 60)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info" if config.debug else "warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
