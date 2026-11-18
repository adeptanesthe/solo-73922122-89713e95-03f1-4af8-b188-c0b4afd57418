# -*- coding: utf-8 -*-
"""版本对读测试：切句、校验、全局最优对齐分类、API 流程与迟到保护。"""
from __future__ import annotations

import time

import pytest

from app.revision_aligner import (
    MAX_NEW_CHARS,
    MAX_NEW_SENTENCES,
    SIM_THRESHOLD,
    align_revisions,
    split_sentences,
    validate_new_text,
)

OLD = (
    "大语言模型的微调并不等于重新训练。"
    "工程师通常先冻结大部分网络参数，只在顶层注入任务相关的适配层。"
    "学习率过大会破坏预训练阶段学到的语言知识，过小则收敛缓慢。"
    "微调后的模型需要在独立测试集上评估推理质量。"
    "数据清洗、指令模板设计和负样本构造，往往比单纯堆叠算力更能决定最终效果。"
)


# ---------------- 切句 ----------------


def test_split_sentences_basic_and_ranges():
    sents = split_sentences(OLD)
    assert len(sents) == 5
    # 字符范围与原文严格对应，句末标点归入本句
    for s in sents:
        assert OLD[s["start"]:s["end"]] == s["text"]
        assert s["text"].endswith("。")
    assert sents[0]["start"] == 0
    assert sents[-1]["end"] == len(OLD)
    # 范围首尾相接、无重叠
    for a, b in zip(sents, sents[1:]):
        assert a["end"] == b["start"]


def test_split_sentences_all_endings_and_trailing_residual():
    text = "问句吗？感叹句！陈述句。没有标点的残余"
    sents = split_sentences(text)
    assert [s["text"] for s in sents] == ["问句吗？", "感叹句！", "陈述句。", "没有标点的残余"]
    assert [s["index"] for s in sents] == [0, 1, 2, 3]


def test_split_sentences_skips_blank_between():
    text = "第一句。\n\n  第二句！\t第三句。"
    sents = split_sentences(text)
    assert [s["text"] for s in sents] == ["第一句。", "第二句！", "第三句。"]
    for s in sents:
        assert text[s["start"]:s["end"]] == s["text"]


# ---------------- 新稿校验 ----------------


def test_validate_new_text_limits():
    assert validate_new_text("") is not None
    assert validate_new_text("   \n ") is not None
    assert validate_new_text("正常的一句话。") is None
    # 超长：1201 字
    assert "上限" in validate_new_text("字" * (MAX_NEW_CHARS + 1))
    # 超句：11 句
    too_many = "。".join(["短句"] * (MAX_NEW_SENTENCES + 1)) + "。"
    assert "句" in validate_new_text(too_many)
    # 边界：恰好 10 句合法
    exactly = "。".join(["短句"] * MAX_NEW_SENTENCES) + "。"
    assert validate_new_text(exactly) is None


# ---------------- 对齐与分类 ----------------


def test_align_identical_text_all_exact():
    r = align_revisions(OLD, OLD)
    assert r["summary"]["exact"] == 5
    assert r["summary"]["pairs"] == 5
    assert r["summary"]["moved"] == 0
    assert all(p["kind"] == "exact" for p in r["pairs"])
    assert abs(r["summary"]["total_similarity"] - 5.0) < 1e-6


def test_align_rewrite_detected():
    new = OLD.replace("工程师通常先冻结", "工程师一般先冻结")
    r = align_revisions(OLD, new)
    assert r["summary"]["rewritten"] == 1
    pair = next(p for p in r["pairs"] if p["kind"] == "rewritten")
    assert pair["old_index"] == 1
    assert pair["new_indices"] == [1]
    assert SIM_THRESHOLD <= pair["similarity"] < 1.0


def test_align_split_detected():
    # 旧第 3 句（含逗号的长句）在新稿中拆成两句
    new = (
        "大语言模型的微调并不等于重新训练。"
        "工程师通常先冻结大部分网络参数，只在顶层注入任务相关的适配层。"
        "学习率过大会破坏预训练阶段学到的语言知识。过小则收敛缓慢。"
        "微调后的模型需要在独立测试集上评估推理质量。"
        "数据清洗、指令模板设计和负样本构造，往往比单纯堆叠算力更能决定最终效果。"
    )
    r = align_revisions(OLD, new)
    split = [p for p in r["pairs"] if p["kind"] == "split"]
    assert len(split) == 1
    assert split[0]["old_index"] == 2
    assert split[0]["new_indices"] == [2, 3]
    # 拆句占用了两个新句，二者状态都是 split
    assert r["new_sentences"][2]["status"] == "split"
    assert r["new_sentences"][3]["status"] == "split"


def test_align_move_detected():
    sents = split_sentences(OLD)
    # 新稿把第 1、2 句互换顺序
    new = sents[1]["text"] + sents[0]["text"] + "".join(s["text"] for s in sents[2:])
    r = align_revisions(OLD, new)
    assert r["summary"]["moved"] >= 1
    moved_pairs = [p for p in r["pairs"] if p["moved"]]
    assert moved_pairs, "调序后的对应应被标记为移位"
    # 保序的三句不应被标记
    assert r["pairs"] and all(
        not p["moved"] for p in r["pairs"] if p["old_index"] >= 2
    )


def test_align_added_and_deleted():
    new = (
        "大语言模型的微调并不等于重新训练。"
        "工程师通常先冻结大部分网络参数，只在顶层注入任务相关的适配层。"
        "编辑审校时需要特别注意版本之间的对应关系。"  # 新增句
    )
    r = align_revisions(OLD, new)
    assert r["summary"]["added"] == 1
    assert r["new_sentences"][2]["status"] == "added"
    # 旧稿后三句无对应，应标记删除
    assert r["summary"]["deleted"] == 3
    assert [s["index"] for s in r["old_sentences"] if s["status"] == "deleted"] == [2, 3, 4]


def test_align_threshold_excludes_dissimilar():
    new = "量子纠缠与弦理论是前沿物理学的热门方向。黑洞视界附近的时空曲率极大。"
    r = align_revisions(OLD, new)
    assert r["pairs"] == []
    assert r["summary"]["added"] == 2
    assert r["summary"]["deleted"] == 5


def test_align_no_double_occupancy_and_global_optimum():
    # 两个旧句都与同一个新句相似：全局最优应把新句配给完全相同的旧句，
    # 且新句只被占用一次
    old = (
        "深度学习模型需要大量训练数据与算力支持。"
        "深度学习模型需要大量训练数据与算力支撑。"
    )
    new = "深度学习模型需要大量训练数据与算力支持。"
    r = align_revisions(old, new)
    assert len(r["pairs"]) == 1
    assert r["pairs"][0]["old_index"] == 0  # 完全相同者胜出
    assert r["pairs"][0]["similarity"] == pytest.approx(1.0)
    assert r["old_sentences"][1]["status"] == "deleted"


def test_align_split_preferred_when_concat_matches_better():
    # 拼接相似度高于任何单句匹配时必须选择拆句，且两句都被占用
    old = "学习率过大会破坏预训练阶段学到的语言知识，过小则收敛缓慢。"
    new = "学习率过大会破坏预训练阶段学到的语言知识。过小则收敛缓慢。"
    r = align_revisions(old, new)
    assert len(r["pairs"]) == 1
    assert r["pairs"][0]["kind"] == "split"
    assert r["pairs"][0]["new_indices"] == [0, 1]
    # 拼接文本与旧句仅句中标点不同，相似度应远高于阈值
    assert r["pairs"][0]["similarity"] >= 0.7


def test_align_pair_ranges_match_source_text():
    new = OLD.replace("工程师通常先冻结", "工程师一般先冻结")
    r = align_revisions(OLD, new)
    for p in r["pairs"]:
        s, e = p["old_range"]
        assert OLD[s:e] == r["old_sentences"][p["old_index"]]["text"]
        for (ns, ne), t in zip(p["new_ranges"], p["new_indices"]):
            assert new[ns:ne] == r["new_sentences"][t]["text"]


# ---------------- API 流程与迟到保护 ----------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import database, main

    main.get_worker().stop()
    main.get_diff_worker().stop()
    db_path = tmp_path / "diff.db"
    monkeypatch.setenv("RECOMMENDER_DB", str(db_path))
    database._DB_PATH = str(db_path)
    database._reset_reveal_batch()
    database.init_db()
    main.get_worker().start()
    main.get_diff_worker().start()
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c, database
    main.get_worker().stop()
    main.get_diff_worker().stop()


def _wait_diff_done(c, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = c.get("/api/diff").json()
        job = data["diff"]
        if job and job["status"] in ("done", "failed", "stale"):
            return job
        time.sleep(0.05)
    raise AssertionError("比对任务未在预期时间内结束")


def test_diff_api_flow(client):
    c, _ = client
    old_body = c.get("/api/articles/1").json()["body"]
    new = old_body.replace("工程师通常先冻结", "工程师一般先冻结")
    r = c.post("/api/diff", json={"article_id": 1, "new_text": new})
    assert r.status_code == 200
    revision = r.json()["revision"]
    assert revision == 1

    job = _wait_diff_done(c)
    assert job["status"] == "done"
    assert job["revision"] == revision
    result = job["result"]
    assert result["article_id"] == 1
    assert result["summary"]["rewritten"] == 1
    assert result["summary"]["exact"] == 4
    # 字符范围与双方原文对应
    pair = next(p for p in result["pairs"] if p["kind"] == "rewritten")
    s, e = pair["old_range"]
    assert old_body[s:e] == result["old_sentences"][pair["old_index"]]["text"]
    ns, ne = pair["new_ranges"][0]
    assert new[ns:ne] == result["new_sentences"][pair["new_indices"][0]]["text"]


def test_diff_api_validation(client):
    c, _ = client
    assert c.post("/api/diff", json={"article_id": 999, "new_text": "一句。"}).status_code == 404
    assert c.post("/api/diff", json={"article_id": 1, "new_text": ""}).status_code == 422
    assert c.post(
        "/api/diff", json={"article_id": 1, "new_text": "字" * 1201}
    ).status_code == 422
    too_many = "。".join(["短句"] * 11) + "。"
    assert c.post(
        "/api/diff", json={"article_id": 1, "new_text": too_many}
    ).status_code == 422
    # 非法提交不产生任务
    assert c.get("/api/diff").json()["diff"] is None


def test_stale_diff_never_overwrites_newer(client):
    c, database = client
    old_body = c.get("/api/articles/1").json()["body"]
    # 慢比对 A（1.5 秒延迟）
    ra = c.post("/api/diff", json={
        "article_id": 1, "new_text": old_body, "compute_delay_ms": 1500,
    })
    assert ra.status_code == 200
    rev_a = ra.json()["revision"]

    # 立即改稿提交新比对 B（无延迟）
    new_b = old_body.replace("工程师通常先冻结", "工程师一般先冻结")
    rb = c.post("/api/diff", json={
        "article_id": 1, "new_text": new_b, "compute_delay_ms": 0,
    })
    rev_b = rb.json()["revision"]
    assert rev_b == rev_a + 1

    # 等两个版本都结算完毕
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ja = database.get_diff(rev_a)
        jb = database.get_diff(rev_b)
        if ja["status"] in ("stale", "done", "failed") and jb["status"] == "done":
            break
        time.sleep(0.05)

    ja = database.get_diff(rev_a)
    jb = database.get_diff(rev_b)
    # 迟到的旧比对必须 stale 且无结果；新稿结果不被覆盖
    assert ja["status"] == "stale"
    assert ja["result"] is None
    assert jb["status"] == "done"
    assert jb["result"]["summary"]["rewritten"] == 1

    latest = c.get("/api/diff").json()["diff"]
    assert latest["revision"] == rev_b
    assert latest["status"] == "done"
    assert latest["result"]["summary"]["rewritten"] == 1


def test_recommendation_unaffected_by_diff(client):
    c, _ = client
    # 提交比对后，推荐链路照常工作
    c.post("/api/diff", json={"article_id": 2, "new_text": OLD})
    data = c.get("/api/status").json()
    assert data["articles"] and len(data["articles"]) == 30
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = c.get("/api/status").json()
        if data["job"] and data["job"]["status"] == "done":
            break
        time.sleep(0.05)
    assert data["plan"] is not None and data["plan"]["feasible"]
    out = c.post("/api/reveal").json()
    assert len(out["state"]["revealed"]) == 2
