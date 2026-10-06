const loginView = document.getElementById("loginView");
const appView = document.getElementById("appView");
const loginForm = document.getElementById("loginForm");
const toast = document.getElementById("toast");
const oldInput = document.getElementById("oldCsv");
const newInput = document.getElementById("newCsv");
const oldName = document.getElementById("oldName");
const newName = document.getElementById("newName");
const compareBtn = document.getElementById("compareBtn");
const clearBtn = document.getElementById("clearBtn");
const exportBtn = document.getElementById("exportBtn");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const scoreCard = document.getElementById("scoreCard");
const summaryCards = document.getElementById("summaryCards");
const columnsTable = document.getElementById("columnsTable");
const diffTable = document.getElementById("diffTable");

let latestReport = null;
const authClient = window.supabase?.createClient(
  "https://nirnlapkeqfpaaskrrhl.supabase.co",
  "sb_publishable_ZsZTnKgxeikxr0eIh8Wsqw_oGikgYpq",
  { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false } }
);
function authError(error) {
  if (error?.status === 429) return "Muitas tentativas. Aguarde e tente novamente.";
  if (error?.code === "invalid_credentials") return "E-mail ou senha incorretos.";
  if (error?.code === "email_not_confirmed") return "Seu e-mail ainda não está confirmado no Supabase.";
  if (error?.code === "same_password") return "Escolha uma senha diferente da atual.";
  if (error?.code === "weak_password") return "A senha não atende aos requisitos configurados no Supabase.";
  if (error?.code === "invalid_current_password") return "A senha atual não confere.";
  return "Não foi possível concluir. Confira a conexão e tente novamente.";
}
let activeEmail = "";
function showPage(page) {
  const selected = ["comparacao", "ajuda", "configuracoes"].includes(page) ? page : "comparacao";
  document.querySelectorAll(".app-page").forEach(el => el.classList.toggle("hidden", el.id !== `${selected}Page`));
  document.querySelectorAll(".page-nav a").forEach(link => {
    link.classList.toggle("active", link.dataset.page === selected);
    if (link.dataset.page === selected) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}
window.addEventListener("hashchange", () => showPage(location.hash.slice(1)));
showPage(location.hash.slice(1));
document.getElementById("settingsLogout").addEventListener("click", () => document.getElementById("logoutButton").click());
document.getElementById("changePasswordForm").addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.getElementById("passwordStatus");
  const current = document.getElementById("currentPassword").value;
  const next = document.getElementById("newPassword").value;
  const confirm = document.getElementById("confirmPassword").value;
  if (next.length < 8) { status.textContent = "Use pelo menos 8 caracteres."; return; }
  if (next !== confirm) { status.textContent = "A confirmação não coincide com a nova senha."; return; }
  if (next === current) { status.textContent = "Escolha uma senha diferente da atual."; return; }
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  status.textContent = "Salvando nova senha...";
  try {
    if (!authClient || !activeEmail) throw new Error("Sem sessão");
    const { error } = await authClient.auth.updateUser({ password: next, current_password: current });
    if (error) { status.textContent = authError(error); return; }
    form.reset();
    status.textContent = "Senha alterada com sucesso. Use a nova senha no próximo acesso.";
  } catch (error) { status.textContent = authError(error); }
  finally { button.disabled = false; }
});
const userButton = document.getElementById("userButton");
const userMenu = document.getElementById("userMenu");

function closeUserMenu() {
  userMenu.classList.add("hidden");
  userButton.setAttribute("aria-expanded", "false");
}

userButton.addEventListener("click", () => {
  const open = userMenu.classList.contains("hidden");
  userMenu.classList.toggle("hidden", !open);
  userButton.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", event => {
  if (!event.target.closest(".user-control")) closeUserMenu();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !userMenu.classList.contains("hidden")) {
    closeUserMenu();
    userButton.focus();
  }
});
function resetSignedOutView() {
  closeUserMenu();
  clearComparison();
  activeEmail = "";
  document.getElementById("changePasswordForm").reset();
  document.getElementById("passwordStatus").textContent = "";
  showPage("comparacao");
  history.replaceState(null, "", "#comparacao");
  loginForm.reset();
  document.getElementById("password").type = "password";
  document.getElementById("passwordToggle").setAttribute("aria-label", "Mostrar senha");
  document.getElementById("passwordToggle").setAttribute("aria-pressed", "false");
  appView.classList.add("hidden");
  loginView.classList.remove("hidden");
  toast.classList.add("hidden");
  loginForm.querySelector('input[type="email"]').focus();
}
document.getElementById("logoutButton").addEventListener("click", async () => {
  const button = document.getElementById("logoutButton");
  button.disabled = true;
  try {
    if (authClient) {
      const { error } = await authClient.auth.signOut({ scope: "local" });
      if (error) { showToast(authError(error)); return; }
    }
    resetSignedOutView();
  } catch (error) { showToast(authError(error)); }
  finally { button.disabled = false; }
});

loginForm.addEventListener("submit", async event => {
  event.preventDefault();
  const email = loginForm.querySelector('input[type="email"]').value.trim();
  const account = email.toLowerCase();
  const password = document.getElementById("password").value;
  if (!email || !password) { showToast("Informe o e-mail e a senha para entrar."); return; }
  const button = loginForm.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = "Entrando...";
  try {
    if (!authClient) throw new Error("Autenticação indisponível");
    const { data, error } = await authClient.auth.signInWithPassword({ email: account, password });
    if (error) { showToast(authError(error)); return; }
    showSignedInView(data.user);
    loginForm.reset();
    showToast("Login realizado com sucesso!");
  } catch (error) { showToast(authError(error)); }
  finally { button.disabled = false; button.textContent = "Entrar"; }
});

function showSignedInView(user) {
  const email = user.email || "";
  activeEmail = email;
  showPage(location.hash.slice(1));
  const name = (email.split("@")[0] || "Usuário").split(/[._-]/).filter(Boolean).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
  const initial = name.charAt(0).toUpperCase();
  document.getElementById("userName").textContent = name.split(" ")[0];
  document.getElementById("menuName").textContent = name;
  document.getElementById("menuEmail").textContent = email;
  document.getElementById("userInitial").textContent = initial;
  document.getElementById("menuInitial").textContent = initial;
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
}
if (authClient) {
  authClient.auth.onAuthStateChange((event, session) => {
    if (session?.user) showSignedInView(session.user);
    else if (activeEmail) resetSignedOutView();
  });
  authClient.auth.getSession().then(({ data, error }) => {
    if (!error && data.session?.user) showSignedInView(data.session.user);
  }).catch(() => showToast("Não foi possível recuperar a sessão. Entre novamente."));
} else {
  showToast("Não foi possível carregar a autenticação. Recarregue a página.");
}

document.getElementById("passwordToggle").addEventListener("click", event => {
  const password = document.getElementById("password");
  password.type = password.type === "password" ? "text" : "password";
  event.currentTarget.setAttribute("aria-label", password.type === "password" ? "Mostrar senha" : "Ocultar senha");
  event.currentTarget.setAttribute("aria-pressed", String(password.type === "text"));
});

oldInput.addEventListener("change", () => oldName.textContent = oldInput.files[0]?.name || "Selecionar CSV");
newInput.addEventListener("change", () => newName.textContent = newInput.files[0]?.name || "Selecionar CSV");
compareBtn.addEventListener("click", compareFiles);
clearBtn.addEventListener("click", clearComparison);
exportBtn.addEventListener("click", exportSummary);

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 2800);
}

async function compareFiles() {
  if (!oldInput.files[0] || !newInput.files[0]) {
    setStatus("Selecione os dois arquivos CSV.", true);
    return;
  }

  setStatus("Lendo arquivos e comparando...");
  const limit = Number(document.querySelector('input[name="rowLimit"]:checked').value);

  try {
    const [oldRows, newRows] = await Promise.all([
      readCsvFile(oldInput.files[0], limit),
      readCsvFile(newInput.files[0], limit)
    ]);

    latestReport = buildReport(oldRows, newRows);
    renderReport(latestReport);
    setStatus("Comparação finalizada.");
  } catch (error) {
    setStatus(error.message || "Não foi possível comparar os arquivos.", true);
  }
}

function clearComparison() {
  oldInput.value = "";
  newInput.value = "";
  oldName.textContent = "Selecionar CSV";
  newName.textContent = "Selecionar CSV";
  latestReport = null;
  resultsEl.classList.add("hidden");
  setStatus("Envie os dois CSVs para iniciar a validação.");
}

function exportSummary() {
  if (!latestReport) {
    setStatus("Faça uma comparação antes de exportar.", true);
    return;
  }

  const lines = [
    "Indicador;Valor",
    `Linhas antiga;${latestReport.oldRows.length}`,
    `Linhas otimizada;${latestReport.newRows.length}`,
    `Colunas comuns;${latestReport.commonCols.length}`,
    `Registros diferentes;${latestReport.onlyOldRows.length + latestReport.onlyNewRows.length}`,
    `Duplicados antiga;${latestReport.duplicateOld}`,
    `Duplicados otimizada;${latestReport.duplicateNew}`,
    `Colunas só antiga;${latestReport.onlyOldCols.join(", ") || "-"}`,
    `Colunas só otimizada;${latestReport.onlyNewCols.join(", ") || "-"}`
  ];

  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "resumo-validacao-queries.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? "#ff4d5a" : "#a9bacb";
}

function readCsvFile(file, limit) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Erro ao ler o arquivo."));
    reader.onload = () => {
      try {
        resolve(parseCsv(String(reader.result || ""), limit));
      } catch (error) {
        reject(error);
      }
    };
    reader.readAsText(file, "utf-8");
  });
}

function parseCsv(text, limit) {
  const delimiter = detectDelimiter(text);
  const rows = [];
  let row = [];
  let value = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const next = text[i + 1];

    if (char === '"' && inQuotes && next === '"') {
      value += '"';
      i++;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === delimiter && !inQuotes) {
      row.push(value.trim());
      value = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") i++;
      row.push(value.trim());
      if (row.some(cell => cell !== "")) rows.push(row);
      row = [];
      value = "";
      if (limit && rows.length > limit) break;
    } else {
      value += char;
    }
  }

  if (value || row.length) {
    row.push(value.trim());
    if (row.some(cell => cell !== "")) rows.push(row);
  }

  if (rows.length < 2) throw new Error("O CSV precisa ter cabeçalho e pelo menos uma linha.");

  const headers = rows[0].map(normalizeHeader);
  const dataRows = rows.slice(1, limit ? limit + 1 : undefined);

  return dataRows.map(values => {
    const record = {};
    headers.forEach((header, index) => {
      record[header || `coluna_${index + 1}`] = values[index] ?? "";
    });
    return record;
  });
}

function detectDelimiter(text) {
  const firstLine = text.split(/\r?\n/).find(Boolean) || "";
  const comma = (firstLine.match(/,/g) || []).length;
  const semicolon = (firstLine.match(/;/g) || []).length;
  const tab = (firstLine.match(/\t/g) || []).length;
  if (semicolon > comma && semicolon >= tab) return ";";
  if (tab > comma) return "\t";
  return ",";
}

function normalizeHeader(header) {
  return String(header || "").trim().toLowerCase();
}

function buildReport(oldRows, newRows) {
  const oldCols = Object.keys(oldRows[0] || {});
  const newCols = Object.keys(newRows[0] || {});
  const commonCols = oldCols.filter(col => newCols.includes(col));
  const onlyOldCols = oldCols.filter(col => !newCols.includes(col));
  const onlyNewCols = newCols.filter(col => !oldCols.includes(col));
  const oldFull = oldRows.map(row => fingerprint(row, commonCols));
  const newFull = newRows.map(row => fingerprint(row, commonCols));
  const oldCount = countMap(oldFull);
  const newCount = countMap(newFull);
  const onlyOldRows = diffRows(oldCount, newCount, "Apenas antiga");
  const onlyNewRows = diffRows(newCount, oldCount, "Apenas otimizada");
  const columnStats = commonCols.map(col => columnReport(col, oldRows, newRows));

  return {
    oldRows,
    newRows,
    commonCols,
    onlyOldCols,
    onlyNewCols,
    onlyOldRows,
    onlyNewRows,
    columnStats,
    duplicateOld: oldRows.length - new Set(oldFull).size,
    duplicateNew: newRows.length - new Set(newFull).size
  };
}

function columnReport(col, oldRows, newRows) {
  const oldValues = oldRows.map(row => row[col] ?? "");
  const newValues = newRows.map(row => row[col] ?? "");
  const oldNumeric = oldValues.map(toNumber).filter(value => value !== null);
  const newNumeric = newValues.map(toNumber).filter(value => value !== null);
  const canSum = oldNumeric.length > 0 || newNumeric.length > 0;

  return {
    coluna: col,
    nulosAntiga: oldValues.filter(isBlank).length,
    nulosNova: newValues.filter(isBlank).length,
    distintosAntiga: new Set(oldValues).size,
    distintosNova: new Set(newValues).size,
    somaAntiga: canSum ? sum(oldNumeric) : null,
    somaNova: canSum ? sum(newNumeric) : null
  };
}

function isBlank(value) {
  return value === "" || value === null || value === undefined;
}

function toNumber(value) {
  if (isBlank(value)) return null;
  const clean = String(value).replace(/\./g, "").replace(",", ".");
  if (!/^-?\d+(\.\d+)?$/.test(clean)) return null;
  const parsed = Number(clean);
  return Number.isFinite(parsed) ? parsed : null;
}

function sum(values) {
  return values.reduce((acc, value) => acc + value, 0);
}

function fingerprint(row, cols) {
  return cols.map(col => `${col}:${row[col] ?? ""}`).join("||");
}

function countMap(items) {
  return items.reduce((map, item) => {
    map.set(item, (map.get(item) || 0) + 1);
    return map;
  }, new Map());
}

function diffRows(source, target, label) {
  const rows = [];
  source.forEach((qty, key) => {
    const missing = qty - (target.get(key) || 0);
    for (let i = 0; i < missing; i++) rows.push({ origem: label, registro: key });
  });
  return rows;
}

function renderReport(report) {
  resultsEl.classList.remove("hidden");
  const exact = report.oldRows.length === report.newRows.length &&
    report.onlyOldCols.length === 0 &&
    report.onlyNewCols.length === 0 &&
    report.onlyOldRows.length === 0 &&
    report.onlyNewRows.length === 0 &&
    report.columnStats.every(col => col.nulosAntiga === col.nulosNova && col.distintosAntiga === col.distintosNova && sameNumber(col.somaAntiga, col.somaNova));

  scoreCard.className = `score ${exact ? "ok" : "bad"}`;
  scoreCard.innerHTML = `
    <strong>${exact ? "Os CSVs batem na amostra analisada." : "Foram encontradas diferenças."}</strong>
    <p>${exact ? "A estrutura e os registros comparados estão equivalentes." : "Revise as diferenças abaixo antes de considerar a query otimizada validada."}</p>
  `;

  const metrics = [
    ["Quantidade de linhas", report.oldRows.length, report.newRows.length],
    ["Quantidade de colunas", report.commonCols.length + report.onlyOldCols.length, report.commonCols.length + report.onlyNewCols.length],
    ["Linhas duplicadas", report.duplicateOld, report.duplicateNew],
    ["Colunas exclusivas", report.onlyOldCols.length, report.onlyNewCols.length],
    ["Registros exclusivos", report.onlyOldRows.length, report.onlyNewRows.length]
  ];
  summaryCards.innerHTML = `<div class="table-wrap"><table class="comparison-table">
    <thead><tr><th>Indicador</th><th>Antiga</th><th>Nova · otimizada</th><th>Comparação</th></tr></thead>
    <tbody>${metrics.map(([label, oldValue, newValue]) => `<tr><td>${label}</td><td class="metric-value">${formatNumber(oldValue)}</td><td class="metric-value">${formatNumber(newValue)}</td><td>${statusTag(oldValue === newValue)}</td></tr>`).join("")}</tbody>
  </table></div>`;

  renderColumnsTable(report);
  renderDiffTable(report);
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function renderColumnsTable(report) {
  const rows = report.columnStats.map(col => `
    <tr>
      <td>${escapeHtml(col.coluna)}</td>
      <td>${statusTag(col.nulosAntiga === col.nulosNova)}</td>
      <td>${col.nulosAntiga}</td>
      <td>${col.nulosNova}</td>
      <td>${statusTag(col.distintosAntiga === col.distintosNova)}</td>
      <td>${col.distintosAntiga}</td>
      <td>${col.distintosNova}</td>
      <td>${statusTag(sameNumber(col.somaAntiga, col.somaNova))}</td>
      <td>${formatNumber(col.somaAntiga)}</td>
      <td>${formatNumber(col.somaNova)}</td>
    </tr>
  `).join("");

  columnsTable.innerHTML = `
    <thead>
      <tr>
        <th>Coluna</th>
        <th>Nulos</th>
        <th>Antiga</th>
        <th>Otimizada</th>
        <th>Distintos</th>
        <th>Antiga</th>
        <th>Otimizada</th>
        <th>Soma</th>
        <th>Antiga</th>
        <th>Otimizada</th>
      </tr>
    </thead>
    <tbody>${rows || `<tr><td colspan="10">Nenhuma coluna comum encontrada.</td></tr>`}</tbody>
  `;
}

function renderDiffTable(report) {
  const render = (table, count, items) => {
    count.textContent = `${formatNumber(items.length)} registro(s) · considerando as colunas comuns${items.length > 100 ? " · exibindo os primeiros 100" : ""}`;
    table.innerHTML = `<thead><tr><th>Registro</th></tr></thead><tbody>${items.slice(0, 100).map(item => `<tr><td class="record-value">${escapeHtml(item.registro)}</td></tr>`).join("") || '<tr><td>Nenhum registro nesta categoria.</td></tr>'}</tbody>`;
  };
  render(diffTable, document.getElementById("removedCount"), report.onlyOldRows);
  render(document.getElementById("addedTable"), document.getElementById("addedCount"), report.onlyNewRows);
}

function statusTag(ok) {
  return `<span class="tag ${ok ? "" : "bad"}">${ok ? "OK" : "Difere"}</span>`;
}

function sameNumber(a, b) {
  if (a === null && b === null) return true;
  if (a === null || b === null) return false;
  return Math.abs(a - b) < 0.000001;
}

function formatNumber(value) {
  if (value === null) return "-";
  return value.toLocaleString("pt-BR", { maximumFractionDigits: 4 });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
