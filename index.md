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
      document.getElementById('readme-content').innerHTML =
        marked.parse(body, { gfm: true, breaks: true });
    });
</script>
