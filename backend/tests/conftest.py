import os
import tempfile

_tmp = os.path.join(tempfile.gettempdir(), "game_test.db")
if os.path.exists(_tmp):
    os.remove(_tmp)
os.environ["GAME_DB_PATH"] = _tmp

import pytest
from fastapi.testclient import TestClient

from app import db, service
from app.main import app


@pytest.fixture(autouse=True)
def reset_db():
    db.init_db()
    # 清空 profile/表以便隔离
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM battle_events")
        conn.execute("DELETE FROM profile")
        conn.execute("DELETE FROM expeditions")
        conn.execute("DELETE FROM expedition_events")
        conn.execute("DELETE FROM act_requests")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM battle_events")
        conn.execute("DELETE FROM profile")
        conn.execute("DELETE FROM expeditions")
        conn.execute("DELETE FROM expedition_events")
        conn.execute("DELETE FROM act_requests")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def win_current_battle(client, run_id):
    """测试辅助：合法打赢当前战斗（敌人压到 1 血后出一张打击）。

    不绕过行动日志：战斗与战后状态全部走正式 /act 接口。修复「战斗中也能
    选节点」后，跨节点的测试路径不能再借旧 bug 跳过中途战斗，统一用本辅助。
    """
    from app import db, service
    rec = service.load_run(run_id)
    rec["state"]["battle"]["entities"]["enemy"]["hp"] = 1
    db.save_run(run_id, rec["state"]["status"], rec["state"]["position"], rec["state"])
    view = client.get(f"/api/runs/{run_id}/resume").json()
    strike = next(h for h in view["battle"]["hand"]
                  if (h["id"] if isinstance(h, dict) else h) == "strike")
    uid = strike["uid"] if isinstance(strike, dict) else strike
    r = client.post(f"/api/runs/{run_id}/act", json={"action": "play", "card": uid})
    assert r.status_code == 200, r.text
    return r.json()


def walk_nodes(client, run_id, nodes):
    """按路径依次进入节点；中途触发战斗则合法打完（战后奖励允许不领）。"""
    view = None
    for node in nodes:
        r = client.post(f"/api/runs/{run_id}/act",
                        json={"action": "choose_node", "node": node})
        assert r.status_code == 200, r.text
        view = r.json()["run"]
        if view["in_battle"]:
            view = win_current_battle(client, run_id)["run"]
    return view


def win_current_battle_direct(run_id):
    """win_current_battle 的直连 service 版（不经 HTTP，给纯 service 测试用）。"""
    rec = service.load_run(run_id)
    state = rec["state"]
    state["battle"]["entities"]["enemy"]["hp"] = 1
    db.save_run(run_id, state["status"], state["position"], state)
    strike_uid = next(u for u in state["battle"]["hand"]
                      if state["card_instances"][u]["id"] == "strike")
    return service.act(run_id, {"action": "play", "card": strike_uid})


def walk_nodes_direct(run_id, nodes):
    """walk_nodes 的直连 service 版。"""
    for node in nodes:
        res = service.act(run_id, {"action": "choose_node", "node": node})
        if res["run"]["in_battle"]:
            win_current_battle_direct(run_id)