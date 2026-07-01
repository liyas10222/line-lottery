const DEFAULT_SEGMENTS = [
  { code: "COUPON30", name: "30元折價券", shortLabel: "30元" },
  { code: "COUPON170", name: "170元折價券", shortLabel: "170元" },
  { code: "COUPON990", name: "990元折價券", shortLabel: "990元" },
  { code: "COUPON1690", name: "1690元折價券", shortLabel: "1690元" },
  { code: "COUPON3280", name: "3280元折價券", shortLabel: "3280元" },
  { code: "NONE", name: "銘謝惠顧", shortLabel: "銘謝" },
];

const WHEEL_COLORS = ["#43c6d8", "#ffd15a", "#ff6f73", "#8a5cf6", "#718198", "#26d968", "#2fbf71"];

let segments = [...DEFAULT_SEGMENTS];

const state = {
  profile: null,
  canSpin: false,
  spinning: false,
};

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (page === "lottery") {
    initLotteryPage();
  }
  if (page === "history") {
    initHistoryPage();
  }
});

async function initLotteryPage() {
  bindLotteryButtons();
  await loadPrizeSegments();
  renderWheel();

  const profile = await loadLineProfile();
  if (!profile) return;

  state.profile = profile;
  sessionStorage.setItem("lineLotteryProfile", JSON.stringify(profile));
  renderProfile(profile);

  try {
    await syncMember(profile);
    await refreshStatus();
    await renderAdminEntry(profile.lineUserId);
  } catch (error) {
    console.error(error);
    setMessage("會員資料同步失敗，請稍後再試。", true);
  }
}

async function initHistoryPage() {
  document.getElementById("backButton").addEventListener("click", () => {
    window.location.href = "/lottery";
  });

  const profile = await loadLineProfile({ allowStoredProfile: true });
  if (!profile) return;

  state.profile = profile;
  document.getElementById("historyUser").textContent = `${profile.displayName} / ${profile.lineUserId}`;
  await loadHistory(profile.lineUserId);
}

function bindLotteryButtons() {
  document.getElementById("spinButton").addEventListener("click", spinLottery);
  document.getElementById("historyButton").addEventListener("click", () => {
    window.location.href = "/history";
  });
  document.getElementById("adminButton").addEventListener("click", () => {
    window.location.href = "/admin";
  });
}

async function renderAdminEntry(lineUserId) {
  const response = await fetch(`/api/member/admin-status?lineUserId=${encodeURIComponent(lineUserId)}`);
  const data = await response.json();
  const adminButton = document.getElementById("adminButton");
  if (data.ok && data.isAdmin) {
    adminButton.hidden = false;
  }
}

async function loadPrizeSegments() {
  try {
    const response = await fetch("/api/lottery/prizes");
    const data = await response.json();
    if (data.ok && Array.isArray(data.prizes) && data.prizes.length >= 2) {
      segments = data.prizes
        .filter((prize) => prize.isActive)
        .map((prize) => ({
          code: prize.code,
          name: prize.name,
          shortLabel: prize.shortLabel || prize.name,
        }));
    }
  } catch (error) {
    console.warn("Unable to load prize segments", error);
  }
}

async function loadLineProfile(options = {}) {
  if (options.allowStoredProfile) {
    const stored = sessionStorage.getItem("lineLotteryProfile");
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (_error) {
        sessionStorage.removeItem("lineLotteryProfile");
      }
    }
  }

  const liffId = window.LINE_LOTTERY_CONFIG?.liffId || "";
  if (!liffId) {
    setMessage("系統尚未設定 LIFF ID，請確認 .env。", true);
    return null;
  }

  if (!window.liff) {
    setMessage("LINE 會員登入載入失敗，請重新整理頁面。", true);
    return null;
  }

  try {
    await liff.init({ liffId, withLoginOnExternalBrowser: true });
    if (!liff.isLoggedIn()) {
      liff.login();
      return null;
    }

    const profile = await liff.getProfile();
    return {
      lineUserId: profile.userId,
      displayName: profile.displayName,
      pictureUrl: profile.pictureUrl || "",
    };
  } catch (error) {
    console.error(error);
    setMessage("無法取得 LINE 會員資料，請確認 LIFF Endpoint 已指向目前網域。", true);
    return null;
  }
}

function renderProfile(profile) {
  const avatar = document.getElementById("avatar");
  const displayName = document.getElementById("displayName");
  const userId = document.getElementById("userId");

  displayName.textContent = profile.displayName;
  userId.textContent = `userId：${profile.lineUserId}`;

  if (profile.pictureUrl) {
    avatar.src = profile.pictureUrl;
    avatar.classList.remove("empty-avatar");
  } else {
    avatar.removeAttribute("src");
    avatar.classList.add("empty-avatar");
  }
}

async function syncMember(profile) {
  const response = await fetch("/api/member", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      lineUserId: profile.lineUserId,
      displayName: profile.displayName,
      pictureUrl: profile.pictureUrl,
    }),
  });
  const data = await response.json();
  if (!data.ok) {
    throw new Error(data.message || "會員同步失敗");
  }
}

async function refreshStatus() {
  const response = await fetch(`/api/lottery?lineUserId=${encodeURIComponent(state.profile.lineUserId)}`);
  const data = await response.json();
  if (!data.ok) {
    setMessage(data.message || "無法讀取抽獎狀態", true);
    return;
  }

  state.canSpin = data.canSpin;
  document.getElementById("remaining").textContent = String(data.remaining);
  updateSpinButton();

  if (data.isBlocked) {
    setMessage("此會員目前無法抽獎。", true);
  } else {
    setMessage(data.canSpin ? `今天還可以抽 ${data.remaining} 次。` : "今日已抽過。", !data.canSpin);
  }
}

function updateSpinButton() {
  const button = document.getElementById("spinButton");
  button.disabled = !state.canSpin || state.spinning;
  button.textContent = state.spinning ? "抽獎中..." : state.canSpin ? "開始抽獎" : "今日已抽過";
}

async function spinLottery() {
  if (!state.profile || !state.canSpin || state.spinning) return;

  state.spinning = true;
  updateSpinButton();
  setMessage("正在抽獎...");

  try {
    const response = await fetch("/api/lottery", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lineUserId: state.profile.lineUserId,
        displayName: state.profile.displayName,
      }),
    });
    const data = await response.json();

    if (!data.ok) {
      setMessage(data.message || "抽獎失敗", true);
      await refreshStatus();
      return;
    }

    await animateWheel(data.prize.code);
    renderResult(data.prize);
    await refreshStatus();
  } catch (error) {
    console.error(error);
    setMessage("連線失敗，請稍後再試。", true);
  } finally {
    state.spinning = false;
    updateSpinButton();
  }
}

function renderWheel() {
  const wheel = document.getElementById("wheel");
  if (!wheel) return;

  const size = 400;
  const center = size / 2;
  const radius = 188;
  const labelRadius = 126;
  const slice = 360 / segments.length;
  const labelFontSize = segments.length >= 10 ? 11 : segments.length >= 8 ? 12 : segments.length >= 7 ? 14 : 17;
  const paths = [];
  const labels = [];

  segments.forEach((segment, index) => {
    const start = -90 - slice / 2 + index * slice;
    const end = start + slice;
    const labelAngle = start + slice / 2;
    const color = WHEEL_COLORS[index % WHEEL_COLORS.length];
    const textColor = index === 1 || index === 2 ? "#171b22" : "#ffffff";

    paths.push(`<path d="${sectorPath(center, center, radius, start, end)}" fill="${color}" stroke="rgba(255,255,255,.22)" stroke-width="2"></path>`);

    const labelPoint = polarToCartesian(center, center, labelRadius, labelAngle);
    labels.push(`
      <text
        x="${labelPoint.x}"
        y="${labelPoint.y}"
        fill="${textColor}"
        font-size="${labelFontSize}"
        font-weight="800"
        text-anchor="middle"
        dominant-baseline="middle"
      >${escapeHtml(segment.shortLabel)}</text>
    `);
  });

  wheel.innerHTML = `
    <svg class="wheel-svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="抽獎轉盤">
      <g>${paths.join("")}</g>
      <circle cx="${center}" cy="${center}" r="62" fill="#111a26" stroke="rgba(255,255,255,.18)" stroke-width="8"></circle>
      <g class="wheel-labels">${labels.join("")}</g>
    </svg>
  `;
}

function sectorPath(cx, cy, radius, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, radius, startAngle);
  const end = polarToCartesian(cx, cy, radius, endAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? 0 : 1;

  return [
    `M ${cx} ${cy}`,
    `L ${start.x} ${start.y}`,
    `A ${radius} ${radius} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`,
    "Z",
  ].join(" ");
}

function polarToCartesian(cx, cy, radius, angleInDegrees) {
  const angleInRadians = (angleInDegrees * Math.PI) / 180;
  return {
    x: cx + radius * Math.cos(angleInRadians),
    y: cy + radius * Math.sin(angleInRadians),
  };
}

function animateWheel(prizeCode) {
  const wheel = document.getElementById("wheel");
  const segmentIndex = Math.max(0, segments.findIndex((segment) => segment.code === prizeCode));
  const segmentSize = 360 / segments.length;
  const segmentCenter = -90 + segmentIndex * segmentSize;
  const finalRotation = 360 * 6 + (-90 - segmentCenter);

  wheel.style.transition = "none";
  wheel.style.transform = "rotate(0deg)";
  wheel.offsetHeight;

  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      wheel.style.transition = "transform 3.4s cubic-bezier(.12,.68,.12,1)";
      wheel.style.transform = `rotate(${finalRotation}deg)`;
      window.setTimeout(resolve, 3500);
    });
  });
}

function renderResult(prize) {
  const result = document.getElementById("result");
  const statusText = prize.code === "NONE"
    ? "再接再厲"
    : prize.serialCode
      ? `序號：${prize.serialCode}`
      : `獎品代碼：${prize.code}`;

  result.innerHTML = `<span>${escapeHtml(statusText)}</span><strong>${escapeHtml(prize.name)}</strong>`;
  setMessage(prize.code === "NONE" ? "這次沒有中獎，明天再來。" : "恭喜中獎。");
}

async function loadHistory(lineUserId) {
  const response = await fetch(`/api/history?lineUserId=${encodeURIComponent(lineUserId)}`);
  const data = await response.json();
  const body = document.getElementById("historyBody");

  if (!data.ok) {
    setHistoryMessage(data.message || "讀取紀錄失敗", true);
    return;
  }

  body.innerHTML = "";
  if (data.records.length === 0) {
    body.innerHTML = `<tr><td colspan="3" class="empty-cell">尚無抽獎紀錄</td></tr>`;
    setHistoryMessage("尚無抽獎紀錄。");
    return;
  }

  for (const record of data.records) {
    const codeText = record.serialCode || record.prizeCode || "";
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <strong>${escapeHtml(record.prizeName)}</strong>
        <span>${escapeHtml(codeText)}</span>
      </td>
      <td>${escapeHtml(formatDateTime(record.createdAt))}</td>
      <td>${escapeHtml(formatStatus(record.status))}</td>
    `;
    body.appendChild(row);
  }
  setHistoryMessage(`共 ${data.records.length} 筆紀錄。`);
}

function setMessage(text, isError = false) {
  const message = document.getElementById("message");
  if (!message) return;
  message.textContent = text;
  message.classList.toggle("is-error", isError);
}

function setHistoryMessage(text, isError = false) {
  const message = document.getElementById("historyMessage");
  if (!message) return;
  message.textContent = text;
  message.classList.toggle("is-error", isError);
}

function formatStatus(status) {
  if (status === "won") return "已中獎";
  if (status === "not_won") return "未中獎";
  return status || "-";
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
