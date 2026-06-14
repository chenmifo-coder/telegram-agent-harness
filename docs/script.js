console.log("全雲端公司網站 - 由 AI Agent 動態維護");

document.addEventListener('DOMContentLoaded', function() {
  const modal = document.getElementById('contact-modal');
  const btn = document.getElementById('contact-btn');
  const closeBtn = document.querySelector('.close-btn');
  const form = document.getElementById('contact-form');
  const responseDiv = document.getElementById('form-response');
  const modalForm = document.getElementById('modal-contact-form');
  const modalResponse = document.getElementById('modal-response');

  // 聯絡按鈕開啟模態視窗
  btn.addEventListener('click', () => {
    modal.style.display = 'block';
  });

  // 關閉模態視窗
  closeBtn.addEventListener('click', () => {
    modal.style.display = 'none';
  });

  // 點擊視窗外部關閉
  window.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.style.display = 'none';
    }
  });

  // 頁面表單提交
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      responseDiv.textContent = '感謝您的訊息！我們將盡快回覆。';
      responseDiv.style.display = 'block';
      form.reset();
    });
  }

  // 樞態視窗表單提交
  if (modalForm) {
    modalForm.addEventListener('submit', (e) => {
      e.preventDefault();
      modalResponse.textContent = '感謝您的訊息！我們將盡快回覆。';
      modalResponse.style.display = 'block';
      modalForm.reset();
    });
  }

  // ---- Theme toggle logic ----
  const themeToggleBtn = document.getElementById('theme-toggle');
  function setTheme(isDark) {
    if (isDark) {
      document.body.classList.add('dark');
    } else {
      document.body.classList.remove('dark');
    }
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
  }
  // 初始化主題
  const savedTheme = localStorage.getItem('theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  setTheme(savedTheme === 'dark');
  themeToggleBtn.addEventListener('click', () => {
    const isDark = document.body.classList.toggle('dark');
    setTheme(isDark);
  });
});
</script>