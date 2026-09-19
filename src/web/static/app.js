/* ==========================================================================
   English-Windish · 前端逻辑

   三个值得说明的技术点：

   1. **用 fetch 读流，而不是 EventSource**
      EventSource 只支持 GET 请求，但我们上传文件必须用 POST。
      所以用 fetch + response.body.getReader() 手动解析 SSE 流。
      代价是要自己处理分块边界（一个事件可能被切成两半），
      所以用 buffer 累积、按 \n\n 切分。

   2. **所有来自服务器的文本都用 textContent 写入，不用 innerHTML**
      分析结果里包含用户图片中识别出的文字，属于不可信输入。
      如果拼 innerHTML，图片里的恶意内容就会被当作 HTML 执行。
      用 createElement + textContent 从根上杜绝 XSS。

   3. **结果分页签而不是堆在一页**
      全文翻译、语法解析、单词精讲各自独立成一页。
      堆在一页时，一篇长文章的语法解析 + 词卡会有上万像素，
      用户必须不停滚动；分页后每页内容聚焦，切换成本也低。
   ========================================================================== */

(() => {
  'use strict';

  // ---------------------------------------------------------------- 元素引用
  const $ = (id) => document.getElementById(id);

  const el = {
    statusDot: document.querySelector('#status .dot'),
    statusText: $('status-text'),

    // 输入区
    inputPanel: $('input-panel'),
    modeTabs: document.querySelectorAll('.mode-tab'),
    modeImage: $('mode-image'),
    modeText: $('mode-text'),

    // 图片模式
    dropzone: $('dropzone'),
    fileInput: $('file-input'),
    preview: $('preview'),
    previewImg: $('preview-img'),
    previewName: $('preview-name'),
    previewMeta: $('preview-meta'),
    wordCount: $('word-count'),
    analyzeBtn: $('analyze-btn'),
    resetBtn: $('reset-btn'),

    // 文本模式
    textInput: $('text-input'),
    textCounter: $('text-counter'),
    textWordCount: $('text-word-count'),
    textAnalyzeBtn: $('text-analyze-btn'),
    textClearBtn: $('text-clear-btn'),

    // 进度 / 错误
    progressPanel: $('progress-panel'),
    progressLabel: $('progress-label'),
    progressBar: $('progress-bar'),
    progressSteps: $('progress-steps'),
    errorPanel: $('error-panel'),
    errorMessage: $('error-message'),
    errorBack: $('error-back'),

    // 结果
    resultSection: $('result-section'),
    resultSource: $('result-source'),
    backBtn: $('back-btn'),
    reanalyzeBtn: $('reanalyze-btn'),
    summaryBar: $('summary-bar'),
    resultTabs: document.querySelectorAll('.result-tab'),
    tabLabelTranslation: $('tab-label-translation'),
    tabLabelGrammar: $('tab-label-grammar'),
    tabCountGrammar: $('tab-count-grammar'),
    tabCountVocabulary: $('tab-count-vocabulary'),
    fullTranslation: $('full-translation'),
    translationEmpty: $('translation-empty'),
    sentences: $('sentences'),
    words: $('words'),
    rawText: $('raw-text'),

    // 历史记录
    historyOpen: $('history-open'),
    historyBadge: $('history-badge'),
    historyDrawer: $('history-drawer'),
    historyClose: $('history-close'),
    historyList: $('history-list'),
    historyTotal: $('history-total'),
    historyClear: $('history-clear'),
    drawerOverlay: $('drawer-overlay'),

    footerConfig: $('footer-config'),
  };

  const MAX_TEXT_CHARS = 20000;

  // 当前状态
  const state = {
    mode: 'image',      // image | text
    file: null,         // 待分析的图片文件
    lastInput: null,    // 上次分析的输入，用于「重新分析」
    loading: false,
  };

  // ================================================================ 工具函数

  /** 创建元素并设置文本（避免 innerHTML，防 XSS）。 */
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function show(node, visible) {
    if (node) node.hidden = !visible;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  /** HTML 转义 + 换行处理，用于安全地展示用户文本。 */
  function setText(node, text) {
    node.textContent = text || '';
  }

  function escapeForAlert(text) {
    return String(text).replace(/</g, '＜').replace(/>/g, '＞');
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
    show(el.inputPanel, false);
  }

  // ================================================================ 模式切换

  function switchMode(mode) {
    state.mode = mode;

    el.modeTabs.forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.mode === mode);
    });

    show(el.modeImage, mode === 'image');
    show(el.modeText, mode === 'text');
  }

  el.modeTabs.forEach((tab) => {
    tab.addEventListener('click', () => switchMode(tab.dataset.mode));
  });

  // ================================================================ 图片选择

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

    state.file = file;

    el.previewImg.src = URL.createObjectURL(file);
    el.previewName.textContent = file.name;
    el.previewMeta.textContent =
      `${formatSize(file.size)} · ${file.type.replace('image/', '').toUpperCase()}`;

    show(el.dropzone, false);
    show(el.preview, true);
  }

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

  // ================================================================ 文本输入

  function updateTextCounter() {
    const len = el.textInput.value.length;
    el.textCounter.textContent = `${len} 字符`;
    el.textCounter.classList.toggle('over', len > MAX_TEXT_CHARS);
  }

  el.textInput.addEventListener('input', updateTextCounter);

  el.textClearBtn.addEventListener('click', () => {
    el.textInput.value = '';
    updateTextCounter();
    el.textInput.focus();
  });

  // ================================================================ 进度渲染

  function initProgress(stages) {
    el.progressSteps.replaceChildren();

    (stages || []).forEach((name, i) => {
      const li = make('li');
      li.appendChild(make('span', 'step-mark', String(i + 1)));
      li.appendChild(make('span', null, name));
      el.progressSteps.appendChild(li);
    });

    el.progressBar.style.width = '0%';
  }

  function updateProgress(step, total, message) {
    el.progressLabel.textContent = message;
    el.progressBar.style.width =
      (total > 0 ? Math.round((step / total) * 100) : 0) + '%';

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

  function beginAnalysis() {
    state.loading = true;
    show(el.inputPanel, false);
    show(el.errorPanel, false);
    show(el.resultSection, false);
    show(el.progressPanel, true);

    el.progressLabel.textContent = '正在准备…';
    el.progressBar.style.width = '0%';
    el.progressSteps.replaceChildren();
  }

  function endAnalysis() {
    state.loading = false;
  }

  /** 提交图片分析。 */
  async function analyzeImageFile(file, wordCount) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('word_count', String(wordCount));

    state.lastInput = { type: 'image', file, wordCount };

    beginAnalysis();
    await runStream('/api/analyze', formData);
  }

  /** 提交文本分析。 */
  async function analyzeTextContent(text, wordCount) {
    const formData = new FormData();
    formData.append('text', text);
    formData.append('word_count', String(wordCount));

    state.lastInput = { type: 'text', text, wordCount };

    beginAnalysis();
    await runStream('/api/analyze-text', formData);
  }

  el.analyzeBtn.addEventListener('click', () => {
    if (!state.file || state.loading) return;
    const n = clampWordCount(el.wordCount.value);
    analyzeImageFile(state.file, n);
  });

  el.textAnalyzeBtn.addEventListener('click', () => {
    const text = el.textInput.value.trim();
    if (!text) {
      alert('请输入要分析的英文内容');
      el.textInput.focus();
      return;
    }
    if (text.length > MAX_TEXT_CHARS) {
      alert(`文本过长（${text.length} 字符），上限 ${MAX_TEXT_CHARS} 字符`);
      return;
    }
    if (state.loading) return;

    analyzeTextContent(text, clampWordCount(el.textWordCount.value));
  });

  el.textInput.addEventListener('keydown', (e) => {
    // Ctrl / Cmd + Enter 快捷提交
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      el.textAnalyzeBtn.click();
    }
  });

  function clampWordCount(value) {
    return Math.min(15, Math.max(3, parseInt(value, 10) || 8));
  }

  /** 发起请求并读取 SSE 流。 */
  async function runStream(endpoint, formData) {
    try {
      const resp = await fetch(endpoint, { method: 'POST', body: formData });

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
        throw new Error('当前浏览器不支持流式响应，请使用较新的 Chrome / Edge / Firefox');
      }

      await readEventStream(resp.body);
    } catch (err) {
      endAnalysis();
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
        endAnalysis();
        el.progressBar.style.width = '100%';
        el.progressLabel.textContent = '分析完成';
        renderResult(event.result, {
          source: event.filename,
          canReanalyze: true,
        });
        refreshHistoryBadge();
        break;

      case 'error':
        endAnalysis();
        showError(event.message);
        break;
    }
  }

  function showError(message) {
    show(el.progressPanel, false);
    show(el.resultSection, false);
    show(el.errorPanel, true);
    el.errorMessage.textContent = message;

    // 出错后让用户能回到输入界面重试
    show(el.inputPanel, false);
    switchMode(state.mode);
    show(el.preview, state.mode === 'image' && !!state.file);
    show(el.dropzone, state.mode === 'image' && !state.file);
  }

  // ================================================================ 结果渲染

  /**
   * 判断输入粒度：单词 / 词组 / 句子。
   *
   * 为什么要判断？因为「全文翻译」这个标题只对句子和段落成立。
   * 输入 accommodate 时，结果里的 translation 是词义（容纳；适应…），
   * 叫「全文翻译」名不副实。
   *
   * 判断依据是原文本的形态，而不是模型输出。
   * 模型的 structure 字段虽然也会说明「仅为词条」，但那是它的判断，可能不准；
   * 「有没有句子标点、一共几个词」是客观事实，判断更稳。
   */
  function detectInputKind(rawText) {
    const text = (rawText || '').trim();
    if (!text) return 'sentence';

    const wordCount = text.split(/\s+/).filter(Boolean).length;
    const hasSentenceMark = /[.!?;:。！？；：]/.test(text);
    const hasLineBreak = /\r?\n/.test(text);

    if (hasSentenceMark || hasLineBreak || wordCount > 5) return 'sentence';
    return wordCount <= 1 ? 'word' : 'phrase';
  }

  function renderResult(result, meta = {}) {
    show(el.progressPanel, false);
    show(el.errorPanel, false);
    show(el.inputPanel, false);
    show(el.resultSection, true);

    const inputKind = detectInputKind(result.raw_text);
    const isWordLike = inputKind === 'word' || inputKind === 'phrase';

    // 标题跟着输入粒度走：单词/词组叫「释义」，句子/段落才叫「全文翻译」
    setText(el.tabLabelTranslation, isWordLike ? '释义' : '全文翻译');

    el.resultSource.textContent = meta.source ? `来源：${meta.source}` : '';
    el.reanalyzeBtn.hidden = !meta.canReanalyze;

    renderSummary(result.stats || {}, result, inputKind);
    renderTranslation(result.full_translation || '');
    renderSentences(result.sentences || [], inputKind);
    renderWords(result.words || []);

    setText(el.rawText, result.raw_text || '');

    switchResultTab('translation');

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function renderSummary(stats, result, inputKind = 'sentence') {
    el.summaryBar.replaceChildren();

    const isWordLike = inputKind === 'word' || inputKind === 'phrase';

    // 单词/词组输入时，句子数和语法点必然是 0（没有句子可分析），
    // 摆在概览栏里只会让人以为哪里出错了，所以直接不显示这两项。
    const items = isWordLike
      ? [
          ['生词', stats.word_count ?? (result.words || []).length],
          ['搭配例句', stats.collocation_count ?? 0],
        ]
      : [
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

  function renderTranslation(text) {
    const hasText = !!text.trim();

    setText(el.fullTranslation, text);
    show(el.fullTranslation, hasText);
    show(el.translationEmpty, !hasText);
  }

  function renderSentences(sentences, inputKind = 'sentence') {
    el.sentences.replaceChildren();

    // 单词/词组输入没有句子结构可分析。
    // 这里必须提前拦截：模型会把单词本身当成「一个句子」返回
    // （structure 写着「单个动词，无句子主干」），
    // 如果照常渲染，用户看到的是一个语法点为 0 的空卡片，像是分析失败了。
    if (inputKind === 'word' || inputKind === 'phrase') {
      el.tabCountGrammar.textContent = '';
      el.sentences.appendChild(
        buildEmptyNote(
          `本次输入是${inputKind === 'word' ? '单词' : '词组'}，没有句子结构可分析。`,
          '语法标注针对的是句子成分，比如从句、倒装、固定搭配在句中的位置。'
          + '要看这个单词或词组的用法和例句，请到「单词精讲」页签。'
        )
      );
      return;
    }

    el.tabCountGrammar.textContent = sentences.length ? String(
      sentences.reduce((sum, s) => sum + (s.grammar_points || []).length, 0)
    ) : '';

    if (!sentences.length) {
      el.sentences.appendChild(
        buildEmptyNote(
          '本次输入没有可解析的完整句子。',
          '如果是单个单词或词组，可以到「单词精讲」页签查看详细词卡。'
        )
      );
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

  function buildEmptyNote(title, hint) {
    const box = make('div', 'panel');
    box.appendChild(make('p', 'empty-note', title));
    if (hint) {
      const sub = make('p', 'empty-note');
      sub.style.paddingTop = '0';
      sub.style.fontSize = '13px';
      sub.textContent = hint;
      box.appendChild(sub);
    }
    return box;
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

  function renderWords(words) {
    el.words.replaceChildren();
    el.tabCountVocabulary.textContent = words.length ? String(words.length) : '';

    if (!words.length) {
      el.words.appendChild(
        buildEmptyNote('本次未生成词卡。', '可以增加词卡数量后重新分析。')
      );
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

  // ================================================================ 结果页签

  function switchResultTab(name) {
    el.resultTabs.forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.tab === name);
    });

    document.querySelectorAll('.tab-panel').forEach((panel) => {
      show(panel, panel.dataset.panel === name);
    });
  }

  el.resultTabs.forEach((tab) => {
    tab.addEventListener('click', () => switchResultTab(tab.dataset.tab));
  });

  // ================================================================ 返回与重试

  function goBackToInput() {
    show(el.resultSection, false);
    show(el.progressPanel, false);
    show(el.errorPanel, false);
    show(el.inputPanel, true);

    switchMode(state.mode);

    if (state.mode === 'image') {
      show(el.preview, !!state.file);
      show(el.dropzone, !state.file);
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  el.backBtn.addEventListener('click', goBackToInput);
  el.errorBack.addEventListener('click', goBackToInput);

  el.resetBtn.addEventListener('click', () => {
    state.file = null;
    el.fileInput.value = '';
    el.previewImg.src = '';
    show(el.preview, false);
    show(el.dropzone, true);
  });

  el.reanalyzeBtn.addEventListener('click', () => {
    const input = state.lastInput;
    if (!input) {
      goBackToInput();
      return;
    }

    show(el.resultSection, false);

    if (input.type === 'image' && input.file) {
      analyzeImageFile(input.file, input.wordCount);
    } else if (input.type === 'text' && input.text) {
      analyzeTextContent(input.text, input.wordCount);
    } else {
      goBackToInput();
    }
  });

  // ================================================================ 历史记录

  function openDrawer() {
    show(el.drawerOverlay, true);
    show(el.historyDrawer, true);
    loadHistory();
  }

  function closeDrawer() {
    show(el.drawerOverlay, false);
    show(el.historyDrawer, false);
  }

  el.historyOpen.addEventListener('click', openDrawer);
  el.historyClose.addEventListener('click', closeDrawer);
  el.drawerOverlay.addEventListener('click', closeDrawer);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el.historyDrawer.hidden) closeDrawer();
  });

  async function loadHistory() {
    el.historyList.replaceChildren(
      make('p', 'history-empty', '加载中…')
    );

    try {
      const resp = await fetch('/api/history?limit=100');
      const data = await resp.json();

      el.historyTotal.textContent = data.total ? `（${data.total}）` : '';
      renderHistoryList(data.items || []);
    } catch (err) {
      el.historyList.replaceChildren(
        make('p', 'history-empty', '加载失败，请稍后重试。')
      );
    }
  }

  function renderHistoryList(items) {
    el.historyList.replaceChildren();

    if (!items.length) {
      el.historyList.appendChild(
        make('p', 'history-empty', '还没有分析记录。\n分析过的内容会自动保存在这里。')
      );
      return;
    }

    items.forEach((item) => {
      el.historyList.appendChild(buildHistoryItem(item));
    });
  }

  function buildHistoryItem(item) {
    const box = make('div', 'history-item');

    const top = make('div', 'history-top');
    const kind = make('span', 'history-kind',
      item.kind === 'image' ? '图片' : '文本');
    kind.dataset.kind = item.kind;
    top.appendChild(kind);
    top.appendChild(make('span', 'history-time', item.created_at));
    box.appendChild(top);

    box.appendChild(make('p', 'history-preview', item.preview || '（无内容）'));
    box.appendChild(make('p', 'history-summary', item.summary));

    // 删除按钮（阻止冒泡，避免触发加载）
    const del = make('button', 'history-delete', '✕');
    del.title = '删除这条记录';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteHistoryItem(item.id, box);
    });
    box.appendChild(del);

    box.addEventListener('click', () => loadHistoryDetail(item.id));

    return box;
  }

  async function loadHistoryDetail(id) {
    try {
      const resp = await fetch(`/api/history/${id}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.message || `加载失败（HTTP ${resp.status}）`);
      }

      const record = await resp.json();
      closeDrawer();

      renderResult(record.result || {}, {
        source: record.source,
        // 历史记录没有原始文件，无法直接重跑
        canReanalyze: false,
      });
    } catch (err) {
      alert('加载历史记录失败：' + escapeForAlert(err.message));
    }
  }

  async function deleteHistoryItem(id, node) {
    try {
      const resp = await fetch(`/api/history/${id}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error('删除失败');

      node.remove();

      // 如果列表空了，重新渲染空状态
      if (!el.historyList.children.length) {
        renderHistoryList([]);
      }
      refreshHistoryBadge();
    } catch (err) {
      alert('删除失败，请稍后重试。');
    }
  }

  el.historyClear.addEventListener('click', async () => {
    if (!confirm('确定清空全部历史记录？此操作不可恢复。')) return;

    try {
      await fetch('/api/history', { method: 'DELETE' });
      renderHistoryList([]);
      el.historyTotal.textContent = '';
      refreshHistoryBadge();
    } catch (err) {
      alert('清空失败，请稍后重试。');
    }
  });

  async function refreshHistoryBadge() {
    try {
      const resp = await fetch('/api/history?limit=1');
      const data = await resp.json();

      const total = data.total || 0;
      el.historyBadge.textContent = total > 99 ? '99+' : String(total);
      show(el.historyBadge, total > 0);
    } catch (_) { /* 忽略 */ }
  }

  // ================================================================ 启动

  checkHealth().then(() => refreshHistoryBadge());
  updateTextCounter();
})();
