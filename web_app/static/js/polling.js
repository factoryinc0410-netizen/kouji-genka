/**
 * polling.js
 *
 * 2-step flow:
 *   Step 1: Upload Excel -> extract data -> show confirmation table
 *   Step 2: Confirm (with henkou_kaisuu) -> queue job -> poll -> download
 */
(function () {
  "use strict";

  // ── DOM ──────────────────────────────────────────────────
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");
  const fileSelectBtn = document.getElementById("file-select-btn");
  const uploadForm = document.getElementById("upload-form");
  const selectedFileInfo = document.getElementById("selected-file-info");
  const selectedFileName = document.getElementById("selected-file-name");
  const uploadBtn = document.getElementById("upload-btn");
  const removeFileBtn = document.getElementById("remove-file-btn");

  const uploadAlert = document.getElementById("upload-alert");

  const confirmSection = document.getElementById("confirm-section");
  const confirmTableBody = document.getElementById("confirm-table-body");
  const confirmGenerateBtn = document.getElementById("confirm-generate-btn");
  const confirmCancelBtn = document.getElementById("confirm-cancel-btn");

  const progressSection = document.getElementById("progress-section");
  const progressBar = document.getElementById("progress-bar");
  const progressStatus = document.getElementById("progress-status");
  const progressDetail = document.getElementById("progress-detail");

  const resultSection = document.getElementById("result-section");
  const resultContent = document.getElementById("result-content");

  let currentFile = null;
  let pollingTimer = null;
  let pendingJobId = null;  // confirm 待ちの job_id

  // ── File Select ─────────────────────────────────────────
  fileSelectBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) setFile(e.target.files[0]);
  });

  // ── Drag & Drop ─────────────────────────────────────────
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length > 0) setFile(e.dataTransfer.files[0]);
  });

  // ── File validation ─────────────────────────────────────
  function setFile(file) {
    const ext = file.name.split(".").pop().toLowerCase();
    if (!["xlsx", "xls"].includes(ext)) {
      showAlert("danger", "対応していないファイル形式です。.xlsx または .xls ファイルを選択してください。");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      showAlert("danger", "ファイルサイズが上限（50MB）を超えています。");
      return;
    }
    currentFile = file;
    const sizeMB = (file.size / 1024 / 1024).toFixed(1);
    selectedFileName.textContent = `${file.name}（${sizeMB} MB）`;
    selectedFileInfo.classList.remove("d-none");
    uploadBtn.disabled = false;
    hideAlert();
  }

  removeFileBtn.addEventListener("click", clearFile);

  function clearFile() {
    currentFile = null;
    fileInput.value = "";
    selectedFileInfo.classList.add("d-none");
    uploadBtn.disabled = true;
  }

  // ══════════════════════════════════════════════════════════
  //  Step 1: Upload → Extract → Show Confirmation Table
  // ══════════════════════════════════════════════════════════
  uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentFile) return;

    hideAlert();
    confirmSection.classList.add("d-none");
    resultSection.classList.add("d-none");
    progressSection.classList.add("d-none");
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>アップロード中...';

    const formData = new FormData();
    formData.append("file", currentFile);

    try {
      const res = await fetch("/orders/upload", { method: "POST", body: formData });
      const data = await res.json();

      if (!data.ok) {
        showAlert("danger", data.error || "アップロードに失敗しました。");
        resetUploadBtn();
        return;
      }

      if (data.duplicate_warning) {
        showAlert("warning", data.duplicate_warning);
      }

      clearFile();
      resetUploadBtn();

      // vendors が返ってきたら確認画面を表示
      if (data.vendors && data.vendors.length > 0) {
        showConfirmTable(data.job_id, data.vendors, data.filename);
      } else {
        // 抽出失敗時はそのまま confirm して即実行
        await confirmJob(data.job_id, []);
        startPolling(data.job_id, data.filename);
      }

    } catch (err) {
      showAlert("danger", "通信エラーが発生しました。ネットワーク接続を確認してください。");
      resetUploadBtn();
    }
  });

  function resetUploadBtn() {
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<i class="bi bi-cloud-arrow-up me-1"></i>アップロード';
  }

  // ══════════════════════════════════════════════════════════
  //  Confirmation Table
  // ══════════════════════════════════════════════════════════
  function showConfirmTable(jobId, vendors, filename) {
    pendingJobId = jobId;
    confirmTableBody.innerHTML = "";

    vendors.forEach((v, i) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(v.vendor_company)}</td>
        <td>${escapeHtml(v.contract_date || "")}</td>
        <td class="text-end">${formatNumber(v.kingaku_ukeoi)}</td>
        <td class="text-end">${formatNumber(v.kingaku_koji)}</td>
        <td class="text-end">${formatNumber(v.kingaku_zei)}</td>
        <td>${escapeHtml(v.kouki_start)}</td>
        <td>${escapeHtml(v.kouki_end)}</td>
      `;
      confirmTableBody.appendChild(tr);
    });

    confirmSection.classList.remove("d-none");
    // scroll into view
    confirmSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── Generate Button ─────────────────────────────────────
  confirmGenerateBtn.addEventListener("click", async () => {
    if (!pendingJobId) return;

    const jobId = pendingJobId;

    // 自動取得になったため、空の配列を渡す
    const vendorsData = [];

    // disable button
    confirmGenerateBtn.disabled = true;
    confirmGenerateBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>処理を開始しています...';

    let confirmOk = false;
    try {
      await confirmJob(jobId, vendorsData);
      confirmOk = true;
    } catch (err) {
      showAlert("danger", "処理の開始に失敗しました: " + err.message);
    }

    // reset button regardless
    confirmGenerateBtn.disabled = false;
    confirmGenerateBtn.innerHTML = '<i class="bi bi-file-earmark-pdf me-1"></i>この内容でPDFを生成する';

    if (confirmOk) {
      // confirm succeeded — hide table, start polling
      pendingJobId = null;
      confirmSection.classList.add("d-none");
      startPolling(jobId, "");
    }
    // if confirmOk is false, keep the confirm table visible so user can retry
  });

  // ── Cancel Button ───────────────────────────────────────
  confirmCancelBtn.addEventListener("click", () => {
    confirmSection.classList.add("d-none");
    pendingJobId = null;
    showAlert("info", "キャンセルしました。再度アップロードしてください。");
  });

  // ── Confirm API call ────────────────────────────────────
  async function confirmJob(jobId, vendorsData) {
    const res = await fetch(`/orders/${jobId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vendors: vendorsData }),
    });
    const data = await res.json();
    if (!data.ok) {
      throw new Error(data.error || "確定処理に失敗しました");
    }
    return data;
  }

  // ══════════════════════════════════════════════════════════
  //  Step 2: Polling
  // ══════════════════════════════════════════════════════════
  function startPolling(jobId, filename) {
    progressSection.classList.remove("d-none");
    progressBar.style.width = "10%";
    progressBar.className = "progress-bar progress-bar-striped progress-bar-animated bg-primary";
    progressStatus.textContent = "アップロード完了 — 処理待ちキューに登録しました...";
    progressDetail.textContent = filename;

    let pollCount = 0;

    pollingTimer = setInterval(async () => {
      pollCount++;
      try {
        const res = await fetch(`/orders/${jobId}/status`);
        if (!res.ok) {
          clearInterval(pollingTimer);
          showProgressError("ステータスの取得に失敗しました。");
          return;
        }
        const job = await res.json();
        updateProgress(job, jobId);

        if (job.status === "completed" || job.status === "error") {
          clearInterval(pollingTimer);
          pollingTimer = null;
          refreshJobHistory();
        }
      } catch {
        if (pollCount > 60) {
          clearInterval(pollingTimer);
          showProgressError("タイムアウト: サーバーからの応答がありません。ページを更新してください。");
        }
      }
    }, 3000);
  }

  function updateProgress(job, jobId) {
    if (job.status === "draft") {
      progressBar.style.width = "10%";
      progressBar.className = "progress-bar progress-bar-striped progress-bar-animated bg-warning";
      progressStatus.textContent = "確認待ち — データの確認中です...";

    } else if (job.status === "pending") {
      progressBar.style.width = "15%";
      progressBar.className = "progress-bar progress-bar-striped progress-bar-animated bg-secondary";
      progressStatus.textContent = "待機中 — 他の処理が完了するまでお待ちください...";

    } else if (job.status === "processing") {
      let pct = 30;
      if (job.total_vendors && job.success_count !== null) {
        pct = 30 + Math.round((job.success_count / job.total_vendors) * 60);
      } else {
        pct = 50;
      }
      progressBar.style.width = pct + "%";
      progressBar.className = "progress-bar progress-bar-striped progress-bar-animated bg-primary";
      const detail = job.total_vendors
        ? `${job.success_count || 0} / ${job.total_vendors} 社完了`
        : "";
      progressStatus.textContent = "PDF生成中...";
      progressDetail.textContent = detail || job.filename;

    } else if (job.status === "completed") {
      progressBar.style.width = "100%";
      progressBar.className = "progress-bar bg-success";
      progressStatus.textContent = "完了";
      progressDetail.textContent = "";
      showResult("success", job, jobId);

    } else if (job.status === "error") {
      progressBar.style.width = "100%";
      progressBar.className = "progress-bar bg-danger";
      progressStatus.textContent = "エラー";
      progressDetail.textContent = "";
      showResult("error", job, jobId);
    }
  }

  function showProgressError(msg) {
    progressBar.style.width = "100%";
    progressBar.className = "progress-bar bg-danger";
    progressStatus.textContent = msg;
  }

  // ── Result ──────────────────────────────────────────────
  function showResult(type, job, jobId) {
    resultSection.classList.remove("d-none");

    if (type === "success") {
      const vendorInfo = job.total_vendors
        ? `（${job.success_count} / ${job.total_vendors} 社成功）`
        : "";
      resultContent.innerHTML = `
        <div class="alert alert-success mb-3">
          <i class="bi bi-check-circle-fill me-2"></i>
          <strong>処理が完了しました！</strong> ${vendorInfo}
        </div>
        ${job.has_zip ? `
        <a href="/orders/${jobId}/download" class="btn btn-primary btn-lg">
          <i class="bi bi-download me-2"></i>注文書一式をダウンロード（ZIP）
        </a>` : `
        <div class="alert alert-warning">
          <i class="bi bi-exclamation-triangle me-1"></i>ダウンロード可能なファイルがありません。
        </div>`}
      `;
    } else {
      const errorMsg = job.error_message
        ? job.error_message.split("\n").slice(-2).join(" ").substring(0, 200)
        : "不明なエラーが発生しました。";
      resultContent.innerHTML = `
        <div class="alert alert-danger">
          <i class="bi bi-x-circle-fill me-2"></i>
          <strong>処理中にエラーが発生しました。</strong>
          <p class="mt-2 mb-0 small">${escapeHtml(errorMsg)}</p>
        </div>
        <button class="btn btn-outline-secondary" onclick="location.reload()">
          <i class="bi bi-arrow-clockwise me-1"></i>再度アップロードする
        </button>
      `;
    }
  }

  // ── Job History Refresh ─────────────────────────────────
  async function refreshJobHistory() {
    try {
      const res = await fetch("/orders/", { headers: { "Accept": "text/html" } });
      if (!res.ok) return;
      const html = await res.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");
      const newTable = doc.getElementById("job-history-table");
      const currentTable = document.getElementById("job-history-table");
      if (newTable && currentTable) {
        currentTable.innerHTML = newTable.innerHTML;
      }
    } catch {
      // ignore
    }
  }

  // ── Alerts ──────────────────────────────────────────────
  function showAlert(type, message) {
    const iconMap = {
      danger: "bi-exclamation-triangle-fill",
      warning: "bi-exclamation-circle-fill",
      info: "bi-info-circle-fill",
      success: "bi-check-circle-fill",
    };
    uploadAlert.className = `alert alert-${type} alert-dismissible fade show`;
    uploadAlert.innerHTML = `
      <i class="bi ${iconMap[type] || "bi-info-circle"} me-1"></i>${escapeHtml(message)}
      <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    uploadAlert.classList.remove("d-none");
  }

  function hideAlert() {
    uploadAlert.classList.add("d-none");
  }

  // ── Utility ─────────────────────────────────────────────
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function formatNumber(val) {
    if (!val) return "";
    // Remove existing commas/spaces, then format
    const num = String(val).replace(/[,\s]/g, "");
    if (/^\d+$/.test(num)) {
      return Number(num).toLocaleString("ja-JP");
    }
    return escapeHtml(String(val));
  }

  // ── History table delegated events ──────────────────────
  const historyTable = document.getElementById("job-history-table");
  if (historyTable) {
    historyTable.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-poll-job]");
      if (btn) {
        e.preventDefault();
        const jobId = btn.dataset.pollJob;
        const filename = btn.dataset.filename || "";
        startPolling(jobId, filename);
      }
    });
  }
})();
