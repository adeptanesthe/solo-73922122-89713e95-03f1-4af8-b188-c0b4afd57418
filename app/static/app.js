/* 技术编辑选题推荐系统前端（原生 JS） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { data: null, pollTimer: null };

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(body.detail || `请求失败 (${resp.status})`);
  }
  return body;
}

function articleMap() {
  const m = new Map();
  state.data.articles.forEach((a) => m.set(a.id, a));
  return m;
}

function renderQuotas() {
  const quotas = state.data.state.quotas;
  const grid = $("#quota-grid");
  grid.innerHTML = "";
  state.data.topics.forEach((topic) => {
    const wrap = document.createElement("div");
    wrap.className = "quota-item";
    wrap.innerHTML = `<label>${topic}</label>`;
    const input = document.createElement("input");
    input.type = "number";
    input.min = 0;
    input.max = 6;
    input.value = quotas[topic];
    input.dataset.topic = topic;
    input.addEventListener("input", renderQuotaSum);
    wrap.appendChild(input);
    grid.appendChild(wrap);
  });
  renderQuotaSum();
}

function renderQuotaSum() {
  const sum = [...document.querySelectorAll("#quota-grid input")]
    .reduce((acc, el) => acc + (parseInt(el.value, 10) || 0), 0);
  const el = $("#quota-sum");
  el.innerHTML = `当前合计：<b>${sum}</b> / 6`;
  el.className = `quota-sum ${sum === 6 ? "valid" : "invalid"}`;
}

function renderExclusions() {
  const excluded = new Set(state.data.state.excluded_ids);
  const select = $("#exclude-select");
  const revealedIds = new Set(state.data.state.revealed.map((r) => r.article_id));
  select.innerHTML = "";
  state.data.articles.forEach((a) => {
    if (revealedIds.has(a.id)) return;
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = `#${a.id} ${a.title}（${a.topic}/${a.author}）${excluded.has(a.id) ? " — 已排除" : ""}`;
    if (excluded.has(a.id)) opt.disabled = true;
    select.appendChild(opt);
  });

  const list = $("#exclusion-list");
  list.innerHTML = "";
  if (excluded.size === 0) {
    list.innerHTML = `<span class="msg" style="color:#64748b">暂无排除文章</span>`;
    return;
  }
  const amap = articleMap();
  excluded.forEach((id) => {
    const a = amap.get(id);
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `#${id} ${a ? a.title : "未知"} <button title="取消排除" data-id="${id}">×</button>`;
    chip.querySelector("button").addEventListener("click", () => removeExclusion(id));
    list.appendChild(chip);
  });
}

function renderQueue() {
  const { state: st, plan, job } = state.data;
  const jobEl = $("#job-status");
  const revealedIds = new Set(st.revealed.map((r) => r.article_id));
  const amap = articleMap();

  if (job) {
    const label = {
      pending: "等待计算",
      running: "正在重算（慢重算期间仍可修改条件，迟到结果不会揭示旧推荐）",
      done: "重算完成",
      failed: `重算失败：${job.error || "未知错误"}（进度未推进）`,
      stale: "该结果已过期（条件已变更）",
    }[job.status] || job.status;
    jobEl.className = `job-status ${job.status}`;
    jobEl.textContent = `世代 ${job.generation} · ${label}`;
  }

  const queueEl = $("#queue");
  queueEl.innerHTML = "";
  const activePlan = plan && job.status === "done" && job.generation === st.generation ? plan : null;

  for (let pos = 1; pos <= 6; pos++) {
    const li = document.createElement("li");
    const revealedItem = st.revealed.find((r) => r.position === pos);
    if (revealedItem) {
      const a = amap.get(revealedItem.article_id);
      li.className = "revealed";
      li.innerHTML = `
        <span class="pos">${pos}</span>
        <div class="q-main">
          <div class="q-title">${a ? a.title : "文章 #" + revealedItem.article_id}</div>
          <div class="q-sub">
            <span class="tag">${revealedItem.topic}</span>
            <span class="tag author">${revealedItem.author}</span>
            已揭示 · 固定
          </div>
        </div>
        <div class="q-rel">相关度<br/><b>${revealedItem.relevance.toFixed(4)}</b><br/>权重 ${7 - pos}</div>`;
    } else if (activePlan && activePlan.feasible) {
      const item = activePlan.queue.find((q) => q.position === pos);
      li.className = "hidden-item";
      li.innerHTML = `
        <span class="pos">${pos}</span>
        <div class="q-main">
          <div class="q-title">未揭示位置 ${pos}（候选已锁定，等待揭示）</div>
          <div class="q-sub">主题：${item ? item.topic : "—"}</div>
        </div>
        <div class="q-rel">相关度<br/><b>${item ? item.relevance.toFixed(4) : "—"}</b><br/>权重 ${7 - pos}</div>`;
    } else {
      li.className = "hidden-item";
      const reason = activePlan && !activePlan.feasible ? activePlan.reason : "等待重算结果";
      li.innerHTML = `
        <span class="pos">${pos}</span>
        <div class="q-main"><div class="q-title">未揭示</div><div class="q-sub">${reason}</div></div>
        <div class="q-rel">权重 ${7 - pos}</div>`;
    }
    queueEl.appendChild(li);
  }

  $("#generation-line").textContent =
    `当前世代 ${st.generation} · 已揭示 ${st.revealed.length}/6 篇` +
    (activePlan && activePlan.feasible ? ` · 队列目标分 Σ(7−位置)×相关度 = ${activePlan.score.toFixed(4)}` : "");

  const revealBtn = $("#btn-reveal");
  const blocked = !activePlan || !activePlan.feasible || st.revealed.length >= 6;
  revealBtn.disabled = !!blocked;
  if (st.revealed.length >= 6) revealBtn.textContent = "队列已全部揭示";
  else revealBtn.textContent = "揭示下一组（两篇）";

  renderExclusions();
  renderArticleList();
}

function renderArticleList() {
  const filter = $("#article-filter").value.trim().toLowerCase();
  const excluded = new Set(state.data.state.excluded_ids);
  const revealedIds = new Set(state.data.state.revealed.map((r) => r.article_id));
  const list = $("#article-list");
  list.innerHTML = "";
  state.data.articles
    .filter((a) => {
      if (!filter) return true;
      return `${a.id} ${a.title} ${a.topic} ${a.author}`.toLowerCase().includes(filter);
    })
    .forEach((a) => {
      const card = document.createElement("div");
      card.className = `article-card ${excluded.has(a.id) ? "excluded" : ""}`;
      card.innerHTML = `
        <div class="t">#${a.id} ${a.title}</div>
        <div class="s">
          <span class="tag">${a.topic}</span>
          <span class="tag author">${a.author}</span>
          ${excluded.has(a.id) ? '<span class="tag excluded-tag">已排除</span>' : ""}
          ${revealedIds.has(a.id) ? '<span class="tag" style="background:#dcfce7;color:#166534">已揭示</span>' : ""}
        </div>`;
      card.addEventListener("click", () => openArticle(a.id));
      list.appendChild(card);
    });
}

async function openArticle(id) {
  const a = await api(`/api/articles/${id}`);
  $("#dlg-title").textContent = `#${a.id} ${a.title}`;
  $("#dlg-meta").innerHTML = `<span class="tag">${a.topic}</span><span class="tag author">${a.author}</span>`;
  $("#dlg-body").textContent = a.body;
  $("#article-dialog").showModal();
}

function readQuotas() {
  const quotas = {};
  document.querySelectorAll("#quota-grid input").forEach((el) => {
    quotas[el.dataset.topic] = parseInt(el.value, 10) || 0;
  });
  return quotas;
}

function showMsg(el, text, ok = false) {
  el.textContent = text;
  el.className = `msg ${ok ? "ok" : ""}`;
  if (text) setTimeout(() => { el.textContent = ""; el.className = "msg"; }, 5000);
}

async function refresh() {
  state.data = await api("/api/status");
  $("#interest").value = state.data.state.interest;
  renderQuotas();
  renderQueue();
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const j = state.data.job;
  if (j && (j.status === "pending" || j.status === "running")) {
    state.pollTimer = setTimeout(refresh, 600);
  }
}

async function applyCondition() {
  const msg = $("#condition-msg");
  try {
    const payload = {
      interest: $("#interest").value,
      quotas: readQuotas(),
      compute_delay_ms: parseInt($("#delay").value, 10) || 0,
    };
    $("#btn-apply").disabled = true;
    state.data = await api("/api/condition", { method: "PUT", body: JSON.stringify(payload) });
    renderQuotas();
    renderQueue();
    showMsg(msg, "条件已更新，新世代重算已开始", true);
    schedulePoll();
  } catch (err) {
    showMsg(msg, err.message);
  } finally {
    $("#btn-apply").disabled = false;
  }
}

async function revealNext() {
  const msg = $("#reveal-msg");
  try {
    const data = await api("/api/reveal", { method: "POST" });
    state.data = data;
    renderQueue();
    const outcome = data.reveal_outcome;
    if (outcome.advanced) {
      showMsg(msg, `已揭示位置 ${outcome.new_positions.join("、")}`, true);
    } else {
      showMsg(msg, outcome.reason || "没有推进");
    }
  } catch (err) {
    showMsg(msg, err.message);
  }
}

async function addExclusion() {
  const msg = $("#condition-msg");
  const id = parseInt($("#exclude-select").value, 10);
  if (!id) return;
  try {
    state.data = await api("/api/exclusions", {
      method: "POST",
      body: JSON.stringify({ article_id: id, compute_delay_ms: parseInt($("#delay").value, 10) || 0 }),
    });
    renderQueue();
    showMsg(msg, `已排除文章 #${id}，触发新世代重算`, true);
    schedulePoll();
  } catch (err) {
    showMsg(msg, err.message);
  }
}

async function removeExclusion(id) {
  const msg = $("#condition-msg");
  try {
    const delay = parseInt($("#delay").value, 10) || 0;
    state.data = await api(`/api/exclusions/${id}?compute_delay_ms=${delay}`, {
      method: "DELETE",
    });
    renderQueue();
    showMsg(msg, `已取消排除 #${id}`, true);
    schedulePoll();
  } catch (err) {
    showMsg(msg, err.message);
  }
}

async function resetDemo() {
  if (!confirm("确定重置全部演示数据？已揭示前缀将被清空。")) return;
  state.data = await api("/api/reset", { method: "POST" });
  renderQuotas();
  renderQueue();
  schedulePoll();
}

$("#btn-apply").addEventListener("click", applyCondition);
$("#btn-reveal").addEventListener("click", revealNext);
$("#btn-exclude").addEventListener("click", addExclusion);
$("#btn-reset").addEventListener("click", resetDemo);
$("#article-filter").addEventListener("input", renderArticleList);
$("#dlg-close").addEventListener("click", () => $("#article-dialog").close());

/* ---------------- 版本对读 ---------------- */

const diffState = { revision: 0, pollTimer: null, articleBody: "" };

const KIND_LABEL = { exact: "对应", rewritten: "改写", split: "拆句" };

function countSentences(text) {
  // 与后端一致的口径：按 。！？ 切句，非空白残余算一句
  let count = 0, inSent = false;
  for (const ch of text) {
    if (/\s/.test(ch) && !inSent) continue;
    inSent = true;
    if (ch === "。" || ch === "！" || ch === "？") { count++; inSent = false; }
  }
  if (inSent) count++;
  return count;
}

function updateDiffCounters() {
  const text = $("#diff-new").value;
  const chars = text.length;
  const sents = countSentences(text);
  const el = $("#diff-count");
  el.textContent = `${chars} / 1200 字 · ${sents} / 10 句`;
  el.parentElement.classList.toggle("invalid", chars > 1200 || sents > 10);
}

function renderDiffArticleSelect() {
  const select = $("#diff-article");
  const current = select.value;
  select.innerHTML = "";
  state.data.articles.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = `#${a.id} ${a.title}（${a.topic}/${a.author}）`;
    select.appendChild(opt);
  });
  if (current) select.value = current;
}

async function loadDiffOriginal() {
  const id = parseInt($("#diff-article").value, 10);
  if (!id) return;
  const a = await api(`/api/articles/${id}`);
  diffState.articleBody = a.body;
  $("#diff-original").textContent = a.body;
}

function diffStatusLabel(job) {
  if (!job) return "尚未提交比对";
  const label = {
    pending: "等待比对",
    running: "正在比对（慢比对期间可改稿重新提交，迟到旧结果不会覆盖新稿）",
    done: "比对完成",
    failed: `比对失败：${job.error || "未知错误"}`,
    stale: "该结果已过期（已有更新版本的新稿）",
  }[job.status] || job.status;
  return `版本 ${job.revision} · ${label}`;
}

function renderDiffStatus(job) {
  const el = $("#diff-status");
  el.textContent = diffStatusLabel(job);
  el.className = `job-status ${job ? job.status : ""}`;
}

function sentenceCell(sent, sideLabel) {
  if (!sent) return `<div class="diff-cell empty">（无对应）</div>`;
  return `<div class="diff-cell"><span class="range">${sideLabel}句 ${sent.index + 1} · 字符 [${sent.start}, ${sent.end})</span>${escapeHtml(sent.text)}</div>`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderDiffResult(job) {
  const resultEl = $("#diff-result");
  const summaryEl = $("#diff-summary");
  resultEl.innerHTML = "";
  summaryEl.textContent = "";
  if (!job || job.status !== "done" || !job.result) return;
  const r = job.result;
  const sm = r.summary;
  summaryEl.textContent =
    `《${r.article_title}》 原稿 ${r.old_char_count} 字 / 新稿 ${r.new_char_count} 字 · ` +
    `对应 ${sm.exact} · 改写 ${sm.rewritten} · 拆句 ${sm.split} · 移位 ${sm.moved} · ` +
    `新增 ${sm.added} · 删除 ${sm.deleted} · 配对相似度总和 ${sm.total_similarity}`;

  const pairByOld = new Map();
  r.pairs.forEach((p) => pairByOld.set(p.old_index, p));
  const addedNew = r.new_sentences.filter((s) => s.status === "added");
  const emittedNew = new Set();

  const rows = [];
  // 以旧句顺序为主轴；新增句按新稿顺序插入到正确位置
  for (const oldSent of r.old_sentences) {
    const p = pairByOld.get(oldSent.index);
    if (p) {
      for (const a of addedNew) {
        if (!emittedNew.has(a.index) && a.index < p.new_indices[0]) {
          rows.push({ type: "added", newSent: a });
          emittedNew.add(a.index);
        }
      }
      rows.push({ type: "pair", oldSent, pair: p });
      p.new_indices.forEach((t) => emittedNew.add(t));
    } else {
      rows.push({ type: "deleted", oldSent });
    }
  }
  for (const a of addedNew) {
    if (!emittedNew.has(a.index)) rows.push({ type: "added", newSent: a });
  }

  for (const row of rows) {
    const div = document.createElement("div");
    if (row.type === "pair") {
      const p = row.pair;
      const badges = [`<span class="badge ${p.kind}">${KIND_LABEL[p.kind]}</span>`];
      if (p.moved) badges.push(`<span class="badge moved">移位</span>`);
      const newCells = p.new_indices
        .map((t) => sentenceCell(r.new_sentences[t], "新"))
        .join("");
      div.className = `diff-row row-${p.kind}`;
      div.innerHTML = `
        ${sentenceCell(row.oldSent, "旧")}
        <div class="diff-mid">${badges.join("")}<span class="sim">sim ${p.similarity.toFixed(4)}</span></div>
        <div class="diff-new-pair">${newCells}</div>`;
    } else if (row.type === "deleted") {
      div.className = "diff-row row-deleted";
      div.innerHTML = `
        ${sentenceCell(row.oldSent, "旧")}
        <div class="diff-mid"><span class="badge deleted">删除</span></div>
        <div class="diff-cell empty">（新稿中已删除）</div>`;
    } else {
      div.className = "diff-row row-added";
      div.innerHTML = `
        <div class="diff-cell empty">（原稿中无对应）</div>
        <div class="diff-mid"><span class="badge added">新增</span></div>
        ${sentenceCell(row.newSent, "新")}`;
    }
    resultEl.appendChild(div);
  }
}

async function pollDiff() {
  clearTimeout(diffState.pollTimer);
  try {
    const data = await api("/api/diff");
    const job = data.diff;
    // 迟到保护（前端双保险）：只渲染本地最后提交的版本
    if (job && job.revision === diffState.revision) {
      renderDiffStatus(job);
      renderDiffResult(job);
      if (job.status === "pending" || job.status === "running") {
        diffState.pollTimer = setTimeout(pollDiff, 600);
      }
    }
  } catch (err) {
    $("#diff-status").textContent = `比对状态获取失败：${err.message}`;
  }
}

async function submitDiff() {
  const msg = $("#diff-msg");
  try {
    const payload = {
      article_id: parseInt($("#diff-article").value, 10),
      new_text: $("#diff-new").value,
      compute_delay_ms: parseInt($("#diff-delay").value, 10) || 0,
    };
    $("#btn-diff").disabled = true;
    const data = await api("/api/diff", { method: "POST", body: JSON.stringify(payload) });
    diffState.revision = data.revision; // 只认这个版本，旧版本响应一律忽略
    renderDiffStatus(data.diff);
    $("#diff-result").innerHTML = "";
    $("#diff-summary").textContent = "";
    showMsg(msg, `已提交版本 ${data.revision}，比对进行中`, true);
    pollDiff();
  } catch (err) {
    showMsg(msg, err.message);
  } finally {
    $("#btn-diff").disabled = false;
  }
}

async function initDiff() {
  renderDiffArticleSelect();
  await loadDiffOriginal();
  updateDiffCounters();
  // 恢复页面时若已有比对任务（如重启后），直接展示最新版本结果
  const data = await api("/api/diff");
  if (data.diff) {
    diffState.revision = data.diff.revision;
    renderDiffStatus(data.diff);
    renderDiffResult(data.diff);
    if (data.diff.status === "pending" || data.diff.status === "running") pollDiff();
  }
}

$("#diff-article").addEventListener("change", loadDiffOriginal);
$("#diff-new").addEventListener("input", updateDiffCounters);
$("#btn-diff").addEventListener("click", submitDiff);

refresh()
  .then(initDiff)
  .catch((err) => {
    $("#job-status").textContent = `加载失败：${err.message}`;
  });
