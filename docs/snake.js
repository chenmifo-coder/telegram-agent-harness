const canvas = document.getElementById('snake-canvas');
const ctx = canvas.getContext('2d');
let snake = [
  { x: 200, y: 200 },
  { x: 190, y: 200 },
  { x: 180, y: 200 },
];
let food = { x: Math.floor(Math.random() * 40) * 10, y: Math.floor(Math.random() * 40) * 10 };
let direction = 'right';
let score = 0;
let gameInterval;
const scoreDisplay = document.getElementById('score-display');
const restartBtn = document.getElementById('restart-btn');

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  snake.forEach(part => {
    ctx.fillStyle = 'green';
    ctx.fillRect(part.x, part.y, 10, 10);
  });
  ctx.fillStyle = 'red';
  ctx.fillRect(food.x, food.y, 10, 10);
  ctx.fillStyle = 'black';
  ctx.font = '24px Arial';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(`Score: ${score}`, 10, 10);
}

function update() {
  const head = { x: snake[0].x, y: snake[0].y };
  if (direction === 'right') head.x += 10;
  else if (direction === 'left') head.x -= 10;
  else if (direction === 'up') head.y -= 10;
  else if (direction === 'down') head.y += 10;

  if (head.x === food.x && head.y === food.y) {
    score++;
    scoreDisplay.textContent = `Score: ${score}`;
    food = { x: Math.floor(Math.random() * 40) * 10, y: Math.floor(Math.random() * 40) * 10 };
    snake.unshift(head);
  } else {
    snake.unshift(head);
    snake.pop();
  }

  if (head.x < 0 || head.x >= canvas.width || head.y < 0 || head.y >= canvas.height) {
    clearInterval(gameInterval);
    alert('Game Over!');
  }
}

function handleKey(event) {
  if (event.key === 'ArrowUp' && direction !== 'down') direction = 'up';
  else if (event.key === 'ArrowDown' && direction !== 'up') direction = 'down';
  else if (event.key === 'ArrowLeft' && direction !== 'right') direction = 'left';
  else if (event.key === 'ArrowRight' && direction !== 'left') direction = 'right';
}

function resetGame() {
  snake = [
    { x: 200, y: 200 },
    { x: 190, y: 200 },
    { x: 180, y: 200 },
  ];
  food = { x: Math.floor(Math.random() * 40) * 10, y: Math.floor(Math.random() * 40) * 10 };
  direction = 'right';
  score = 0;
  scoreDisplay.textContent = `Score: ${score}`;
  if (gameInterval) clearInterval(gameInterval);
  gameInterval = setInterval(() => {
    update();
    draw();
  }, 100);
  draw();
}

document.addEventListener('keydown', handleKey);
restartBtn.addEventListener('click', resetGame);
resetGame();
