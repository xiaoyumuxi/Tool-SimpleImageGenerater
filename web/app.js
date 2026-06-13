const state = {
  conversations: [],
  current: null,
  isGenerating: false,
  search: "",
};

const $ = (selector) => {
  const element = document.querySelector(selector);
  if (!element) throw new Error(`Missing element: ${selector}`);
  return element;
};

const conversationList = $("#conversationList");
const messagesEl = $("#messages");
const promptInput = $("#promptInput");
const titleInput = $("#titleInput");
const statusText = $("#statusText");
const sendBtn = $("#sendBtn");
const newConversationBtn = $("#newConversationBtn");
const saveTitleBtn = $("#saveTitleBtn");
const archiveConversationBtn = $("#archiveConversationBtn");
const searchInput = $("#searchInput");
const archiveBtn = $("#archiveBtn");
const archiveDialog = $("#archiveDialog");
const closeArchiveBtn = $("#closeArchiveBtn");
const archiveContent = $("#archiveContent");

const api = async (path, init) => {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
};

const formatTime = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
};

const setStatus = (message) => {
  statusText.textContent = message;
};

const renderConversations = () => {
  const query = state.search.trim().toLowerCase();
  const conversations = query
    ? state.conversations.filter((item) => item.title.toLowerCase().includes(query))
    : state.conversations;

  conversationList.replaceChildren(
    ...conversations.map((conversation) => {
      const button = document.createElement("button");
      button.className = `conversation-item${state.current?.id === conversation.id ? " active" : ""}`;
      button.type = "button";
      button.innerHTML = `
        <span class="conversation-title"></span>
        <span class="conversation-meta">${conversation.message_count ?? 0} 条消息 · ${formatTime(conversation.updated_at)}</span>
      `;
      button.querySelector(".conversation-title").textContent = conversation.title;
      button.addEventListener("click", () => loadConversation(conversation.id));
      return button;
    }),
  );
};

const renderMessages = () => {
  const messages = state.current?.messages ?? [];
  titleInput.value = state.current?.title ?? "";
  archiveConversationBtn.disabled = !state.current;

  if (!state.current || messages.length === 0) {
    messagesEl.className = "messages empty";
    messagesEl.innerHTML = `
      <div class="empty-state">
        <span>IMG</span>
        <h2>${state.current ? "发送第一条提示词开始生成。" : "新建一个对话，然后发送提示词。"}</h2>
      </div>
    `;
    return;
  }

  messagesEl.className = "messages";
  messagesEl.replaceChildren(...messages.map(renderMessage));
  messagesEl.scrollTop = messagesEl.scrollHeight;
};

const renderMessage = (message) => {
  const article = document.createElement("article");
  article.className = `message ${message.role}`;
  const role = document.createElement("span");
  role.className = "role";
  role.textContent = message.role === "assistant" ? "结果" : "提示词";

  const time = document.createElement("span");
  time.textContent = `${formatTime(message.created_at)}${message.model ? ` · ${message.model}` : ""}`;

  const head = document.createElement("div");
  head.className = "message-head";
  const meta = document.createElement("div");
  meta.append(role, document.createTextNode(" · "), time);

  const actions = document.createElement("div");
  actions.className = "message-actions";
  const editButton = document.createElement("button");
  editButton.type = "button";
  editButton.textContent = "编辑";
  editButton.addEventListener("click", () => editMessage(message));
  const archiveButton = document.createElement("button");
  archiveButton.type = "button";
  archiveButton.className = "danger";
  archiveButton.textContent = "归档";
  archiveButton.addEventListener("click", () => archiveMessage(message));
  actions.append(editButton, archiveButton);
  head.append(meta, actions);

  const content = document.createElement("p");
  content.className = "message-content";
  content.textContent = message.content;
  article.append(head, content);

  if (message.image_urls.length > 0) {
    const grid = document.createElement("div");
    grid.className = "image-grid";
    for (const url of message.image_urls) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noreferrer";
      const image = document.createElement("img");
      image.src = url;
      image.alt = "Generated image";
      link.append(image);
      grid.append(link);
    }
    article.append(grid);
  }

  return article;
};

const loadConversations = async () => {
  state.conversations = await api("/api/conversations");
  renderConversations();
};

const loadConversation = async (id) => {
  state.current = await api(`/api/conversations/${id}`);
  renderConversations();
  renderMessages();
};

const createConversation = async () => {
  const prompt = promptInput.value.trim();
  const conversation = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
  state.current = conversation;
  await loadConversations();
  renderMessages();
  setStatus("已新建对话");
};

const saveTitle = async () => {
  if (!state.current) return;
  const title = titleInput.value.trim();
  if (!title) {
    setStatus("标题不能为空");
    return;
  }
  state.current = await api(`/api/conversations/${state.current.id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  await loadConversations();
  renderMessages();
  setStatus("标题已保存");
};

const sendPrompt = async () => {
  const prompt = promptInput.value.trim();
  if (!prompt || state.isGenerating) return;
  if (!state.current) {
    await createConversation();
  }
  if (!state.current) return;

  state.isGenerating = true;
  sendBtn.disabled = true;
  setStatus("生成中，可能需要一两分钟...");
  try {
    state.current = await api(`/api/conversations/${state.current.id}/generate`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
    promptInput.value = "";
    await loadConversations();
    renderMessages();
    const last = state.current.messages?.at(-1);
    setStatus(last?.content.startsWith("Generation failed") ? last.content : "生成完成");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "生成失败");
  } finally {
    state.isGenerating = false;
    sendBtn.disabled = false;
  }
};

const editMessage = async (message) => {
  const next = window.prompt("编辑内容", message.content);
  if (next === null || !next.trim()) return;
  state.current = await api(`/api/messages/${message.id}`, {
    method: "PATCH",
    body: JSON.stringify({ content: next }),
  });
  await loadConversations();
  renderMessages();
  setStatus("消息已更新");
};

const archiveMessage = async (message) => {
  if (!window.confirm("归档这条消息？")) return;
  state.current = await api(`/api/messages/${message.id}`, { method: "DELETE" });
  await loadConversations();
  renderMessages();
  setStatus("消息已归档");
};

const archiveConversation = async () => {
  if (!state.current || !window.confirm("归档当前对话？")) return;
  await api(`/api/conversations/${state.current.id}`, { method: "DELETE" });
  state.current = null;
  await loadConversations();
  renderMessages();
  setStatus("对话已归档");
};

const showArchive = async () => {
  const archive = await api("/api/archive");
  const rows = [];
  for (const item of archive.conversations) {
    const row = document.createElement("div");
    row.className = "archive-row";
    row.innerHTML = `
      <strong></strong>
      <span>对话 #${item.original_id} · 归档于 ${formatTime(String(item.archived_at))}</span>
    `;
    row.querySelector("strong").textContent = String(item.title);
    rows.push(row);
  }
  for (const item of archive.messages.slice(0, 40)) {
    const row = document.createElement("div");
    row.className = "archive-row";
    row.innerHTML = `
      <strong></strong>
      <span>消息 #${item.original_id} · ${item.role} · 归档于 ${formatTime(String(item.archived_at))}</span>
    `;
    row.querySelector("strong").textContent = String(item.content).slice(0, 160);
    rows.push(row);
  }
  archiveContent.replaceChildren(...(rows.length ? rows : [document.createTextNode("暂无归档记录")]));
  archiveDialog.showModal();
};

newConversationBtn.addEventListener("click", () => void createConversation());
saveTitleBtn.addEventListener("click", () => void saveTitle());
sendBtn.addEventListener("click", () => void sendPrompt());
archiveConversationBtn.addEventListener("click", () => void archiveConversation());
archiveBtn.addEventListener("click", () => void showArchive());
closeArchiveBtn.addEventListener("click", () => archiveDialog.close());
searchInput.addEventListener("input", () => {
  state.search = searchInput.value;
  renderConversations();
});
promptInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    void sendPrompt();
  }
});

void (async () => {
  await loadConversations();
  if (state.conversations.length > 0) {
    await loadConversation(state.conversations[0].id);
  } else {
    renderMessages();
  }
})();
