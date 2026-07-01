const DEFAULT_SEGMENTS = [
  { code: "COUPON30", name: "30折價券", shortLabel: "30券" },
  { code: "COUPON170", name: "170折價券", shortLabel: "170券" },
  { code: "COUPON990", name: "990折價券", shortLabel: "990券" },
  { code: "COUPON1690", name: "1690折價券", shortLabel: "1690券" },
  { code: "COUPON3280", name: "3280折價券", shortLabel: "3280券" },
  { code: "IPHONE16", name: "iPhone 16", shortLabel: "iPhone" },
  { code: "NONE", name: "銘謝惠顧", shortLabel: "銘謝" },
];

const WHEEL_COLORS = ["#4ec9d8", "#f6c85f", "#ff6b70", "#8f63f4", "#7d8da3", "#29d76b", "#39b782", "#f08c4a"];
const REDEEM_NOTICE = "請截圖保存中獎序號，並將中獎序號提供給鮭魚代儲官方 LINE 兌換獎品喔！";

let segments = [...DEFAULT_SEGMENTS];

const state = {
  liffReady: false,
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

  const liffReady = await initLiff();
  if (!liffReady || !liff.isLoggedIn()) {
    renderLoggedOut();
    return;
  }

  await completeLogin();
}

async function initHistoryPage() {
  document.getElementById("backButton").addEventListener("click", () => {
    window.location.href = "/lottery";
  });

  await initLiff();
  const profile = await loadLineProfile({ allowStoredProfile: true });
  if (!profile) {
    setHistoryMessage("請先回到抽獎頁完成 LINE 登入。", true);
    return;
  }

  state.profile = profile;
  document.getElementById("historyUser").textContent = `${profile.displayName} / ${profile.lineUserId}`;
  await loadHistory(profile.lineUserId);
}

function bindLotteryButtons() {
  document.getElementById("loginButton").addEventListener("click", loginWithLine);
  document.getElementById("logoutButton").addEventListener("click", logoutLine);
  document.getElementById("spinButton").addEventListener("click", spinLottery);
  document.getElementById("historyButton").addEventListener("click", () => {
    window.location.href = "/history";
  });
  document.getElementById("adminButton").addEventListener("click", () => {
    window.location.href = "/admin";
  });
}

async function initLiff() {
  const liffId = window.LINE_LOTTERY_CONFIG?.liffId || "";
  if (!liffId) {
    setMessage("系統尚未設定 LIFF ID，請確認環境變數。", true);
    return false;
  }
  if (!window.liff) {
    setMessage("LINE LIFF SDK 載入失敗，請重新整理頁面。", true);
    return false;
  }
  if (state.liffReady) return true;

  try {
    await liff.init({ liffId, withLoginOnExternalBrowser: true });
    state.liffReady = true;
    return true;
  } catch (error) {
    console.error(error);
    setMessage("無法初始化 LINE 登入，請確認 LIFF Endpoint 設定。", true);
    return false;
  }
}

function loginWithLine() {
  if (!state.liffReady) {
    setMessage("LINE 登入尚未準備完成，請稍後再試。", true);
    return;
  }
  liff.login({ redirectUri: window.location.href });
}

function logoutLine() {
  if (state.liffReady && liff.isLoggedIn()) {
    liff.logout();
  }
  sessionStorage.removeItem("lineLotteryProfile");
  state.profile = null;
  state.canSpin = false;
  renderLoggedOut();
  setMessage("已登出 LINE。");
}

async function completeLogin() {
  const profile = await loadLineProfile();
  if (!profile) {
    renderLoggedOut();
    return;
  }

  state.profile = profile;
  sessionStorage.setItem("lineLotteryProfile", JSON.stringify(profile));
  renderLoggedIn(profile);

  try {
    await syncMember(profile);
    await refreshStatus();
    await renderAdminEntry(profile.lineUserId);
  } catch (error) {
    console.error(error);
    setMessage("會員資料同步失敗，請重新整理後再試。", true);
  }
}

function renderLoggedOut() {
  const loggedOut = document.getElementById("authLoggedOut");
  const loggedIn = document.getElementById("authLoggedIn");
  loggedOut.hidden = false;
  loggedIn.hidden = true;
  document.getElementById("adminButton").hidden = true;
  document.getElementById("remaining").textContent = "-";
  state.canSpin = false;
  updateSpinButton();
  setMessage("請先使用 LINE 登入後再開始抽獎。");
}

function renderLoggedIn(profile) {
  document.getElementById("authLoggedOut").hidden = true;
  document.getElementById("authLoggedIn").hidden = false;
  renderProfile(profile);
}

async function renderAdminEntry(lineUserId) {
  try {
    const response = await fetch(`/api/member/admin-status?lineUserId=${encodeURIComponent(lineUserId)}`);
    const data = await response.json();
    document.getElementById("adminButton").hidden = !(data.ok && data.isAdmin);
  } catch (error) {
    console.warn("Unable to check admin status", error);
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

  if (!state.liffReady || !liff.isLoggedIn()) return null;

  try {
    const profile = await liff.getProfile();
    return {
      lineUserId: profile.userId,
      displayName: profile.displayName,
      pictureUrl: profile.pictureUrl || "",
    };
  } catch (error) {
    console.error(error);
    setMessage("無法取得 LINE 會員資料，請重新登入。", true);
    return null;
  }
}

function renderProfile(profile) {
  const avatar = document.getElementById("avatar");
  document.getElementById("displayName").textContent = profile.displayName;
  document.getElementById("userId").textContent = `userId：${profile.lineUserId}`;

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
  if (!state.profile) return;

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
  button.disabled = !state.profile || !state.canSpin || state.spinning;
  button.textContent = state.spinning ? "抽獎中..." : state.canSpin ? "開始抽獎" : "今日已抽過";
  if (!state.profile) {
    button.textContent = "請先登入";
  }
}

async function spinLottery() {
  if (!state.profile || !state.canSpin || state.spinning) return;

  state.spinning = true;
  updateSpinButton();
  setMessage("轉盤轉動中...");

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
    setMessage("系統忙碌中，請稍後再試。", true);
  } finally {
    state.spinning = false;
    updateSpinButton();
  }
}

function renderWheel() {
  const wheel = document.getElementById("wheel");
  if (!wheel) return;

  const size = 420;
  const center = size / 2;
  const radius = 198;
  const labelRadius = segments.length >= 8 ? 130 : 136;
  const slice = 360 / segments.length;
  const labelFontSize = segments.length >= 10 ? 11 : segments.length >= 8 ? 12 : 14;
  const paths = [];
  const labels = [];

  segments.forEach((segment, index) => {
    const start = -90 - slice / 2 + index * slice;
    const end = start + slice;
    const labelAngle = start + slice / 2;
    const color = WHEEL_COLORS[index % WHEEL_COLORS.length];
    const textColor = index % 3 === 1 ? "#121820" : "#ffffff";

    paths.push(`<path d="${sectorPath(center, center, radius, start, end)}" fill="${color}" stroke="rgba(255,255,255,.26)" stroke-width="2"></path>`);

    const labelPoint = polarToCartesian(center, center, labelRadius, labelAngle);
    const lines = labelLines(segment.shortLabel || segment.name);
    labels.push(`
      <text
        x="${labelPoint.x}"
        y="${labelPoint.y - ((lines.length - 1) * labelFontSize) / 2}"
        fill="${textColor}"
        font-size="${labelFontSize}"
        font-weight="800"
        text-anchor="middle"
        dominant-baseline="middle"
      >${lines.map((line, lineIndex) => `<tspan x="${labelPoint.x}" dy="${lineIndex === 0 ? 0 : labelFontSize + 2}">${escapeHtml(line)}</tspan>`).join("")}</text>
    `);
  });

  wheel.innerHTML = `
    <svg class="wheel-svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="抽獎轉盤">
      <g>${paths.join("")}</g>
      <g class="wheel-labels">${labels.join("")}</g>
      <circle cx="${center}" cy="${center}" r="58" fill="#111a26" stroke="rgba(255,255,255,.2)" stroke-width="8"></circle>
    </svg>
  `;
}

function labelLines(value) {
  const text = String(value || "").trim();
  if (text.length <= 5) return [text];
  if (text.length <= 9) return [text.slice(0, 4), text.slice(4)];
  return [text.slice(0, 4), text.slice(4, 9), text.slice(9, 14)];
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
  const finalRotation = 360 * 7 + (-90 - segmentCenter);

  wheel.style.transition = "none";
  wheel.style.transform = "rotate(0deg)";
  wheel.offsetHeight;

  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      wheel.style.transition = "transform 3.6s cubic-bezier(.12,.72,.1,1)";
      wheel.style.transform = `rotate(${finalRotation}deg)`;
      window.setTimeout(resolve, 3700);
    });
  });
}

function renderResult(prize) {
  const result = document.getElementById("result");
  const isNone = prize.code === "NONE";
  const codeText = prize.serialCode || prize.code || "";
  const statusText = isNone ? "銘謝惠顧" : prize.serialCode ? `中獎序號：${prize.serialCode}` : `兌換代碼：${codeText}`;
  const notice = isNone ? "" : `<p class="redeem-notice">${escapeHtml(REDEEM_NOTICE)}</p>`;

  result.innerHTML = `
    <span>${escapeHtml(statusText)}</span>
    <strong>${escapeHtml(prize.name)}</strong>
    ${notice}
  `;
  setMessage(isNone ? "這次沒有中獎，明天再來試試。" : "恭喜中獎，請保存中獎序號。");
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
    body.innerHTML = `<tr><td colspan="3" class="empty-cell">目前沒有中獎紀錄</td></tr>`;
    setHistoryMessage("目前沒有中獎紀錄。");
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
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
