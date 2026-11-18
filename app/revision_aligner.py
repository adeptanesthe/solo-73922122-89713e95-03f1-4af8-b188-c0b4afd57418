# -*- coding: utf-8 -*-
"""版本对读：原稿与新稿的句子级对齐。

流程
====
1. 切句：两篇文本均按 。！？ 切句，记录每句在原文中的字符范围 [start, end)；
2. 向量化：使用**与推荐模型完全分开**的字符 2~3 元 TF-IDF —— 每次比对新建
   一个 ``TfidfVectorizer``，仅在本次比对的句子集合（旧句、新句、相邻两新句
   的拼接文本）上 fit，不触碰推荐系统的文章拟合单例；
3. 候选边：旧句 × 单个新句、旧句 × 相邻两新句拼接，余弦相似度低于
   ``SIM_THRESHOLD``（0.35）的边不参与配对；
4. 全局最优对齐：在“每个旧句至多配对一次、每个新句至多被占用一次”的约束下，
   用 DFS + 分支定界求**相似度总和最高**的对应方案；匹配不要求保序，
   因此拆句后调序（移位）天然被允许；
5. 分类标注：对应（文本完全一致）、改写（单句对单句但文本不同）、
   拆句（旧一句对相邻新两句）、移位（配对顺序与旧稿不一致，按 LIS 判定）、
   新增（未配对的新句）、删除（未配对的旧句）。
"""
from __future__ import annotations

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 配对相似度阈值：低于该值的候选边一律不配对
SIM_THRESHOLD = 0.35
# 新稿限制：最多 1200 字、切句后最多 10 句
MAX_NEW_CHARS = 1200
MAX_NEW_SENTENCES = 10

_SENT_ENDINGS = "。！？"


def split_sentences(text: str) -> list[dict]:
    """按 。！？ 切句，返回 [{index, text, start, end}]。

    * 句末标点归入本句，end 为开区间；
    * 句间空白（空格、换行）不属于任何句子；
    * 末尾没有标点收尾的残余文本也算一句。
    """
    sentences: list[dict] = []
    n = len(text)
    i = 0
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and text[i] not in _SENT_ENDINGS:
            i += 1
        if i < n:  # 命中句末标点，包含进本句
            i += 1
        end = i
        # 无标点收尾时去掉句尾空白（句中空白保留）
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            sentences.append(
                {
                    "index": len(sentences),
                    "text": text[start:end],
                    "start": start,
                    "end": end,
                }
            )
    return sentences


def validate_new_text(new_text: str) -> str | None:
    """校验新稿，返回错误信息；合法则返回 None。"""
    if not new_text or not new_text.strip():
        return "新稿不能为空"
    if len(new_text) > MAX_NEW_CHARS:
        return f"新稿超长：{len(new_text)} 字（上限 {MAX_NEW_CHARS} 字）"
    count = len(split_sentences(new_text))
    if count > MAX_NEW_SENTENCES:
        return f"新稿句子过多：{count} 句（上限 {MAX_NEW_SENTENCES} 句）"
    return None


class SentenceTfidf:
    """每次比对独立构建的字符 2~3 元 TF-IDF（与推荐模型分开的实例与语料）。

    仅在本次比对的旧句、新句及相邻两新句拼接文本上 fit，
    与推荐系统“仅文章拟合”的全局单例互不影响。
    """

    def __init__(self, old_texts: list[str], new_texts: list[str],
                 pair_texts: list[str]) -> None:
        self.vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 3),
            min_df=1,
            sublinear_tf=True,
            norm="l2",
        )
        corpus = old_texts + new_texts + pair_texts
        self.vectorizer.fit(corpus)
        self.old_matrix = self.vectorizer.transform(old_texts)
        self.new_matrix = self.vectorizer.transform(new_texts)
        self.pair_matrix = (
            self.vectorizer.transform(pair_texts) if pair_texts else None
        )

    def single_similarity(self) -> list[list[float]]:
        """sim[i][j]：旧句 i 与单个新句 j 的余弦相似度。"""
        if not self.old_matrix.shape[0] or not self.new_matrix.shape[0]:
            return []
        return cosine_similarity(self.old_matrix, self.new_matrix).tolist()

    def pair_similarity(self) -> list[list[float]]:
        """sim[i][j]：旧句 i 与相邻新句 j、j+1 拼接文本的余弦相似度。"""
        if self.pair_matrix is None or not self.old_matrix.shape[0]:
            return []
        return cosine_similarity(self.old_matrix, self.pair_matrix).tolist()


def _best_alignment(
    single_sim: list[list[float]],
    pair_sim: list[list[float]],
    n_old: int,
    n_new: int,
    threshold: float,
) -> list[tuple]:
    """求相似度总和最高的对齐（句子不重复占用，允许调序）。

    返回边列表，边为 ("s", i, j)（旧 i ↔ 新 j）或 ("p", i, j)
    （旧 i ↔ 相邻新句 j、j+1）。DFS + 分支定界；平局按边的规范元组
    字典序取小，保证结果确定。
    """
    # 每个旧句的候选边（仅保留 >= threshold 的），按相似度降序
    edges_by_old: list[list[tuple]] = []
    for i in range(n_old):
        edges: list[tuple] = []
        for j in range(n_new):
            s = single_sim[i][j]
            if s >= threshold:
                edges.append(("s", i, j, s))
        for j in range(n_new - 1):
            s = pair_sim[i][j]
            if s >= threshold:
                edges.append(("p", i, j, s))
        edges.sort(key=lambda e: (-e[3], e[0], e[1], e[2]))
        edges_by_old.append(edges)

    # 松弛上界：剩余旧句各自最大候选相似度之和（忽略占用，只会高估）
    max_possible = [max((e[3] for e in edges), default=0.0) for edges in edges_by_old]
    suffix_bound = [0.0] * (n_old + 1)
    for i in range(n_old - 1, -1, -1):
        suffix_bound[i] = suffix_bound[i + 1] + max_possible[i]

    best_score = 0.0
    best_key: tuple | None = None
    best_edges: list[tuple] = []

    def norm_key(edges: list[tuple]) -> tuple:
        return tuple(sorted((e[0], e[1], e[2]) for e in edges))

    def dfs(i: int, used_mask: int, score: float, chosen: list[tuple]) -> None:
        nonlocal best_score, best_key, best_edges
        if best_key is not None and score + suffix_bound[i] < best_score - 1e-12:
            return
        if i == n_old:
            key = norm_key(chosen)
            if (
                best_key is None
                or score > best_score + 1e-12
                or (abs(score - best_score) <= 1e-12 and key < best_key)
            ):
                best_score = score
                best_key = key
                best_edges = list(chosen)
            return
        # 选择一：旧句 i 不配对
        dfs(i + 1, used_mask, score, chosen)
        # 选择二：旧句 i 配对某条候选边
        for kind, _i, j, s in edges_by_old[i]:
            if kind == "s":
                bit = 1 << j
                if used_mask & bit:
                    continue
                chosen.append((kind, i, j, s))
                dfs(i + 1, used_mask | bit, score + s, chosen)
                chosen.pop()
            else:  # "p"：占用相邻两句 j、j+1
                bits = (1 << j) | (1 << (j + 1))
                if used_mask & bits:
                    continue
                chosen.append((kind, i, j, s))
                dfs(i + 1, used_mask | bits, score + s, chosen)
                chosen.pop()

    dfs(0, 0, 0.0, [])
    return best_edges


def _lis_membership(keys: list[int]) -> list[bool]:
    """返回每个元素是否在某个确定的 LIS（最长严格递增子序列）中。"""
    n = len(keys)
    if n == 0:
        return []
    dp = [1] * n
    for i in range(n):
        for k in range(i):
            if keys[k] < keys[i] and dp[k] + 1 > dp[i]:
                dp[i] = dp[k] + 1
    best = max(dp)
    # 从后往前确定性地回溯出一条 LIS
    members = [False] * n
    need = best
    prev_key = None
    for i in range(n - 1, -1, -1):
        if dp[i] == need and (prev_key is None or keys[i] < prev_key):
            members[i] = True
            prev_key = keys[i]
            need -= 1
            if need == 0:
                break
    return members


def align_revisions(
    old_text: str,
    new_text: str,
    threshold: float = SIM_THRESHOLD,
) -> dict:
    """对原稿与新稿做句子级对齐，返回可 JSON 序列化的完整结果。"""
    old_sents = split_sentences(old_text)
    new_sents = split_sentences(new_text)
    n_old, n_new = len(old_sents), len(new_sents)

    old_texts = [s["text"] for s in old_sents]
    new_texts = [s["text"] for s in new_sents]
    pair_texts = [new_texts[j] + new_texts[j + 1] for j in range(n_new - 1)]

    if old_texts and new_texts:
        model = SentenceTfidf(old_texts, new_texts, pair_texts)
        single_sim = model.single_similarity()
        pair_sim = model.pair_similarity()
        edges = _best_alignment(single_sim, pair_sim, n_old, n_new, threshold)
    else:
        # 任一侧无句子：无可配对边，全部落入新增/删除
        edges = []

    # ---- 组装配对 ----
    pairs = []
    used_new: set[int] = set()
    used_old: set[int] = set()
    for kind, i, j, sim in sorted(edges, key=lambda e: e[1]):
        used_old.add(i)
        if kind == "s":
            new_indices = [j]
            exact = old_sents[i]["text"] == new_sents[j]["text"]
            pair_kind = "exact" if exact else "rewritten"
        else:
            new_indices = [j, j + 1]
            pair_kind = "split"
        used_new.update(new_indices)
        pairs.append(
            {
                "old_index": i,
                "new_indices": new_indices,
                "similarity": round(float(sim), 4),
                "kind": pair_kind,
                "moved": False,
                "old_range": [old_sents[i]["start"], old_sents[i]["end"]],
                "new_ranges": [
                    [new_sents[t]["start"], new_sents[t]["end"]] for t in new_indices
                ],
            }
        )

    # ---- 移位判定：按旧句顺序看新句下标，不在 LIS 中的配对视为移位 ----
    membership = _lis_membership([p["new_indices"][0] for p in pairs])
    for p, in_lis in zip(pairs, membership):
        p["moved"] = not in_lis

    # ---- 句子级状态 ----
    pair_by_old = {p["old_index"]: p for p in pairs}
    pair_by_new: dict[int, dict] = {}
    for p in pairs:
        for t in p["new_indices"]:
            pair_by_new[t] = p

    for s in old_sents:
        p = pair_by_old.get(s["index"])
        if p is None:
            s["status"] = "deleted"
            s["moved"] = False
        else:
            s["status"] = p["kind"]
            s["moved"] = p["moved"]
    for s in new_sents:
        p = pair_by_new.get(s["index"])
        if p is None:
            s["status"] = "added"
            s["moved"] = False
        else:
            s["status"] = p["kind"]
            s["moved"] = p["moved"]

    summary = {
        "exact": sum(1 for p in pairs if p["kind"] == "exact"),
        "rewritten": sum(1 for p in pairs if p["kind"] == "rewritten"),
        "split": sum(1 for p in pairs if p["kind"] == "split"),
        "moved": sum(1 for p in pairs if p["moved"]),
        "added": sum(1 for s in new_sents if s["status"] == "added"),
        "deleted": sum(1 for s in old_sents if s["status"] == "deleted"),
        "pairs": len(pairs),
        "total_similarity": round(sum(p["similarity"] for p in pairs), 4),
        "threshold": threshold,
    }

    return {
        "threshold": threshold,
        "old_sentences": old_sents,
        "new_sentences": new_sents,
        "pairs": pairs,
        "summary": summary,
    }
