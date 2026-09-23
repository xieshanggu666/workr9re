"""修复：战斗未结束也能推进路线 / 首领战中可返回旧节点。

两个同源缺陷都在 _choose_node 的入口校验：
1. 进战斗后仍可提交 choose_node：当前战斗（含奇遇伏击）被整体遗弃，
   battle_index 照常累加，等于跳过整场战斗；
2. 位置在首领节点时豁免了可达性校验（`position != BOSS` 才校验），首领战中
   可以选任意旧节点重进——重置商店库存/锻造次数/待抉择奇遇，甚至重打旧敌人。

修复后：战斗进行中一律禁止移动；任何位置都只能沿地图出边前进。
"""
import pytest

from app import mapgen, service


# ---------- 纯规则层（_choose_node） ----------

def test_cannot_advance_while_battle_ongoing():
    rid = service.create_run(seed=1)["run_id"]
    battle_node = next(
        n for n, nd in mapgen.generate_map(1)["nodes"].items()
        if nd["type"] in (mapgen.ENCOUNTER, mapgen.ELITE))
    service.act(rid, {"action": "choose_node", "node": battle_node})
    rec = service.load_run(rid)
    run, m = rec["state"], rec["map"]
    assert run["in_battle"] and run["battle"]
    forward = m["routes"][battle_node][0]
    battle_index = run["battle_index"]

    with pytest.raises(service.InvalidAction, match="cannot move while battle"):
        service._choose_node(run, m, forward)

    # 被拒绝后状态原地不动：仍在同一场战斗
    assert run["position"] == battle_node
    assert run["in_battle"] is True
    assert run["battle_index"] == battle_index


def test_cannot_return_to_old_node_during_boss_fight():
    seed = 1
    m = mapgen.generate_map(seed)
    run = service._new_run_state(seed)
    # 直接落在末行节点（合法前序），进入首领战
    row_last = next(n for n, nd in m["nodes"].items() if nd.get("row") == mapgen.ROWS - 1)
    run["position"] = row_last
    service._choose_node(run, m, "boss")
    assert run["position"] == "boss"
    assert run["in_battle"] and run["battle"]

    # 首领战中：先被「战斗中不可移动」拦截
    for old in ("start", "0-0", row_last):
        with pytest.raises(service.InvalidAction, match="cannot move while battle"):
            service._choose_node(run, m, old)

    assert run["position"] == "boss"
    assert run["in_battle"] is True

    # 战斗已清空但仍停在首领节点（旧档/异常续局形态）：首领节点不再豁免
    # 可达性校验——boss 没有任何出边，任何移动都必须被拒（旧 bug 放行）
    run["in_battle"] = False
    run["battle"] = None
    for old in ("start", "0-0", row_last):
        with pytest.raises(service.InvalidAction, match="unreachable"):
            service._choose_node(run, m, old)
    assert run["position"] == "boss"


def test_cannot_flee_ambush_battle():
    """奇遇伏击也是战斗：战斗中移动同样被拦截（旧逻辑会重进奇遇节点重置链）。"""
    rid = service.create_run(seed=1)["run_id"]
    # 找一个奇遇节点进入并触发伏击链需要构造，直接用纯状态验证 in_battle 拦截即可
    rec = service.load_run(rid)
    run, m = rec["state"], rec["map"]
    battle_node = next(
        n for n, nd in m["nodes"].items()
        if nd["type"] in (mapgen.ENCOUNTER, mapgen.ELITE))
    service._choose_node(run, m, battle_node)
    assert run["in_battle"]
    # 伏击形态：位置停在奇遇节点，出边指向下一行——旧逻辑允许借移动逃战
    forward = m["routes"][battle_node][0]
    with pytest.raises(service.InvalidAction, match="cannot move while battle"):
        service._choose_node(run, m, forward)


# ---------- API 层：非法移动返回 400，不推进存档 ----------

def test_api_blocks_move_during_battle(client):
    rid = client.post("/api/runs", json={"seed": 1}).json()["run_id"]
    battle_node = next(
        n for n, nd in mapgen.generate_map(1)["nodes"].items()
        if nd["type"] in (mapgen.ENCOUNTER, mapgen.ELITE))
    r = client.post(f"/api/runs/{rid}/act",
                    json={"action": "choose_node", "node": battle_node})
    assert r.status_code == 200
    forward = mapgen.generate_map(1)["routes"][battle_node][0]

    r = client.post(f"/api/runs/{rid}/act",
                    json={"action": "choose_node", "node": forward})
    assert r.status_code == 400
    view = client.get(f"/api/runs/{rid}/resume").json()
    assert view["position"] == battle_node
    assert view["in_battle"] is True


def test_api_blocks_return_to_old_node_during_boss_fight(client):
    rid = client.post("/api/runs", json={"seed": 1}).json()["run_id"]
    m = mapgen.generate_map(1)
    row_last = next(n for n, nd in m["nodes"].items() if nd.get("row") == mapgen.ROWS - 1)
    # 走到末行：纯服务端推演落库（避开战斗支线，末行任选可达）
    rec = service.load_run(rid)
    rec["state"]["position"] = row_last
    from app import db
    db.save_run(rid, rec["state"]["status"], rec["state"]["position"], rec["state"])

    r = client.post(f"/api/runs/{rid}/act",
                    json={"action": "choose_node", "node": "boss"})
    assert r.status_code == 200
    assert r.json()["run"]["in_battle"] is True

    r = client.post(f"/api/runs/{rid}/act",
                    json={"action": "choose_node", "node": "start"})
    assert r.status_code == 400
    view = client.get(f"/api/runs/{rid}/resume").json()
    assert view["position"] == "boss"
    assert view["in_battle"] is True


# ---------- 视口：战斗中不暴露任何可选路线 ----------

def test_reachable_empty_during_battle():
    rid = service.create_run(seed=1)["run_id"]
    battle_node = next(
        n for n, nd in mapgen.generate_map(1)["nodes"].items()
        if nd["type"] in (mapgen.ENCOUNTER, mapgen.ELITE))
    r = service.act(rid, {"action": "choose_node", "node": battle_node})
    assert r["run"]["reachable"] == []
