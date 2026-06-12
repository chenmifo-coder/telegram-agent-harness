console.log("全雲端公司網站 - 由 AI Agent 動態維護");

document.addEventListener('DOMContentLoaded', function() {
  const modal = document.getElementById('contact-modal');
  const btn = document.getElementById('contact-btn');
  const closeBtn = document.querySelector('.close-btn');
  const form = document.getElementById('contact-form');
  const responseDiv = document.getElementById('form-response');

  btn.addEventListener('click', () => {
    modal.style.display = 'block';
  });

  closeBtn.addEventListener('click', () => {
    modal.style.display = 'none';
  });

  window.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.style.display = 'none';
    }
  });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    responseDiv.textContent = '感謝您的訊息';
    responseDiv.style.display = 'block';
    form.reset();
  });
});