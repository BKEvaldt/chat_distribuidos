(function () {
  const config = window.CHAT_CONFIG || {};
  const serverStatus = document.getElementById("serverStatus");
  const messageForm = document.getElementById("messageForm");
  const messageInput = document.getElementById("messageInput");
  const messages = document.getElementById("messages");
  const usersList = document.getElementById("usersList");
  const failoverButton = document.getElementById("failoverButton");
  const restoreButton = document.getElementById("restoreButton");

  const candidates = uniqueUrls([
    config.primaryUrl,
    config.backupUrl,
    window.location.origin
  ]);

  let socket = null;
  let candidateIndex = 0;
  let activeUrl = "";
  let activeRole = "";
  let joined = false;
  let userName = config.currentUser || "";
  let reconnectTimer = null;

  function normalizeUrl(url) {
    return String(url || "").replace(/\/$/, "");
  }

  function uniqueUrls(urls) {
    return urls.filter(Boolean).reduce((acc, url) => {
      const normalized = normalizeUrl(url);
      if (!acc.includes(normalized)) {
        acc.push(normalized);
      }
      return acc;
    }, []);
  }

  function setStatus(text) {
    serverStatus.textContent = text;
  }

  function updateServerControls(role, active) {
    if (failoverButton) {
      failoverButton.hidden = !(active && role === "primary");
      failoverButton.disabled = false;
    }

    if (restoreButton) {
      restoreButton.hidden = !(active && role === "backup");
      restoreButton.disabled = false;
    }
  }

  function preferCandidate(url) {
    const normalized = normalizeUrl(url);
    const index = candidates.indexOf(normalized);
    if (index >= 0) {
      candidateIndex = index;
    }
  }

  function scheduleReconnect(delay) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connectToCandidate, delay);
  }

  function connectToCandidate() {
    if (!candidates.length) {
      setStatus("Nenhum servidor configurado.");
      return;
    }

    const url = candidates[candidateIndex % candidates.length];
    candidateIndex += 1;
    setStatus(`Conectando em ${url}...`);

    if (socket) {
      socket.removeAllListeners();
      socket.disconnect();
    }

    socket = io(url, {
      auth: { token: config.socketAuthToken || "" },
      transports: ["websocket", "polling"],
      reconnection: false,
      timeout: 3000
    });

    socket.on("connect_error", function () {
      setStatus("Servidor indisponivel. Tentando alternativa...");
      scheduleReconnect(900);
    });

    socket.on("disconnect", function () {
      joined = false;
      setStatus("Conexao perdida. Reconectando...");
      scheduleReconnect(900);
    });

    socket.on("server_info", function (info) {
      if (!info.active) {
        updateServerControls(info.role, false);
        setStatus("Servidor em espera. Procurando ativo...");
        socket.disconnect();
        scheduleReconnect(900);
        return;
      }

      activeUrl = info.server_url || url;
      activeRole = info.role || "servidor";
      setStatus(`Conectado ao ${activeRole}: ${activeUrl}`);
      updateServerControls(activeRole, true);

      if (userName) {
        joinChat(userName);
      }
    });

    socket.on("server_promoted", function (info) {
      activeUrl = info.server_url || activeUrl;
      activeRole = info.role || "backup";
      setStatus(`Conectado ao ${activeRole}: ${activeUrl}`);
      updateServerControls(activeRole, true);
    });

    socket.on("primary_restored", function (info) {
      preferCandidate(info.server_url || config.primaryUrl);
      setStatus("Primario restaurado. Reconectando...");
      updateServerControls("backup", false);
      if (socket) {
        socket.disconnect();
      }
      scheduleReconnect(900);
    });

    socket.on("message_history", function (history) {
      messages.innerHTML = "";
      history.forEach(renderMessage);
      scrollToBottom();
    });

    socket.on("chat_message", function (message) {
      renderMessage(message);
      scrollToBottom();
    });

    socket.on("system_message", function (message) {
      renderMessage(message);
      scrollToBottom();
    });

    socket.on("users_update", renderUsers);

    socket.on("chat_error", function (error) {
      if (error && error.message) {
        setStatus(error.message);
      }
    });
  }

  function joinChat(name) {
    const cleanName = String(name || "").trim().slice(0, 32);
    if (!cleanName || !socket || !socket.connected) {
      return;
    }

    userName = cleanName;
    socket.emit("join");
    joined = true;
  }

  function renderUsers(users) {
    usersList.innerHTML = "";

    if (!users.length) {
      const empty = document.createElement("li");
      empty.textContent = "Nenhum usuario conectado";
      usersList.appendChild(empty);
      return;
    }

    users.forEach(function (user) {
      const item = document.createElement("li");
      item.textContent = user.name;
      usersList.appendChild(item);
    });
  }

  function renderMessage(message) {
    const item = document.createElement("article");
    item.className = "message";

    if (message.type === "system") {
      item.classList.add("system");
      item.textContent = message.text;
      messages.appendChild(item);
      return;
    }

    if (message.user === userName) {
      item.classList.add("mine");
    }

    const header = document.createElement("div");
    header.className = "message-header";

    const user = document.createElement("span");
    user.className = "message-user";
    user.textContent = message.user || "anonimo";

    const time = document.createElement("time");
    time.className = "message-time";
    time.dateTime = message.timestamp || "";
    time.textContent = formatTime(message.timestamp);

    const text = document.createElement("div");
    text.className = "message-text";
    text.textContent = message.text || "";

    header.appendChild(user);
    header.appendChild(time);
    item.appendChild(header);
    item.appendChild(text);
    messages.appendChild(item);
  }

  function formatTime(timestamp) {
    if (!timestamp) {
      return "";
    }

    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) {
      return "";
    }

    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  messageForm.addEventListener("submit", function (event) {
    event.preventDefault();
    const text = messageInput.value.trim();

    if (!userName) {
      setStatus("Sessao expirada. Entre novamente.");
      return;
    }

    if (!joined) {
      joinChat(userName);
    }

    if (!text || !socket || !socket.connected) {
      return;
    }

    socket.emit("send_message", { text: text });
    messageInput.value = "";
    messageInput.focus();
  });

  if (failoverButton && config.failoverUrl) {
    failoverButton.addEventListener("click", function () {
      if (!window.confirm("Derrubar o servidor primario e promover o backup?")) {
        return;
      }

      failoverButton.disabled = true;
      setStatus("Acionando failover manual...");

      fetch(config.failoverUrl, {
        method: "POST",
        headers: { "Accept": "application/json" }
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Falha ao acionar o backup.");
          }
          return response.json();
        })
        .then(function (data) {
          preferCandidate(data.backup_url || config.backupUrl);
          setStatus("Primario desligando. Conectando ao backup...");
          if (socket) {
            socket.disconnect();
          }
          scheduleReconnect(1200);
        })
        .catch(function () {
          failoverButton.disabled = false;
          setStatus("Nao foi possivel acionar o failover manual.");
        });
    });
  }

  if (restoreButton) {
    restoreButton.addEventListener("click", function () {
      if (!window.confirm("Restaurar o servidor primario e voltar para ele?")) {
        return;
      }

      if (!socket || !socket.connected || activeRole !== "backup") {
        setStatus("Conecte-se ao backup ativo para restaurar o primario.");
        return;
      }

      restoreButton.disabled = true;
      setStatus("Restaurando servidor primario...");
      socket.emit("restore_primary");
    });
  }

  renderUsers([]);
  updateServerControls("", false);
  connectToCandidate();
})();
