/* ==========================================================================
   English-Windish · 前端逻辑

   两个值得说明的技术点：

   1. **用 fetch 读流，而不是 EventSource**
      EventSource 只支持 GET 请求，但我们上传文件必须用 POST。
      所以用 fetch + response.body.getReader() 手动解析 SSE 流。
      代价是要自己处理分块边界（一个事件可能被切成两半），
      所以用 buffer 累积、按 \n\n 切分。

   2. **所有来自服务器的文本都用 textContent 写入，不用 innerHTML**
      分析结果里包含用户图片中识别出的文字，属于不可信输入。
      如果拼 innerHTML，图片里的恶意内容就会被当作 HTML 执行。
      用 createElement + textContent 从根上杜绝 XSS。
   ========================================================================== */

(() => {
  'use strict';

  // ---------------------------------------------------------------- 元素引用
  const $ = (id) => document.getElementById(id);

  const el = {
    statusDot: document.querySelector('#status .dot'),
    statusText: $('status-text'),

    uploadPanel: $('upload-panel'),
    dropzone: $('dropzone'),
    fileInput: $('file-input'),

    preview: $('preview'),
    previewImg: $('preview-img'),
    previewName: $('preview-name'),
    previewMeta: $('preview-meta'),
    wordCount: $('word-count'),
    analyzeBtn: $('analyze-btn'),
    resetBtn: $('reset-btn'),

    progressPanel: $('progress-panel'),
    progressLabel: $('progress-label'),
    progressBar: $('progress-bar'),
    progressSteps: $('progress-steps'),

    errorPanel: $('error-panel'),
    errorMessage: $('error-message'),
    errorBack: $('error-back'),

    resultSection: $('result-section'),
    summaryBar: $('summary-bar'),
    sentenceCount: $('sentence-count'),
    sentences: $('sentences'),
    translationPanel: $('translation-panel'),
    fullTranslation: $('full-translation'),
    wordCountLabel: $('word-count-label'),
    words: $('words'),
    rawText: $('raw-text'),

    footerConfig: $('footer-config'),
  };

  let currentFile = null;

  // ================================================================ 工具函数

  /** 创建元素并设置文本（避免 innerHTML，防 XSS）。 */
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  function show(node, visible) {
    node.hidden = !visible;
  }

  // ================================================================ 健康检查

  async function checkHealth() {
    try {
      const resp = await fetch('/api/health');
      const data = await resp.json();

      if (data.problems && data.problems.length) {
        el.statusDot.className = 'dot dot-bad';
        el.statusText.textContent = '配置不完整';
        el.footerConfig.textContent = data.problems.join(' | ');
        showConfigError(data.problems);
      } else {
        el.statusDot.className = 'dot dot-ok';
        el.statusText.textContent = '服务正常';
        el.footerConfig.textContent =
          `视觉 ${data.vision_model} · 文本 ${data.text_model}`;
      }
      return data;
    } catch (err) {
      el.statusDot.className = 'dot dot-bad';
      el.statusText.textContent = '服务未启动';
      el.footerConfig.textContent = '';
      return null;
    }
  }

  function showConfigError(problems) {
    show(el.errorPanel, true);
    el.errorMessage.textContent = '服务器配置不完整：\n\n' +
      problems.map((p, i) => `${i + 1}. ${p}`).join('\n\n');
    show(el.uploadPanel, false);
  }

  // ================================================================ 文件选择

  function selectFile(file) {
    if (!file) return;

    if (!file.type.startsWith('image/')) {
      alert('请选择图片文件（JPG / PNG / WEBP / BMP）');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      alert(`图片太大了（${formatSize(file.size)}），上限 10 MB`);
      return;
    }

    currentFile = file;

    el.previewImg.src = URL.createObjectURL(file);
    el.previewName.textContent = file.name;
    el.previewMeta.textContent =
      `${formatSize(file.size)} · ${file.type.replace('image/', '').toUpperCase()}`;

    show(el.dropzone, false);
    show(el.preview, true);
  }

  function resetAll() {
    currentFile = null;
    el.fileInput.value = '';
    el.previewImg.src = '';

    show(el.preview, false);
    show(el.dropzone, true);
    show(el.progressPanel, false);
    show(el.errorPanel, false);
    show(el.resultSection, false);

    el.sentences.replaceChildren();
    el.words.replaceChildren();
    el.summaryBar.replaceChildren();
    el.progressSteps.replaceChildren();
    el.progressBar.style.width = '0%';
  }

  // ================================================================ 事件绑定

  el.dropzone.addEventListener('click', () => el.fileInput.click());
  el.dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      el.fileInput.click();
    }
  });

  el.fileInput.addEventListener('change', (e) => selectFile(e.target.files[0]));

  ['dragenter', 'dragover'].forEach((type) => {
    el.dropzone.addEventListener(type, (e) => {
      e.preventDefault();
      el.dropzone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach((type) => {
    el.dropzone.addEventListener(type, (e) => {
      e.preventDefault();
      el.dropzone.classList.remove('dragover');
    });
  });

  el.dropzone.addEventListener('drop', (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) selectFile(file);
  });

  el.resetBtn.addEventListener('click', resetAll);
  el.errorBack.addEventListener('click', resetAll);
  el.analyzeBtn.addEventListener('click', startAnalysis);

  // ================================================================ 进度渲染

  let stageNames = [];

  function initProgress(stages) {
    stageNames = stages || [];
    el.progressSteps.replaceChildren();

    stageNames.forEach((name, i) => {
      const li = make('li');
      const mark = make('span', 'step-mark', String(i + 1));
      li.appendChild(mark);
      li.appendChild(make('span', null, name));
      el.progressSteps.appendChild(li);
    });

    el.progressBar.style.width = '0%';
  }

  function updateProgress(step, total, message) {
    el.progressLabel.textContent = message;

    const pct = total > 0 ? Math.round((step / total) * 100) : 0;
    el.progressBar.style.width = pct + '%';

    const items = el.progressSteps.children;
    for (let i = 0; i < items.length; i++) {
      const li = items[i];
      const n = i + 1;
      if (n < step) {
        li.className = 'done';
        li.firstChild.textContent = '✓';
      } else if (n === step) {
        li.className = 'active';
        li.firstChild.textContent = String(n);
      } else {
        li.className = '';
        li.firstChild.textContent = String(n);
      }
    }
  }

  // ================================================================ 分析主流程

  async function startAnalysis() {
    if (!currentFile) return;

    const wordCount = Math.min(
      15,
      Math.max(3, parseInt(el.wordCount.value, 10) || 8)
    );

    show(el.uploadPanel, false);
    show(el.errorPanel, false);
    show(el.resultSection, false);
    show(el.progressPanel, true);

    el.progressLabel.textContent = '正在上传图片…';
    el.progressBar.style.width = '0%';
    el.progressSteps.replaceChildren();

    const formData = new FormData();
    formData.append('file', currentFile);
    formData.append('word_count', String(wordCount));

    try {
      const resp = await fetch('/api/analyze', {
        method: 'POST',
        body: formData,
      });

      // 非流式错误（如 400 / 503），响应是 JSON
      if (!resp.ok) {
        let detail = `请求失败（HTTP ${resp.status}）`;
        try {
          const errData = await resp.json();
          if (errData.message) detail = errData.message;
        } catch (_) { /* 响应不是 JSON，用默认文案 */ }
        throw new Error(detail);
      }

      if (!resp.body) {
        throw new Error('当前浏览器不支持流式响应，请使用较新版本的 Chrome / Edge / Firefox');
      }

      await readEventStream(resp.body);
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  /** 解析 SSE 事件流。 */
  async function readEventStream(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8');

    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE 用空行分隔事件。最后一段可能不完整，留在 buffer 里等下一块
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';

      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith('data:')) continue;

        const payload = line.slice(5).trim();
        if (!payload) continue;

        let event;
        try {
          event = JSON.parse(payload);
        } catch (_) {
          continue; // 忽略解析不了的分片
        }

        handleEvent(event);
      }
    }
  }

  function handleEvent(event) {
    switch (event.type) {
      case 'start':
        initProgress(event.stages);
        el.progressLabel.textContent = '开始分析…';
        break;

      case 'progress':
        updateProgress(event.step, event.total, event.message);
        break;

      case 'done':
        el.progressBar.style.width = '100%';
        el.progressLabel.textContent = '分析完成';
        renderResult(event.result);
        break;

      case 'error':
        showError(event.message);
        break;
    }
  }

  function showError(message) {
    show(el.progressPanel, false);
    show(el.resultSection, false);
    show(el.errorPanel, true);
    el.errorMessage.textContent = message;

    // 出错后允许重新选择图片
    show(el.uploadPanel, false);
    show(el.preview, true);
  }

  // ================================================================ 结果渲染

  function renderResult(result) {
    show(el.progressPanel, false);
    show(el.resultSection, true);

    renderSummary(result.stats || {}, result);
    renderSentences(result.sentences || []);
    renderTranslation(result.full_translation || '');
    renderWords(result.words || []);

    el.rawText.textContent = result.raw_text || '';

    el.resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderSummary(stats, result) {
    el.summaryBar.replaceChildren();

    const items = [
      ['句子', stats.sentence_count ?? (result.sentences || []).length],
      ['语法点', stats.grammar_count ?? 0],
      ['生词', stats.word_count ?? (result.words || []).length],
      ['搭配例句', stats.collocation_count ?? 0],
    ];

    // 按语法类型分别统计
    const typeCounts = stats.grammar_types || {};
    Object.keys(typeCounts).forEach((type) => {
      items.push([type, typeCounts[type]]);
    });

    items.forEach(([label, value]) => {
      const box = make('div', 'stat');
      box.appendChild(make('span', 'stat-num', value));
      box.appendChild(make('span', 'stat-label', label));
      el.summaryBar.appendChild(box);
    });
  }

  function renderSentences(sentences) {
    el.sentences.replaceChildren();
    el.sentenceCount.textContent = sentences.length ? `共 ${sentences.length} 句` : '';

    if (!sentences.length) {
      const empty = make('p', 'word-body', '本次未解析出句子。');
      el.sentences.appendChild(empty);
      return;
    }

    sentences.forEach((sentence) => {
      const card = make('article', 'sentence');

      card.appendChild(make('span', 'sentence-no', `第 ${sentence.index} 句`));

      // 原文（带语法高亮）
      const original = make('p', 'sentence-original');
      original.appendChild(buildHighlighted(sentence));
      card.appendChild(original);

      // 翻译
      if (sentence.translation) {
        card.appendChild(make('p', 'sentence-trans', sentence.translation));
      }

      // 主干结构
      if (sentence.structure) {
        const struct = make('p', 'sentence-structure');
        struct.appendChild(make('b', null, '主干结构：'));
        struct.appendChild(document.createTextNode(sentence.structure));
        card.appendChild(struct);
      }

      // 语法点解释
      const points = sentence.grammar_points || [];
      if (points.length) {
        const list = make('div', 'gp-list');
        points.forEach((point) => list.appendChild(buildGrammarItem(point)));
        card.appendChild(list);
      }

      el.sentences.appendChild(card);
    });
  }

  /**
   * 把句子原文转成带高亮标记的 DOM 片段。
   *
   * 难点：多个语法点的片段可能重叠（比如一个长从句里还含一个固定搭配）。
   * 处理方式是先按起点排序，然后贪心地保留不重叠的区间。
   * 高亮只是视觉提示，完整的解释列表在下方，所以重叠部分丢弃不会丢信息。
   */
  function buildHighlighted(sentence) {
    const original = sentence.original || '';
    const frag = document.createDocumentFragment();

    const ranges = [];
    for (const point of sentence.grammar_points || []) {
      const text = point.text;
      if (!text) continue;
      const start = original.indexOf(text);
      if (start === -1) continue; // 后端已过滤，这里再兜一层
      ranges.push({ start, end: start + text.length, point });
    }

    ranges.sort((a, b) => a.start - b.start);

    // 贪心去重叠
    const kept = [];
    let lastEnd = -1;
    for (const range of ranges) {
      if (range.start >= lastEnd) {
        kept.push(range);
        lastEnd = range.end;
      }
    }

    let cursor = 0;
    for (const range of kept) {
      if (range.start > cursor) {
        frag.appendChild(
          document.createTextNode(original.slice(cursor, range.start))
        );
      }

      const mark = make('mark', 'gp', original.slice(range.start, range.end));
      mark.dataset.type = range.point.grammar_type;
      mark.title = range.point.subtype || range.point.grammar_type;
      frag.appendChild(mark);

      cursor = range.end;
    }

    if (cursor < original.length) {
      frag.appendChild(document.createTextNode(original.slice(cursor)));
    }

    return frag;
  }

  function buildGrammarItem(point) {
    const item = make('div', 'gp-item');
    item.dataset.type = point.grammar_type;

    const head = make('div', 'gp-head');
    head.appendChild(make('code', 'gp-frag', point.text));

    let label = point.grammar_type;
    if (point.subtype) label += ' · ' + point.subtype;
    const tag = make('span', 'gp-tag', label);
    tag.dataset.type = point.grammar_type;
    head.appendChild(tag);

    item.appendChild(head);

    if (point.explanation) {
      item.appendChild(make('p', 'gp-explain', point.explanation));
    }

    const signals = point.signal_words || [];
    if (signals.length) {
      const line = make('p', 'gp-signals');
      line.appendChild(document.createTextNode('标志词：'));
      signals.forEach((word, i) => {
        if (i > 0) line.appendChild(document.createTextNode('、'));
        line.appendChild(make('code', null, word));
      });
      item.appendChild(line);
    }

    return item;
  }

  function renderTranslation(text) {
    if (!text) {
      show(el.translationPanel, false);
      return;
    }
    show(el.translationPanel, true);
    el.fullTranslation.textContent = text;
  }

  function renderWords(words) {
    el.words.replaceChildren();
    el.wordCountLabel.textContent = words.length ? `共 ${words.length} 个` : '';

    if (!words.length) {
      el.words.appendChild(make('p', 'word-body', '本次未生成词卡。'));
      return;
    }

    words.forEach((word) => el.words.appendChild(buildWordCard(word)));
  }

  function buildWordCard(word) {
    const card = make('article', 'word-card');

    // ---- 标题：单词 + 音标 ----
    const head = make('div', 'word-head');
    head.appendChild(make('h3', 'word-title', word.word));

    const phonetics = [];
    if (word.phonetic_uk) phonetics.push('英 ' + word.phonetic_uk);
    if (word.phonetic_us) phonetics.push('美 ' + word.phonetic_us);
    if (phonetics.length) {
      head.appendChild(make('span', 'word-phonetic', phonetics.join('  ')));
    }
    card.appendChild(head);

    // ---- 本文语境 ----
    if (word.context_sentence) {
      card.appendChild(make('p', 'context-quote', '本文语境：' + word.context_sentence));
    }

    // ---- 构词分析 ----
    if (word.morphology) {
      card.appendChild(
        buildSection('构词分析', make('p', 'word-body', word.morphology))
      );
    }

    // ---- 词源与核心原意 ----
    if (word.etymon) {
      card.appendChild(
        buildSection('词源 · 核心原意', make('p', 'word-body', word.etymon))
      );
    }

    // ---- 释义（高频 + 折叠全部）----
    const defNodes = [];
    const highFreq = word.high_freq_definitions || [];
    if (highFreq.length) {
      defNodes.push(buildDefList(highFreq));
    }

    const allGroups = word.all_definitions || [];
    const totalDefs = allGroups.reduce(
      (sum, g) => sum + (g.definitions || []).length, 0
    );

    if (totalDefs > highFreq.length) {
      defNodes.push(buildAllDefsDetails(allGroups, totalDefs));
    }

    if (defNodes.length) {
      card.appendChild(buildSectionMultiline('中文释义', defNodes));
    }

    // ---- 固定搭配 ----
    const collocations = word.collocations || [];
    if (collocations.length) {
      const nodes = collocations.map(buildCollocation);
      card.appendChild(
        buildSectionMultiline(`固定搭配（${collocations.length}）`, nodes)
      );
    }

    // ---- 补充说明 ----
    if (word.notes) {
      card.appendChild(
        buildSection('补充说明', make('p', 'word-body', word.notes))
      );
    }

    return card;
  }

  function buildSection(label, bodyNode) {
    return buildSectionMultiline(label, [bodyNode]);
  }

  function buildSectionMultiline(label, bodyNodes) {
    const section = make('div', 'word-section');
    section.appendChild(make('span', 'word-label', label));
    bodyNodes.forEach((node) => section.appendChild(node));
    return section;
  }

  /** 高频释义列表。文本形如 "n. 服务，效劳"，把词性单独标记出来。 */
  function buildDefList(definitions) {
    const list = make('ul', 'def-list');

    definitions.forEach((def) => {
      const li = make('li');
      // 匹配开头的词性缩写，如 "n." "v." "adj." "conj."
      const match = String(def).match(/^([a-z]+\.)\s*(.*)$/);

      if (match) {
        li.appendChild(make('span', 'def-pos', match[1]));
        li.appendChild(document.createTextNode(match[2]));
      } else {
        li.appendChild(document.createTextNode(def));
      }

      list.appendChild(li);
    });

    return list;
  }

  /** 完整释义的折叠块。 */
  function buildAllDefsDetails(groups, totalDefs) {
    const details = make('details', 'more-defs');
    details.appendChild(
      make('summary', null, `展开查看全部释义（共 ${totalDefs} 条）`)
    );

    const body = make('div', 'more-defs-body');

    groups.forEach((group) => {
      const defs = group.definitions || [];
      if (!defs.length) return;

      const block = make('div', 'pos-group');
      block.appendChild(make('strong', null, group.part_of_speech || '—'));
      block.appendChild(buildDefList(defs));
      body.appendChild(block);
    });

    details.appendChild(body);
    return details;
  }

  function buildCollocation(collocation) {
    const box = make('div', 'collocation');

    box.appendChild(make('p', 'collocation-phrase', collocation.phrase));

    if (collocation.meaning) {
      box.appendChild(make('p', 'collocation-meaning', collocation.meaning));
    }

    if (collocation.example) {
      const example = make('p', 'collocation-example', collocation.example);
      if (collocation.example_translation) {
        example.appendChild(make('em', null, collocation.example_translation));
      }
      box.appendChild(example);
    }

    return box;
  }

  // ================================================================ 启动

  checkHealth();
})();
