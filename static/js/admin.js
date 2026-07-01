const adminState = {
  token: localStorage.getItem("lineLotteryAdminToken") || "",
};

document.addEventListener("DOMContentLoaded", () => {
  bindAdmin();
  document.getElementById("adminToken").value = adminState.token;

  const defaultUserId = window.ADMIN_DEFAULT_USER_IDS?.[0] || "";
  document.getElementById("memberLineUserId").value = defaultUserId;
});

function bindAdmin() {
  document.getElementById("adminBackButton").addEventListener("click", () => {
    window.location.href = "/lottery";
  });
  document.getElementById("saveTokenButton").addEventListener("click", saveToken);
  document.getElementById("clearTokenButton").addEventListener("click", clearToken);
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
}

function saveToken() {
  adminState.token = document.getElementById("adminToken").value.trim();
  localStorage.setItem("lineLotteryAdminToken", adminState.token);
  setAdminMessage("Token 已儲存。");
}

function clearToken() {
  adminState.token = "";
  localStorage.removeItem("lineLotteryAdminToken");
  document.getElementById("adminToken").value = "";
  setAdminMessage("Token 已清除。");
}

async function adminFetch(url, options = {}) {
  const token = adminState.token || document.getElementById("adminToken").value.trim();
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": token,
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.message || `HTTP ${response.status}`);
  }
  return data;
}

async function loadMember() {
  const lineUserId = memberId();
  try {
    const data = await adminFetch(`/api/admin/members/${encodeURIComponent(lineUserId)}/spin-limit`);
    document.getElementById("memberDailyLimit").value = data.quota.dailyLimit;
    document.getElementById("memberBlocked").checked = data.quota.isBlocked;
    setOutput("memberOutput", data);
    setAdminMessage("會員設定已讀取。");
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
    setAdminMessage("會員抽獎次數設定已儲存。");
  } catch (error) {
    showError(error);
  }
}

async function resetToday() {
  const lineUserId = memberId();
  try {
    const data = await adminFetch(`/api/admin/members/${encodeURIComponent(lineUserId)}/daily-spin/reset`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setOutput("memberOutput", data);
    setAdminMessage("今日抽獎次數已重製。");
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
    setAdminMessage("轉盤分頁表頭已更新。");
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
  const confirmed = window.confirm(
    "這會清空抽獎紀錄、今日抽獎次數、獎項與序號，然後從 Google Sheet 重建獎池。確定要繼續？"
  );
  if (!confirmed) return;

  try {
    const data = await adminFetch("/api/admin/google-sheet/rebuild", {
      method: "POST",
      body: JSON.stringify({ confirm: "REBUILD_LOTTERY_POOL" }),
    });
    setOutput("sheetOutput", data);
    setAdminMessage("獎池已從 Google Sheet 重建。");
    await loadPrizes();
  } catch (error) {
    showError(error);
  }
}

async function resetSheetRecordsAndRebuild() {
  const firstConfirm = window.confirm(
    "這會清空 Google Sheet 上所有序號列的抽中狀態、LINE ID、抽獎紀錄 ID、抽中時間，並重建 SQLite 獎池。確定要繼續？"
  );
  if (!firstConfirm) return;

  const secondConfirm = window.confirm("再次確認：這個動作會改寫試算表紀錄欄位，正式活動中請勿誤按。");
  if (!secondConfirm) return;

  try {
    const data = await adminFetch("/api/admin/google-sheet/reset-records-and-rebuild", {
      method: "POST",
      body: JSON.stringify({ confirm: "RESET_SHEET_RECORDS_AND_REBUILD" }),
    });
    setOutput("sheetOutput", data);
    setAdminMessage("試算表紀錄已清空，獎池已重建。");
    await loadPrizes();
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
    setAdminMessage("操作紀錄已讀取。");
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
    setOutput("operationOutput", { ok: data.ok, exportedAt: data.exportedAt, databaseMode: data.databaseMode });
    downloadJson(data, `line-lottery-backup-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.json`);
    setAdminMessage("備份已匯出。");
  } catch (error) {
    showError(error);
  }
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

async function loadPrizes() {
  try {
    const data = await adminFetch("/api/admin/prizes");
    const list = document.getElementById("prizeList");
    list.innerHTML = data.prizes.map((prize) => `
      <div class="admin-row admin-prize-row" data-prize-id="${prize.id}">
        <strong>${escapeHtml(prize.name)}</strong>
        <span>${escapeHtml(prize.code)} / 目前機率 ${prize.probabilityPercent}% / 可用序號 ${prize.availableSerials}</span>
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
            權重
            <input class="admin-input" data-field="weight" type="number" min="0" step="0.01" value="${escapeHtml(prize.weight)}">
          </label>
          <label>
            庫存
            <input class="admin-input" data-field="stock" type="number" min="0" step="1" value="${prize.stock ?? ""}">
          </label>
          <label class="admin-check">
            <input data-field="isActive" type="checkbox" ${prize.isActive ? "checked" : ""}>
            <span>啟用</span>
          </label>
          <button class="ghost-button" data-action="save-prize" type="button">儲存獎項</button>
        </div>
      </div>
    `).join("");
    setAdminMessage("獎項狀態已更新。");
  } catch (error) {
    showError(error);
  }
}

async function handlePrizeListClick(event) {
  const button = event.target.closest("[data-action='save-prize']");
  if (!button) return;

  const row = button.closest(".admin-prize-row");
  const prizeId = row?.dataset.prizeId;
  if (!prizeId) return;

  const weightValue = row.querySelector("[data-field='weight']").value;
  const stockValue = row.querySelector("[data-field='stock']").value;
  const nameValue = row.querySelector("[data-field='name']").value.trim();
  const shortLabelValue = row.querySelector("[data-field='shortLabel']").value.trim();
  const payload = {
    name: nameValue,
    shortLabel: shortLabelValue,
    weight: weightValue === "" ? 0 : Number(weightValue),
    stock: stockValue === "" ? null : Number(stockValue),
    isActive: row.querySelector("[data-field='isActive']").checked,
  };

  try {
    const data = await adminFetch(`/api/admin/prizes/${encodeURIComponent(prizeId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    setOutput("sheetOutput", data);
    setAdminMessage("獎項設定已儲存。");
    await loadPrizes();
  } catch (error) {
    showError(error);
  }
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
