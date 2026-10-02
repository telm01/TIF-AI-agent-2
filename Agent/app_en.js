const games = [

  {
    name: "Language",
    icon: "📚",
    description: "A vocabulary game — match pictures with words.",
    size: "12 MB",
    players: "1 PLAYER",
    c1: "#1477a0", c2: "#40319b",
    link: "language_game.html"
  },
  {
    name: "Math",
    icon: "🔢",
    description: "Count the pictures and find the correct number.",
    size: "10 MB",
    players: "1 PLAYER",
    c1: "#9b3b7c", c2: "#192d75",
    link: "game.html"
  },
  {
    name: "Logic",
    icon: "🧩",
    description: "A thinking game — find matching colors and shapes.",
    size: "8 MB",
    players: "1 PLAYER",
    c1: "#c06a2b", c2: "#4c267e",
    link: "logic_game.html"
  },
  {
    name: "AI Assistant",
    icon: "🤖",
    description: "Ask a question, get help — a smart assistant by your side.",
    size: "3 MB",
    players: "CHAT",
    c1: "#2b6dae", c2: "#7b2ff7",
    link: "assistant.html"
  },
  {
    name: "Battle",
    icon: "⚔️",
    description: "An adventure game where a hero overcomes obstacles.",
    size: "15 MB",
    players: "1–2 PLAYERS",
    c1: "#a82e3f", c2: "#1e224d"
  },
  {
    name: "Racing",
    icon: "🏎️",
    description: "A racing game with fast cars and circuit tracks.",
    size: "14 MB",
    players: "1–2 PLAYERS",
    c1: "#137c65", c2: "#203b8c"
  },
  {
    name: "Settings",
    icon: "⚙️",
    description: "System settings, display and control configuration.",
    size: "2 MB",
    players: "MENU",
    c1: "#43592a", c2: "#24182e"
  }
];


let selected = 0;

const $ = id => document.getElementById(id);

function render() {
  const game = games[selected];

  $("heroIcon").textContent = game.icon;
  $("heroTitle").textContent = game.name;
  $("detailsTitle").textContent = game.name;
  $("detailsDescription").textContent = game.description;
  $("detailsSize").textContent = game.size;
  $("detailsPlayers").textContent = game.players;
  $("heroArt").style.background =
    `radial-gradient(circle at 70% 30%, rgba(255,255,255,.35), transparent 8%),
     linear-gradient(135deg, ${game.c1}, ${game.c2})`;

  $("carousel").innerHTML = games.map((g, i) => `
    <div class="card ${i === selected ? "selected" : ""}"
         style="--c1:${g.c1};--c2:${g.c2}"
         data-index="${i}">
      <div class="emoji">${g.icon}</div>
      <div class="name">${g.name}</div>
    </div>
  `).join("");

  $("dots").innerHTML = games.map((_, i) =>
    `<div class="dot ${i === selected ? "active" : ""}"></div>`
  ).join("");

  document.querySelectorAll(".card").forEach(card => {
    card.onclick = () => {
      selected = Number(card.dataset.index);
      render();
    };
  });
}

function move(direction) {
  selected = (selected + direction + games.length) % games.length;
  render();
}

$("prev").onclick = () => move(-1);
$("next").onclick = () => move(1);

$("launch").onclick = () => {
  const selectedGame = games[selected];

  if (selectedGame && selectedGame.link) {
    window.location.href = selectedGame.link;
  }
};

document.addEventListener("keydown", e => {
  if (e.key === "ArrowLeft") move(-1);
  if (e.key === "ArrowRight") move(1);
  if (e.key === "Enter" || e.key === " ") $("launch").click();
  if (e.key === "Escape") selected = 0;
});

function updateClock() {
  $("time").textContent = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit"
  });
}

render();
updateClock();
setInterval(updateClock, 1000);
