"""Private pilot dashboard and audit evidence export."""

from __future__ import annotations

import hashlib
import html
import io
import json
import zipfile

from .core import now
from .service import PostgresStore, Principal, format_cost_amount


def build_summary(store: PostgresStore, github_store, principal: Principal) -> dict:
    workspaces = store.list_workspaces(principal)
    projects = []
    for workspace in workspaces:
        if workspace.get("snapshot_count"):
            snapshot = store.latest_snapshot(principal, workspace["id"])
            for project in snapshot["payload"].get("projects", []):
                projects.append({"workspace": workspace["name"], **project})
    installations = github_store.list_installations(principal)
    deliveries = github_store.list_deliveries(principal, 20)
    audit = store.list_audit(principal, 20)
    costs = store.list_cost_records(principal, 50)
    cost_summary = store.summarize_costs(principal)
    acceptance = store.list_acceptance_items(principal)
    acceptance_summary = store.summarize_acceptance(principal)
    support = store.list_support_records(principal, 50)
    support_summary = store.summarize_support(principal)
    for item in cost_summary:
        item["amount"] = format_cost_amount(item["amount_micros"])
    snapshot_count = sum(item.get("snapshot_count", 0) for item in workspaces)
    stats = {
        "workspaces": len(workspaces),
        "snapshots": snapshot_count,
        "projects": len(projects),
        "ci_passing": sum(item.get("ci") == "PASSING" for item in projects),
        "pending_events": sum(item["status"] == "PENDING" for item in deliveries),
        "cost_records": len(costs),
        "acceptance_passed": acceptance_summary["passed"],
        "support_minutes": support_summary["minutes"],
    }
    onboarding = [
        {"label": "组织令牌可用", "complete": True},
        {"label": "创建工作空间", "complete": bool(workspaces)},
        {"label": "绑定 GitHub installation", "complete": bool(installations)},
        {"label": "接收 GitHub delivery", "complete": bool(deliveries)},
        {"label": "发布标准化 PR/CI 快照", "complete": snapshot_count > 0},
        {"label": "配置试点验收标准", "complete": bool(acceptance)},
        {"label": "记录客户确认", "complete": bool(acceptance) and
         acceptance_summary["customer_confirmed"] == len(acceptance)},
    ]
    return {
        "schema_version": 1,
        "kind": "yanxu.pilot_dashboard",
        "created_at": now(),
        "organization_slug": principal.organization_slug,
        "role": principal.role,
        "stats": stats,
        "onboarding": onboarding,
        "workspaces": workspaces,
        "projects": projects,
        "installations": installations,
        "deliveries": deliveries,
        "audit": audit,
        "costs": costs,
        "cost_summary": cost_summary,
        "acceptance": acceptance,
        "acceptance_summary": acceptance_summary,
        "support": support,
        "support_summary": support_summary,
        "data_boundary": "Dashboard contains normalized delivery, acceptance, support, cost and audit facts; source code, diffs, PR bodies, logs and credentials are excluded.",
    }


def build_audit_export(store: PostgresStore, principal: Principal, limit: int = 200) -> tuple[bytes, str]:
    payload = {
        "schema_version": 1,
        "kind": "yanxu.audit_export",
        "created_at": now(),
        "organization_slug": principal.organization_slug,
        "events": store.list_audit(principal, limit),
        "data_boundary": "Organization-scoped audit metadata only; no source code, diffs, PR bodies, logs or credentials.",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    payload["fingerprint"] = fingerprint
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return body, fingerprint


def build_pilot_bundle(store: PostgresStore, github_store, principal: Principal) -> tuple[bytes, str]:
    summary = build_summary(store, github_store, principal)
    audit_body, audit_fingerprint = build_audit_export(store, principal)
    files = {
        "summary.json": (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "costs.json": (json.dumps({
            "schema_version": 1,
            "kind": "yanxu.cost_export",
            "created_at": now(),
            "organization_slug": principal.organization_slug,
            "summary": summary["cost_summary"],
            "records": store.list_cost_records(principal, 200),
            "calculation_boundary": "Amounts are customer-recorded actual costs; Yanxu does not infer prices from token counts.",
        }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "audit.json": audit_body,
        "acceptance.json": (json.dumps({
            "schema_version": 1,
            "kind": "yanxu.pilot_acceptance_export",
            "created_at": now(),
            "organization_slug": principal.organization_slug,
            "summary": summary["acceptance_summary"],
            "items": summary["acceptance"],
            "claim_boundary": "Customer confirmation is an owner-recorded fact with an evidence reference; Yanxu does not provide an electronic signature or independently verify the customer.",
        }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "support.json": (json.dumps({
            "schema_version": 1,
            "kind": "yanxu.pilot_support_export",
            "created_at": now(),
            "organization_slug": principal.organization_slug,
            "summary": summary["support_summary"],
            "records": summary["support"],
            "calculation_boundary": "Minutes are owner-recorded support effort and are not inferred from activity logs.",
        }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    manifest = {
        "schema_version": 1,
        "kind": "yanxu.pilot_evidence_bundle",
        "created_at": now(),
        "organization_slug": principal.organization_slug,
        "files": [{"path": path, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
                  for path, body in sorted(files.items())],
        "audit_fingerprint": audit_fingerprint,
        "efficiency_claim_status": "NOT_MEASURED",
        "acceptance_claim_status": summary["acceptance_summary"]["status"],
        "claim_boundary": "The bundle records delivery state, actual costs, support effort and owner-entered acceptance evidence; it does not independently prove the customer identity, revenue, or an efficiency percentage.",
    }
    manifest_canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")
    bundle_fingerprint = hashlib.sha256(manifest_canonical).hexdigest()
    manifest["fingerprint"] = bundle_fingerprint
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, body in sorted(files.items()):
            archive.writestr(path, body)
    return buffer.getvalue(), bundle_fingerprint


def _escape(value) -> str:
    return html.escape(str(value if value is not None else ""))


def render_login() -> str:
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>研序团队登录</title><link rel="icon" href="data:,"><style>body{margin:0;background:#eef5f2;color:#14352e;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif;display:grid;min-height:100vh;place-items:center}.box{width:min(420px,calc(100% - 40px));background:white;border:1px solid #d8e7e0;border-radius:18px;padding:30px;box-shadow:0 18px 60px #14352e18}h1{margin:0 0 6px;font-size:30px}.muted{color:#658078}label{display:block;font-weight:700;margin:24px 0 8px}input{box-sizing:border-box;width:100%;padding:13px;border:1px solid #b9cdc5;border-radius:9px;font:inherit}.username{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}button{width:100%;margin-top:14px;padding:13px;border:0;border-radius:9px;background:#087a61;color:white;font:inherit;font-weight:800;cursor:pointer}.error{min-height:24px;color:#a63232;margin-top:9px}.note{font-size:13px;background:#f3f8f6;padding:11px;border-radius:8px}</style></head><body><main class="box"><div class="muted">YANXU DEV PRIVATE SERVICE</div><h1>研序团队工作台</h1><p class="muted">使用组织 API Token 建立 8 小时的本机浏览器会话。</p><form id="login"><input class="username" type="text" name="username" value="yanxu-team-token" autocomplete="username" tabindex="-1" aria-hidden="true"><label for="token">组织 Token</label><input id="token" type="password" minlength="24" autocomplete="current-password" required><button>进入工作台</button><div id="error" class="error" role="alert"></div></form><p class="note">Token 只发送到当前私有服务，用于换取 HttpOnly 会话，不写入页面存储。</p></main><script>document.getElementById('login').addEventListener('submit',async(e)=>{e.preventDefault();const error=document.getElementById('error');error.textContent='';const response=await fetch('/v1/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:document.getElementById('token').value})});if(response.ok){location.href='/app';return}const body=await response.json().catch(()=>({}));error.textContent=body.detail||'登录失败';});</script></body></html>"""


def render_dashboard(summary: dict) -> str:
    stats = summary["stats"]
    onboarding = "".join(
        f'<li class="{"done" if item["complete"] else "todo"}"><b>{"✓" if item["complete"] else "○"}</b> {_escape(item["label"])}</li>'
        for item in summary["onboarding"]
    )
    workspace_rows = "".join(
        "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            item["name"], item.get("snapshot_count", 0), item.get("latest_snapshot_at") or "—"
        )) + "</tr>" for item in summary["workspaces"]
    ) or '<tr><td colspan="3">尚未创建工作空间。</td></tr>'
    project_rows = "".join(
        "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            item["workspace"], item.get("repo") or "—", item.get("pr") or "—",
            item.get("ci") or "—", item.get("assessment") or "—"
        )) + "</tr>" for item in summary["projects"]
    ) or '<tr><td colspan="5">尚未发布标准化 PR/CI 快照。</td></tr>'
    delivery_rows = "".join(
        "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            item["event_name"], item.get("action") or "—", item["status"],
            item.get("facts", {}).get("repository") or "—", item["received_at"]
        )) + "</tr>" for item in summary["deliveries"]
    ) or '<tr><td colspan="5">尚未收到已绑定 installation 的事件。</td></tr>'
    audit_rows = "".join(
        "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            item["action"], item["resource_type"], item["created_at"]
        )) + "</tr>" for item in summary["audit"]
    ) or '<tr><td colspan="3">暂无审计事件。</td></tr>'
    cost_summary = "".join(
        f'<span class="pill">{_escape(item["currency"])} · {_escape(item["category"])} · {_escape(item["amount"])}</span>'
        for item in summary["cost_summary"]
    ) or '<span class="muted">尚未记录实际成本。</span>'
    cost_rows = "".join(
        "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            item["category"], f'{item["currency"]} {item["amount"]}', item["evidence_ref"], item["created_at"]
        )) + "</tr>" for item in summary["costs"]
    ) or '<tr><td colspan="4">尚未记录实际成本。</td></tr>'
    acceptance_summary = summary["acceptance_summary"]
    acceptance_status_labels = {
        "NOT_CONFIGURED": "尚未配置",
        "NEEDS_REVIEW": "待验收",
        "INTERNAL_PASS": "内部通过",
        "CUSTOMER_CONFIRMATION_RECORDED": "已记录客户确认",
        "PENDING": "待验收",
        "PASS": "通过",
        "FAIL": "未通过",
    }
    acceptance_rows = []
    for item in summary["acceptance"]:
        status_cell = _escape(
            f'{acceptance_status_labels[item["status"]]} · {item.get("evidence_ref") or "—"}')
        if summary["role"] == "owner":
            options = "".join(
                f'<option value="{status}"{" selected" if item["status"] == status else ""}>{acceptance_status_labels[status]}</option>'
                for status in ("PENDING", "PASS", "FAIL")
            )
            checked = " checked" if item["customer_confirmed"] else ""
            status_cell = (f'<form class="acceptance-update" data-id="{_escape(item["id"])}">'
                           f'<select aria-label="验收状态">{options}</select>'
                           f'<input aria-label="验收证据" maxlength="500" value="{_escape(item.get("evidence_ref") or "")}" placeholder="证据引用">'
                           f'<label><input type="checkbox"{checked}> 客户确认</label><button>保存</button>'
                           '<span class="error" role="alert"></span></form>')
        acceptance_rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in (
            _escape(item["criterion"]), status_cell,
            "是" if item["customer_confirmed"] else "否", _escape(item["updated_at"])
        )) + "</tr>")
    acceptance_rows = "".join(acceptance_rows) or '<tr><td colspan="4">尚未配置试点验收标准。</td></tr>'
    support_rows = "".join(
        "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            item["category"], item["minutes"], item["evidence_ref"], item["created_at"]
        )) + "</tr>" for item in summary["support"]
    ) or '<tr><td colspan="4">尚未记录支持投入。</td></tr>'
    owner_form = ""
    acceptance_form = ""
    support_form = ""
    if summary["role"] == "owner":
        owner_form = """<form id="cost-form" class="cost-form"><select id="category" aria-label="成本类型"><option value="model">模型</option><option value="ci">CI</option><option value="infrastructure">基础设施</option><option value="support">支持</option><option value="custom">其他</option></select><input id="amount" aria-label="金额" inputmode="decimal" placeholder="金额，例如 12.50" required><input id="currency" aria-label="币种" value="CNY" pattern="[A-Z]{3}" maxlength="3" required><input id="evidence" aria-label="证据引用" placeholder="账单、工单或记录引用" maxlength="500" required><button>记录成本</button><span id="cost-error" class="error" role="alert"></span></form><script>document.getElementById('cost-form').addEventListener('submit',async(e)=>{e.preventDefault();const error=document.getElementById('cost-error');error.textContent='';const response=await fetch('/v1/costs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:document.getElementById('category').value,amount:document.getElementById('amount').value,currency:document.getElementById('currency').value.toUpperCase(),evidence_ref:document.getElementById('evidence').value})});if(response.ok){location.reload();return}const body=await response.json().catch(()=>({}));error.textContent=body.detail||'记录失败';});</script>"""
        acceptance_form = """<form id="acceptance-form" class="entry-form"><input id="criterion" aria-label="验收标准" maxlength="240" placeholder="验收标准，例如：三个目标仓库 CI 证据可见" required><button>新增标准</button><span id="acceptance-error" class="error" role="alert"></span></form><script>document.getElementById('acceptance-form').addEventListener('submit',async(e)=>{e.preventDefault();const error=document.getElementById('acceptance-error');error.textContent='';const response=await fetch('/v1/pilot/acceptance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({criterion:document.getElementById('criterion').value})});if(response.ok){location.reload();return}const body=await response.json().catch(()=>({}));error.textContent=body.detail||'新增失败';});document.addEventListener('submit',async(e)=>{const form=e.target;if(!form.classList.contains('acceptance-update'))return;e.preventDefault();const error=form.querySelector('.error');error.textContent='';const inputs=form.querySelectorAll('input');const response=await fetch('/v1/pilot/acceptance/'+form.dataset.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:form.querySelector('select').value,evidence_ref:inputs[0].value||null,customer_confirmed:inputs[1].checked})});if(response.ok){location.reload();return}const body=await response.json().catch(()=>({}));error.textContent=body.detail||'保存失败';});</script>"""
        support_form = """<form id="support-form" class="support-form"><select id="support-category" aria-label="支持类型"><option value="onboarding">接入</option><option value="configuration">配置</option><option value="incident">故障</option><option value="training">培训</option><option value="custom">其他</option></select><input id="support-minutes" aria-label="支持分钟" type="number" min="1" max="100000" placeholder="分钟" required><input id="support-evidence" aria-label="支持证据" maxlength="500" placeholder="工单、会议纪要或记录引用" required><button>记录支持</button><span id="support-error" class="error" role="alert"></span></form><script>document.getElementById('support-form').addEventListener('submit',async(e)=>{e.preventDefault();const error=document.getElementById('support-error');error.textContent='';const response=await fetch('/v1/pilot/support',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:document.getElementById('support-category').value,minutes:Number(document.getElementById('support-minutes').value),evidence_ref:document.getElementById('support-evidence').value})});if(response.ok){location.reload();return}const body=await response.json().catch(()=>({}));error.textContent=body.detail||'记录失败';});</script>"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>研序团队工作台</title><link rel="icon" href="data:,"><style>body{{margin:0;background:#edf4f1;color:#14352e;font:14px/1.55 system-ui,-apple-system,"PingFang SC",sans-serif}}main{{max-width:1180px;margin:auto;padding:24px 18px 50px}}header,section{{background:white;border:1px solid #d8e7e0;border-radius:15px;padding:20px;margin-bottom:16px}}header{{background:#14352e;color:#f4fff9;display:flex;justify-content:space-between;gap:18px;align-items:center}}h1{{margin:0;font-size:28px}}h2{{margin:0 0 12px;font-size:18px}}.muted{{color:#6b837c}}header .muted{{color:#c2d8cf}}.actions{{display:flex;gap:8px;align-items:center}}a,button{{color:#087a61}}header a,header button{{background:#f4fff9;color:#14352e;border:0;border-radius:8px;padding:9px 12px;text-decoration:none;font:inherit;font-weight:700;cursor:pointer}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}}.card{{background:white;border:1px solid #d8e7e0;border-radius:12px;padding:15px}}.value{{font-size:25px;font-weight:850}}.label{{color:#6b837c;font-size:12px}}.grid{{display:grid;grid-template-columns:1fr 2fr;gap:16px}}ul{{padding:0;list-style:none;margin:0}}li{{padding:8px 0;border-bottom:1px solid #edf3f0}}.done b{{color:#087a61}}.todo{{color:#8b6a28}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:9px 7px;border-bottom:1px solid #e8f0ec;overflow-wrap:anywhere}}th{{color:#6b837c}}.boundary{{background:#f4f8f6;border-left:4px solid #087a61;padding:11px 13px}}.pill{{display:inline-block;background:#edf6f2;border-radius:99px;padding:5px 9px;margin:0 6px 8px 0}}.cost-form{{display:grid;grid-template-columns:130px 160px 90px 1fr auto;gap:8px;margin:12px 0}}.entry-form{{display:grid;grid-template-columns:1fr auto;gap:8px;margin:12px 0}}.support-form{{display:grid;grid-template-columns:130px 120px 1fr auto;gap:8px;margin:12px 0}}.acceptance-update{{display:grid;grid-template-columns:90px 1fr 110px auto;gap:6px;align-items:center}}.cost-form input,.cost-form select,.cost-form button,.entry-form input,.entry-form button,.support-form input,.support-form select,.support-form button,.acceptance-update input,.acceptance-update select,.acceptance-update button{{box-sizing:border-box;border:1px solid #b9cdc5;border-radius:7px;padding:9px;font:inherit}}.cost-form button,.entry-form button,.support-form button,.acceptance-update button{{background:#087a61;color:white;font-weight:700}}.error{{color:#a63232;grid-column:1/-1}}@media(max-width:800px){{.cards{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}header{{align-items:flex-start;flex-direction:column}}.cost-form,.support-form,.acceptance-update{{grid-template-columns:1fr 1fr}}}}@media(max-width:460px){{.cards,.cost-form,.entry-form,.support-form,.acceptance-update{{grid-template-columns:1fr}}}}</style></head><body><main><header><div><div class="muted">YANXU DEV · {_escape(summary['organization_slug'])} · {_escape(summary['role'])}</div><h1>团队交付工作台</h1><div class="muted">状态生成于 {_escape(summary['created_at'])}</div></div><div class="actions"><a href="/v1/pilot/export">导出试点包</a><a href="/v1/audit/export">导出审计</a><form method="post" action="/logout"><button>退出</button></form></div></header><div class="cards"><div class="card"><div class="value">{stats['workspaces']}</div><div class="label">工作空间</div></div><div class="card"><div class="value">{stats['projects']}</div><div class="label">已同步项目</div></div><div class="card"><div class="value">{stats['ci_passing']}</div><div class="label">CI 通过</div></div><div class="card"><div class="value">{stats['pending_events']}</div><div class="label">待处理事件</div></div><div class="card"><div class="value">{stats['snapshots']}</div><div class="label">证据快照</div></div><div class="card"><div class="value">{stats['cost_records']}</div><div class="label">成本记录</div></div><div class="card"><div class="value">{stats['acceptance_passed']}</div><div class="label">验收通过项</div></div><div class="card"><div class="value">{stats['support_minutes']}</div><div class="label">支持分钟</div></div></div><div class="grid"><section><h2>接入进度</h2><ul>{onboarding}</ul></section><section><h2>工作空间</h2><table><thead><tr><th>名称</th><th>快照</th><th>最近发布</th></tr></thead><tbody>{workspace_rows}</tbody></table></section></div><section><h2>PR / CI 证据</h2><table><thead><tr><th>工作空间</th><th>仓库</th><th>PR</th><th>CI</th><th>判断</th></tr></thead><tbody>{project_rows}</tbody></table></section><section><h2>试点验收</h2><div><span class="pill">状态 · {_escape(acceptance_status_labels[acceptance_summary['status']])}</span><span class="pill">通过 · {acceptance_summary['passed']} / {acceptance_summary['total']}</span><span class="pill">客户确认 · {acceptance_summary['customer_confirmed']}</span></div>{acceptance_form}<table><thead><tr><th>验收标准</th><th>状态与证据</th><th>客户确认</th><th>更新时间</th></tr></thead><tbody>{acceptance_rows}</tbody></table><p class="muted">“客户确认”是 owner 按证据引用录入的事实，不构成电子签名或独立身份核验。</p></section><section><h2>支持投入</h2>{support_form}<table><thead><tr><th>类型</th><th>分钟</th><th>证据引用</th><th>记录时间</th></tr></thead><tbody>{support_rows}</tbody></table><p class="muted">累计 {summary['support_summary']['minutes']} 分钟；平台不从活动日志推测支持工时。</p></section><section><h2>实际成本</h2><div>{cost_summary}</div>{owner_form}<table><thead><tr><th>类型</th><th>金额</th><th>证据引用</th><th>记录时间</th></tr></thead><tbody>{cost_rows}</tbody></table><p class="muted">不同币种分别汇总；金额由客户按账单或工时记录，平台不根据 Token 数量猜测价格。</p></section><section><h2>GitHub Webhook</h2><table><thead><tr><th>事件</th><th>动作</th><th>状态</th><th>仓库</th><th>接收时间</th></tr></thead><tbody>{delivery_rows}</tbody></table></section><section><h2>最近审计</h2><table><thead><tr><th>动作</th><th>资源</th><th>时间</th></tr></thead><tbody>{audit_rows}</tbody></table></section><p class="boundary">{_escape(summary['data_boundary'])}</p></main></body></html>"""
