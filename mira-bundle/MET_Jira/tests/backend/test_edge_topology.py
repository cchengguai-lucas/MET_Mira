from __future__ import annotations

import importlib.util
import stat
from pathlib import Path


def _edge_config_module():
    root = Path(__file__).parents[2]
    path = root / "scripts/ops/edge_config.py"
    spec = importlib.util.spec_from_file_location("edge_config", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_edge_generator_renders_secure_transaction_pool_config(tmp_path):
    edge_config = _edge_config_module()
    root = Path(__file__).parents[2]
    runtime = tmp_path / "runtime"
    state = tmp_path / "state"
    units = tmp_path / "units"
    password = "not-a-real-password"
    verifier = "SCRAM-SHA-256$4096:dGVzdA==$stored:server"

    generated = edge_config.generate(
        root=root,
        runtime_dir=runtime,
        state_dir=state,
        unit_dir=units,
        pgbouncer_bin="/usr/sbin/pgbouncer",
        nginx_bin="/usr/sbin/nginx",
        direct_url=(
            f"postgresql+psycopg://met_jira:{password}"
            "@127.0.0.1:5433/met_jira_staging"
        ),
        auth_secret=verifier,
    )

    assert generated
    assert stat.S_IMODE((runtime / "app.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((runtime / "userlist.txt").stat().st_mode) == 0o600
    assert verifier in (runtime / "userlist.txt").read_text()
    pool = (runtime / "pgbouncer.ini").read_text()
    assert password not in pool
    for setting in (
        "pool_mode = transaction",
        "default_pool_size = 8",
        "reserve_pool_size = 2",
        "max_client_conn = 100",
        "query_wait_timeout = 5",
        "max_prepared_statements = 0",
    ):
        assert setting in pool

    app_env = (runtime / "app.env").read_text()
    assert "@127.0.0.1:6433/met_jira_staging" in app_env
    assert "METJIRA_DB_PREPARE_THRESHOLD=-1" in app_env
    nginx = (runtime / "nginx.conf").read_text()
    assert "location ^~ /api/agent/" in nginx
    assert "proxy_buffering off;" in nginx
    assert nginx.count("proxy_read_timeout 180s;") == 1
    assert "location = /metrics" in nginx and "return 404;" in nginx
    assert "X-Forwarded-For $remote_addr" in nginx
    web_unit = (units / "met-jira-staging.service").read_text()
    assert "METJIRA_WEB_PORT=18801" in web_unit
    assert "METJIRA_WEB_WORKERS=2" in web_unit
    assert "--reload" not in web_unit


def test_agent_sse_response_disables_acceleration_buffering():
    from backend.routers.agent import _sse

    response = _sse(iter(({"type": "done"},)))
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
