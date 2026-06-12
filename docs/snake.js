const canvas = document.getElementById('snake-canvas');
const ctx = canvas.getContext('2d');
const snake = [
  { x: 200, y: 200 },
  { x: 190, y: 200 },
  { x: 180, y: 200 },
];
const food = { x: Math.floor(Math.random() * 40) * 10, y: Math.floor(Math.random() * 40) * 10 };
let direction = 'right';
let score = 0;
function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  snake.forEach((part) => {
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
  const head = snake[0];
  if (direction === 'right') {
    head.x += 10;
  } else if (direction === 'left') {
    head.x -= 10;
  } else if (direction === 'up') {
    head.y -= 10;
  } else if (direction === 'down') {
    head.y += 10;
  }
  if (head.x === food.x && head.y === food.y) {
    score++;
    food.x = Math.floor(Math.random() * 40) * 10;
    food.y = Math.floor(Math.random() * 40) * 10;
  } else {
    snake.pop();
  }
  if (head.x < 0 || head.x >= canvas.width || head.y < 0 || head.y >= canvas.height) {
    alert('Game Over!');
    score = 0;
    snake = [
      { x: 200, y: 200 },
      { x: 190, y: 200 },
      { x: 180, y: 200 },
    ];
    direction = 'right';
  }
}
function handleKey(event) {
  if (event.key === 'ArrowUp' && direction !== 'down') {
    direction = 'up';
  } else if (event.key === 'ArrowDown' && direction !== 'up') {
    direction = 'down';
  } else if (event.key === 'ArrowLeft' && direction !== 'right') {
    direction = 'left';
  } else if (event.key === 'ArrowRight' && direction !== 'left') {
    direction = 'right';
  }
}
document.addEventListener('keydown', handleKey);
setInterval(() => {
  update();
  draw();
}, 100);
draw();