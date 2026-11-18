# -*- coding: utf-8 -*-
"""版本对读的后台比对 worker。

* 与推荐重算 worker 完全独立的守护线程，互不影响；
* 支持 compute_delay_ms 模拟慢比对，演示“改稿后迟到的旧比对不得覆盖新稿结果”；
* 写回前由 database.complete_diff 核对版本号，过期结果一律拒绝落库。
"""
from __future__ import annotations

import logging
import threading
import time

from . import database
from .articles_data import ARTICLES
from .revision_aligner import align_revisions

log = logging.getLogger("recommender.diff_worker")


class DiffWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="diff-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def wait_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._busy.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self._busy.is_set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = database.claim_pending_diff()
            except Exception:  # pragma: no cover - 数据库临时故障重试
                log.exception("领取比对任务失败，稍后重试")
                time.sleep(0.5)
                continue
            if job is None:
                self._stop.wait(0.25)
                continue
            self._busy.set()
            try:
                self._process(job)
            finally:
                self._busy.clear()

    def _process(self, job: dict) -> None:
        revision = job["revision"]
        delay = max(0, int(job.get("compute_delay_ms", 0))) / 1000.0
        try:
            # 慢比对窗口：此期间用户可能改稿并提交新版本
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                if self._stop.is_set():
                    return
                time.sleep(min(0.1, deadline - time.monotonic()))

            article = next(
                (a for a in ARTICLES if a["id"] == job["article_id"]), None
            )
            if article is None:
                database.fail_diff(revision, f"文章 #{job['article_id']} 不存在")
                return

            result = align_revisions(article["body"], job["new_text"])
            result["article_id"] = article["id"]
            result["article_title"] = article["title"]
            result["old_char_count"] = len(article["body"])
            result["new_char_count"] = len(job["new_text"])

            accepted = database.complete_diff(revision, result)
            if not accepted:
                log.info("版本 %s 的迟到比对结果已被丢弃", revision)
        except Exception as exc:  # 失败只记录错误，不影响推荐链路
            log.exception("版本 %s 比对失败", revision)
            try:
                database.fail_diff(revision, f"{type(exc).__name__}: {exc}")
            except Exception:  # pragma: no cover
                log.exception("写入比对失败状态时出错")


_diff_worker = DiffWorker()


def get_diff_worker() -> DiffWorker:
    return _diff_worker
