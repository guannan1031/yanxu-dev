import html
import json
from urllib.parse import urlparse


def escape(value):
    return html.escape(str(value))


def github_link(url, label):
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        return escape(label)
    return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(label)} ↗</a>'


def render(evidence):
    snapshot, policy, ai = evidence["snapshot"], evidence["assessment"], evidence["ai"]
    pr = snapshot["pr"]
    answer = ai.get("answer", {})
    rows = "".join(f'<tr><td>{escape(c["name"])}</td><td>{escape(c["app"])}</td><td>{escape(c["conclusion"] or c["status"])}</td><td>{github_link(c["url"], "查看检查")}</td></tr>' for c in snapshot["checks"])
    issues = "".join(f'<article><b>{escape(f["severity"])} · {escape(f["path"])}</b><p>{escape(f["evidence"])}</p><p><strong>建议：</strong>{escape(f["recommendation"])}</p></article>' for f in answer.get("findings", []))
    blockers = "".join(f'<li>{escape(x)}</li>' for x in policy["blockers"])
    limitations = snapshot["warnings"] + answer.get("limitations", [])
    limitations += ["这是只读诊断报告，不是审批或合并授权。报告生成后，PR 和 CI 仍可能变化。", "未测量人工基线，不能从报告耗时推导研发效率提升百分比。", "AI 修复建议未经执行；人工审查、修复、验证和部署均需另行完成。"]
    notes = "".join(f'<li>{escape(x)}</li>' for x in limitations)
    plan = "".join(f'<li>{escape(x)}</li>' for x in answer.get("repair_plan", []))
    files = "".join(f'<details><summary>{escape(f["path"])} · +{f["additions"]} / −{f["deletions"]}</summary><pre>{escape(f["patch"] or "No patch available")}</pre></details>' for f in snapshot["files"])
    ai_status = "已完成" if ai["status"] == "completed" else "未运行"
    if ai["status"] == "failed":
        ai_status = "执行失败"
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>研序 · PR #{pr['number']} 交付审查</title><link rel="icon" href="data:,"><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f3f5f1;color:#203a2d;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;line-height:1.8}}header{{background:#1b3429;color:#e4f0e5;padding:35px max(5vw,20px)}}main{{max-width:1160px;margin:auto;padding:32px 20px}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:20px}}h3{{font-size:16px}}p{{overflow-wrap:anywhere}}a{{color:#36734b}}header a{{color:#c1e5b1}}.eyebrow{{letter-spacing:3px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card,section{{background:white;border:1px solid #dbe5da;border-radius:14px;padding:24px;margin-bottom:20px}}.card b{{display:block;font-size:25px}}.muted{{color:#61766a;font-size:13px}}.badge{{display:inline-block;background:#f7eacb;color:#795020;border-radius:18px;padding:3px 12px;font-size:13px}}.binding{{overflow-wrap:anywhere;font:12px ui-monospace,monospace}}article{{border-left:3px solid #7ba077;padding:8px 20px;background:#f5f8f2;margin:16px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f0;border-radius:7px;padding:16px;font-size:12px;line-height:1.7}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;border-bottom:1px solid #e1e7df;padding:10px}}.scroll{{overflow:auto}}summary{{cursor:pointer;padding:12px 0}}li{{margin:7px 0}}footer{{font-size:12px;color:#65786a;padding:24px 0}}button{{border:1px solid #9db59e;color:#dcebdc;background:transparent;border-radius:7px;padding:7px 15px;cursor:pointer}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}h1{{font-size:24px}}section{{padding:18px}}}}@media print{{button{{display:none}}body{{background:white}}header{{color:#203a2d;background:white}}section{{break-inside:avoid}}}}
    </style></head><body><header><div class="eyebrow">YANXU DEV / PR DELIVERY REVIEW</div><h1>{escape(pr['title'])}</h1><p>{github_link(pr['url'], snapshot['repo']+' #'+str(pr['number']))} · {escape(snapshot['captured_at'])}</p><button onclick="window.print()">打印报告</button></header><main>
    <div class="grid"><div class="card"><span class="muted">确定性检查</span><b>{escape(policy['status'])}</b><span class="muted">不执行自动 merge</span></div><div class="card"><span class="muted">AI 诊断</span><b>{ai_status}</b><span class="muted">依据本次采集的代码与检查</span></div><div class="card"><span class="muted">报告生成耗时</span><b>{evidence['elapsed_seconds']:.1f}s</b><span class="muted">机器墙钟时间，不是人工净节省</span></div></div>
    <section><h2>当前结论</h2><p>{escape(answer.get('summary', ai.get('error', '未调用 AI；以下是 GitHub 事实与规则检查。')))}</p><ul>{blockers or '<li>未发现已采集检查中的阻断项，仍需人工审查与 GitHub 规则校验。</li>'}</ul><p class="muted">敏感路径：{escape(', '.join(policy['sensitive_paths']) or '本次未识别')} · 当前 head 的审批人数：{len(policy['current_head_approvals'])}</p></section>
    <section><h2>AI 发现与修复建议</h2>{issues or '<p>没有 AI 发现记录；不表示代码无缺陷。</p>'}<ol>{plan}</ol><details><summary>查看建议补丁 · 尚未应用或验证</summary><pre>{escape(answer.get('suggested_patch', '') or '无建议补丁')}</pre></details></section>
    <section><h2>CI 原始状态</h2><div class="scroll"><table><thead><tr><th>检查</th><th>来源</th><th>结果</th><th>证据</th></tr></thead><tbody>{rows or '<tr><td colspan="4">没有 Check Runs</td></tr>'}</tbody></table></div><p class="muted">Legacy status：{escape(json.dumps(snapshot['statuses'], ensure_ascii=False))}</p></section>
    <section><h2>版本与可追溯性</h2><p>HEAD <span class="binding">{escape(pr['head_sha'])}</span><br>BASE <span class="binding">{escape(pr['base_sha'])}</span></p><p class="binding">EVIDENCE {escape(snapshot['binding'])}</p><p>合并前执行 <code>python -m yanxu verify &lt;本次 evidence.json&gt;</code>，核对提交、基线、CI 和 Review 是否变化。UNCHANGED 仅表示本次重新采集时一致。</p></section>
    <section><h2>代码改动上下文</h2>{files}</section><section><h2>能力边界与费用</h2><ul>{notes}</ul><p class="muted">执行器：{escape(ai.get('executor','未调用'))}；Token 用量：{escape(json.dumps(ai.get('usage'),ensure_ascii=False))}；金额：未核算。</p></section><footer>研序 · Yanxu Dev v0.1 · 开源 PR / CI 诊断助手 · 报告可能包含仓库内容，请按仓库原有范围分享。</footer></main></body></html>'''
