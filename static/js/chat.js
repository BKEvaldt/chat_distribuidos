(function () {
  const config = window.CHAT_CONFIG || {};
  const serverStatus = document.getElementById("serverStatus");
  const messageForm = document.getElementById("messageForm");
  const messageInput = document.getElementById("messageInput");
  const messages = document.getElementById("messages");
  const toastRegion = document.getElementById("toastRegion");
  const usersList = document.getElementById("usersList");
  const serverSwitchButton = document.getElementById("serverSwitchButton");
  const clearMessagesButton = document.getElementById("clearMessagesButton");

  let chatWorker = null;
  let userName = config.currentUser || "";

  function setStatus(text) {
    serverStatus.textContent = text;
  }

  function updateServerControls(active) {
    if (serverSwitchButton) {
      serverSwitchButton.hidden = !active;
      serverSwitchButton.disabled = false;
    }
  }

  function startChatWorker() {
    if (!window.Worker) {
      setStatus("Este navegador nao suporta recepcao em thread separada.");
      return;
    }

    chatWorker = new Worker(config.workerUrl || "/static/js/chat-worker.js");
    chatWorker.onmessage = handleWorkerMessage;
    chatWorker.onerror = function () {
      setStatus("Nao foi possivel iniciar a thread de recepcao.");
      updateServerControls(false);
    };
    chatWorker.postMessage({ type: "init", config: config });
  }

  function handleWorkerMessage(event) {
    const data = event.data || {};

    if (data.type === "status") {
      setStatus(data.text || "");
      return;
    }

    if (data.type === "server_controls") {
      updateServerControls(Boolean(data.active));
      return;
    }

    if (data.type === "message_history") {
      messages.innerHTML = "";
      (data.history || []).forEach(renderMessage);
      scrollToBottom();
      return;
    }

    if (data.type === "chat_message") {
      const message = data.message || {};
      const shouldStickToBottom = isNearBottom() || message.user === userName;
      renderMessage(message);
      if (shouldStickToBottom) {
        scrollToBottom();
      }
      return;
    }

    if (data.type === "chat_notification") {
      const notification = data.notification || {};
      if (notification.text && !isPresenceNotification(notification.text)) {
        showToast(notification.text, notification.level);
      }
      return;
    }

    if (data.type === "messages_cleared") {
      const payload = data.payload || {};
      messages.innerHTML = "";
      if (clearMessagesButton) {
        clearMessagesButton.disabled = false;
      }
      showToast(`Historico apagado por ${payload.by || "administrador"}.`, "info");
      return;
    }

    if (data.type === "users_update") {
      renderUsers(data.users || []);
      return;
    }

    if (data.type === "chat_error") {
      const error = data.error || {};
      if (error.message && !isServerRoutingMessage(error.message)) {
        setStatus(error.message);
      }
      return;
    }

    if (data.type === "clear_messages_failed") {
      const error = data.error || {};
      if (clearMessagesButton) {
        clearMessagesButton.disabled = false;
      }
      setStatus(error.message || "Nao foi possivel limpar o chat.");
      return;
    }

    if (data.type === "switch_failed") {
      if (serverSwitchButton) {
        serverSwitchButton.disabled = false;
      }
      setStatus("Nao foi possivel aplicar a troca.");
    }
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

  function isNearBottom() {
    return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  }

  messageForm.addEventListener("submit", function (event) {
    event.preventDefault();
    const text = messageInput.value.trim();

    if (!userName) {
      setStatus("Sessao expirada. Entre novamente.");
      return;
    }

    if (!text || !chatWorker) {
      return;
    }

    chatWorker.postMessage({ type: "send_message", text: text });
    messageInput.value = "";
    messageInput.focus();
  });

  if (serverSwitchButton) {
    serverSwitchButton.addEventListener("click", function () {
      if (!chatWorker) {
        setStatus("Nao foi possivel aplicar a troca.");
        return;
      }

      serverSwitchButton.disabled = true;
      setStatus("Aplicando troca...");
      chatWorker.postMessage({ type: "switch_server" });
    });
  }

  if (clearMessagesButton) {
    clearMessagesButton.addEventListener("click", function () {
      if (!chatWorker) {
        setStatus("Nao foi possivel limpar o chat.");
        return;
      }

      clearMessagesButton.disabled = true;
      chatWorker.postMessage({ type: "clear_messages" });
    });
  }

  renderUsers([]);
  updateServerControls(false);
  startChatWorker();
})();
