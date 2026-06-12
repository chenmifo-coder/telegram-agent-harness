const canvas = document.getElementById('snake-canvas');
const ctx = canvas.getContext('2d');
const gridSize = 20; // number of cells per side
let cellSize = Math.min(canvas.width, canvas.height) / gridSize;
let snake = [
  { x: Math.floor(gridSize/2) * cellSize, y: Math.floor(gridSize/2) * cellSize },
  { x: (Math.floor(gridSize/2)-1) * cellSize, y: Math.floor(gridSize/2) * cellSize },
  { x: (Math.floor(gridSize/2)-2) * cellSize, y: Math.floor(gridSize/2) * cellSize },
];
let food = { x: 0, y: 0 };
let direction = 'right';
let score = 0;
let gameInterval;
const scoreDisplay = document.getElementById('score-display');
const restartBtn = document.getElementById('restart-btn');

function resizeCanvas() {
  // Adjust canvas size based on container while maintaining square
  const size = Math.min(window.innerWidth * 0.9, window.innerHeight * 0.7, 400);
  canvas.width = size;
  canvas.height = size;
  cellSize = Math.min(canvas.width, canvas.height) / gridSize;
  // Redraw
  draw();
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  snake.forEach(part => {
    ctx.fillStyle = 'green';
    ctx.fillRect(part.x, part.y, cellSize, cellSize);
  });
  ctx.fillStyle = 'red';
  ctx.fillRect(food.x, food.y, cellSize, cellSize);
  ctx.fillStyle = 'black';
  ctx.font = `${Math.round(cellSize * 0.4)}px Arial`;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(`Score: ${score}`, 10, 10);
}

function update() {
  const head = { x: snake[0].x, y: snake[0].y };
  if (direction === 'right') head.x += cellSize;
  else if (direction === 'left') head.x -= cellSize;
  else if (direction === 'up') head.y -= cellSize;
  else if (direction === 'down') head.y += cellSize;

  if (head.x === food.x && head.y === food.y) {
    score++;
    scoreDisplay.textContent = `Score: ${score}`;
    placeFood();
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

function placeFood() {
  food = {
    x: Math.floor(Math.random() * gridSize) * cellSize,
    y: Math.floor(Math.random() * gridSize) * cellSize
  };
  // Ensure not on snake
  for (let segment of snake) {
    if (segment.x === food.x && segment.y === food.y) {
      placeFood();
      return;
    }
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
    { x: Math.floor(gridSize/2) * cellSize, y: Math.floor(gridSize/2) * cellSize },
    { x: (Math.floor(gridSize/2)-1) * cellSize, y: Math.floor(gridSize/2) * cellSize },
    { x: (Math.floor(gridSize/2)-2) * cellSize, y: Math.floor(gridSize/2) * cellSize },
  ];
  placeFood();
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

window.addEventListener('load', () => {
  resizeCanvas();
});
window.addEventListener('resize', resizeCanvas);
document.addEventListener('keydown', handleKey);
restartBtn.addEventListener('click', resetGame);
