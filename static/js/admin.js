const adminState = {
  token: localStorage.getItem("lineLotteryAdminToken") || "",
  liffReady: false,
  profile: null,
  isAdmin: false,
  members: new Map(),
  csvText: "",
  csvPreviewOk: false,
};

document.addEventListener("DOMContentLoaded", () => {
  bindAdmin();
  document.getElementById("adminToken").value = adminState.token;
  document.getElementById("memberLineUserId").value = window.ADMIN_DEFAULT_USER_IDS?.[0] || "";
  initAdminPage();
});

function bindAdmin() {
  document.getElementById("adminBackButton").addEventListener("click", () => {
    window.location.href = "/lottery";
  });
  document.getElementById("adminLoginButton").addEventListener("click", adminLogin);
  document.getElementById("adminLogoutButton").addEventListener("click", adminLogout);
  document.getElementById("saveTokenButton").addEventListener("click", saveToken);
  document.getElementById("clearTokenButton").addEventListener("click", clearToken);
  document.getElementById("loadAdminUsersButton").addEventListener("click", loadAdminUsers);
  document.getElementById("addAdminUserButton").addEventListener("click", addAdminUser);
  document.getElementById("adminUserList").addEventListener("click", handleAdminUserClick);
  document.getElementById("loadMembersButton").addEventListener("click", loadMembers);
  document.getElementById("memberSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadMembers();
  });
  document.getElementById("memberList").addEventListener("click", handleMemberListClick);
  document.getElementById("loadMemberButton").addEventListener("click", loadMember);
  document.getElementById("saveMemberButton").addEventListener("click", saveMember);
  document.getElementById("resetTodayButton").addEventListener("click", resetToday);
  document.getElementById("sheetStatusButton").addEventListener("click", loadSheetStatus);
  document.getElementById("sheetSetupButton").addEventListener("click", setupSheet);
  document.getElementById("sheetSyncButton").addEventListener("click", syncSheet);
  document.getElementById("sheetRebuildButton").addEventListener("click", rebuildSheet);
  document.getElementById("sheetResetRebuildButton").addEventListener("click", resetSheetRecordsAndRebuild);
  document.getElementById("loadPrizesButton").addEventListener("click", loadPrizes);
  document.getElementById("prizeList").addEventListener("click", handlePrizeListClick);
  document.getElementById("loadWritebackFailuresButton").addEventListener("click", loadWritebackFailures);
  document.getElementById("loadOperationLogsButton").addEventListener("click", loadOperationLogs);
  document.getElementById("exportBackupButton").addEventListener("click", exportBackup);
  document.getElementById("downloadCsvTemplateButton").addEventListener("click", downloadCsvTemplate);
  document.getElementById("csvImportFile").addEventListener("change", handleCsvFileChange);
  document.getElementById("previewCsvImportButton").addEventListener("click", previewCsvImport);
  document.getElementById("confirmCsvImportButton").addEventListener("click", confirmCsvImport);
}

async function initAdminPage() {
  const ready = await initLiff();
  if (!ready || !liff.isLoggedIn()) {
    renderAdminLocked("請先使用 LINE 登入確認管理員身分。");
    return;
  }

  try {
    const profile = await liff.getProfile();
    adminState.profile = {
      lineUserId: profile.userId,
      displayName: profile.displayName,
      pictureUrl: profile.pictureUrl || "",
    };
    await syncMember(adminState.profile);
    const status = await fetchJson(`/api/member/admin-status?lineUserId=${encodeURIComponent(adminState.profile.lineUserId)}`);
    adminState.isAdmin = Boolean(status.ok && status.isAdmin);
    if (!adminState.isAdmin) {
      renderAdminLocked("此 LINE 帳號不是管理員，無法查看後台。", true);
      return;
    }
    renderAdminUnlocked();
    if (adminState.token) {
      await loadDashboard();
    } else {
      setAdminMessage("請輸入 Admin Token 後載入後台資料。");
    }
  } catch (error) {
    showError(error);
    renderAdminLocked("管理員驗證失敗，請重新登入。", true);
  }
}

async function initLiff() {
  const liffId = window.LINE_LOTTERY_CONFIG?.liffId || "";
  if (!liffId) {
    setAdminMessage("系統尚未設定 LIFF ID。", true);
    return false;
  }
  if (!window.liff) {
    setAdminMessage("LINE LIFF SDK 載入失敗。", true);
    return false;
  }
  if (adminState.liffReady) return true;

  await liff.init({ liffId, withLoginOnExternalBrowser: true });
  adminState.liffReady = true;
  return true;
}

function adminLogin() {
  if (!adminState.liffReady) {
    setAdminMessage("LINE 登入尚未準備完成，請稍後再試。", true);
    return;
  }
  liff.login({ redirectUri: window.location.href });
}

function adminLogout() {
  if (adminState.liffReady && liff.isLoggedIn()) {
    liff.logout();
  }
  adminState.profile = null;
  adminState.isAdmin = false;
  renderAdminLocked("已登出 LINE 管理員。");
}

function renderAdminLocked(message, isError = false) {
  document.getElementById("adminContent").hidden = true;
  document.getElementById("adminGate").hidden = false;
  document.getElementById("adminLoginButton").hidden = adminState.liffReady && liff.isLoggedIn();
  document.getElementById("adminLogoutButton").hidden = !(adminState.liffReady && liff.isLoggedIn());
  document.getElementById("adminUser").textContent = "尚未通過管理員驗證";
  setAdminMessage(message, isError);
}

function renderAdminUnlocked() {
  document.getElementById("adminContent").hidden = false;
  document.getElementById("adminGate").hidden = false;
  document.getElementById("adminLoginButton").hidden = true;
  document.getElementById("adminLogoutButton").hidden = false;
  document.getElementById("adminUser").textContent = `${adminState.profile.displayName} / ${adminState.profile.lineUserId}`;
  setAdminMessage("管理員驗證完成。");
}

async function loadDashboard() {
  await Promise.all([loadAdminUsers(), loadMembers(), loadPrizes()]);
}

function saveToken() {
  adminState.token = document.getElementById("adminToken").value.trim();
  localStorage.setItem("lineLotteryAdminToken", adminState.token);
  setAdminMessage("Token 已儲存。");
  if (adminState.isAdmin) {
    loadDashboard().catch(showError);
  }
}

function clearToken() {
  adminState.token = "";
  localStorage.removeItem("lineLotteryAdminToken");
  document.getElementById("adminToken").value = "";
  setAdminMessage("Token 已清除。");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.message || `HTTP ${response.status}`);
  }
  return data;
}

async function adminFetch(url, options = {}) {
  const token = adminState.token || document.getElementById("adminToken").value.trim();
  if (!token) {
    throw new Error("請先輸入 Admin Token。");
  }
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": token,
      "X-Admin-Line-User-Id": adminState.profile?.lineUserId || "",
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.message || `HTTP ${response.status}`);
  }
  return data;
}

async function adminRawFetch(url, options = {}) {
  const token = adminState.token || document.getElementById("adminToken").value.trim();
  if (!token) {
    throw new Error("請先輸入 Admin Token。");
  }
  const response = await fetch(url, {
    ...options,
    headers: {
      "X-Admin-Token": token,
      "X-Admin-Line-User-Id": adminState.profile?.lineUserId || "",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(await response.text() || `HTTP ${response.status}`);
  }
  return response;
}

async function adminJsonFetchAllowError(url, options = {}) {
  const token = adminState.token || document.getElementById("adminToken").value.trim();
  if (!token) {
    throw new Error("請先輸入 Admin Token。");
  }
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": token,
      "X-Admin-Line-User-Id": adminState.profile?.lineUserId || "",
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  return { response, data };
}

async function syncMember(profile) {
  await fetchJson("/api/member", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
}

async function loadAdminUsers() {
  try {
    const data = await adminFetch("/api/admin/admin-users");
    const list = document.getElementById("adminUserList");
    list.innerHTML = data.admins.map((admin) => `
      <div class="admin-row admin-user-row" data-line-user-id="${escapeHtml(admin.lineUserId)}">
        <strong>${escapeHtml(admin.displayName || admin.lineUserId)}</strong>
        <span>${escapeHtml(admin.lineUserId)}</span>
        <span>來源：${escapeHtml(admin.source === "env" ? "環境變數" : "後台設定")}</span>
        <div class="admin-actions">
          <button class="danger-button" data-action="delete-admin" type="button" ${admin.canDelete ? "" : "disabled"}>刪除管理員</button>
        </div>
      </div>
    `).join("") || `<div class="empty-cell">尚未設定管理員</div>`;
    setAdminMessage("管理員清單已更新。");
  } catch (error) {
    showError(error);
  }
}

async function addAdminUser() {
  const lineUserId = document.getElementById("newAdminLineUserId").value.trim();
  const note = document.getElementById("newAdminNote").value.trim();
  try {
    await adminFetch("/api/admin/admin-users", {
      method: "POST",
      body: JSON.stringify({ lineUserId, note }),
    });
    document.getElementById("newAdminLineUserId").value = "";
    document.getElementById("newAdminNote").value = "";
    setAdminMessage("管理員已新增。");
    await loadAdminUsers();
  } catch (error) {
    showError(error);
  }
}

async function handleAdminUserClick(event) {
  const button = event.target.closest("[data-action='delete-admin']");
  if (!button || button.disabled) return;
  const row = button.closest(".admin-user-row");
  const lineUserId = row?.dataset.lineUserId;
  if (!lineUserId) return;
  if (!window.confirm(`確定刪除管理員 ${lineUserId}？`)) return;

  try {
    await adminFetch(`/api/admin/admin-users/${encodeURIComponent(lineUserId)}`, { method: "DELETE" });
    setAdminMessage("管理員已刪除。");
    await loadAdminUsers();
  } catch (error) {
    showError(error);
  }
}

async function loadMembers() {
  try {
    const q = document.getElementById("memberSearch").value.trim();
    const data = await adminFetch(`/api/admin/members?limit=100&q=${encodeURIComponent(q)}`);
    const list = document.getElementById("memberList");
    adminState.members.clear();
    for (const member of data.members) {
      adminState.members.set(member.lineUserId, member);
    }
    list.innerHTML = data.members.map((member) => `
      <div class="member-row" data-line-user-id="${escapeHtml(member.lineUserId)}">
        ${member.pictureUrl
          ? `<img class="member-avatar" alt="" src="${escapeHtml(member.pictureUrl)}">`
          : `<div class="member-avatar member-avatar-placeholder">${escapeHtml((member.displayName || "?").slice(0, 1))}</div>`}
        <div class="member-main">
          <strong>${escapeHtml(member.displayName)}</strong>
          <span>${escapeHtml(member.lineUserId)}</span>
          <div class="member-stats">
            <span>今日 ${member.todayUsed}/${member.dailyLimit}</span>
            <span>剩餘 ${member.remaining}</span>
            <span>紀錄 ${member.lotteryRecordCount}</span>
            <span>中獎 ${member.wonRecordCount}</span>
          </div>
        </div>
        <button class="ghost-button compact-button" data-action="edit-member" type="button">修改次數</button>
      </div>
    `).join("") || `<div class="empty-cell">目前沒有會員資料</div>`;
    setAdminMessage("會員清單已更新。");
  } catch (error) {
    showError(error);
  }
}

function handleMemberListClick(event) {
  const button = event.target.closest("[data-action='edit-member']");
  if (!button) return;
  const row = button.closest(".member-row");
  const member = adminState.members.get(row?.dataset.lineUserId || "");
  if (!member) return;

  document.getElementById("memberLineUserId").value = member.lineUserId;
  document.getElementById("memberDailyLimit").value = member.dailyLimit ?? "";
  document.getElementById("memberBlocked").checked = member.isBlocked;
  document.getElementById("memberNote").value = member.note || "";
  setOutput("memberOutput", member);
  setAdminMessage(`已選取 ${member.displayName}。`);
}

async function loadMember() {
  const lineUserId = memberId();
  try {
    const data = await adminFetch(`/api/admin/members/${encodeURIComponent(lineUserId)}/spin-limit`);
    document.getElementById("memberDailyLimit").value = data.quota.dailyLimit;
    document.getElementById("memberBlocked").checked = data.quota.isBlocked;
    setOutput("memberOutput", data);
    setAdminMessage("會員抽獎設定已讀取。");
  } catch (error) {
    showError(error);
  }
}

async function saveMember() {
  const lineUserId = memberId();
  const dailyLimitValue = document.getElementById("memberDailyLimit").value;
  const payload = {
    dailyLimit: dailyLimitValue === "" ? null : Number(dailyLimitValue),
    isBlocked: document.getElementById("memberBlocked").checked,
    note: document.getElementById("memberNote").value.trim(),
  };

  try {
    const data = await adminFetch(`/api/admin/members/${encodeURIComponent(lineUserId)}/spin-limit`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    setOutput("memberOutput", data);
    setAdminMessage("會員抽獎次數已儲存。");
    await loadMembers();
  } catch (error) {
    showError(error);
  }
}

async function resetToday() {
  const lineUserId = memberId();
  if (!window.confirm(`確定重置 ${lineUserId} 今日抽獎次數？`)) return;

  try {
    const data = await adminFetch(`/api/admin/members/${encodeURIComponent(lineUserId)}/daily-spin/reset`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setOutput("memberOutput", data);
    setAdminMessage("今日抽獎次數已重置。");
    await loadMembers();
  } catch (error) {
    showError(error);
  }
}

async function loadSheetStatus() {
  try {
    const data = await adminFetch("/api/admin/google-sheet");
    setOutput("sheetOutput", data);
    setAdminMessage("Google Sheet 狀態已讀取。");
  } catch (error) {
    showError(error);
  }
}

async function setupSheet() {
  try {
    const data = await adminFetch("/api/admin/google-sheet/setup", { method: "POST", body: JSON.stringify({}) });
    setOutput("sheetOutput", data);
    setAdminMessage("Google Sheet 表頭已建立或更新。");
  } catch (error) {
    showError(error);
  }
}

async function syncSheet() {
  try {
    const data = await adminFetch("/api/admin/google-sheet/sync", { method: "POST", body: JSON.stringify({}) });
    setOutput("sheetOutput", data);
    setAdminMessage("Google Sheet 已同步。");
    await loadPrizes();
  } catch (error) {
    showError(error);
  }
}

async function rebuildSheet() {
  const confirmed = window.confirm("重建獎池會清空目前獎項、序號、抽獎紀錄與今日抽數，再由 Google Sheet 重建。確定要繼續？");
  if (!confirmed) return;

  try {
    const data = await adminFetch("/api/admin/google-sheet/rebuild", {
      method: "POST",
      body: JSON.stringify({ confirm: "REBUILD_LOTTERY_POOL" }),
    });
    setOutput("sheetOutput", data);
    setAdminMessage("獎池已由 Google Sheet 重建。");
    await Promise.all([loadMembers(), loadPrizes()]);
  } catch (error) {
    showError(error);
  }
}

async function resetSheetRecordsAndRebuild() {
  const firstConfirm = window.confirm("此操作會清空 Google Sheet 上的中獎登記欄位，並重建資料庫獎池。確定要繼續？");
  if (!firstConfirm) return;
  const secondConfirm = window.confirm("這是高風險操作。請再次確認要清空紀錄並重建。");
  if (!secondConfirm) return;

  try {
    const data = await adminFetch("/api/admin/google-sheet/reset-records-and-rebuild", {
      method: "POST",
      body: JSON.stringify({ confirm: "RESET_SHEET_RECORDS_AND_REBUILD" }),
    });
    setOutput("sheetOutput", data);
    setAdminMessage("Google Sheet 紀錄已清空，獎池已重建。");
    await Promise.all([loadMembers(), loadPrizes()]);
  } catch (error) {
    showError(error);
  }
}

async function loadWritebackFailures() {
  try {
    const data = await adminFetch("/api/admin/google-sheet/writeback-failures?limit=50");
    setOutput("operationOutput", data);
    setAdminMessage("Google Sheet 回寫失敗紀錄已讀取。");
  } catch (error) {
    showError(error);
  }
}

async function loadOperationLogs() {
  try {
    const data = await adminFetch("/api/admin/operation-logs?limit=100");
    setOutput("operationOutput", data);
    setAdminMessage("操作日誌已讀取。");
  } catch (error) {
    showError(error);
  }
}

async function exportBackup() {
  try {
    const data = await adminFetch("/api/admin/backup/export", {
      method: "POST",
      body: JSON.stringify({}),
    });
    setOutput("operationOutput", {
      ok: data.ok,
      exportedAt: data.exportedAt,
      databaseMode: data.databaseMode,
      tableCounts: countBackupTables(data),
    });
    downloadJson(data, `line-lottery-backup-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.json`);
    setAdminMessage("備份已匯出。");
  } catch (error) {
    showError(error);
  }
}

async function downloadCsvTemplate() {
  try {
    const response = await adminRawFetch("/api/admin/prizes/import-template");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "lottery_prize_import_sample.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setAdminMessage("CSV 範例已下載。");
  } catch (error) {
    showError(error);
  }
}

async function handleCsvFileChange(event) {
  const file = event.target.files?.[0];
  adminState.csvText = "";
  adminState.csvPreviewOk = false;
  document.getElementById("confirmCsvImportButton").disabled = true;

  if (!file) {
    document.getElementById("csvFileName").textContent = "尚未選擇 CSV 檔案";
    setOutput("csvImportOutput", {});
    return;
  }

  document.getElementById("csvFileName").textContent = `${file.name} / ${Math.round(file.size / 1024)} KB`;
  adminState.csvText = await file.text();
  setAdminMessage("CSV 檔案已載入，請先預覽 / 驗證。");
}

async function previewCsvImport() {
  if (!adminState.csvText) {
    setAdminMessage("請先選擇 CSV 檔案。", true);
    return;
  }

  try {
    const { response, data } = await adminJsonFetchAllowError("/api/admin/prizes/import-preview", {
      method: "POST",
      body: JSON.stringify({ csvText: adminState.csvText }),
    });
    adminState.csvPreviewOk = data.ok;
    document.getElementById("confirmCsvImportButton").disabled = !data.ok;
    setOutput("csvImportOutput", data.summary);
    setAdminMessage(
      data.ok ? formatImportSummary("CSV 驗證通過", data.summary) : formatImportSummary("CSV 驗證失敗", data.summary),
      !response.ok || !data.ok,
    );
  } catch (error) {
    adminState.csvPreviewOk = false;
    document.getElementById("confirmCsvImportButton").disabled = true;
    setOutput("csvImportOutput", { ok: false, message: error.message });
    showError(error);
  }
}

async function confirmCsvImport() {
  if (!adminState.csvText || !adminState.csvPreviewOk) {
    setAdminMessage("請先完成 CSV 預覽 / 驗證。", true);
    return;
  }
  if (!window.confirm("確認匯入這份 CSV？此操作不會清空抽獎紀錄，也不會重置已抽中序號。")) return;

  try {
    const data = await adminFetch("/api/admin/prizes/import", {
      method: "POST",
      body: JSON.stringify({ confirm: "IMPORT_PRIZE_CSV", csvText: adminState.csvText }),
    });
    setOutput("csvImportOutput", data.summary);
    setAdminMessage(formatImportSummary("匯入完成", data.summary));
    adminState.csvPreviewOk = false;
    document.getElementById("confirmCsvImportButton").disabled = true;
    await loadPrizes();
  } catch (error) {
    showError(error);
  }
}

function formatImportSummary(prefix, summary = {}) {
  return [
    prefix,
    `新增 ${summary.newPrizeCount || 0} 個獎項`,
    `更新 ${summary.updatePrizeCount || 0} 個獎項`,
    `新增 ${summary.newSerialCount || 0} 組序號`,
    `略過重複 ${summary.skippedExistingSerialCount || 0} 組`,
    `錯誤列 ${summary.errorRowCount || 0} 列`,
  ].join("，");
}

async function loadPrizes() {
  try {
    const data = await adminFetch("/api/admin/prizes");
    const list = document.getElementById("prizeList");
    list.innerHTML = data.prizes.map(renderPrizeRow).join("") || `<div class="empty-cell">目前沒有獎項</div>`;
    setAdminMessage("獎項狀態已更新。");
  } catch (error) {
    showError(error);
  }
}

function renderPrizeRow(prize) {
  const remainingText = prize.remainingQuantity === null || prize.remainingQuantity === undefined ? "不限" : prize.remainingQuantity;
  const totalText = prize.totalQuantity === null || prize.totalQuantity === undefined ? "未設定" : prize.totalQuantity;
  const isThanksPrize = ["NONE", "THANKS"].includes(String(prize.code || "").toUpperCase()) || String(prize.name || "").includes("銘謝");
  const transferText = isThanksPrize && prize.transferredWeightToNone > 0
    ? `<span>承接抽光權重 ${formatNumber(prize.transferredWeightToNone)}</span>`
    : "";

  return `
    <div class="admin-row admin-prize-row" data-prize-id="${prize.id}">
      <div class="prize-heading">
        <div>
          <strong>${escapeHtml(prize.name)}</strong>
          <span>${escapeHtml(prize.code)} / ${escapeHtml(prize.shortLabel || prize.name)}</span>
        </div>
        <span class="status-pill ${prize.isActive ? "is-active" : "is-muted"}">${prize.isActive ? "啟用" : "停用"}</span>
      </div>
      <div class="prize-metrics">
        <span>總量 <strong>${escapeHtml(totalText)}</strong></span>
        <span>已抽 <strong>${prize.drawnCount}</strong></span>
        <span>剩餘 <strong>${escapeHtml(remainingText)}</strong></span>
        <span>可用序號 <strong>${prize.availableSerials}</strong></span>
        <span>權重 <strong>${formatNumber(prize.weight)}</strong></span>
        <span>目前機率 <strong>${formatNumber(prize.probabilityPercent)}%</strong></span>
        ${transferText}
      </div>
      <div class="admin-prize-controls">
        <label>
          獎項名稱
          <input class="admin-input" data-field="name" type="text" maxlength="120" value="${escapeHtml(prize.name)}">
        </label>
        <label>
          轉盤文字
          <input class="admin-input" data-field="shortLabel" type="text" maxlength="24" value="${escapeHtml(prize.shortLabel || prize.name)}">
        </label>
        <label>
          權重 / 機率
          <input class="admin-input" data-field="weight" type="number" min="0" step="0.01" value="${escapeHtml(prize.weight)}">
        </label>
        <label>
          設定總量
          <input class="admin-input" data-field="stock" type="number" min="0" step="1" value="${prize.stock ?? ""}">
        </label>
        <label class="admin-check">
          <input data-field="requiresSerial" type="checkbox" ${prize.requiresSerial ? "checked" : ""}>
          <span>需要序號</span>
        </label>
        <label class="admin-check">
          <input data-field="isActive" type="checkbox" ${prize.isActive ? "checked" : ""}>
          <span>啟用</span>
        </label>
        <button class="primary-button" data-action="save-prize" type="button">儲存獎項</button>
      </div>
      <div class="serial-add-panel">
        <label>
          新增序號
          <textarea class="admin-input serial-textarea" data-field="serialCodes" rows="3" placeholder="一行一組序號，也可以用逗號分隔"></textarea>
        </label>
        <button class="ghost-button" data-action="add-serials" type="button">新增序號</button>
      </div>
    </div>
  `;
}

async function handlePrizeListClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;

  const row = button.closest(".admin-prize-row");
  const prizeId = row?.dataset.prizeId;
  if (!prizeId) return;

  if (button.dataset.action === "save-prize") {
    await savePrize(row, prizeId);
  }
  if (button.dataset.action === "add-serials") {
    await addPrizeSerials(row, prizeId);
  }
}

async function savePrize(row, prizeId) {
  const weightValue = row.querySelector("[data-field='weight']").value;
  const stockValue = row.querySelector("[data-field='stock']").value;
  const payload = {
    name: row.querySelector("[data-field='name']").value.trim(),
    shortLabel: row.querySelector("[data-field='shortLabel']").value.trim(),
    weight: weightValue === "" ? 0 : Number(weightValue),
    stock: stockValue === "" ? null : Number(stockValue),
    requiresSerial: row.querySelector("[data-field='requiresSerial']").checked,
    isActive: row.querySelector("[data-field='isActive']").checked,
  };

  try {
    const data = await adminFetch(`/api/admin/prizes/${encodeURIComponent(prizeId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    setOutput("sheetOutput", data);
    setAdminMessage("獎項已儲存。");
    await loadPrizes();
  } catch (error) {
    showError(error);
  }
}

async function addPrizeSerials(row, prizeId) {
  const textarea = row.querySelector("[data-field='serialCodes']");
  const serialCodes = splitSerialCodes(textarea.value);
  if (serialCodes.length === 0) {
    setAdminMessage("請先輸入要新增的序號。", true);
    return;
  }

  try {
    const data = await adminFetch(`/api/admin/prizes/${encodeURIComponent(prizeId)}/serials`, {
      method: "POST",
      body: JSON.stringify({ serialCodes }),
    });
    textarea.value = "";
    setOutput("sheetOutput", data);
    setAdminMessage(`已新增 ${data.created.length} 組序號，略過 ${data.skipped.length} 組。`);
    await loadPrizes();
  } catch (error) {
    showError(error);
  }
}

function splitSerialCodes(value) {
  return String(value || "")
    .split(/[\n\r,，、\t ]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function countBackupTables(data) {
  const counts = {};
  for (const [tableName, rows] of Object.entries(data.tables || {})) {
    counts[tableName] = Array.isArray(rows) ? rows.length : 0;
  }
  return counts;
}

function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function memberId() {
  const value = document.getElementById("memberLineUserId").value.trim();
  if (!value) {
    throw new Error("請輸入 LINE userId。");
  }
  return value;
}

function setOutput(id, data) {
  document.getElementById(id).textContent = JSON.stringify(data, null, 2);
}

function setAdminMessage(text, isError = false) {
  const message = document.getElementById("adminMessage");
  message.textContent = text;
  message.classList.toggle("is-error", isError);
}

function showError(error) {
  console.error(error);
  setAdminMessage(error.message || "操作失敗", true);
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return value ?? "-";
  return number.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
