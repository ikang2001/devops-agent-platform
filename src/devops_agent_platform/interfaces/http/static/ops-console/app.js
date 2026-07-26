const state = {
  tenantId: sessionStorage.getItem("ops.tenantId") || "",
  token: "",
  incidentStatus: "",
  incidents: [],
  incident: null,
  workflow: null,
  remediationPlan: null,
  ticket: null,
  dialogAction: null,
};

const elements = {
  accessForm: document.querySelector("#access-form"),
  tenantId: document.querySelector("#tenant-id"),
  token: document.querySelector("#admin-token"),
  connectionDot: document.querySelector("#connection-dot"),
  connectionLabel: document.querySelector("#connection-label"),
  incidentList: document.querySelector("#incident-list"),
  caseEmpty: document.querySelector("#case-empty"),
  caseContent: document.querySelector("#case-content"),
  caseSeverity: document.querySelector("#case-severity"),
  caseStatus: document.querySelector("#case-status"),
  caseService: document.querySelector("#case-service"),
  caseName: document.querySelector("#case-name"),
  caseId: document.querySelector("#case-id"),
  incidentFacts: document.querySelector("#incident-facts"),
  workflowId: document.querySelector("#workflow-id"),
  workflowSummary: document.querySelector("#workflow-summary"),
  evidenceGrid: document.querySelector("#evidence-grid"),
  remediationForm: document.querySelector("#remediation-form"),
  remediationPlanId: document.querySelector("#remediation-plan-id"),
  remediationActionKey: document.querySelector("#remediation-action-key"),
  remediationTarget: document.querySelector("#remediation-target"),
  remediationEvidenceIds: document.querySelector("#remediation-evidence-ids"),
  remediationCard: document.querySelector("#remediation-card"),
  remediationEmpty: document.querySelector("#remediation-empty"),
  feedbackForm: document.querySelector("#feedback-form"),
  feedbackVerdict: document.querySelector("#feedback-verdict"),
  feedbackRootCause: document.querySelector("#feedback-root-cause"),
  feedbackUnsafeIndexes: document.querySelector(
    "#feedback-unsafe-indexes",
  ),
  feedbackLabel: document.querySelector("#feedback-label"),
  feedbackNotes: document.querySelector("#feedback-notes"),
  feedbackList: document.querySelector("#feedback-list"),
  notificationTarget: document.querySelector("#notification-target"),
  ticketCard: document.querySelector("#ticket-card"),
  ticketEmpty: document.querySelector("#ticket-empty"),
  healthSummary: document.querySelector("#health-summary"),
  activityLog: document.querySelector("#activity-log"),
  toastRegion: document.querySelector("#toast-region"),
  dialog: document.querySelector("#action-dialog"),
  actionForm: document.querySelector("#action-form"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogCopy: document.querySelector("#dialog-copy"),
  dialogReasonField: document.querySelector("#dialog-reason-field"),
  dialogReason: document.querySelector("#dialog-reason"),
  dialogTargetField: document.querySelector("#dialog-target-field"),
  dialogTarget: document.querySelector("#dialog-target"),
  dialogConfirm: document.querySelector("#dialog-confirm"),
};

function textNode(tag, value, className = "") {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  node.textContent = value ?? "—";
  return node;
}

function replaceChildren(target, ...children) {
  target.replaceChildren(...children);
}

function formatTime(value) {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(parsed);
}

function makeRequestId(prefix) {
  const randomPart =
    globalThis.crypto?.randomUUID?.().replaceAll("-", "") ||
    `${Date.now()}${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${randomPart}`.slice(0, 128);
}

function setConnection(mode, label) {
  elements.connectionDot.className = "status-dot";
  if (mode) {
    elements.connectionDot.classList.add(`is-${mode}`);
  }
  elements.connectionLabel.textContent = label;
}

function addActivity(message) {
  const row = document.createElement("li");
  const time = document.createElement("time");
  time.dateTime = new Date().toISOString();
  time.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date());
  row.append(time, textNode("span", message));
  elements.activityLog.prepend(row);
  while (elements.activityLog.children.length > 12) {
    elements.activityLog.lastElementChild.remove();
  }
}

function showToast(message, isError = false) {
  const toast = textNode("div", message, "toast");
  if (isError) {
    toast.classList.add("is-error");
  }
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5200);
}

function errorMessage(payload, statusCode) {
  const error = payload?.error;
  if (error?.message) {
    return `${error.message}${error.code ? ` [${error.code}]` : ""}`;
  }
  if (payload?.detail) {
    if (Array.isArray(payload.detail)) {
      return payload.detail
        .map((item) => item.msg || "请求字段不合法")
        .join("；");
    }
    return String(payload.detail);
  }
  return `请求失败（HTTP ${statusCode}）`;
}

async function apiRequest(path, options = {}) {
  const {
    method = "GET",
    body,
    headers = {},
    authenticated = true,
  } = options;
  if (authenticated && (!state.token || !state.tenantId)) {
    throw new Error("请先连接运维租户");
  }
  const requestHeaders = {
    Accept: "application/json",
    "X-Trace-Id": makeRequestId("trc_console"),
    ...headers,
  };
  if (authenticated) {
    requestHeaders.Authorization = `Bearer ${state.token}`;
  }
  if (body !== undefined) {
    requestHeaders["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }
  return {
    data: payload?.data,
    etag: response.headers.get("ETag"),
    traceId: payload?.trace_id || response.headers.get("X-Trace-Id"),
  };
}

function tenantPath(suffix) {
  return `/api/v1/admin/tenants/${encodeURIComponent(state.tenantId)}${suffix}`;
}

function renderEmpty(target, symbol, message) {
  const wrapper = document.createElement("div");
  wrapper.className = "empty-state compact";
  wrapper.append(textNode("span", symbol), textNode("p", message));
  replaceChildren(target, wrapper);
}

async function loadHealth() {
  try {
    const result = await apiRequest("/readyz", { authenticated: false });
    const data = result.data || {};
    const rows = [];
    const components = data.components || {};
    rows.push(healthRow("平台就绪状态", data.status || "unknown"));
    for (const [name, snapshot] of Object.entries(components)) {
      rows.push(healthRow(name, snapshot?.status || "unknown"));
    }
    replaceChildren(elements.healthSummary, ...rows);
  } catch (error) {
    replaceChildren(
      elements.healthSummary,
      healthRow("平台就绪状态", "down"),
    );
    addActivity(`健康检查失败：${error.message}`);
  }
}

function healthRow(name, status) {
  const row = document.createElement("div");
  row.className = "health-row";
  if (!["ok", "up", "ready", "healthy"].includes(String(status).toLowerCase())) {
    row.classList.add("is-down");
  }
  row.append(textNode("span", name), textNode("span", status));
  return row;
}

async function loadIncidents() {
  if (!state.token || !state.tenantId) {
    renderEmpty(elements.incidentList, "⌁", "连接租户后，这里会显示最近事故。");
    return;
  }
  setConnection("", "正在读取事故");
  const params = new URLSearchParams({ limit: "50" });
  if (state.incidentStatus) {
    params.set("status", state.incidentStatus);
  }
  try {
    const result = await apiRequest(
      tenantPath(`/incidents?${params.toString()}`),
    );
    state.incidents = result.data?.items || [];
    renderIncidents();
    setConnection("online", `${state.tenantId} · 已连接`);
    addActivity(`读取 ${state.incidents.length} 条事故`);
  } catch (error) {
    state.incidents = [];
    renderEmpty(elements.incidentList, "!", error.message);
    setConnection("error", "连接失败");
    showToast(error.message, true);
    addActivity(`事故读取失败：${error.message}`);
  }
}

function renderIncidents() {
  if (!state.incidents.length) {
    renderEmpty(elements.incidentList, "✓", "当前筛选条件下没有事故。");
    return;
  }
  const rows = state.incidents.map((incident) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "incident-row";
    button.dataset.incidentId = incident.incident_id;
    if (state.incident?.incident_id === incident.incident_id) {
      button.classList.add("is-active");
    }
    const meta = document.createElement("span");
    meta.className = "incident-row-meta";
    meta.append(
      textNode("span", incident.severity || "UNKNOWN"),
      textNode("span", incident.status || "UNKNOWN"),
    );
    button.append(
      meta,
      textNode("strong", incident.title || incident.incident_id),
      textNode(
        "span",
        `${incident.service_name || "unknown"} · ${formatTime(incident.updated_at)}`,
        "incident-row-meta",
      ),
    );
    button.addEventListener("click", () => loadIncident(incident.incident_id));
    return button;
  });
  replaceChildren(elements.incidentList, ...rows);
}

async function loadIncident(incidentId) {
  try {
    const result = await apiRequest(
      tenantPath(`/incidents/${encodeURIComponent(incidentId)}`),
    );
    state.incident = { ...result.data, etag: result.etag };
    renderIncidents();
    renderIncident();
    addActivity(`打开事故 ${incidentId}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderIncident() {
  const incident = state.incident;
  if (!incident) {
    elements.caseEmpty.hidden = false;
    elements.caseContent.hidden = true;
    return;
  }
  elements.caseEmpty.hidden = true;
  elements.caseContent.hidden = false;
  elements.caseSeverity.textContent = incident.severity || "UNKNOWN";
  elements.caseSeverity.dataset.level = incident.severity || "UNKNOWN";
  elements.caseStatus.textContent = incident.status || "UNKNOWN";
  elements.caseService.textContent = incident.service_name || "unknown-service";
  elements.caseName.textContent = incident.title || "未命名事故";
  elements.caseId.textContent = incident.incident_id;

  const facts = [
    ["版本", incident.version],
    ["创建时间", formatTime(incident.created_at)],
    ["更新时间", formatTime(incident.updated_at)],
    ["解决人", incident.resolved_by],
    ["解决原因", incident.resolution_reason],
    ["解决时间", formatTime(incident.resolved_at)],
  ];
  const factNodes = facts.map(([label, value]) => {
    const wrapper = document.createElement("div");
    wrapper.append(textNode("dt", label), textNode("dd", value));
    return wrapper;
  });
  replaceChildren(elements.incidentFacts, ...factNodes);
  document.querySelector("#start-rca").disabled =
    incident.status === "CLOSED";
  document.querySelector("#resolve-incident").disabled =
    !["OPEN", "ANALYZING"].includes(incident.status);
  document.querySelector("#close-incident").disabled =
    incident.status !== "RESOLVED";
}

async function startRca() {
  if (!state.incident) {
    return;
  }
  try {
    const result = await apiRequest(
      tenantPath(
        `/incidents/${encodeURIComponent(state.incident.incident_id)}/rca`,
      ),
      {
        method: "POST",
        headers: { "Idempotency-Key": makeRequestId("idem_console_rca") },
      },
    );
    elements.workflowId.value = result.data.workflow_run_id;
    state.workflow = result.data;
    renderWorkflowSummary(result.data);
    addActivity(`RCA 已启动：${result.data.workflow_run_id}`);
    showToast("RCA 请求已进入工作流队列");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadWorkflow() {
  const workflowId = elements.workflowId.value.trim();
  if (!workflowId) {
    showToast("请输入 Workflow Run ID", true);
    return;
  }
  try {
    const result = await apiRequest(
      tenantPath(
        `/workflow-runs/${encodeURIComponent(workflowId)}/result?limit=100`,
      ),
    );
    state.workflow = { ...result.data, etag: result.etag };
    renderWorkflowSummary(state.workflow);
    renderEvidence(state.workflow.evidence || []);
    prefillRemediationEvidence(state.workflow);
    addActivity(`读取 RCA 结果：${state.workflow.status}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderWorkflowSummary(workflow) {
  elements.workflowSummary.hidden = false;
  const parts = [
    `状态 ${workflow.status || "UNKNOWN"}`,
    `步骤 ${workflow.step_count ?? "—"}`,
    `证据 ${(workflow.evidence || []).length}`,
    `尝试 ${workflow.execution_attempts ?? "—"}`,
  ];
  if (workflow.report) {
    parts.push(
      `结论 ${workflow.report.conclusion_status}`,
      `置信度 ${Math.round((workflow.report.confidence || 0) * 100)}%`,
    );
  }
  replaceChildren(
    elements.workflowSummary,
    ...parts.map((item) => textNode("span", item)),
  );
}

function renderEvidence(evidence) {
  if (!evidence.length) {
    renderEmpty(
      elements.evidenceGrid,
      "∿",
      "工作流还没有生成可展示的证据。",
    );
    return;
  }
  const cards = evidence.map((item) => {
    const card = document.createElement("article");
    card.className = "evidence-card";
    const header = document.createElement("header");
    header.append(
      textNode("h4", item.evidence_id),
      textNode("span", item.evidence_type, "evidence-kind"),
    );
    card.append(
      header,
      textNode("p", item.summary),
      textNode(
        "p",
        `${item.tool_name}@${item.tool_version} · ${item.source}`,
        "mono-label",
      ),
      textNode(
        "p",
        `置信度 ${Math.round((item.confidence || 0) * 100)}% · ${formatTime(item.collected_at)}`,
        "mono-label",
      ),
    );
    return card;
  });
  replaceChildren(elements.evidenceGrid, ...cards);
}

function workflowPath(suffix = "") {
  const workflowId = elements.workflowId.value.trim();
  if (!workflowId) {
    throw new Error("请先输入 Workflow Run ID");
  }
  return tenantPath(
    `/workflow-runs/${encodeURIComponent(workflowId)}/ticket-draft${suffix}`,
  );
}

function feedbackPath() {
  const workflowId = elements.workflowId.value.trim();
  if (!workflowId) {
    throw new Error("请先输入 Workflow Run ID");
  }
  return tenantPath(
    `/workflow-runs/${encodeURIComponent(workflowId)}/feedback`,
  );
}

function remediationCreatePath() {
  const workflowId = elements.workflowId.value.trim();
  if (!workflowId) {
    throw new Error("请先输入 Workflow Run ID");
  }
  return tenantPath(
    `/workflow-runs/${encodeURIComponent(workflowId)}/remediation-plans`,
  );
}

function remediationPlanPath(suffix = "") {
  const planId = elements.remediationPlanId.value.trim();
  if (!planId) {
    throw new Error("请先输入 Remediation Plan ID");
  }
  return tenantPath(
    `/remediation-plans/${encodeURIComponent(planId)}${suffix}`,
  );
}

function parseEvidenceIds(value) {
  const ids = value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (ids.length < 4 || ids.length > 100) {
    throw new Error("证据 ID 必须包含 4 到 100 个唯一值");
  }
  if (new Set(ids).size !== ids.length) {
    throw new Error("证据 ID 不能重复");
  }
  return ids;
}

function prefillRemediationEvidence(workflow) {
  if (elements.remediationEvidenceIds.value.trim()) {
    return;
  }
  const reportIds = workflow.report?.evidence_ids || [];
  const fallbackIds = (workflow.evidence || []).map((item) => item.evidence_id);
  const ids = reportIds.length ? reportIds : fallbackIds;
  if (ids.length) {
    elements.remediationEvidenceIds.value = ids.join(", ");
  }
}

function parseUnsafeIndexes(value) {
  if (!value.trim()) {
    return [];
  }
  const indexes = value
    .split(",")
    .map((item) => Number(item.trim()));
  if (
    indexes.some(
      (item) => !Number.isInteger(item) || item < 0 || item > 99,
    )
  ) {
    throw new Error("不安全建议序号必须是 0 到 99 的整数");
  }
  return [...new Set(indexes)].sort((left, right) => left - right);
}

async function createFeedback(event) {
  event.preventDefault();
  try {
    const verdict = elements.feedbackVerdict.value;
    const accepted = verdict === "ACCEPTED";
    const missingEvidenceTypes = accepted
      ? []
      : Array.from(
          elements.feedbackForm.querySelectorAll(
            "fieldset input:checked",
          ),
          (item) => item.value,
        );
    const unsafeIndexes = accepted
      ? []
      : parseUnsafeIndexes(elements.feedbackUnsafeIndexes.value);
    const rootCause = accepted
      ? null
      : elements.feedbackRootCause.value.trim() || null;
    const result = await apiRequest(feedbackPath(), {
      method: "POST",
      headers: {
        "Idempotency-Key": makeRequestId("idem_console_feedback"),
      },
      body: {
        verdict,
        corrected_root_cause: rootCause,
        missing_evidence_types: missingEvidenceTypes,
        unsafe_recommendation_indexes: unsafeIndexes,
        follow_up_label: elements.feedbackLabel.value.trim() || null,
        notes: elements.feedbackNotes.value.trim() || null,
      },
    });
    showToast(`反馈已记录：${result.data.verdict}`);
    addActivity(`RCA 人工复核：${result.data.verdict}`);
    elements.feedbackForm.reset();
    await loadFeedback();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadFeedback() {
  try {
    const result = await apiRequest(`${feedbackPath()}?limit=50`);
    const cards = (result.data?.items || []).map((item) => {
      const card = document.createElement("article");
      card.className = "feedback-card";
      const findings = [
        item.corrected_root_cause,
        item.missing_evidence_types?.length
          ? `缺失 ${item.missing_evidence_types.join("/")}`
          : null,
        item.unsafe_recommendation_indexes?.length
          ? `风险建议 #${item.unsafe_recommendation_indexes.join(", #")}`
          : null,
        item.notes,
      ].filter(Boolean);
      card.append(
        textNode("strong", item.verdict),
        textNode("p", findings.join("；") || "报告结论已接受"),
        textNode("time", formatTime(item.created_at)),
      );
      return card;
    });
    replaceChildren(elements.feedbackList, ...cards);
    if (!cards.length) {
      renderEmpty(elements.feedbackList, "✓", "尚无人工复核记录。");
    }
    addActivity(`读取 ${cards.length} 条 RCA 反馈`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function sendNotification() {
  const workflowId = elements.workflowId.value.trim();
  if (!workflowId) {
    showToast("请先输入 Workflow Run ID", true);
    return;
  }
  try {
    const target = elements.notificationTarget.value;
    const result = await apiRequest(
      tenantPath(
        `/workflow-runs/${encodeURIComponent(
          workflowId,
        )}/notifications`,
      ),
      {
        method: "POST",
        headers: {
          "Idempotency-Key": makeRequestId(
            "idem_console_notification",
          ),
        },
        body: { target_system: target },
      },
    );
    showToast(`RCA 摘要已发送到 ${result.data.target_system}`);
    addActivity(`通知投递：${result.data.target_system}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function createRemediation(event) {
  event.preventDefault();
  try {
    const result = await apiRequest(remediationCreatePath(), {
      method: "POST",
      headers: {
        "Idempotency-Key": makeRequestId("idem_console_remediation"),
      },
      body: {
        action_key: elements.remediationActionKey.value.trim(),
        target: elements.remediationTarget.value.trim(),
        evidence_ids: parseEvidenceIds(elements.remediationEvidenceIds.value),
      },
    });
    state.remediationPlan = { ...result.data, etag: result.etag };
    elements.remediationPlanId.value = state.remediationPlan.remediation_plan_id;
    renderRemediation();
    addActivity(
      `创建修复 dry-run 计划：${state.remediationPlan.remediation_plan_id}`,
    );
    showToast("修复计划已创建，风险和回滚动作来自动作目录");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadRemediation() {
  try {
    const result = await apiRequest(remediationPlanPath());
    state.remediationPlan = { ...result.data, etag: result.etag };
    renderRemediation();
    addActivity(`读取修复计划：${state.remediationPlan.status}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderRemediation() {
  const plan = state.remediationPlan;
  elements.remediationEmpty.hidden = Boolean(plan);
  elements.remediationCard.hidden = !plan;
  if (!plan) {
    return;
  }
  elements.remediationPlanId.value = plan.remediation_plan_id;
  const header = document.createElement("div");
  header.className = "remediation-card-header";
  const heading = document.createElement("div");
  heading.append(
    textNode("span", plan.risk, "severity-badge"),
    textNode("h3", plan.action_key),
    textNode(
      "p",
      `${plan.remediation_plan_id} · v${plan.version} · ${plan.target}`,
      "mono-label",
    ),
  );
  const status = textNode("span", plan.status, "status-badge");
  header.append(heading, status);

  const facts = [
    ["预期效果", plan.expected_effect],
    ["回滚动作", plan.rollback_action_key],
    ["Dry-run", plan.dry_run_summary],
    ["证据数量", plan.evidence_ids?.length ?? 0],
    ["创建人", plan.created_by],
    ["Trace", plan.trace_id],
  ].map(([label, value]) => {
    const wrapper = document.createElement("div");
    wrapper.append(textNode("dt", label), textNode("dd", value));
    return wrapper;
  });
  const factGrid = document.createElement("dl");
  factGrid.className = "remediation-facts";
  factGrid.append(...facts);

  const actions = document.createElement("div");
  actions.className = "remediation-decision-row";
  actions.append(
    stateButton("批准计划", "approve-remediation", "primary-action", plan.status === "DRAFT"),
    stateButton("拒绝计划", "reject-remediation", "quiet-action", plan.status === "DRAFT"),
    stateButton("执行修复", "execute-remediation", "primary-action", plan.status === "APPROVED"),
    stateButton("回滚修复", "rollback-remediation", "secondary-action", plan.status === "SUCCEEDED"),
  );

  const summaries = [
    plan.decision_reason ? `审批：${plan.decision_reason}` : null,
    plan.execution_summary ? `执行：${plan.execution_summary}` : null,
    plan.rollback_summary ? `回滚：${plan.rollback_summary}` : null,
  ].filter(Boolean);
  elements.remediationCard.replaceChildren(
    header,
    factGrid,
    ...summaries.map((item) => textNode("p", item)),
    actions,
  );
}

function stateButton(label, action, className, enabled) {
  const button = actionButton(label, action, className);
  button.disabled = !enabled;
  return button;
}

async function decideRemediation(approved, reason) {
  const result = await apiRequest(remediationPlanPath("/decision"), {
    method: "POST",
    body: {
      approved,
      reason,
    },
    headers: {
      "Idempotency-Key": makeRequestId("idem_console_remediation_decision"),
      "If-Match": state.remediationPlan.etag || `"${state.remediationPlan.version}"`,
    },
  });
  state.remediationPlan = { ...result.data, etag: result.etag };
  renderRemediation();
  addActivity(`修复计划已${approved ? "批准" : "拒绝"}`);
}

async function executeRemediation() {
  const result = await apiRequest(remediationPlanPath("/execute"), {
    method: "POST",
    body: {},
    headers: {
      "Idempotency-Key": makeRequestId("idem_console_remediation_execute"),
      "If-Match": state.remediationPlan.etag || `"${state.remediationPlan.version}"`,
    },
  });
  state.remediationPlan = { ...result.data, etag: result.etag };
  renderRemediation();
  addActivity(`修复执行完成：${state.remediationPlan.status}`);
}

async function rollbackRemediation() {
  const result = await apiRequest(remediationPlanPath("/rollback"), {
    method: "POST",
    body: {},
    headers: {
      "Idempotency-Key": makeRequestId("idem_console_remediation_rollback"),
      "If-Match": state.remediationPlan.etag || `"${state.remediationPlan.version}"`,
    },
  });
  state.remediationPlan = { ...result.data, etag: result.etag };
  renderRemediation();
  addActivity(`修复回滚完成：${state.remediationPlan.status}`);
}

async function loadTicket() {
  try {
    const result = await apiRequest(workflowPath());
    state.ticket = { ...result.data, etag: result.etag };
    renderTicket();
    addActivity(`读取工单草稿：${state.ticket.status}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function createTicket() {
  try {
    const result = await apiRequest(workflowPath(), {
      method: "POST",
      headers: { "Idempotency-Key": makeRequestId("idem_console_draft") },
    });
    state.ticket = { ...result.data, etag: result.etag };
    renderTicket();
    addActivity(`生成工单草稿：${state.ticket.ticket_draft_id}`);
    showToast("工单草稿已生成，提交前仍需人工审批");
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderTicket() {
  const ticket = state.ticket;
  elements.ticketEmpty.hidden = Boolean(ticket);
  elements.ticketCard.hidden = !ticket;
  if (!ticket) {
    return;
  }
  const header = document.createElement("div");
  header.className = "ticket-card-header";
  const heading = document.createElement("div");
  heading.append(
    textNode("span", ticket.priority, "severity-badge"),
    textNode("h3", ticket.title),
    textNode("p", `${ticket.ticket_draft_id} · v${ticket.version}`, "mono-label"),
  );
  const status = textNode("span", ticket.status, "status-badge");
  header.append(heading, status);

  const recommendations = document.createElement("ul");
  for (const recommendation of ticket.recommendations || []) {
    recommendations.append(textNode("li", recommendation));
  }
  const actions = document.createElement("div");
  actions.className = "ticket-decision-row";
  if (ticket.status === "DRAFT") {
    actions.append(
      actionButton("批准草稿", "approve-ticket", "primary-action"),
      actionButton("拒绝草稿", "reject-ticket", "quiet-action"),
    );
  }
  if (ticket.status === "APPROVED") {
    actions.append(
      actionButton("提交外部工单", "submit-ticket", "primary-action"),
    );
  }
  actions.append(
    actionButton("读取提交状态", "load-submissions", "quiet-action"),
  );
  elements.ticketCard.replaceChildren(
    header,
    textNode("p", ticket.description),
    recommendations,
    actions,
  );
}

function actionButton(label, action, className) {
  const button = textNode("button", label, className);
  button.type = "button";
  button.dataset.action = action;
  return button;
}

function openDialog(action) {
  const config = {
    "resolve-incident": {
      title: "标记事故已解决",
      copy: "提交后事故版本会递增，请填写已验证的缓解或修复结果。",
      confirm: "标记已解决",
      reason: true,
    },
    "close-incident": {
      title: "关闭事故",
      copy: "仅在复盘和后续事项已登记后关闭事故。",
      confirm: "关闭事故",
      reason: true,
    },
    "approve-ticket": {
      title: "批准工单草稿",
      copy: "批准只授权后续提交请求，不会在当前请求中调用外部系统。",
      confirm: "批准草稿",
      reason: false,
    },
    "reject-ticket": {
      title: "拒绝工单草稿",
      copy: "说明证据或建议中需要修正的内容。",
      confirm: "拒绝草稿",
      reason: true,
    },
    "submit-ticket": {
      title: "提交外部工单",
      copy: "请求通过 Outbox 异步交给已配置的供应商连接器。",
      confirm: "登记提交请求",
      reason: false,
      target: true,
    },
    "approve-remediation": {
      title: "批准修复计划",
      copy: "批准后计划进入可执行状态；执行前仍会重新检查动作目录、证据和维护窗口。",
      confirm: "批准计划",
      reason: true,
    },
    "reject-remediation": {
      title: "拒绝修复计划",
      copy: "拒绝会冻结当前计划，请写明证据、目标或风险上的问题。",
      confirm: "拒绝计划",
      reason: true,
    },
    "execute-remediation": {
      title: "执行受审修复",
      copy: "只会调用已持久化计划中的预注册动作，不会接受页面输入的命令文本。",
      confirm: "执行修复",
      reason: false,
    },
    "rollback-remediation": {
      title: "回滚受审修复",
      copy: "回滚使用计划创建时保存的受审回滚动作，避免目录后续变更影响恢复路径。",
      confirm: "执行回滚",
      reason: false,
    },
  }[action];
  if (!config) {
    return;
  }
  state.dialogAction = action;
  elements.dialogTitle.textContent = config.title;
  elements.dialogCopy.textContent = config.copy;
  elements.dialogConfirm.textContent = config.confirm;
  elements.dialogReasonField.hidden = !config.reason;
  elements.dialogReason.required = Boolean(config.reason);
  elements.dialogReason.value = "";
  elements.dialogTargetField.hidden = !config.target;
  elements.dialog.showModal();
  if (config.reason) {
    elements.dialogReason.focus();
  }
}

async function executeDialogAction() {
  const action = state.dialogAction;
  const reason = elements.dialogReason.value.trim();
  if (elements.dialogReason.required && reason.length < 3) {
    showToast("请填写至少 3 个字符的原因", true);
    return;
  }
  try {
    if (action === "resolve-incident" || action === "close-incident") {
      await changeIncidentState(action, reason);
    } else if (action === "approve-ticket" || action === "reject-ticket") {
      await decideTicket(action, reason);
    } else if (action === "submit-ticket") {
      await submitTicket(elements.dialogTarget.value);
    } else if (action === "approve-remediation") {
      await decideRemediation(true, reason);
    } else if (action === "reject-remediation") {
      await decideRemediation(false, reason);
    } else if (action === "execute-remediation") {
      await executeRemediation();
    } else if (action === "rollback-remediation") {
      await rollbackRemediation();
    }
    elements.dialog.close();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function changeIncidentState(action, reason) {
  const endpoint = action === "resolve-incident" ? "resolution" : "closure";
  const result = await apiRequest(
    tenantPath(
      `/incidents/${encodeURIComponent(state.incident.incident_id)}/${endpoint}`,
    ),
    {
      method: "POST",
      body: { reason },
      headers: {
        "Idempotency-Key": makeRequestId(`idem_console_${endpoint}`),
        "If-Match": state.incident.etag || `"${state.incident.version}"`,
      },
    },
  );
  state.incident = { ...state.incident, ...result.data, etag: result.etag };
  renderIncident();
  renderIncidents();
  addActivity(`事故状态更新为 ${state.incident.status}`);
  showToast(`事故已更新为 ${state.incident.status}`);
}

async function decideTicket(action, reason) {
  const decision = action === "approve-ticket" ? "APPROVE" : "REJECT";
  const body = { decision };
  if (decision === "REJECT") {
    body.reason = reason;
  }
  const result = await apiRequest(workflowPath("/decision"), {
    method: "POST",
    body,
    headers: {
      "Idempotency-Key": makeRequestId("idem_console_decision"),
      "If-Match": state.ticket.etag || `"${state.ticket.version}"`,
    },
  });
  state.ticket = { ...result.data, etag: result.etag };
  renderTicket();
  addActivity(`工单草稿已${decision === "APPROVE" ? "批准" : "拒绝"}`);
}

async function submitTicket(targetSystem) {
  const result = await apiRequest(workflowPath("/submissions"), {
    method: "POST",
    body: { target_system: targetSystem },
    headers: {
      "Idempotency-Key": makeRequestId("idem_console_submission"),
      "If-Match": state.ticket.etag || `"${state.ticket.version}"`,
    },
  });
  addActivity(`已登记 ${targetSystem} 工单提交请求`);
  showToast(`工单提交请求已登记：${result.data.ticket_submission_id}`);
}

async function loadSubmissions() {
  try {
    const result = await apiRequest(workflowPath("/submissions?limit=20"));
    const items = result.data?.items || [];
    const activity = items.length
      ? items
          .map((item) => `${item.target_system}: ${item.status}`)
          .join("；")
      : "尚无外部提交记录";
    showToast(activity);
    addActivity(`读取 ${items.length} 条工单提交记录`);
  } catch (error) {
    showToast(error.message, true);
  }
}

elements.accessForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.tenantId = elements.tenantId.value.trim();
  state.token = elements.token.value;
  sessionStorage.setItem("ops.tenantId", state.tenantId);
  elements.token.value = "";
  loadIncidents();
  loadHealth();
});

document.querySelector("#refresh-incidents").addEventListener("click", loadIncidents);
document.querySelector("#refresh-health").addEventListener("click", loadHealth);
document.querySelector("#start-rca").addEventListener("click", startRca);
document.querySelector("#load-workflow").addEventListener("click", loadWorkflow);
document.querySelector("#load-ticket").addEventListener("click", loadTicket);
document.querySelector("#create-ticket").addEventListener("click", createTicket);
document
  .querySelector("#load-remediation")
  .addEventListener("click", loadRemediation);
document.querySelector("#load-feedback").addEventListener("click", loadFeedback);
document
  .querySelector("#send-notification")
  .addEventListener("click", sendNotification);
elements.feedbackForm.addEventListener("submit", createFeedback);
elements.remediationForm.addEventListener("submit", createRemediation);
document
  .querySelector("#resolve-incident")
  .addEventListener("click", () => openDialog("resolve-incident"));
document
  .querySelector("#close-incident")
  .addEventListener("click", () => openDialog("close-incident"));

document.querySelectorAll(".filter-chip").forEach((button) => {
  button.addEventListener("click", () => {
    document
      .querySelectorAll(".filter-chip")
      .forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
    state.incidentStatus = button.dataset.status;
    loadIncidents();
  });
});

elements.ticketCard.addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) {
    return;
  }
  if (action === "load-submissions") {
    loadSubmissions();
    return;
  }
  openDialog(action);
});

elements.remediationCard.addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) {
    return;
  }
  openDialog(action);
});

elements.actionForm.addEventListener("submit", (event) => {
  if (event.submitter?.value !== "confirm") {
    state.dialogAction = null;
    return;
  }
  event.preventDefault();
  executeDialogAction();
});

elements.tenantId.value = state.tenantId;
loadHealth();
