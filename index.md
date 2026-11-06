---
layout: default
title: gadgets
---

<div id="readme-content"></div>

<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
  fetch('README.md')
    .then(r => r.text())
    .then(text => {
      // 最初の # 見出し行を除去（テーマが title として表示するため）
      const body = text.replace(/^#\s+.+\n/, '');
      const el = document.getElementById('readme-content');
      el.innerHTML = marked.parse(body, { gfm: true, breaks: true });
      // 生成されたリンクをすべて新しいタブで開く
      el.querySelectorAll('a[href]').forEach(a => {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      });
    });
</script>
