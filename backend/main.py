from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_CONFIG_PATH
from .backtester import BacktestError, run_backtest
from .llm_client import LLMClient, LLMClientError
from .strategy_parser import StrategyConfigError, parse_strategy_yaml
from .schemas import BacktestRequest, BacktestResponse, ChatMessage, ChatResponse


app = FastAPI(title="AI Trading System", version="0.1.0")

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


def _get_llm_client() -> LLMClient:
    try:
        return LLMClient()
    except LLMClientError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def serve_index() -> HTMLResponse:
    if not frontend_dir.exists():
        return HTMLResponse("<h1>Frontend not built yet.</h1>", status_code=200)
    index_path = frontend_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Missing index.html in frontend/</h1>", status_code=200)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/config")
def get_default_config() -> dict:
    raw_yaml = ""
    defaults = {}
    if DEFAULT_CONFIG_PATH.exists():
        raw_yaml = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        try:
            import yaml

            defaults = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError:
            defaults = {}

    app_cfg = defaults.get("app", {})
    return {
        "default_symbol": app_cfg.get("default_symbol", "510300.SH"),
        "default_years": app_cfg.get("default_years", 4),
        "initial_cash": app_cfg.get("initial_cash", 100000.0),
        "raw_yaml": raw_yaml,
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatMessage) -> ChatResponse:
    llm = _get_llm_client()
    try:
        plan = llm.generate_strategy_plan(request.message)
        parsed_config = parse_strategy_yaml(plan.yaml)
    except (LLMClientError, StrategyConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        bt_result = run_backtest(
            parsed_config,
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
        )
    except BacktestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    reasoning = plan.reasoning or "Strategy modules:\n" + "\n".join(
        f"- {comp.category}: {comp.objective}" for comp in plan.components
    )

    try:
        interpretation = llm.interpret_backtest(reasoning, bt_result["metrics"])
    except LLMClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ChatResponse(
        plan=plan,
        backtest_summary=bt_result["summary"],
        interpretation=interpretation,
        raw_metrics=bt_result["metrics"],
    )


@app.post("/api/backtest", response_model=BacktestResponse)
def backtest_endpoint(request: BacktestRequest) -> BacktestResponse:
    try:
        parsed_config = parse_strategy_yaml(request.config_yaml)
        bt_result = run_backtest(
            parsed_config,
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
        )
    except (StrategyConfigError, BacktestError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return BacktestResponse(summary=bt_result["summary"], metrics=bt_result["metrics"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
