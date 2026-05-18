(function () {
  const config = window.CHAT_CONFIG || {};
  const serverStatus = document.getElementById("serverStatus");
  const messageForm = document.getElementById("messageForm");
  const messageInput = document.getElementById("messageInput");
  const messages = document.getElementById("messages");
  const toastRegion = document.getElementById("toastRegion");
  const usersList = document.getElementById("usersList");
  const serverSwitchButton = document.getElementById("serverSwitchButton");

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
  let reconnectNoticeTimer = null;

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

  function setConnectedStatus() {
    setStatus("Conectado ao chat");
  }

  function updateServerControls(role, active) {
    if (serverSwitchButton) {
      serverSwitchButton.hidden = !(active && ["primary", "backup"].includes(role));
      serverSwitchButton.disabled = false;
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

  function clearReconnectNotice() {
    window.clearTimeout(reconnectNoticeTimer);
  }

  function scheduleReconnectNotice() {
    clearReconnectNotice();
    reconnectNoticeTimer = window.setTimeout(function () {
      if (!socket || !socket.connected) {
        setStatus("Reconectando...");
      }
    }, 2500);
  }

  function connectToCandidate() {
    if (!candidates.length) {
      setStatus("Chat indisponivel.");
      return;
    }

    const url = candidates[candidateIndex % candidates.length];
    candidateIndex += 1;

    if (!activeUrl) {
      setStatus("Conectando...");
    }

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
      scheduleReconnectNotice();
      scheduleReconnect(900);
    });

    socket.on("disconnect", function () {
      joined = false;
      scheduleReconnectNotice();
      scheduleReconnect(900);
    });

    socket.on("server_info", function (info) {
      if (!info.active) {
        updateServerControls(info.role, false);
        scheduleReconnectNotice();
        socket.disconnect();
        scheduleReconnect(900);
        return;
      }

      activeUrl = info.server_url || url;
      activeRole = info.role || "servidor";
      clearReconnectNotice();
      setConnectedStatus();
      updateServerControls(activeRole, true);

      if (userName) {
        joinChat(userName);
      }
    });

    socket.on("server_promoted", function (info) {
      activeUrl = info.server_url || activeUrl;
      activeRole = info.role || "backup";
      clearReconnectNotice();
      setConnectedStatus();
      updateServerControls(activeRole, true);
    });

    socket.on("primary_restored", function (info) {
      preferCandidate(info.server_url || config.primaryUrl);
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

    socket.on("chat_notification", function (notification) {
      if (notification && notification.text) {
        if (isPresenceNotification(notification.text)) {
          return;
        }
        showToast(notification.text, notification.level);
      }
    });

    socket.on("users_update", renderUsers);

    socket.on("chat_error", function (error) {
      if (error && error.message) {
        if (isServerRoutingMessage(error.message)) {
          return;
        }
        setStatus(error.message);
      }
    });
  }

  function isServerRoutingMessage(message) {
    return [
      "Servidor primario em espera.",
      "Backup em espera. Tentando servidor principal.",
      "Backup ainda nao esta ativo."
    ].includes(message);
  }

  function isPresenceNotification(message) {
    return /\s(entrou|saiu) no chat\.$/.test(message);
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
      showToast(message.text, message.level);
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

  function showToast(text, level) {
    if (!toastRegion) {
      return;
    }

    const toast = document.createElement("div");
    toast.className = `toast ${level || "info"}`;
    toast.textContent = text;
    toastRegion.appendChild(toast);

    window.setTimeout(function () {
      toast.classList.add("leaving");
    }, 3600);

    window.setTimeout(function () {
      toast.remove();
    }, 4200);
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

  if (serverSwitchButton) {
    serverSwitchButton.addEventListener("click", function () {
      if (!window.confirm("Trocar para o outro servidor?")) {
        return;
      }

      serverSwitchButton.disabled = true;
      setStatus("Aplicando troca...");

      if (activeRole === "backup") {
        if (!socket || !socket.connected) {
          setStatus("Nao foi possivel aplicar a troca.");
          serverSwitchButton.disabled = false;
          return;
        }

        socket.emit("restore_primary");
        return;
      }

      if (activeRole !== "primary" || !config.failoverUrl) {
        setStatus("Nao foi possivel aplicar a troca.");
        serverSwitchButton.disabled = false;
        return;
      }

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
          if (socket) {
            socket.disconnect();
          }
          scheduleReconnect(1200);
        })
        .catch(function () {
          serverSwitchButton.disabled = false;
          setStatus("Nao foi possivel aplicar a troca.");
        });
    });
  }

  renderUsers([]);
  updateServerControls("", false);
  connectToCandidate();
})();
