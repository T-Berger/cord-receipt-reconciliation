import Image from "next/image";
import demo from "./data/demo.json";
import OcrInspector from "./components/OcrInspector";

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: demo.claim.currency,
  maximumFractionDigits: 0,
});

function money(value: string | null) {
  return value === null ? "—" : currency.format(Number(value));
}

function shortRule(rule: string) {
  return rule.replaceAll("_", " ").toLowerCase();
}

function findingValueSummary(finding: { rule_id: string; claimed_value: string; evidence_value: string }) {
  switch (finding.rule_id) {
    case "CHANGE_NOT_REIMBURSABLE":
      return `${money(finding.evidence_value)} change excluded`;
    case "CASH_TENDERED_NOT_REIMBURSABLE":
      return `${money(finding.claimed_value)} tendered · ${money(finding.evidence_value)} paid`;
    case "UNSUPPORTED_CLAIM_COMPONENT":
      return `${money(finding.claimed_value)} unsupported`;
    default:
      return `Claim ${money(finding.claimed_value)} · Receipt ${money(finding.evidence_value)}`;
  }
}

const claimed = Number(demo.decision.claimed_amount);
const reimbursable = Number(demo.decision.reimbursable_amount);
const excluded = claimed - reimbursable;
const reimbursementRate = Math.round((reimbursable / claimed) * 100);

export default function Home() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" aria-label="Receipt Review home">
          <span className="brand-mark">RR</span>
          <span>
            <strong>Receipt Review</strong>
            <small>Finance risk operations</small>
          </span>
        </a>

        <nav aria-label="Dashboard sections">
          <a className="nav-link" href="#overview"><span>01</span>Overview</a>
          <a className="nav-link" href="#review"><span>02</span>Review</a>
          <a className="nav-link" href="#evidence"><span>03</span>Evidence</a>
          <a className="nav-link" href="#policy"><span>04</span>Policy checks</a>
          <a className="nav-link" href="#models"><span>05</span>Model QA</a>
          <a className="nav-link" href="#architecture"><span>06</span>Architecture</a>
          <a className="nav-link" href="#trace"><span>07</span>Trace</a>
        </nav>

        <div className="sidebar-foot">
          <span className="connection-dot" />
          <div>
            <strong>Langfuse cloud verified</strong>
            <small>{demo.trace.backend_observation_count} observations · {demo.trace.backend_score_count} score events</small>
          </div>
        </div>
      </aside>

      <main>
        <header className="topbar" id="overview">
          <div>
            <p className="eyebrow">Challenge 01 / CORD v2</p>
            <h1>Receipt reconciliation</h1>
          </div>
          <div className="run-meta">
            <span className="live-pill"><i /> Sanitized evaluation</span>
            <span>{demo.trace.run_label}</span>
          </div>
        </header>

        <section className="decision-hero" aria-labelledby="decision-heading">
          <div className="decision-copy">
            <p className="section-kicker">Automated decision</p>
            <div className="decision-title-row">
              <span className="decision-icon">½</span>
              <div>
                <h2 id="decision-heading">Partially approved</h2>
                <p>{demo.decision.summary}</p>
              </div>
            </div>

            <div className="allocation" aria-label={`${reimbursementRate}% reimbursable`}>
              <div className="allocation-labels">
                <span>{reimbursementRate}% reimbursable</span>
                <span>{100 - reimbursementRate}% excluded</span>
              </div>
              <div className="allocation-bar"><span style={{ width: `${reimbursementRate}%` }} /></div>
            </div>
          </div>

          <div className="decision-amount">
            <p>Recommended reimbursement</p>
            <strong>{money(demo.decision.reimbursable_amount)}</strong>
            <div className="decision-delta">
              <span>of {money(demo.decision.claimed_amount)} claimed</span>
              <b>{money(String(excluded))} excluded</b>
            </div>
            <p className="no-followup"><span>✓</span> No additional evidence required</p>
          </div>
        </section>

        <section className="metric-grid" aria-label="Claim metrics">
          <article className="metric-card">
            <span className="metric-label">Claimed</span>
            <strong>{money(demo.claim.claimed_total)}</strong>
            <small>Cash tendered used as total</small>
          </article>
          <article className="metric-card">
            <span className="metric-label">Receipt total</span>
            <strong>{money(demo.receipt.total_paid)}</strong>
            <small>Validated by both extractors</small>
          </article>
          <article className="metric-card warning">
            <span className="metric-label">Variance</span>
            <strong>{money(String(excluded))}</strong>
            <small>Returned change, not expense</small>
          </article>
          <article className="metric-card">
            <span className="metric-label">Policy findings</span>
            <strong>{demo.decision.findings.length}</strong>
            <small>All materially explain the variance</small>
          </article>
        </section>

        <section className="section review-section" id="review">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Human in the loop</p>
              <h2>Finance review</h2>
            </div>
            <span className="review-ready"><i /> {demo.review.state}</span>
          </div>

          <div className="review-grid">
            <article className="panel queue-card">
              <div className="panel-head">
                <div><span className="queue-icon">01</span><strong>Receipt case queue</strong></div>
                <span>1 open case · sanitized demo</span>
              </div>
              <div className="queue-columns" aria-hidden="true">
                <span>Case</span><span>Decision</span><span>Claimed</span><span>Recommended</span><span>Variance</span><span>Evidence</span>
              </div>
              <div className="queue-row">
                <div><strong>{demo.claim.claim_id}</strong><small>{demo.claim.business_purpose}</small></div>
                <span className="queue-decision">Partial approval</span>
                <strong>{money(demo.claim.claimed_total)}</strong>
                <strong className="recommended-value">{money(demo.decision.reimbursable_amount)}</strong>
                <strong className="variance-value">{money(String(excluded))} · 60%</strong>
                <span className="evidence-complete">{demo.review.evidence_state}</span>
              </div>
              <div className="queue-context">
                <span>Cash payment</span>
                <span>2 × Thai iced tea</span>
                <span>Full OCR agreement</span>
                <span>4 policy controls</span>
              </div>
            </article>

            <article className="panel reviewer-card">
              <div className="panel-head">
                <div><span className="reviewer-icon">✓</span><strong>Reviewer checklist</strong></div>
                <span>{demo.review.reviewer}</span>
              </div>
              <ul className="review-checklist">
                {demo.review.checklist.map((item) => <li key={item}><span>✓</span>{item}</li>)}
              </ul>
              <div className="review-recommendation">
                <span>Recommended action</span>
                <strong>{demo.review.recommended_action}</strong>
                <b>{money(demo.decision.reimbursable_amount)}</b>
              </div>
              <div className="demo-actions" aria-label="Disabled demonstration review actions">
                <button type="button" disabled>Approve recommended</button>
                <button type="button" disabled>Request evidence</button>
                <button type="button" disabled>Escalate</button>
              </div>
              <small className="demo-action-note">Demo actions are read-only; production use requires authorization and audit logging.</small>
            </article>
          </div>
        </section>

        <section className="section" id="evidence">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Source of truth</p>
              <h2>Receipt evidence</h2>
            </div>
            <span className="source-pill">CORD v2 · {demo.split} / row {demo.row_index}</span>
          </div>

          <div className="evidence-grid">
            <article className="receipt-card panel">
              <div className="panel-head">
                <div><span className="paper-icon">▤</span><strong>Original receipt</strong></div>
                <span>121.8 KB</span>
              </div>
              <div className="receipt-stage">
                <Image
                  src="/receipt.jpg"
                  width={840}
                  height={1188}
                  priority
                  unoptimized
                  alt="CORD v2 receipt showing Thai iced tea, subtotal 40,000, cash 100,000 and change 60,000"
                />
              </div>
              <div className="receipt-caption">
                <span>Image evidence</span>
                <b>Verified</b>
              </div>
            </article>

            <article className="panel comparison-card">
              <div className="panel-head">
                <div><span className="compare-icon">⇄</span><strong>Claim vs. receipt</strong></div>
                <span className="mismatch-count">1 overclaim pattern · 3 discrepancies · 4 controls</span>
              </div>

              <div className="claim-meta">
                <div><span>Claim ID</span><strong>{demo.claim.claim_id}</strong></div>
                <div><span>Employee</span><strong>{demo.claim.employee_id}</strong></div>
                <div><span>Purpose</span><strong>{demo.claim.business_purpose}</strong></div>
                <div><span>Payment</span><strong>{demo.claim.payment_method}</strong></div>
              </div>

              <div className="comparison-table" role="table" aria-label="Claim and receipt field comparison">
                <div className="comparison-row header" role="row">
                  <span role="columnheader">Field</span><span role="columnheader">Claim</span><span role="columnheader">Receipt</span><span role="columnheader">Result</span>
                </div>
                <div className="comparison-row" role="row">
                  <span role="cell">Item total</span><b role="cell">{money(demo.claim.claimed_subtotal)}</b><b role="cell">{money(demo.receipt.subtotal)}</b><em className="match" role="cell">Match</em>
                </div>
                <div className="comparison-row mismatch" role="row">
                  <span role="cell">Total paid</span><b role="cell">{money(demo.claim.claimed_total)}</b><b role="cell">{money(demo.receipt.total_paid)}</b><em role="cell">Mismatch</em>
                </div>
                <div className="comparison-row mismatch" role="row">
                  <span role="cell">Cash tendered</span><b role="cell">{money(demo.claim.claimed_total)}</b><b role="cell">{money(demo.receipt.cash_tendered)}</b><em role="cell">Not expense</em>
                </div>
                <div className="comparison-row mismatch" role="row">
                  <span role="cell">Change</span><b role="cell">Included</b><b role="cell">{money(demo.receipt.change)}</b><em role="cell">Excluded</em>
                </div>
              </div>

              <div className="item-line">
                <div><span>2 ×</span><strong>THAI ICED TEA</strong></div>
                <strong>{money(demo.receipt.items[0].line_total)}</strong>
              </div>
              <p className="scenario-note"><span>Injected test scenario</span>{demo.claim.injected_scenario.replaceAll("_", " ")}</p>
            </article>
          </div>
        </section>

        <section className="section" id="policy">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Deterministic controls</p>
              <h2>Policy findings</h2>
            </div>
            <span className="status-note"><i /> Decision matches ground truth</span>
          </div>

          <div className="findings-list">
            {demo.decision.findings.map((finding, index) => (
              <article className="finding" key={finding.rule_id}>
                <span className="finding-number">{String(index + 1).padStart(2, "0")}</span>
                <div className="finding-body">
                  <div><strong>{shortRule(finding.rule_id)}</strong><span>{finding.severity}</span></div>
                  <p>{finding.message}</p>
                </div>
                <div className="finding-values">
                  <span>{finding.receipt_field}</span>
                  <strong>{findingValueSummary(finding)}</strong>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="section" id="models">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Extraction benchmark</p>
              <h2>Mistral OCR vs. Docling pipeline</h2>
            </div>
            <span className="source-pill">11 normalized fields</span>
          </div>

          <div className="model-grid">
            {demo.extractions.map((extraction) => (
                <article className="model-card panel" key={extraction.engine}>
                  <div className="model-title">
                    <div className={`model-logo ${extraction.engine}`}>{extraction.engine === "mistral" ? "M" : "D"}</div>
                    <div><h3>{extraction.label}</h3><span>{extraction.model}</span></div>
                    <strong>{Math.round(extraction.field_accuracy * 100)}%</strong>
                  </div>
                  <div className="accuracy-bar"><span style={{ width: `${extraction.field_accuracy * 100}%` }} /></div>
                  <div className="model-stats">
                    <div><span>Matched</span><b>{extraction.matched_fields}/{extraction.compared_fields}</b></div>
                    <div><span>Total</span><b>{money(extraction.total_paid)}</b></div>
                    <div><span>Change</span><b>{money(extraction.change)}</b></div>
                  </div>
                  <p className="strategy-note"><span>Structuring strategy</span>{extraction.strategy}</p>
                </article>
            ))}
          </div>

          <OcrInspector inspector={demo.ocr_inspector} />

          <div className="field-coverage panel">
            <div className="panel-head"><div><span className="compare-icon">✓</span><strong>Ground-truth field coverage</strong></div><span>22 / 22 checks passed</span></div>
            <div className="field-chips">
              {demo.evaluated_fields.map((field) => <span key={field}><i />{field}</span>)}
            </div>
          </div>
        </section>

        <section className="section" id="architecture">
          <div className="section-heading">
            <div>
              <p className="section-kicker">System design</p>
              <h2>Architecture pipeline</h2>
            </div>
            <span className="source-pill">Langfuse instrumented end to end</span>
          </div>

          <article className="architecture panel" aria-label="Receipt reconciliation architecture pipeline">
            <div className="architecture-main">
              <div className="pipeline-node input-node">
                <span>01 · Input</span>
                <strong>CORD v2 evidence</strong>
                <small>Receipt image + ground truth</small>
              </div>
              <span className="pipeline-arrow">→</span>
              <div className="pipeline-node claim-node">
                <span>02 · Scenario</span>
                <strong>Synthetic claim</strong>
                <small>Seeded inconsistency injection</small>
              </div>
              <span className="pipeline-arrow">→</span>
              <div className="pipeline-branch">
                <div className="pipeline-node model-node mistral-node">
                  <span>03A · OCR</span>
                  <strong>Mistral OCR</strong>
                  <small>Image → normalized schema</small>
                </div>
                <div className="pipeline-node model-node docling-node">
                  <span>03B · Compare</span>
                  <strong>Docling + Mistral</strong>
                  <small>Markdown → normalized schema</small>
                </div>
              </div>
              <span className="pipeline-arrow">→</span>
              <div className="pipeline-node policy-node">
                <span>04 · Control</span>
                <strong>Policy engine</strong>
                <small>Evidence caps + exclusions</small>
              </div>
              <span className="pipeline-arrow">→</span>
              <div className="pipeline-node decision-node">
                <span>05 · Output</span>
                <strong>Partial approval</strong>
                <small>{money(demo.decision.reimbursable_amount)} reimbursable</small>
              </div>
            </div>

            <div className="observability-lane">
              <div className="langfuse-mark">LF</div>
              <div>
                <strong>Langfuse observability plane</strong>
                <span>Every fetch, generation, comparison, policy decision, and evaluator is traced.</span>
              </div>
              <div className="observability-metrics">
                <span><b>{demo.trace.observation_count}</b> observations</span>
                <span><b>{demo.trace.score_count}</b> SDK score events</span>
                <span><b>0</b> mirror errors</span>
              </div>
            </div>

            <div className="architecture-guards">
              <span><i>1</i> Accuracy below 75% → escalate</span>
              <span><i>2</i> Critical OCR disagreement → escalate</span>
              <span><i>3</i> Cash tendered and change → exclude</span>
            </div>
          </article>
        </section>

        <section className="section" id="trace">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Observability</p>
              <h2>Langfuse trace</h2>
            </div>
            <span className="status-note"><i /> Backend trace verified · sanitized view</span>
          </div>

          <article className="root-observation panel">
            <div className="root-marker">ROOT</div>
            <div>
              <span>Chain · success</span>
              <strong>receipt-reconciliation</strong>
              <small>CORD v2 → synthetic claim → extraction → policy decision → evaluation</small>
            </div>
            <div className="root-result">
              <span>Result</span>
              <strong>Partially approved</strong>
              <small>{money(demo.decision.reimbursable_amount)} reimbursable</small>
            </div>
            <div className="root-duration">
              <span>Duration</span>
              <strong>{demo.trace.workflow_duration}</strong>
              <small>1 root + 9 child stages</small>
            </div>
          </article>

          <div className="trace-grid">
            <article className="panel timeline-card">
              <div className="panel-head"><div><span className="trace-icon">⌁</span><strong>Child-stage timeline</strong></div><span>9 stages / {demo.trace.observation_count} total observations</span></div>
              <ol className="timeline">
                {demo.trace.stages.map((observation) => (
                  <li key={observation.name}>
                    <span className={`timeline-dot ${observation.type}`} />
                    <div><strong>{observation.name}</strong><small>{observation.type} · {observation.status}</small></div>
                    <time>{observation.duration}</time>
                  </li>
                ))}
              </ol>
            </article>

            <aside className="trace-side">
              <article className="trace-summary panel">
                <p>Execution health</p>
                <div className="health-score">10<span>/10</span></div>
                <strong>All observations succeeded</strong>
                <small>Authenticated cloud read returned all {demo.trace.backend_observation_count} observations and {demo.trace.backend_score_count} score events.</small>
                <div className="trace-stats">
                  <div><span>Semantic KPIs</span><b>{demo.trace.semantic_evaluations}/4</b></div>
                  <div><span>SDK score events</span><b>{demo.trace.accepted_scores}/{demo.trace.score_count}</b></div>
                  <div><span>Mirror errors</span><b>{demo.trace.error_count}</b></div>
                </div>
              </article>

              <article className="latency-card panel">
                <div className="panel-head"><div><span className="trace-icon">↗</span><strong>Latency profile</strong></div><span>Docling dominates</span></div>
                <div className="latency-list">
                  {demo.trace.latency.map((entry) => (
                    <div className="latency-row" key={entry.label}>
                      <div><span>{entry.label}</span><b>{entry.duration}</b></div>
                      <div className="latency-bar"><span style={{ width: `${Math.max(entry.percent, 1)}%` }} /></div>
                      <small>{entry.percent}%</small>
                    </div>
                  ))}
                </div>
              </article>

              <article className="trace-id panel">
                <span>Cloud verification</span>
                <code>{demo.trace.run_label}</code>
                <small>Langfuse API returned receipt-reconciliation with {demo.trace.backend_observation_count} observations and {demo.trace.backend_score_count} scores; identifiers are masked here.</small>
              </article>
            </aside>
          </div>

          <div className="evaluation-grid" aria-label="Semantic evaluation results">
            {demo.trace.evaluations.map((evaluation) => (
              <article key={evaluation.label}>
                <span>{evaluation.label}</span>
                <strong>{evaluation.value}</strong>
                <small>{evaluation.detail}</small>
              </article>
            ))}
          </div>
        </section>

        <footer>
          <span>Receipt Review / CORD v2 evaluation</span>
          <span>Evidence-backed reimbursement decision</span>
        </footer>
      </main>
    </div>
  );
}
