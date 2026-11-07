"use client";

/**
 * §30.4/§30.5 DQ rule authoring on one Bronze onboarding object, backed by `app.dq_rules`
 * (`/onboarding/objects/{key}/dq-rules`). Rules authored here are pulled into a pipeline's
 * `dataQualityRules` when the object is imported in Build (`dqRulesToPipelineRules`) — every check
 * type generates a pre-copy activity (row_count/freshness as ROW_COUNT_MIN/FRESHNESS_HOURS).
 */
import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api/client";
import type { OnboardingDqCheckType, OnboardingDqRule } from "@/types";

const CHECK_TYPES: Array<{ value: OnboardingDqCheckType; label: string; generated: boolean }> = [
  { value: "not_null", label: "Not null", generated: true },
  { value: "unique", label: "Unique", generated: true },
  { value: "range", label: "Range (min–max)", generated: true },
  { value: "row_count", label: "Row count ≥ threshold", generated: true },
  { value: "freshness", label: "Freshness (hours)", generated: true },
];

type Enforcement = OnboardingDqRule["enforcement"];

interface DqRulesDialogProps {
  projectId: string;
  objectKey: string;
  canEdit: boolean;
  onClose: () => void;
}

function describe(rule: OnboardingDqRule): string {
  const c = rule.config;
  if (rule.checkType === "range") return `${c.column ?? "?"} between ${c.min ?? "?"} and ${c.max ?? "?"}`;
  if (rule.checkType === "freshness") return `${c.column ?? "?"} within ${c.threshold ?? "?"}h`;
  if (rule.checkType === "row_count") return `threshold ${c.threshold ?? "?"}`;
  return c.column ?? "?";
}

export function DqRulesDialog({ projectId, objectKey, canEdit, onClose }: DqRulesDialogProps) {
  const [rules, setRules] = useState<OnboardingDqRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checkType, setCheckType] = useState<OnboardingDqCheckType>("not_null");
  const [enforcement, setEnforcement] = useState<Enforcement>("warn");
  const [column, setColumn] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [threshold, setThreshold] = useState("");

  const load = useCallback(() => {
    api.onboarding.dqRules
      .list(projectId, objectKey)
      .then((r) => {
        setRules(r);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Unable to load data quality rules."));
  }, [projectId, objectKey]);

  useEffect(load, [load]);

  const needsColumn = checkType !== "row_count";
  const needsThreshold = checkType === "row_count" || checkType === "freshness";
  const incomplete =
    (needsColumn && !column.trim()) ||
    (checkType === "range" && (!min.trim() || !max.trim() || Number.isNaN(Number(min)) || Number.isNaN(Number(max)))) ||
    (needsThreshold && !/^\d+$/.test(threshold.trim()));

  async function add() {
    const config: Record<string, string> = {};
    if (needsColumn) config.column = column.trim();
    if (needsThreshold) config.threshold = threshold.trim();
    if (checkType === "range") Object.assign(config, { min: min.trim(), max: max.trim() });
    try {
      await api.onboarding.dqRules.create(projectId, objectKey, { checkType, config, enforcement });
      setColumn("");
      setMin("");
      setMax("");
      setThreshold("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add this rule.");
    }
  }

  async function remove(rule: OnboardingDqRule) {
    try {
      await api.onboarding.dqRules.remove(projectId, objectKey, rule.id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete this rule.");
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      eyebrow="ONBOARDING · DATA QUALITY"
      title={`Data quality rules · ${objectKey}`}
      description="Rules stay with the object. Build copies them into the pipeline when you import this object."
      wide
    >
      {error ? <p className="upload-warning mb-3">{error}</p> : null}

      <div className="table-wrap mb-4">
        <table>
          <thead>
            <tr>
              <th>Check</th>
              <th>Applies to</th>
              <th>Enforcement</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 ? (
              <tr>
                <td colSpan={4} className="text-xs text-muted">
                  No rules yet.
                </td>
              </tr>
            ) : (
              rules.map((rule) => {
                const meta = CHECK_TYPES.find((c) => c.value === rule.checkType);
                return (
                  <tr key={rule.id}>
                    <td>
                      {meta?.label ?? rule.checkType}
                      {meta && !meta.generated ? <span className="status-pill gray ml-2">not generated yet</span> : null}
                    </td>
                    <td>{describe(rule)}</td>
                    <td>{rule.enforcement}</td>
                    <td>
                      {canEdit ? (
                        <button type="button" className="editor-remove" title="Delete rule" onClick={() => remove(rule)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {canEdit ? (
        <div className="form-card"><div className="form-fields">
          <label>
            <span>Check</span>
            <Select value={checkType} onChange={(e) => setCheckType(e.target.value as OnboardingDqCheckType)}>
              {CHECK_TYPES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </label>
          <label>
            <span>Enforcement</span>
            <Select value={enforcement} onChange={(e) => setEnforcement(e.target.value as Enforcement)}>
              <option value="warn">Warn</option>
              <option value="block">Block</option>
              <option value="off">Off</option>
            </Select>
          </label>
          {needsColumn ? (
            <label>
              <span>{checkType === "freshness" ? "Timestamp column" : "Column"}</span>
              <Input value={column} onChange={(e) => setColumn(e.target.value)} />
            </label>
          ) : null}
          {needsThreshold ? (
            <label>
              <span>{checkType === "freshness" ? "Max age (hours)" : "Minimum rows"}</span>
              <Input value={threshold} onChange={(e) => setThreshold(e.target.value)} />
            </label>
          ) : null}
          {checkType === "range" ? (
            <>
              <label>
                <span>Min</span>
                <Input value={min} onChange={(e) => setMin(e.target.value)} />
              </label>
              <label>
                <span>Max</span>
                <Input value={max} onChange={(e) => setMax(e.target.value)} />
              </label>
            </>
          ) : null}
        </div></div>
      ) : null}

      <div className="wizard-actions">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
        {canEdit ? (
          <Button disabled={incomplete} onClick={add}>
            <Plus className="h-3.5 w-3.5" />
            Add rule
          </Button>
        ) : null}
      </div>
    </Dialog>
  );
}
