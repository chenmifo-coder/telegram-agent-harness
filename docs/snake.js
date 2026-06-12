const canvas = document.getElementById('snake-canvas');
const ctx = canvas.getContext('2d');
const gridSize = 20;
let cellSize;
let snake = [];
let food = {};
let direction = 'right';
let nextDirection = direction;
let score = 0;
let highScore = 0;
let lastRender = 0;
const scoreDisplay = document.getElementById('score-display');
const highScoreDisplay = document.getElementById('high-score-display');
const restartBtn = document.getElementById('restart-btn');
let gameRunning = false;
let touchStartX = 0, touchStartY = 0;

// Load high score
if (localStorage.getItem('snakeHighScore')) {
  highScore = parseInt(localStorage.getItem('snakeHighScore'), 10);
  highScoreDisplay.textContent = `High Score: ${highScore}`;
}

function resizeCanvas() {
  const size = Math.min(window.innerWidth * 0.9, window.innerHeight * 0.7, 400);
  canvas.width = size;
  canvas.height = size;
  cellSize = Math.min(canvas.width, canvas.height) / gridSize;
  if (gameRunning) {
    draw();
  }
}

function initGame() {
  snake = [
    { x: Math.floor(gridSize/2) * cellSize, y: Math.floor(gridSize/2) * cellSize },
    { x: (Math.floor(gridSize/2)-1) * cellSize, y: Math.floor(gridSize/2) * cellSize },
    { x: (Math.floor(gridSize/2)-2) * cellSize, y: Math.floor(gridSize/2) * cellSize },
  ];
  direction = 'right';
  nextDirection = direction;
  score = 0;
  scoreDisplay.textContent = `Score: ${score}`;
  placeFood();
  gameRunning = true;
  restartBtn.textContent = '重新開始';
  window.cancelAnimationFrame(animationFrameId);
  lastRender = 0;
  requestAnimationFrame(gameLoop);
}

function placeFood() {
  food = {
    x: Math.floor(Math.random() * gridSize) * cellSize,
    y: Math.floor(Math.random() * gridSize) * cellSize
  };
  for (let segment of snake) {
    if (segment.x === food.x && segment.y === food.y) {
      placeFood();
      return;
    }
  }
}

function handleKey(e) {
  switch(e.key) {
    case 'ArrowUp': if (direction !== 'down') nextDirection = 'up'; break;
    case 'ArrowDown': if (direction !== 'up') nextDirection = 'down'; break;
    case 'ArrowLeft': if (direction !== 'right') nextDirection = 'left'; break;
    case 'ArrowRight': if (direction !== 'left') nextDirection = 'right'; break;
  }
}

function setDirection(newDir) {
  const opposites = { up: 'down', down: 'up', left: 'right', right: 'left' };
  if (newDir !== opposites[direction]) {
    direction = newDir;
  }
}

function handleTouchStart(evt) {
  if (evt.touches.length === 1) {
    touchStartX = evt.touches[0].clientX;
    touchStartY = evt.touches[0].clientY;
  }
}

function handleTouchEnd(evt) {
  if (!touchStartX || !touchStartY) return;
  const touchEndX = evt.changedTouches[0].clientX;
  const touchEndY = evt.changedTouches[0].clientY;
  const diffX = touchEndX - touchStartX;
  const diffY = touchEndY - touchStartY;
  const absX = Math.abs(diffX);
  const absY = Math.abs(diffY);
  const threshold = 30;
  if (absX > threshold || absY > threshold) {
    if (absX > absY) {
      if (diffX > 0) setDirection('right');
      else setDirection('left');
    } else {
      if (diffY > 0) setDirection('down');
      else setDirection('up');
    }
  }
  touchStartX = 0;
  touchStartY = 0;
}

function update(deltaTime) {
  // Base speed 100ms, decrease by 5ms per point, min 30ms
  const baseSpeed = 100;
  const speed = Math.max(30, baseSpeed - score * 5);
  if (!update.lastTime) update.lastTime = 0;
  update.lastTime += deltaTime;
  if (update.lastTime < speed) return;
  update.lastTime = 0;

  const head = { x: snake[0].x, y: snake[0].y };
  switch(direction) {
    case 'right': head.x += cellSize; break;
    case 'left': head.x -= cellSize; break;
    case 'up': head.y -= cellSize; break;
    case 'down': head.y += cellSize; break;
  }

  // check food
  if (head.x === food.x && head.y === food.y) {
    score++;
    if (score > highScore) {
      highScore = score;
      localStorage.setItem('snakeHighScore', highScore);
      highScoreDisplay.textContent = `High Score: ${highScore}`;
    }
    scoreDisplay.textContent = `Score: ${score}`;
    placeFood();
    snake.unshift(head);
  } else {
    snake.unshift(head);
    snake.pop();
  }

  // wall collision
  if (head.x < 0 || head.x >= canvas.width || head.y < 0 || head.y >= canvas.height) {
    gameOver();
    return;
  }
  // self collision
  for (let i = 1; i < snake.length; i++) {
    if (snake[i].x === head.x && snake[i].y === head.y) {
      gameOver();
      return;
    }
  }
}
update.lastTime = 0;

function gameOver() {
  gameRunning = false;
  restartBtn.textContent = '開始遊戲';
  alert('Game Over! Your score: ' + score);
}

function draw() {
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  // optional grid
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= gridSize; i++) {
    const pos = i * cellSize;
    ctx.beginPath();
    ctx.moveTo(pos, 0);
    ctx.lineTo(pos, canvas.height);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, pos);
    ctx.lineTo(canvas.width, pos);
    ctx.stroke();
  }
  // draw snake
  ctx.fillStyle = '#39ff14';
  snake.forEach((segment, index) => {
    ctx.fillRect(segment.x, segment.y, cellSize, cellSize);
    // add glow effect for head
    if (index === 0) {
      ctx.shadowColor = '#39ff14';
      ctx.shadowBlur = 8;
      ctx.fillRect(segment.x, segment.y, cellSize, cellSize);
      ctx.shadowBlur = 0;
    }
  });
  // draw food
  ctx.fillStyle = '#ff073a';
  ctx.fillRect(food.x, food.y, cellSize, cellSize);
  ctx.shadowColor = '#ff073a';
  ctx.shadowBlur = 8;
  ctx.fillRect(food.x, food.y, cellSize, cellSize);
  ctx.shadowBlur = 0;
}

let animationFrameId = null;
function gameLoop(timestamp) {
  if (!lastRender) lastRender = timestamp;
  const deltaTime = timestamp - lastRender;
  lastRender = timestamp;
  update(deltaTime);
  draw();
  if (gameRunning) {
    animationFrameId = requestAnimationFrame(gameLoop);
  }
}

// Event listeners
window.addEventListener('load', () => {
  resizeCanvas();
  // show start screen
  restartBtn.textContent = '開始遊戲';
});
window.addEventListener('resize', resizeCanvas);
document.addEventListener('keydown', handleKey);
document.addEventListener('touchstart', handleTouchStart, { passive: true });
document.addEventListener('touchend', handleTouchEnd, { passive: true });
restartBtn.addEventListener('click', () => {
  if (!gameRunning) {
    initGame();
  } else {
    // if running, reset
    initGame();
  }
});

// Touch controls
document.addEventListener('DOMContentLoaded', () => {
  const btnUp = document.getElementById('btn-up');
  const btnDown = document.getElementById('btn-down');
  const btnLeft = document.getElementById('btn-left');
  const btnRight = document.getElementById('btn-right');
  if (btnUp) btnUp.addEventListener('click', () => setDirection('up'));
  if (btnDown) btnDown.addEventListener('click', () => setDirection('down'));
  if (btnLeft) btnLeft.addEventListener('click', () => setDirection('left'));
  if (btnRight) btnRight.addEventListener('click', () => setDirection('right'));
});