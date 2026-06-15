console.log("全雲端公司網站 - 由 AI Agent 動態維護");

document.addEventListener('DOMContentLoaded', function() {
  const form = document.getElementById('contact-form');
  const responseDiv = document.getElementById('form-response');

  // 頁面表單提交
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      responseDiv.textContent = '感謝您的訊息！我們將盡快回覆。';
      responseDiv.style.display = 'block';
      form.reset();
    });
  }

  // ---- Theme toggle logic ----
  const themeToggleBtn = document.getElementById('theme-toggle');
  function setTheme(isDark) {
    if (isDark) {
      document.body.clas
...（僅顯示前 1500 字元，完整內容已省略）