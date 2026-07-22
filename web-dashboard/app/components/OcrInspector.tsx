"use client";

import { useMemo, useState } from "react";
import Image from "next/image";

type OcrEngine = {
  id: string;
  label: string;
  model: string;
  format: string;
  raw_text: string;
};

type OcrMapping = {
  id: string;
  field: string;
  mistral_raw: string;
  docling_raw: string;
  normalized: string;
  status: string;
  mistral_lines: number[];
  docling_lines: number[];
  region_top: number;
  region_height: number;
};

type InspectorData = {
  access_label: string;
  disclosure: string;
  quality: {
    ground_truth_checks: string;
    normalized_disagreements: number;
    native_confidence: string;
    note: string;
  };
  engines: OcrEngine[];
  mappings: OcrMapping[];
  normalization_notes: string[];
};

export default function OcrInspector({ inspector }: { inspector: InspectorData }) {
  const [revealed, setRevealed] = useState(false);
  const [activeId, setActiveId] = useState("total_paid");
  const activeMapping = useMemo(
    () => inspector.mappings.find((mapping) => mapping.id === activeId) ?? inspector.mappings[0],
    [activeId, inspector.mappings],
  );

  return (
    <article className="ocr-inspector panel" id="ocr-inspector">
      <div className="ocr-inspector-head">
        <div>
          <p className="section-kicker">Human-readable provenance</p>
          <h3>OCR Inspector</h3>
          <span>{inspector.access_label}</span>
        </div>
        <div className="ocr-quality" aria-label="OCR comparison quality">
          <span><b>{inspector.quality.ground_truth_checks}</b> ground-truth checks</span>
          <span><b>{inspector.quality.normalized_disagreements}</b> normalized disagreements</span>
          <span><b>{inspector.quality.native_confidence}</b> native confidence</span>
        </div>
        <button
          className="ocr-reveal-button"
          type="button"
          aria-expanded={revealed}
          aria-controls="ocr-transcripts"
          onClick={() => setRevealed((current) => !current)}
        >
          {revealed ? "Hide sanitized OCR transcript" : "Reveal sanitized OCR transcript"}
        </button>
      </div>

      <div className="ocr-disclosure">
        <span aria-hidden="true">◉</span>
        <p><strong>{inspector.access_label}</strong> · {inspector.disclosure}</p>
      </div>

      <div className="ocr-explorer">
        <section className="ocr-receipt-panel" aria-label="Receipt region explorer">
          <div className="ocr-panel-title">
            <div><strong>Receipt region explorer</strong><span>CORD v2 · train · row 9</span></div>
            <small>Layout anchors, not OCR bounding boxes</small>
          </div>
          <div className="ocr-receipt-paper">
            <Image
              src="/receipt.jpg"
              width={840}
              height={1188}
              unoptimized
              alt="Receipt with selectable OCR evidence regions"
            />
            {inspector.mappings.map((mapping, index) => (
              <button
                key={mapping.id}
                type="button"
                className={`receipt-anchor ${mapping.id === activeMapping.id ? "active" : ""}`}
                style={{ top: `${mapping.region_top}%`, height: `${mapping.region_height}%` }}
                aria-label={`Show OCR evidence for ${mapping.field}`}
                aria-pressed={mapping.id === activeMapping.id}
                onClick={() => setActiveId(mapping.id)}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
              </button>
            ))}
          </div>
          <div className="active-receipt-field" aria-live="polite">
            <span>Selected receipt region</span>
            <strong>{activeMapping.field}</strong>
            <small>{activeMapping.normalized}</small>
          </div>
        </section>

        <section className="ocr-evidence-panel" aria-label="Raw and normalized OCR evidence">
          <div className="ocr-panel-title">
            <div><strong>Raw → normalized evidence</strong><span>Select a field to link receipt and transcript evidence</span></div>
            <small>{inspector.quality.note}</small>
          </div>

          <div className="ocr-mapping-table" role="table" aria-label="OCR raw-to-normalized field mappings">
            <div className="ocr-mapping-row header" role="row">
              <span>Field</span><span>Receipt</span><span>Mistral raw</span><span>Docling raw</span><span>Normalized / status</span>
            </div>
            {inspector.mappings.map((mapping, index) => (
              <div
                className={`ocr-mapping-row ${mapping.id === activeMapping.id ? "active" : ""}`}
                key={mapping.id}
                role="row"
              >
                <strong role="cell">{mapping.field}</strong>
                <span role="cell">
                  <button
                    type="button"
                    className="receipt-link"
                    aria-label={`Show receipt region for ${mapping.field}`}
                    onClick={() => setActiveId(mapping.id)}
                  >
                    Region {String(index + 1).padStart(2, "0")}
                  </button>
                </span>
                <code role="cell">{mapping.mistral_raw}</code>
                <code role="cell">{mapping.docling_raw}</code>
                <span role="cell" className="normalized-cell"><b>{mapping.normalized}</b><small>{mapping.status}</small></span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div id="ocr-transcripts" className={`ocr-transcript-disclosure ${revealed ? "revealed" : "concealed"}`}>
        {revealed ? (
          <>
            <div className="ocr-transcript-grid">
              {inspector.engines.map((engine) => {
                const highlighted = engine.id === "mistral" ? activeMapping.mistral_lines : activeMapping.docling_lines;
                return (
                  <section className="ocr-transcript" key={engine.id} aria-label={`Sanitized OCR transcript from ${engine.label}`}>
                    <div className="ocr-transcript-head">
                      <div><strong>{engine.label}</strong><span>{engine.model}</span></div>
                      <small>{engine.format}</small>
                    </div>
                    <ol>
                      {engine.raw_text.split("\n").map((line, index) => (
                        <li className={highlighted.includes(index) ? "highlighted" : ""} key={`${engine.id}-${index}`}>
                          <code>{line}</code>
                        </li>
                      ))}
                    </ol>
                  </section>
                );
              })}
            </div>
            <div className="normalization-notes">
              {inspector.normalization_notes.map((note) => <span key={note}>✓ {note}</span>)}
            </div>
          </>
        ) : (
          <div className="ocr-concealed-state">
            <div aria-hidden="true"><i /><i /><i /><i /></div>
            <strong>Sanitized OCR transcripts are collapsed</strong>
            <span>Reveal them to inspect sanitized OCR text lines from both pipelines. Keep them collapsed during unrelated screen sharing.</span>
          </div>
        )}
      </div>
    </article>
  );
}
