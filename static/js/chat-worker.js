importScripts("https://cdn.socket.io/4.7.5/socket.io.min.js");

let config = {};
let candidates = [];
let socket = null;
let candidateIndex = 0;
let activeUrl = "";
let activeRole = "";
let joined = false;
let userName = "";
let reconnectTimer = null;
let reconnectNoticeTimer = null;

function post(type, payload) {
  self.postMessage(Object.assign({ type: type }, payload || {}));
}

function normalizeUrl(url) {
  return String(url || "").replace(/\/$/, "");
}

function uniqueUrls(urls) {
  return urls.filter(Boolean).reduce(function (acc, url) {
    const normalized = normalizeUrl(url);
    if (!acc.includes(normalized)) {
      acc.push(normalized);
    }
    return acc;
  }, []);
}

function setStatus(text) {
  post("status", { text: text });
}

function setConnectedStatus() {
  setStatus("Conectado ao chat");
}

function updateServerControls(role, active) {
  const canSwitch =
    active && (role === "backup" || (role === "primary" && Boolean(config.failoverUrl)));
  post("server_controls", { role: role, active: canSwitch });
}

function preferCandidate(url) {
  const normalized = normalizeUrl(url);
  const index = candidates.indexOf(normalized);
  if (index >= 0) {
    candidateIndex = index;
  }
}

function scheduleReconnect(delay) {
  self.clearTimeout(reconnectTimer);
  reconnectTimer = self.setTimeout(connectToCandidate, delay);
}

function clearReconnectNotice() {
  self.clearTimeout(reconnectNoticeTimer);
}

function scheduleReconnectNotice() {
  clearReconnectNotice();
  reconnectNoticeTimer = self.setTimeout(function () {
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
      joinChat();
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
    post("message_history", { history: history || [] });
  });

  socket.on("chat_message", function (message) {
    post("chat_message", { message: message || {} });
  });

  socket.on("chat_notification", function (notification) {
    post("chat_notification", { notification: notification || {} });
  });

  socket.on("users_update", function (users) {
    post("users_update", { users: users || [] });
  });

  socket.on("chat_error", function (error) {
    post("chat_error", { error: error || {} });
    if (isSwitchError(error && error.message)) {
      post("switch_failed");
    }
  });
}

function isSwitchError(message) {
  return [
    "Controle de failover desativado.",
    "Backup nao esta ativo.",
    "Nao foi possivel restaurar o primario."
  ].includes(message);
}

function joinChat() {
  if (!userName || !socket || !socket.connected) {
    return;
  }

  socket.emit("join");
  joined = true;
}

function sendMessage(text) {
  if (!userName) {
    setStatus("Sessao expirada. Entre novamente.");
    return;
  }

  if (!joined) {
    joinChat();
  }

  if (!text || !socket || !socket.connected) {
    return;
  }

  socket.emit("send_message", { text: text });
}

function switchServer() {
  setStatus("Aplicando troca...");

  if (activeRole === "backup") {
    if (!socket || !socket.connected) {
      post("switch_failed");
      return;
    }

    socket.emit("restore_primary");
    return;
  }

  if (activeRole !== "primary" || !config.failoverUrl) {
    post("switch_failed");
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
      post("switch_failed");
    });
}

self.onmessage = function (event) {
  const message = event.data || {};

  if (message.type === "init") {
    config = message.config || {};
    userName = config.currentUser || "";
    candidates = uniqueUrls([
      config.primaryUrl,
      config.backupUrl,
      self.location.origin
    ]);
    connectToCandidate();
    return;
  }

  if (message.type === "send_message") {
    sendMessage(String(message.text || "").trim());
    return;
  }

  if (message.type === "switch_server") {
    switchServer();
  }
};
