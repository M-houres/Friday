function loadPage(page) {
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".page").forEach(item => item.classList.remove("active"));
  document.getElementById(`page-${page}`).classList.add("active");
  const loaders = {
    overview: loadOverview,
    users: loadUsers,
    billing: loadBilling,
    tasks: loadTasks,
    content: loadContent,
    models: loadModels,
    growth: loadGrowth,
    support: loadSupport,
    system: loadSystem,
  };
  const loader = loaders[page];
  if (loader) loader();
}

document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => loadPage(item.dataset.page));
});

async function loadOverview() {
  try {
    const [stats, ops, jobs, results, approvals, projects] = await Promise.all([
      api("GET", "/stats"),
      api("GET", "/ops/summary"),
      api("GET", "/jobs?limit=5"),
      api("GET", "/results?limit=5"),
      api("GET", "/approvals?status=pending&limit=5"),
      api("GET", "/projects"),
    ]);

    const summaryCards = [
      {label: "总任务", value: stats.workflows?.total || 0, hint: "累计工作流", cls: "accent"},
      {label: "运行中", value: stats.workflows?.running || 0, hint: "当前交付中的任务", cls: "blue"},
      {label: "用户总数", value: ops.users || 0, hint: "当前应用用户池", cls: "green"},
      {label: "待审批", value: approvals.approvals?.length || 0, hint: "需要人工动作", cls: "gold"},
      {label: "套餐数", value: ops.billing_plans || 0, hint: "当前可售卖能力包", cls: "blue"},
      {label: "订单数", value: ops.payment_orders || 0, hint: "商业化订单池", cls: "accent"},
      {label: "权益余额", value: ops.credits?.balance || 0, hint: "用户总剩余额度", cls: "green"},
      {label: "模型成本", value: `$${(stats.cost?.total_usd || 0).toFixed(4)}`, hint: "累计消耗", cls: "red"},
    ];
    document.getElementById("overview-stats").innerHTML = summaryCards.map(card => `
      <div class="stat-card">
        <div class="label">${card.label}</div>
        <div class="value ${card.cls}">${card.value}</div>
        <div class="hint">${card.hint}</div>
      </div>
    `).join("");

    document.getElementById("overview-jobs").innerHTML = renderSimpleList(
      jobs.jobs || [],
      job => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(job.job_type || "workflow")}</strong> <span class="${statusBadge(job.status)}">${escapeHtml(job.status)}</span></div>
          <div class="meta mono">${escapeHtml(shortText(job.job_id, 14))}</div>
        </div>
        <div class="meta">${fmtTime(job.created_at)}</div>
      </div>`,
      "暂无异步任务。"
    );

    document.getElementById("overview-results").innerHTML = renderSimpleList(
      results.results || [],
      row => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(row.page_id || row.project_id || "result")}</strong></div>
          <div class="meta">${escapeHtml(shortText(row.summary || "暂无摘要", 80))}</div>
        </div>
        <div class="meta">${fmtTime(row.updated_at)}</div>
      </div>`,
      "暂无结果记录。"
    );

    document.getElementById("overview-approvals").innerHTML = renderSimpleList(
      approvals.approvals || [],
      row => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(row.title || row.step_id || "审批")}</strong></div>
          <div class="meta">工作流 ${escapeHtml(shortText(row.workflow_id, 14))}</div>
        </div>
        <div class="${statusBadge(row.status)}">${escapeHtml(row.status)}</div>
      </div>`,
      "暂无待审批任务。"
    );

    const manifests = (projects.projects || []).slice(0, 4);
    document.getElementById("overview-projects").innerHTML = renderSimpleList(
      manifests,
      project => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(project.name || project.id)}</strong></div>
          <div class="meta">${(project.pages || []).length} 个页面 · ${(project.skills || []).length} 个 Skill</div>
        </div>
        <div class="meta">${escapeHtml(project.home_route || "/")}</div>
      </div>`,
      "当前应用还没有加载到页面配置。"
    );
  } catch (error) {
    document.getElementById("overview-stats").innerHTML = renderEmpty("经营总览加载失败，请检查服务和权限。");
    toast(error.message || "经营总览加载失败", "err");
  }
}

function resetUserForm() {
  ["user-id", "user-name", "user-email", "user-roles", "user-metadata"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("user-status").value = "active";
}

async function saveUser() {
  try {
    await api("POST", "/ops/users", {
      user_id: document.getElementById("user-id").value.trim(),
      name: document.getElementById("user-name").value.trim(),
      email: document.getElementById("user-email").value.trim(),
      roles: parseCsv(document.getElementById("user-roles").value),
      status: document.getElementById("user-status").value,
      metadata: parseJsonInput(document.getElementById("user-metadata").value),
    });
    toast("用户已保存", "ok");
    resetUserForm();
    loadUsers();
  } catch (error) {
    toast(error.message || "保存用户失败", "err");
  }
}

async function deleteUser(userId) {
  if (!window.confirm(`确认删除用户 ${userId} ?`)) return;
  try {
    await api("DELETE", `/ops/users/${encodeURIComponent(userId)}`);
    toast("用户已删除", "ok");
    loadUsers();
  } catch (error) {
    toast(error.message || "删除用户失败", "err");
  }
}

async function runUserAction(userId, action) {
  let note = "";
  let creditsDelta = 0;
  if (action === "grant_credits") {
    const raw = window.prompt("补偿额度（正整数）", "10");
    if (raw === null) return;
    creditsDelta = Number(raw || 0);
    if (!Number.isFinite(creditsDelta) || creditsDelta <= 0) {
      toast("补偿额度必须大于 0", "err");
      return;
    }
  }
  if (["ban", "whitelist", "grant_credits"].includes(action)) {
    note = window.prompt("操作备注", "") ?? "";
  }
  try {
    await api("POST", `/ops/users/${encodeURIComponent(userId)}/actions`, {
      action,
      note,
      credits_delta: creditsDelta,
    });
    toast("用户动作已执行", "ok");
    loadUsers();
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "用户动作执行失败", "err");
  }
}

async function loadUsers() {
  try {
    const [response, ledger, audit] = await Promise.all([
      api("GET", "/ops/users"),
      api("GET", "/billing/ledger?limit=12"),
      api("GET", "/ops/audit-logs?resource_type=user&limit=12"),
    ]);
    const users = response.users || [];
    if (!users.length) {
      document.getElementById("users-list").innerHTML = renderEmpty("当前应用还没有用户数据。");
    } else {
      document.getElementById("users-list").innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr><th>用户</th><th>账号状态</th><th>订阅 / 权益</th><th>角色</th><th>备注</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody>
              ${users.map(user => `
                <tr>
                  <td><strong>${escapeHtml(user.name || user.id)}</strong><div class="meta mono">${escapeHtml(user.id)}</div><div class="meta">${escapeHtml(user.email || "")}</div></td>
                  <td><span class="${statusBadge(user.status)}">${escapeHtml(user.status || "active")}</span></td>
                  <td>
                    <span class="${statusBadge(user.subscription_status || "inactive")}">${escapeHtml(user.subscription_status || "inactive")}</span>
                    <div class="meta">${escapeHtml(user.active_plan_name || "未绑定套餐")}</div>
                    <div class="meta">余额 ${escapeHtml(user.credits_balance || 0)} · 发放 ${escapeHtml(user.credits_granted_total || 0)} / 消耗 ${escapeHtml(user.credits_used_total || 0)}</div>
                  </td>
                  <td>${escapeHtml((user.roles || []).join(", ") || "-")}</td>
                  <td class="meta">${escapeHtml(shortText(JSON.stringify(user.metadata || {}), 60))}</td>
                  <td class="meta">${fmtTime(user.created_at)}</td>
                  <td>
                    <div class="toolbar">
                      <button class="btn btn-ghost" onclick="runUserAction('${String(user.id).replaceAll("'", "\\'")}', 'grant_credits')">补偿</button>
                      <button class="btn btn-ghost" onclick="runUserAction('${String(user.id).replaceAll("'", "\\'")}', 'whitelist')">加白</button>
                      <button class="btn btn-danger" onclick="runUserAction('${String(user.id).replaceAll("'", "\\'")}', '${user.status === "disabled" ? "unban" : "ban"}')">${user.status === "disabled" ? "解封" : "封禁"}</button>
                    </div>
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      `;
    }

    document.getElementById("users-ledger").innerHTML = renderSimpleList(
      ledger.entries || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.user_id || "-")}</strong> <span class="meta">${escapeHtml(item.change_type || "")}</span></div>
          <div class="meta">变动 ${escapeHtml(item.delta_credits || 0)} · 余额 ${escapeHtml(item.balance_after || 0)}</div>
        </div>
        <div class="meta">${fmtTime(item.created_at)}</div>
      </div>`,
      "暂无权益流水。"
    );

    document.getElementById("users-audit").innerHTML = renderSimpleList(
      audit.logs || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.action || "-")}</strong></div>
          <div class="meta">${escapeHtml(item.target_user_id || item.resource_id || "-")}</div>
        </div>
        <div class="meta">${fmtTime(item.created_at)}</div>
      </div>`,
      "暂无用户操作审计。"
    );
  } catch (error) {
    document.getElementById("users-list").innerHTML = renderEmpty("用户列表加载失败。");
    toast(error.message || "用户列表加载失败", "err");
  }
}

function resetPlanForm() {
  ["plan-name","plan-metadata","plan-features"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("plan-type").value = "subscription";
  document.getElementById("plan-price").value = "0";
  document.getElementById("plan-currency").value = "CNY";
  document.getElementById("plan-interval").value = "month";
  document.getElementById("plan-credits").value = "0";
  document.getElementById("plan-status").value = "active";
  delete document.getElementById("plan-name").dataset.planId;
}

function resetOrderForm() {
  ["order-user","order-plan","order-provider","order-provider-id","order-detail"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("order-type").value = "subscription";
  document.getElementById("order-amount").value = "0";
  document.getElementById("order-status").value = "pending";
  document.getElementById("order-credits").value = "0";
}

function resetEntitlementForm() {
  ["ent-user","ent-plan","ent-metadata"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("ent-status").value = "inactive";
  document.getElementById("ent-balance").value = "0";
  document.getElementById("ent-granted").value = "0";
  document.getElementById("ent-used").value = "0";
}

async function savePlan() {
  try {
    await api("POST", "/billing/plans", {
      plan_id: document.getElementById("plan-name").dataset.planId || "",
      name: document.getElementById("plan-name").value.trim(),
      plan_type: document.getElementById("plan-type").value,
      price_cents: Number(document.getElementById("plan-price").value || 0),
      currency: document.getElementById("plan-currency").value.trim() || "CNY",
      interval: document.getElementById("plan-interval").value.trim() || "month",
      credits: Number(document.getElementById("plan-credits").value || 0),
      features: parseCsv(document.getElementById("plan-features").value),
      status: document.getElementById("plan-status").value,
      metadata: parseJsonInput(document.getElementById("plan-metadata").value),
    });
    toast("套餐已保存", "ok");
    resetPlanForm();
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "保存套餐失败", "err");
  }
}

async function deletePlan(planId) {
  if (!window.confirm("确认删除套餐？")) return;
  try {
    await api("DELETE", `/billing/plans/${planId}`);
    toast("套餐已删除", "ok");
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "删除套餐失败", "err");
  }
}

async function fillPlan(planId) {
  try {
    const item = await api("GET", `/billing/plans/${planId}`);
    document.getElementById("plan-name").dataset.planId = planId;
    document.getElementById("plan-name").value = item.name || "";
    document.getElementById("plan-type").value = item.plan_type || "subscription";
    document.getElementById("plan-price").value = item.price_cents || 0;
    document.getElementById("plan-currency").value = item.currency || "CNY";
    document.getElementById("plan-interval").value = item.interval || "month";
    document.getElementById("plan-credits").value = item.credits || 0;
    document.getElementById("plan-status").value = item.status || "active";
    document.getElementById("plan-features").value = (item.features || []).join(", ");
    document.getElementById("plan-metadata").value = JSON.stringify(item.metadata || {});
    toast("套餐已回填", "ok");
  } catch (error) {
    toast(error.message || "加载套餐失败", "err");
  }
}

async function saveOrder() {
  try {
    await api("POST", "/billing/orders", {
      user_id: document.getElementById("order-user").value.trim(),
      plan_id: document.getElementById("order-plan").value.trim(),
      order_type: document.getElementById("order-type").value,
      amount_cents: Number(document.getElementById("order-amount").value || 0),
      status: document.getElementById("order-status").value,
      provider: document.getElementById("order-provider").value.trim(),
      provider_order_id: document.getElementById("order-provider-id").value.trim(),
      credits_delta: Number(document.getElementById("order-credits").value || 0),
      detail: parseJsonInput(document.getElementById("order-detail").value),
    });
    toast("订单已保存", "ok");
    resetOrderForm();
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "保存订单失败", "err");
  }
}

async function refundOrder(orderId) {
  const reason = window.prompt("退款原因", "") ?? "";
  try {
    await api("POST", `/billing/orders/${orderId}/refund`, {reason});
    toast("订单已退款", "ok");
    loadBilling();
    loadUsers();
    loadOverview();
  } catch (error) {
    toast(error.message || "订单退款失败", "err");
  }
}

async function processManualCallback() {
  try {
    await api("POST", "/billing/callbacks/manual", {
      provider: document.getElementById("callback-provider").value.trim(),
      order_id: document.getElementById("callback-order-id").value.trim(),
      provider_order_id: document.getElementById("callback-provider-order").value.trim(),
      payment_status: "paid",
      amount_cents: Number(document.getElementById("callback-amount").value || 0),
      payload: parseJsonInput(document.getElementById("callback-payload").value),
    });
    toast("支付回调已处理", "ok");
    ["callback-provider","callback-order-id","callback-provider-order","callback-payload"].forEach(id => document.getElementById(id).value = "");
    document.getElementById("callback-amount").value = "0";
    loadBilling();
    loadUsers();
    loadOverview();
  } catch (error) {
    toast(error.message || "处理支付回调失败", "err");
  }
}

async function saveEntitlement() {
  try {
    await api("POST", "/billing/entitlements", {
      user_id: document.getElementById("ent-user").value.trim(),
      active_plan_id: document.getElementById("ent-plan").value.trim(),
      subscription_status: document.getElementById("ent-status").value,
      credits_balance: Number(document.getElementById("ent-balance").value || 0),
      credits_granted_total: Number(document.getElementById("ent-granted").value || 0),
      credits_used_total: Number(document.getElementById("ent-used").value || 0),
      metadata: parseJsonInput(document.getElementById("ent-metadata").value),
    });
    toast("用户权益已保存", "ok");
    resetEntitlementForm();
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "保存用户权益失败", "err");
  }
}

async function loadBilling() {
  try {
    const [plans, orders, entitlements, ledger, callbacks, ops] = await Promise.all([
      api("GET", "/billing/plans"),
      api("GET", "/billing/orders?limit=20"),
      api("GET", "/billing/entitlements?limit=20"),
      api("GET", "/billing/ledger?limit=20"),
      api("GET", "/billing/callback-events?limit=20"),
      api("GET", "/ops/summary"),
    ]);

    document.getElementById("billing-stats").innerHTML = [
      {label: "套餐数", value: (plans.plans || []).length, cls: "accent", hint: "当前配置"},
      {label: "订单数", value: ops.payment_orders || 0, cls: "blue", hint: "累计订单"},
      {label: "权益用户", value: (entitlements.entitlements || []).length, cls: "green", hint: "已分配权益"},
      {label: "总余额", value: ops.credits?.balance || 0, cls: "gold", hint: "总剩余额度"},
    ].map(item => `
      <div class="stat-card">
        <div class="label">${item.label}</div>
        <div class="value ${item.cls}">${item.value}</div>
        <div class="hint">${item.hint}</div>
      </div>
    `).join("");

    document.getElementById("billing-plans").innerHTML = renderSimpleList(
      plans.plans || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.name)}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status)}</span></div>
          <div class="meta">${escapeHtml(item.plan_type)} · ¥${((item.price_cents || 0) / 100).toFixed(2)} · ${escapeHtml(item.credits || 0)} credits</div>
        </div>
        <div class="toolbar">
          <button class="btn btn-ghost" onclick="fillPlan('${String(item.id).replaceAll("'", "\\'")}')">编辑</button>
          <button class="btn btn-danger" onclick="deletePlan('${String(item.id).replaceAll("'", "\\'")}')">删除</button>
        </div>
      </div>`,
      "暂无套餐。"
    );

    document.getElementById("billing-orders").innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>用户</th><th>订单类型</th><th>金额</th><th>状态</th><th>套餐</th><th>时间</th><th>操作</th></tr></thead>
          <tbody>
            ${(orders.orders || []).map(item => `
              <tr>
                <td><strong>${escapeHtml(item.user_id || "-")}</strong><div class="meta mono">${escapeHtml(shortText(item.id, 14))}</div></td>
                <td>${escapeHtml(item.order_type || "-")}</td>
                <td>¥${((item.amount_cents || 0) / 100).toFixed(2)}<div class="meta">${escapeHtml(item.credits_delta || 0)} credits</div></td>
                <td><span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></td>
                <td>${escapeHtml(item.plan_name || item.plan_id || "-")}</td>
                <td class="meta">${fmtTime(item.created_at)}</td>
                <td>${item.status === "paid" ? `<button class="btn btn-danger" onclick="refundOrder('${String(item.id).replaceAll("'", "\\'")}')">退款</button>` : ""}</td>
              </tr>
            `).join("") || `<tr><td colspan="7">${renderEmpty("暂无订单。")}</td></tr>`}
          </tbody>
        </table>
      </div>
    `;

    document.getElementById("billing-entitlements").innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>用户</th><th>订阅状态</th><th>当前套餐</th><th>余额</th><th>累计发放/消耗</th></tr></thead>
          <tbody>
            ${(entitlements.entitlements || []).map(item => `
              <tr>
                <td class="mono">${escapeHtml(item.user_id || "-")}</td>
                <td><span class="${statusBadge(item.subscription_status)}">${escapeHtml(item.subscription_status || "")}</span></td>
                <td>${escapeHtml(item.active_plan_name || item.active_plan_id || "-")}</td>
                <td>${escapeHtml(item.credits_balance || 0)}</td>
                <td>${escapeHtml(item.credits_granted_total || 0)} / ${escapeHtml(item.credits_used_total || 0)}</td>
              </tr>
            `).join("") || `<tr><td colspan="5">${renderEmpty("暂无权益数据。")}</td></tr>`}
          </tbody>
        </table>
      </div>
    `;

    document.getElementById("billing-ledger").innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>用户</th><th>类型</th><th>变动</th><th>余额</th><th>来源</th><th>时间</th></tr></thead>
          <tbody>
            ${(ledger.entries || []).map(item => `
              <tr>
                <td class="mono">${escapeHtml(item.user_id || "-")}</td>
                <td>${escapeHtml(item.change_type || "-")}</td>
                <td>${escapeHtml(item.delta_credits || 0)}</td>
                <td>${escapeHtml(item.balance_after || 0)}</td>
                <td>${escapeHtml(item.source_type || "-")}<div class="meta mono">${escapeHtml(shortText(item.source_id || "", 14))}</div></td>
                <td class="meta">${fmtTime(item.created_at)}</td>
              </tr>
            `).join("") || `<tr><td colspan="6">${renderEmpty("暂无权益流水。")}</td></tr>`}
          </tbody>
        </table>
      </div>
    `;

    document.getElementById("billing-callbacks").innerHTML = renderSimpleList(
      callbacks.events || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.provider || "-")}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta mono">${escapeHtml(item.provider_order_id || item.order_id || "-")}</div>
        </div>
        <div class="meta">${fmtTime(item.created_at)}</div>
      </div>`,
      "暂无回调处理记录。"
    );
  } catch (error) {
    toast(error.message || "支付与权益加载失败", "err");
  }
}
function resetCouponForm() {
  ["coupon-code","coupon-name","coupon-metadata","coupon-user","coupon-redeem-code"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("coupon-credits").value = "0";
  document.getElementById("coupon-max").value = "0";
  document.getElementById("coupon-status").value = "active";
}

function resetTrialForm() {
  ["trial-user","trial-reason"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("trial-credits").value = "0";
}

async function saveCoupon() {
  try {
    await api("POST", "/growth/coupons", {
      code: document.getElementById("coupon-code").value.trim(),
      name: document.getElementById("coupon-name").value.trim(),
      credits_bonus: Number(document.getElementById("coupon-credits").value || 0),
      max_redemptions: Number(document.getElementById("coupon-max").value || 0),
      status: document.getElementById("coupon-status").value,
      metadata: parseJsonInput(document.getElementById("coupon-metadata").value),
    });
    toast("优惠券已保存", "ok");
    resetCouponForm();
    loadGrowth();
  } catch (error) {
    toast(error.message || "保存优惠券失败", "err");
  }
}

async function redeemCoupon() {
  try {
    await api("POST", "/growth/coupons/redeem", {
      user_id: document.getElementById("coupon-user").value.trim(),
      code: document.getElementById("coupon-redeem-code").value.trim(),
    });
    toast("优惠券已发放", "ok");
    loadGrowth();
    loadUsers();
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "发放优惠券失败", "err");
  }
}

async function saveTrialGrant() {
  try {
    await api("POST", "/growth/trials", {
      user_id: document.getElementById("trial-user").value.trim(),
      credits_amount: Number(document.getElementById("trial-credits").value || 0),
      reason: document.getElementById("trial-reason").value.trim(),
    });
    toast("试用额度已发放", "ok");
    resetTrialForm();
    loadGrowth();
    loadUsers();
    loadBilling();
    loadOverview();
  } catch (error) {
    toast(error.message || "发放试用失败", "err");
  }
}

async function loadGrowth() {
  try {
    const [coupons, trials] = await Promise.all([
      api("GET", "/growth/coupons?limit=20"),
      api("GET", "/growth/trials?limit=20"),
    ]);

    document.getElementById("growth-stats").innerHTML = [
      {label: "优惠券数", value: (coupons.coupons || []).length, cls: "accent", hint: "当前活动资源"},
      {label: "试用发放", value: (trials.grants || []).length, cls: "green", hint: "累计试用记录"},
      {label: "激活券", value: (coupons.coupons || []).filter(item => item.status === "active").length, cls: "blue", hint: "当前可发放"},
    ].map(item => `
      <div class="stat-card">
        <div class="label">${item.label}</div>
        <div class="value ${item.cls}">${item.value}</div>
        <div class="hint">${item.hint}</div>
      </div>
    `).join("");

    document.getElementById("growth-coupons").innerHTML = renderSimpleList(
      coupons.coupons || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.name || item.code)}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta mono">${escapeHtml(item.code || "")}</div>
          <div class="meta">赠送 ${escapeHtml(item.credits_bonus || 0)} credits · 已领 ${escapeHtml(item.redeemed_count || 0)} / ${escapeHtml(item.max_redemptions || 0 || "∞")}</div>
        </div>
        <div class="meta">${fmtTime(item.created_at)}</div>
      </div>`,
      "暂无优惠券。"
    );

    document.getElementById("growth-trials").innerHTML = renderSimpleList(
      trials.grants || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.user_id || "-")}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta">赠送 ${escapeHtml(item.credits_amount || 0)} credits</div>
          <div class="meta">${escapeHtml(item.reason || "")}</div>
        </div>
        <div class="meta">${fmtTime(item.created_at)}</div>
      </div>`,
      "暂无试用赠送记录。"
    );
  } catch (error) {
    toast(error.message || "增长运营加载失败", "err");
  }
}

async function cancelJob(jobId) {
  if (!window.confirm("确认取消任务？")) return;
  try {
    await api("POST", `/jobs/${jobId}/cancel`);
    toast("任务已取消", "ok");
    loadTasks();
  } catch (error) {
    toast(error.message || "取消任务失败", "err");
  }
}

async function retryJob(jobId) {
  try {
    await api("POST", `/jobs/${jobId}/retry`, {});
    toast("任务已重新投递", "ok");
    loadTasks();
  } catch (error) {
    toast(error.message || "重试任务失败", "err");
  }
}

async function refundWorkflowCharge(workflowId) {
  const reason = window.prompt("退款原因", "") ?? "";
  try {
    await api("POST", `/results/${workflowId}/refund`, {reason});
    toast("任务额度已退款", "ok");
    loadTasks();
    loadBilling();
    loadUsers();
    loadOverview();
  } catch (error) {
    toast(error.message || "任务退款失败", "err");
  }
}

async function loadTasks() {
  try {
    const status = document.getElementById("task-filter-status").value.trim();
    const userId = document.getElementById("task-filter-user").value.trim();
    const projectInput = document.getElementById("task-filter-project").value.trim();
    const [workflows, jobs, approvals, results, audit] = await Promise.all([
      api("GET", `/workflows?limit=30&status=${encodeURIComponent(status)}`),
      api("GET", `/jobs?limit=30&status=${encodeURIComponent(status)}&user_id=${encodeURIComponent(userId)}`),
      api("GET", `/approvals?limit=30&workflow_id=&project_id=${encodeURIComponent(projectInput)}&page_id=`),
      api("GET", `/results?limit=30&project_id=${encodeURIComponent(projectInput)}&user_id=${encodeURIComponent(userId)}`),
      api("GET", "/ops/audit-logs?resource_type=async_job&limit=20"),
    ]);

    const workflowItems = workflows.workflows || [];
    const jobItems = jobs.jobs || [];
    const approvalItems = approvals.approvals || [];
    const resultItems = results.results || [];

    document.getElementById("task-stats").innerHTML = [
      {label: "工作流", value: workflowItems.length, cls: "accent"},
      {label: "异步作业", value: jobItems.length, cls: "blue"},
      {label: "审批任务", value: approvalItems.length, cls: "gold"},
      {label: "结果记录", value: resultItems.length, cls: "green"},
    ].map(item => `
      <div class="stat-card">
        <div class="label">${item.label}</div>
        <div class="value ${item.cls}">${item.value}</div>
        <div class="hint">当前筛选范围</div>
      </div>
    `).join("");

    document.getElementById("task-workflows").innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>类型</th><th>标识</th><th>状态</th><th>说明</th><th>时间</th><th>操作</th></tr></thead>
          <tbody>
            ${workflowItems.map(item => `
              <tr>
                <td>workflow</td>
                <td class="mono">${escapeHtml(shortText(item.id, 16))}</td>
                <td><span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></td>
                <td>${escapeHtml(shortText(item.task || "", 56))}</td>
                <td class="meta">${fmtTime(item.started_at)}</td>
                <td>${item.status === "completed" ? `<button class="btn btn-danger" onclick="refundWorkflowCharge('${String(item.id).replaceAll("'", "\\'")}')">退款</button>` : ""}</td>
              </tr>
            `).concat(jobItems.map(item => `
              <tr>
                <td>${escapeHtml(item.job_type || "job")}</td>
                <td class="mono">${escapeHtml(shortText(item.job_id, 16))}</td>
                <td><span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></td>
                <td>${escapeHtml(shortText(item.error || JSON.stringify(item.payload || {}), 56))}</td>
                <td class="meta">${fmtTime(item.created_at)}</td>
                <td>
                  <div class="toolbar">
                    ${["queued", "running"].includes(item.status) ? `<button class="btn btn-danger" onclick="cancelJob('${String(item.job_id).replaceAll("'", "\\'")}')">取消</button>` : ""}
                    ${["failed", "cancelled"].includes(item.status) ? `<button class="btn btn-primary" onclick="retryJob('${String(item.job_id).replaceAll("'", "\\'")}')">重试</button>` : ""}
                    ${item.result?.workflow_id ? `<button class="btn btn-ghost" onclick="refundWorkflowCharge('${String(item.result.workflow_id).replaceAll("'", "\\'")}')">退款</button>` : ""}
                  </div>
                </td>
              </tr>
            `)).join("") || `<tr><td colspan="6">${renderEmpty("暂无任务数据。")}</td></tr>`}
          </tbody>
        </table>
      </div>
    `;

    document.getElementById("task-approvals").innerHTML = renderSimpleList(
      approvalItems,
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.title || item.step_id || "审批")}</strong></div>
          <div class="meta mono">${escapeHtml(shortText(item.workflow_id, 16))}</div>
        </div>
        <div class="toolbar">
          <span class="${statusBadge(item.status)}">${escapeHtml(item.status)}</span>
          ${item.status === "pending" ? `
            <button class="btn btn-primary" onclick="reviewApproval('${String(item.id).replaceAll("'", "\\'")}', true)">通过</button>
            <button class="btn btn-danger" onclick="reviewApproval('${String(item.id).replaceAll("'", "\\'")}', false)">拒绝</button>
          ` : ""}
        </div>
      </div>`,
      "暂无审批任务。"
    );

    document.getElementById("task-results").innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>工作流</th><th>页面</th><th>摘要</th><th>更新时间</th></tr></thead>
          <tbody>
            ${(resultItems || []).map(item => `
              <tr>
                <td class="mono">${escapeHtml(shortText(item.workflow_id, 16))}</td>
                <td>${escapeHtml(item.page_id || item.project_id || "-")}</td>
                <td>${escapeHtml(shortText(item.summary || "", 84))}</td>
                <td class="meta">${fmtTime(item.updated_at)}</td>
              </tr>
            `).join("") || `<tr><td colspan="4">${renderEmpty("暂无结果记录。")}</td></tr>`}
          </tbody>
        </table>
      </div>
    `;

    document.getElementById("task-audit").innerHTML = renderSimpleList(
      audit.logs || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.action || "-")}</strong></div>
          <div class="meta mono">${escapeHtml(shortText(item.resource_id || "", 18))}</div>
        </div>
        <div class="meta">${fmtTime(item.created_at)}</div>
      </div>`,
      "暂无任务操作审计。"
    );
  } catch (error) {
    toast(error.message || "任务视图加载失败", "err");
  }
}

async function reviewApproval(approvalId, approved) {
  const comment = window.prompt(approved ? "审批备注（可空）" : "拒绝原因", "") ?? "";
  try {
    await api("POST", `/approvals/${approvalId}/review`, {approved, comment});
    toast(approved ? "审批已通过" : "审批已拒绝", "ok");
    loadTasks();
    loadOverview();
    loadSupport();
  } catch (error) {
    toast(error.message || "审批失败", "err");
  }
}

function resetProjectForm() {
  ["project-id", "project-name", "project-home-route", "project-skills", "project-description"].forEach(id => document.getElementById(id).value = "");
}

function resetPageForm() {
  ["page-id", "page-name", "page-route", "page-file", "page-nav-label", "page-icon", "page-skills", "page-billing", "page-description", "page-scenario"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("page-visibility").value = "public";
  document.getElementById("page-is-home").value = "false";
}

function fillProject(item) {
  document.getElementById("project-id").value = item.id || "";
  document.getElementById("project-name").value = item.name || "";
  document.getElementById("project-home-route").value = item.home_route || "";
  document.getElementById("project-skills").value = (item.skills || []).join(", ");
  document.getElementById("project-description").value = item.description || "";
}

function fillProjectPage(projectId, page) {
  document.getElementById("project-id").value = projectId || "";
  document.getElementById("page-id").value = page.id || "";
  document.getElementById("page-name").value = page.name || "";
  document.getElementById("page-route").value = page.route || "";
  document.getElementById("page-file").value = page.page || "";
  document.getElementById("page-nav-label").value = page.nav_label || "";
  document.getElementById("page-icon").value = page.icon || "";
  document.getElementById("page-skills").value = (page.skills || []).join(", ");
  document.getElementById("page-visibility").value = page.visibility || "public";
  document.getElementById("page-is-home").value = String(Boolean(page.is_home));
  document.getElementById("page-billing").value = JSON.stringify(page.billing || {}, null, 2);
  document.getElementById("page-description").value = page.description || "";
  document.getElementById("page-scenario").value = JSON.stringify(page.scenario || {}, null, 2);
}

function editProject(encodedProject) {
  fillProject(decodeActionPayload(encodedProject));
}

function editProjectPage(encodedProjectId, encodedPage) {
  fillProjectPage(
    decodeActionPayload(encodedProjectId),
    decodeActionPayload(encodedPage),
  );
}

async function saveProjectManifest() {
  const projectId = document.getElementById("project-id").value.trim();
  if (!projectId) {
    toast("请先填写项目 ID", "err");
    return;
  }
  try {
    await api("POST", "/ops/projects", {
      project_id: projectId,
      name: document.getElementById("project-name").value.trim(),
      home_route: document.getElementById("project-home-route").value.trim(),
      description: document.getElementById("project-description").value.trim(),
      skills: parseCsv(document.getElementById("project-skills").value),
    });
    toast("项目配置已保存", "ok");
    loadContent();
    loadOverview();
  } catch (error) {
    toast(error.message || "保存项目失败", "err");
  }
}

async function deleteCurrentProject() {
  const projectId = document.getElementById("project-id").value.trim();
  if (!projectId) {
    toast("请先填写项目 ID", "err");
    return;
  }
  if (!window.confirm(`确认删除项目 ${projectId} ?`)) return;
  try {
    await api("DELETE", `/ops/projects/${encodeURIComponent(projectId)}`);
    toast("项目已删除", "ok");
    resetProjectForm();
    resetPageForm();
    loadContent();
    loadOverview();
  } catch (error) {
    toast(error.message || "删除项目失败", "err");
  }
}

async function saveProjectPage() {
  const projectId = document.getElementById("project-id").value.trim();
  if (!projectId) {
    toast("请先填写项目 ID", "err");
    return;
  }
  try {
    await api("POST", `/ops/projects/${encodeURIComponent(projectId)}/pages`, {
      page_id: document.getElementById("page-id").value.trim(),
      name: document.getElementById("page-name").value.trim(),
      route: document.getElementById("page-route").value.trim(),
      page: document.getElementById("page-file").value.trim(),
      nav_label: document.getElementById("page-nav-label").value.trim(),
      icon: document.getElementById("page-icon").value.trim(),
      skills: parseCsv(document.getElementById("page-skills").value),
      visibility: document.getElementById("page-visibility").value,
      is_home: document.getElementById("page-is-home").value === "true",
      billing: parseJsonInput(document.getElementById("page-billing").value) || {},
      description: document.getElementById("page-description").value.trim(),
      scenario: parseJsonInput(document.getElementById("page-scenario").value) || {},
    });
    toast("页面配置已保存", "ok");
    loadContent();
    loadOverview();
  } catch (error) {
    toast(error.message || "保存页面失败", "err");
  }
}

async function deleteCurrentPage() {
  const projectId = document.getElementById("project-id").value.trim();
  const pageId = document.getElementById("page-id").value.trim();
  if (!projectId || !pageId) {
    toast("请先填写项目 ID 和页面 ID", "err");
    return;
  }
  if (!window.confirm(`确认删除页面 ${pageId} ?`)) return;
  try {
    await api("DELETE", `/ops/projects/${encodeURIComponent(projectId)}/pages/${encodeURIComponent(pageId)}`);
    toast("页面已删除", "ok");
    resetPageForm();
    loadContent();
    loadOverview();
  } catch (error) {
    toast(error.message || "删除页面失败", "err");
  }
}

function resetTemplateForm() {
  ["tpl-name","tpl-category","tpl-project","tpl-vars","tpl-metadata","tpl-content"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("tpl-scope").value = "project";
  delete document.getElementById("tpl-name").dataset.templateId;
}

async function loadContent() {
  try {
    const [projects, managedProjects, skills, templates, knowledge] = await Promise.all([
      api("GET", "/projects"),
      api("GET", "/ops/projects"),
      api("GET", "/skills"),
      api("GET", "/templates"),
      api("GET", "/knowledge"),
    ]);

    const manifests = projects.projects || [];
    const editableProjects = managedProjects.projects || manifests;
    document.getElementById("content-projects").innerHTML = renderSimpleList(
      editableProjects,
      item => {
        const encodedProject = encodeActionPayload(item);
        return `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.name || item.id)}</strong></div>
          <div class="meta">${escapeHtml(item.description || "未填写描述")}</div>
          <div class="pill-grid" style="margin-top:8px">${(item.pages || []).slice(0, 6).map(page => `<span class="pill" onclick="editProjectPage('${encodeActionPayload(String(item.id))}', '${encodeActionPayload(page)}')" style="cursor:pointer">${escapeHtml(page.name || page.route || page.id)}</span>`).join("")}</div>
        </div>
        <div class="toolbar">
          <button class="btn btn-ghost" onclick="editProject('${encodedProject}')">编辑项目</button>
        </div>
      </div>`;
      },
      "暂无项目/页面配置。"
    );

    document.getElementById("content-skills").innerHTML = renderSimpleList(
      skills.skills || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.name)}</strong></div>
          <div class="meta">${escapeHtml(item.description || "")}</div>
        </div>
        <div class="meta">${escapeHtml(String(item.tools || 0))} tools</div>
      </div>`,
      "暂无 Skill 清单。"
    );

    document.getElementById("templates-list").innerHTML = renderSimpleList(
      templates.templates || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.name)}</strong> <span class="meta">${escapeHtml(item.category || "general")}</span></div>
          <div class="meta">${escapeHtml(shortText(item.content || "", 96))}</div>
        </div>
        <div class="toolbar">
          <button class="btn btn-ghost" onclick="fillTemplate('${String(item.id).replaceAll("'", "\\'")}')">编辑</button>
          <button class="btn btn-danger" onclick="deleteTemplate('${String(item.id).replaceAll("'", "\\'")}')">删除</button>
        </div>
      </div>`,
      "暂无 Prompt 模板。"
    );

    document.getElementById("knowledge-list").innerHTML = renderSimpleList(
      knowledge.documents || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.title)}</strong> <span class="meta">${escapeHtml(item.doc_type || "note")}</span></div>
          <div class="meta">${escapeHtml(shortText(item.content || "", 96))}</div>
        </div>
        <div class="toolbar">
          <button class="btn btn-ghost" onclick="fillKnowledge('${String(item.id).replaceAll("'", "\\'")}')">编辑</button>
          <button class="btn btn-danger" onclick="deleteKnowledge('${String(item.id).replaceAll("'", "\\'")}')">删除</button>
        </div>
      </div>`,
      "暂无知识库文档。"
    );
  } catch (error) {
    toast(error.message || "功能与内容加载失败", "err");
  }
}

async function fillTemplate(templateId) {
  try {
    const item = await api("GET", `/templates/${templateId}`);
    document.getElementById("tpl-name").value = item.name || "";
    document.getElementById("tpl-name").dataset.templateId = templateId;
    document.getElementById("tpl-category").value = item.category || "";
    document.getElementById("tpl-project").value = item.project_id || "";
    document.getElementById("tpl-scope").value = item.scope || "project";
    document.getElementById("tpl-vars").value = (item.variables || []).join(", ");
    document.getElementById("tpl-metadata").value = JSON.stringify(item.metadata || {});
    document.getElementById("tpl-content").value = item.content || "";
    toast("模板已回填", "ok");
  } catch (error) {
    toast(error.message || "加载模板失败", "err");
  }
}

async function saveTemplate() {
  try {
    await api("POST", "/templates", {
      template_id: document.getElementById("tpl-name").dataset.templateId || "",
      name: document.getElementById("tpl-name").value.trim(),
      content: document.getElementById("tpl-content").value,
      category: document.getElementById("tpl-category").value.trim() || "general",
      project_id: document.getElementById("tpl-project").value.trim(),
      scope: document.getElementById("tpl-scope").value,
      variables: parseCsv(document.getElementById("tpl-vars").value),
      metadata: parseJsonInput(document.getElementById("tpl-metadata").value),
    });
    toast("模板已保存", "ok");
    resetTemplateForm();
    loadContent();
  } catch (error) {
    toast(error.message || "保存模板失败", "err");
  }
}

async function deleteTemplate(templateId) {
  if (!window.confirm("确认删除模板？")) return;
  try {
    await api("DELETE", `/templates/${templateId}`);
    toast("模板已删除", "ok");
    loadContent();
  } catch (error) {
    toast(error.message || "删除模板失败", "err");
  }
}

function resetKnowledgeForm() {
  ["kg-title","kg-project","kg-type","kg-tags","kg-metadata","kg-content"].forEach(id => document.getElementById(id).value = "");
  delete document.getElementById("kg-title").dataset.documentId;
}

async function fillKnowledge(documentId) {
  try {
    const item = await api("GET", `/knowledge/${documentId}`);
    document.getElementById("kg-title").value = item.title || "";
    document.getElementById("kg-title").dataset.documentId = documentId;
    document.getElementById("kg-project").value = item.project_id || "";
    document.getElementById("kg-type").value = item.doc_type || "";
    document.getElementById("kg-tags").value = (item.tags || []).join(", ");
    document.getElementById("kg-metadata").value = JSON.stringify(item.metadata || {});
    document.getElementById("kg-content").value = item.content || "";
    toast("知识文档已回填", "ok");
  } catch (error) {
    toast(error.message || "加载知识文档失败", "err");
  }
}

async function saveKnowledge() {
  try {
    await api("POST", "/knowledge", {
      document_id: document.getElementById("kg-title").dataset.documentId || "",
      title: document.getElementById("kg-title").value.trim(),
      project_id: document.getElementById("kg-project").value.trim(),
      doc_type: document.getElementById("kg-type").value.trim() || "note",
      tags: parseCsv(document.getElementById("kg-tags").value),
      metadata: parseJsonInput(document.getElementById("kg-metadata").value),
      content: document.getElementById("kg-content").value,
    });
    toast("知识文档已保存", "ok");
    resetKnowledgeForm();
    loadContent();
  } catch (error) {
    toast(error.message || "保存知识文档失败", "err");
  }
}

async function deleteKnowledge(documentId) {
  if (!window.confirm("确认删除知识文档？")) return;
  try {
    await api("DELETE", `/knowledge/${documentId}`);
    toast("知识文档已删除", "ok");
    loadContent();
  } catch (error) {
    toast(error.message || "删除知识文档失败", "err");
  }
}

async function loadModels() {
  try {
    const [stats, cost, strategy] = await Promise.all([
      api("GET", "/stats"),
      api("GET", "/cost"),
      api("GET", "/models/strategy"),
    ]);
    document.getElementById("model-stats").innerHTML = [
      {label: "提供商", value: (stats.providers || []).length, cls: "accent", hint: "当前已启用"},
      {label: "熔断器", value: (stats.circuit_breakers || []).length, cls: "gold", hint: "保护模型稳定性"},
      {label: "总 Token", value: cost.total_tokens?.total || 0, cls: "blue", hint: "累计调用消耗"},
      {label: "总成本", value: `$${(cost.total_cost_usd || 0).toFixed(4)}`, cls: "red", hint: "累计模型成本"},
    ].map(item => `
      <div class="stat-card">
        <div class="label">${item.label}</div>
        <div class="value ${item.cls}">${item.value}</div>
        <div class="hint">${item.hint}</div>
      </div>
    `).join("");

    document.getElementById("model-providers").innerHTML = renderSimpleList(
      stats.circuit_breakers || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.provider || "-")} / ${escapeHtml(item.model || "-")}</strong></div>
          <div class="meta">失败次数 ${escapeHtml(item.failure_count ?? 0)}</div>
        </div>
        <div><span class="${statusBadge(item.state)}">${escapeHtml(item.state || "closed")}</span></div>
      </div>`,
      "暂无熔断状态数据。"
    ) + renderSimpleList(
      (stats.providers || []).map(name => ({name})),
      item => `<div class="list-row"><strong>${escapeHtml(item.name)}</strong><div class="meta">已加载提供商</div></div>`,
      ""
    );

    const byModel = cost.by_model || {};
    const rows = Object.entries(byModel);
    document.getElementById("model-cost").innerHTML = rows.length ? `
      <div class="table-wrap">
        <table>
          <thead><tr><th>模型</th><th>成本 USD</th></tr></thead>
          <tbody>${rows.map(([model, value]) => `<tr><td>${escapeHtml(model)}</td><td>$${Number(value || 0).toFixed(4)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
    ` : renderEmpty("当前还没有按模型拆分的成本数据。");

    document.getElementById("model-strategy-summary").innerHTML = `
      <div class="list">
        <div class="list-row"><strong>默认模型</strong><span>${escapeHtml(strategy.default_model || "-")}</span></div>
        <div class="list-row"><strong>快速模型</strong><span>${escapeHtml(strategy.fast_model || "-")}</span></div>
        <div class="list-row"><strong>复杂度路由</strong><span>${escapeHtml(strategy.complexity_routing_enabled ? "enabled" : "disabled")}</span></div>
        <div class="list-row"><strong>页面级策略</strong><span>${escapeHtml((strategy.page_strategies || []).length)}</span></div>
      </div>
    `;

    document.getElementById("model-default-model").value = strategy.default_model || "";
    document.getElementById("model-fast-model").value = strategy.fast_model || "";
    document.getElementById("model-routing-enabled").value = String(Boolean(strategy.complexity_routing_enabled));
    document.getElementById("model-complexity-overrides").value = JSON.stringify(strategy.complexity_overrides || {}, null, 2);
    document.getElementById("model-fallback-map").value = JSON.stringify(strategy.fallback_map || {}, null, 2);
    document.getElementById("model-page-strategies").value = JSON.stringify(strategy.page_strategies || [], null, 2);
  } catch (error) {
    toast(error.message || "模型与成本加载失败", "err");
  }
}

async function saveModelStrategy() {
  try {
    await api("PUT", "/models/strategy", {
      default_model: document.getElementById("model-default-model").value.trim(),
      fast_model: document.getElementById("model-fast-model").value.trim(),
      complexity_routing_enabled: document.getElementById("model-routing-enabled").value === "true",
      complexity_overrides: parseJsonInput(document.getElementById("model-complexity-overrides").value),
      fallback_map: parseJsonInput(document.getElementById("model-fallback-map").value),
      page_strategies: parseJsonInput(document.getElementById("model-page-strategies").value),
    });
    toast("模型策略已保存", "ok");
    loadModels();
  } catch (error) {
    toast(error.message || "保存模型策略失败", "err");
  }
}

async function loadSupport() {
  try {
    const [approvals, tickets, appeals, riskCases] = await Promise.all([
      api("GET", "/approvals?status=pending&limit=20"),
      api("GET", "/support/tickets?limit=20"),
      api("GET", "/support/appeals?limit=20"),
      api("GET", "/support/risk-cases?limit=20"),
    ]);
    document.getElementById("support-approvals").innerHTML = renderSimpleList(
      approvals.approvals || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.title || item.step_id || "审批")}</strong></div>
          <div class="meta mono">${escapeHtml(shortText(item.workflow_id, 18))}</div>
        </div>
        <div class="toolbar">
          <button class="btn btn-primary" onclick="reviewApproval('${String(item.id).replaceAll("'", "\\'")}', true)">通过</button>
          <button class="btn btn-danger" onclick="reviewApproval('${String(item.id).replaceAll("'", "\\'")}', false)">拒绝</button>
        </div>
      </div>`,
      "暂无人工审核任务。"
    );

    document.getElementById("support-tickets").innerHTML = renderSimpleList(
      tickets.tickets || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.title || "-")}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta">${escapeHtml(item.user_id || "-")} · ${escapeHtml(item.ticket_type || "general")} · ${escapeHtml(item.priority || "normal")}</div>
        </div>
        <div class="toolbar">
          ${item.status !== "resolved" ? `<button class="btn btn-primary" onclick="resolveTicket('${String(item.id).replaceAll("'", "\\'")}')">解决</button>` : ""}
        </div>
      </div>`,
      "暂无工单。"
    );

    document.getElementById("support-appeals").innerHTML = renderSimpleList(
      appeals.appeals || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.title || "-")}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta">${escapeHtml(item.user_id || "-")} · ${escapeHtml(item.appeal_type || "general")}</div>
        </div>
        <div class="toolbar">
          ${item.status === "pending" ? `
            <button class="btn btn-primary" onclick="reviewAppeal('${String(item.id).replaceAll("'", "\\'")}', true)">通过</button>
            <button class="btn btn-danger" onclick="reviewAppeal('${String(item.id).replaceAll("'", "\\'")}', false)">驳回</button>
          ` : ""}
        </div>
      </div>`,
      "暂无申诉。"
    );

    document.getElementById("support-risk-cases").innerHTML = renderSimpleList(
      riskCases.cases || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.title || "-")}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta">${escapeHtml(item.user_id || "-")} · ${escapeHtml(item.case_type || "general")} · ${escapeHtml(item.severity || "medium")}</div>
        </div>
        <div class="toolbar">
          ${item.status !== "resolved" ? `<button class="btn btn-primary" onclick="resolveRiskCase('${String(item.id).replaceAll("'", "\\'")}')">结案</button>` : ""}
        </div>
      </div>`,
      "暂无风险案件。"
    );
  } catch (error) {
    document.getElementById("support-approvals").innerHTML = renderEmpty("人工审核队列加载失败。");
    toast(error.message || "客服与风控加载失败", "err");
  }
}

async function saveTicket() {
  try {
    await api("POST", "/support/tickets", {
      user_id: document.getElementById("ticket-user").value.trim(),
      ticket_type: document.getElementById("ticket-type").value.trim() || "general",
      title: document.getElementById("ticket-title").value.trim(),
      priority: document.getElementById("ticket-priority").value,
      detail: parseJsonInput(document.getElementById("ticket-detail").value),
    });
    toast("工单已创建", "ok");
    ["ticket-user","ticket-type","ticket-title","ticket-detail"].forEach(id => document.getElementById(id).value = "");
    document.getElementById("ticket-priority").value = "normal";
    loadSupport();
  } catch (error) {
    toast(error.message || "创建工单失败", "err");
  }
}

async function resolveTicket(ticketId) {
  const resolution = window.prompt("处理结果", "") ?? "";
  try {
    await api("POST", `/support/tickets/${ticketId}`, {status: "resolved", resolution});
    toast("工单已解决", "ok");
    loadSupport();
  } catch (error) {
    toast(error.message || "处理工单失败", "err");
  }
}

async function saveAppeal() {
  try {
    await api("POST", "/support/appeals", {
      user_id: document.getElementById("appeal-user").value.trim(),
      appeal_type: document.getElementById("appeal-type").value.trim() || "general",
      title: document.getElementById("appeal-title").value.trim(),
      related_resource_type: document.getElementById("appeal-resource-type").value.trim(),
      related_resource_id: document.getElementById("appeal-resource-id").value.trim(),
      detail: parseJsonInput(document.getElementById("appeal-detail").value),
    });
    toast("申诉已创建", "ok");
    ["appeal-user","appeal-type","appeal-title","appeal-resource-type","appeal-resource-id","appeal-detail"].forEach(id => document.getElementById(id).value = "");
    loadSupport();
  } catch (error) {
    toast(error.message || "创建申诉失败", "err");
  }
}

async function reviewAppeal(appealId, approved) {
  const decisionNote = window.prompt(approved ? "通过说明" : "驳回原因", "") ?? "";
  try {
    await api("POST", `/support/appeals/${appealId}/review`, {approved, decision_note: decisionNote});
    toast(approved ? "申诉已通过" : "申诉已驳回", "ok");
    loadSupport();
  } catch (error) {
    toast(error.message || "处理申诉失败", "err");
  }
}

async function saveRiskCase() {
  try {
    await api("POST", "/support/risk-cases", {
      user_id: document.getElementById("risk-user").value.trim(),
      case_type: document.getElementById("risk-type").value.trim() || "general",
      title: document.getElementById("risk-title").value.trim(),
      severity: document.getElementById("risk-severity").value,
      related_resource_type: document.getElementById("risk-resource-type").value.trim(),
      related_resource_id: document.getElementById("risk-resource-id").value.trim(),
      detail: parseJsonInput(document.getElementById("risk-detail").value),
    });
    toast("风险案件已创建", "ok");
    ["risk-user","risk-type","risk-title","risk-resource-type","risk-resource-id","risk-detail"].forEach(id => document.getElementById(id).value = "");
    document.getElementById("risk-severity").value = "medium";
    loadSupport();
  } catch (error) {
    toast(error.message || "创建风险案件失败", "err");
  }
}

async function resolveRiskCase(caseId) {
  const resolution = window.prompt("结案说明", "") ?? "";
  try {
    await api("POST", `/support/risk-cases/${caseId}`, {status: "resolved", resolution});
    toast("风险案件已结案", "ok");
    loadSupport();
  } catch (error) {
    toast(error.message || "处理风险案件失败", "err");
  }
}

async function loadSystem() {
  try {
    const [health, durable, guardrails, runtimeConfig, managedConfig, releases] = await Promise.all([
      api("GET", "/health/deep"),
      api("GET", "/durable"),
      api("GET", "/guardrails"),
      api("GET", "/config"),
      api("GET", "/config/system"),
      api("GET", "/config/releases?limit=20"),
    ]);

    const components = Object.entries(health.components || {});
    document.getElementById("system-health").innerHTML = components.length ? components.map(([name, state]) => `
      <div class="list-row">
        <strong>${escapeHtml(name)}</strong>
        <span class="${statusBadge(state)}">${escapeHtml(state)}</span>
      </div>
    `).join("") : renderEmpty("暂无健康检查数据。");

    document.getElementById("system-runtime").innerHTML = `
      <div class="list">
        <div class="list-row"><strong>持久化模式</strong><span>${escapeHtml(durable.mode || "-")}</span></div>
        <div class="list-row"><strong>缓存任务</strong><span>${escapeHtml(durable.cached_tasks ?? 0)}</span></div>
        <div class="list-row"><strong>检查点</strong><span>${escapeHtml(durable.checkpoints ?? 0)}</span></div>
        <div class="list-row"><strong>护栏数量</strong><span>${escapeHtml(Object.keys(guardrails || {}).length)}</span></div>
      </div>
    `;

    const summaryFlags = Object.keys(managedConfig.feature_flags || {}).length;
    document.getElementById("system-config-summary").innerHTML = `
      <div class="list">
        <div class="list-row"><strong>站点名称</strong><span>${escapeHtml(managedConfig.site_name || "-")}</span></div>
        <div class="list-row"><strong>默认项目</strong><span>${escapeHtml(managedConfig.default_project_id || "-")}</span></div>
        <div class="list-row"><strong>支持邮箱</strong><span>${escapeHtml(managedConfig.support_email || "-")}</span></div>
        <div class="list-row"><strong>功能开关</strong><span>${escapeHtml(summaryFlags)}</span></div>
      </div>
    `;
    document.getElementById("system-config").textContent = JSON.stringify({
      managed_system_config: managedConfig,
      runtime_config_snapshot: runtimeConfig,
    }, null, 2);

    document.getElementById("system-site-name").value = managedConfig.site_name || "";
    document.getElementById("system-brand-subtitle").value = managedConfig.brand_subtitle || "";
    document.getElementById("system-timezone").value = managedConfig.timezone || "";
    document.getElementById("system-language").value = managedConfig.language || "";
    document.getElementById("system-support-email").value = managedConfig.support_email || "";
    document.getElementById("system-default-project").value = managedConfig.default_project_id || "";
    document.getElementById("system-billing-currency").value = managedConfig.billing_currency || "";
    document.getElementById("system-ops-notice").value = managedConfig.ops_notice || "";
    document.getElementById("system-feature-flags").value = JSON.stringify(managedConfig.feature_flags || {}, null, 2);
    document.getElementById("system-metadata").value = JSON.stringify(managedConfig.metadata || {}, null, 2);

    document.getElementById("system-releases").innerHTML = renderSimpleList(
      releases.releases || [],
      item => `<div class="list-row">
        <div>
          <div><strong>${escapeHtml(item.release_type || "-")}</strong> <span class="${statusBadge(item.status)}">${escapeHtml(item.status || "")}</span></div>
          <div class="meta mono">${escapeHtml(item.target_id || "-")}</div>
          <div class="meta">${escapeHtml(item.version_label || "-")} · ${escapeHtml(item.change_note || "")}</div>
        </div>
        <div class="toolbar">
          <button class="btn btn-danger" onclick="rollbackConfigRelease('${String(item.id).replaceAll("'", "\\'")}')">回滚</button>
        </div>
      </div>`,
      "暂无配置发布记录。"
    );
  } catch (error) {
    toast(error.message || "系统设置加载失败", "err");
  }
}

async function saveSystemConfig() {
  try {
    await api("PUT", "/config/system", {
      site_name: document.getElementById("system-site-name").value.trim(),
      brand_subtitle: document.getElementById("system-brand-subtitle").value.trim(),
      timezone: document.getElementById("system-timezone").value.trim(),
      language: document.getElementById("system-language").value.trim(),
      support_email: document.getElementById("system-support-email").value.trim(),
      default_project_id: document.getElementById("system-default-project").value.trim(),
      billing_currency: document.getElementById("system-billing-currency").value.trim(),
      ops_notice: document.getElementById("system-ops-notice").value.trim(),
      feature_flags: parseJsonInput(document.getElementById("system-feature-flags").value),
      metadata: parseJsonInput(document.getElementById("system-metadata").value),
    });
    toast("系统配置已保存", "ok");
    loadSystem();
  } catch (error) {
    toast(error.message || "保存系统配置失败", "err");
  }
}

async function publishConfigRelease() {
  try {
    const releaseType = document.getElementById("release-type").value;
    const managedTargetTypes = ["system_config", "model_strategy"];
    const targetId = document.getElementById("release-target-id").value.trim() || (managedTargetTypes.includes(releaseType) ? "global" : "");
    await api("POST", "/config/releases", {
      release_type: releaseType,
      target_id: targetId,
      version_label: document.getElementById("release-version").value.trim(),
      change_note: document.getElementById("release-note").value.trim(),
    });
    toast("配置快照已发布", "ok");
    ["release-target-id","release-version","release-note"].forEach(id => document.getElementById(id).value = "");
    document.getElementById("release-type").value = "template";
    syncReleaseTargetId();
    loadSystem();
  } catch (error) {
    toast(error.message || "发布配置失败", "err");
  }
}

async function rollbackConfigRelease(releaseId) {
  const changeNote = window.prompt("回滚说明", "") ?? "";
  try {
    await api("POST", `/config/releases/${releaseId}/rollback`, {change_note: changeNote});
    toast("配置已回滚", "ok");
    loadSystem();
    loadContent();
    loadModels();
  } catch (error) {
    toast(error.message || "回滚配置失败", "err");
  }
}

function syncReleaseTargetId() {
  const releaseType = document.getElementById("release-type").value;
  const targetInput = document.getElementById("release-target-id");
  if (["system_config", "model_strategy"].includes(releaseType)) {
    targetInput.value = "global";
    targetInput.placeholder = "global";
  } else {
    if (targetInput.value === "global") targetInput.value = "";
    targetInput.placeholder = "template-id / document-id / global";
  }
}

bootPanel();
syncReleaseTargetId();
